"""运行时配置对齐的判定规则测试（2026-09-21 修「永远修不好的 WARN」）

背景（实测）：更新脚本按「经纪人改过就不动」保留了 compression.threshold=0.5，
而部署体检按「≠标准」直接报 WARN，还建议重跑 update.sh —— 重跑仍然保留，于是**永远修不好**。
根因：0.5 是**官方默认值**（被官方流程写回 config.yaml），被误判成「经纪人自定义」。

修法：判定收敛到唯一入口 `classify()`：
  - 已是标准值                                → aligned
  - 缺失 / 首次对齐 / 我们写的旧标准 / **等于官方默认值** → reclaimable（拉回标准）
  - 其余（真正没人写过、又不是官方默认的值）      → custom（保留，附 --force 说明）

本文件钉住这四种情形与三种输出级别（pass / warn / info）。
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from coco_config_align import STANDARD, classify, plan, summary  # noqa: E402


# 短名 → 运行时键；eff() 一律**以 STANDARD 为基准**再覆盖，
# 否则 STANDARD 加一项就会在这里表现成"缺失"（2026-09-23 加 cron.catch_up_missed 时踩过）。
_SHORT = {
    "threshold": "compression.threshold",
    "max_turns": "agent.max_turns",
    "protect": "compression.protect_last_n",
    "hygiene": "compression.hygiene_hard_message_limit",
    "tz": "timezone",
    "slash_confirm": "approvals.destructive_slash_confirm",
    "language": "display.language",
    "catch_up": "cron.catch_up_missed",
}


def eff(**overrides):
    """造一份运行时生效值（默认=标准值，按需覆盖；值为 None 表示该项未生效）"""
    values = dict(STANDARD)
    for short, value in overrides.items():
        values[_SHORT[short]] = value
    return values


def st(written=None):
    """造一份状态文件（记录我们上次写进去的值）；不传=记录标准值"""
    return {"written": dict(written or STANDARD)}


class TestClassify:
    def test_aligned(self):
        aligned, reclaimable, custom = classify(eff(), st())
        assert len(aligned) == len(STANDARD) and not reclaimable and not custom

    def test_official_default_is_reclaimable(self):
        """被官方默认值覆盖 → 可拉回（这就是被官方默认值覆盖的情形）"""
        aligned, reclaimable, custom = classify(eff(threshold=0.5), st())
        keys = {k: r for k, _v, r in reclaimable}
        assert keys.get("compression.threshold") == "official-default", reclaimable
        assert not custom

    def test_our_old_standard_is_reclaimable(self):
        """我们上次写的是旧标准值 → 标准升级，可拉回"""
        _a, reclaimable, custom = classify(eff(protect=20), st({**STANDARD, "compression.protect_last_n": 20}))
        assert {k: r for k, _v, r in reclaimable}.get("compression.protect_last_n") == "standard-upgrade"
        assert not custom

    def test_true_custom_is_preserved(self):
        """真正没人写过、也不是官方默认的值 → 判为经纪人自定义，保留"""
        _a, reclaimable, custom = classify(eff(threshold=0.6), st())
        assert not reclaimable
        assert dict(custom).get("compression.threshold") == 0.6

    def test_first_run_aligns_everything(self):
        _a, reclaimable, custom = classify(eff(threshold=0.6), {"written": {}})
        assert {k: r for k, _v, r in reclaimable}.get("compression.threshold") == "first-run"
        assert not custom

    def test_missing_value_is_reclaimable(self):
        _a, reclaimable, _c = classify(eff(threshold=None), st())
        assert {k: r for k, _v, r in reclaimable}.get("compression.threshold") == "missing"


class TestDestructiveSlashConfirmDefault:
    """经纪人不会输入 /always：Coco 默认关闭「清空对话类命令」的确认框（2026-09-21 要求）"""

    def test_standard_and_official_cover_the_key(self):
        assert STANDARD["approvals.destructive_slash_confirm"] is False
        from coco_config_align import OFFICIAL_DEFAULTS

        assert True in OFFICIAL_DEFAULTS["approvals.destructive_slash_confirm"]

    def test_official_true_is_reclaimed(self):
        """服务器上是官方默认 True（弹框）→ 应被拉回成 False（不弹框）"""
        _a, reclaimable, custom = classify(eff(slash_confirm=True), st())
        assert {k: r for k, _v, r in reclaimable}.get("approvals.destructive_slash_confirm") == "official-default"
        assert not custom

    def test_aligned_when_off(self):
        aligned, reclaimable, _c = classify(eff(slash_confirm=False), st())
        assert ("approvals.destructive_slash_confirm", False) in aligned

    def test_code_default_is_off(self):
        """代码默认值也必须是 False（否则新装实例还会弹框）"""
        from hermes_cli.config_defaults import DEFAULT_CONFIG

        assert DEFAULT_CONFIG["approvals"]["destructive_slash_confirm"] is False


class TestPlanAndSummary:
    def test_plan_reclaims_official_default(self):
        to_align, preserved = plan(eff(threshold=0.5), st())
        assert ("compression.threshold", 0.5, 0.8) in to_align
        assert preserved == []

    def test_plan_preserves_true_custom(self):
        to_align, preserved = plan(eff(threshold=0.6), st())
        assert to_align == []
        assert dict(preserved).get("compression.threshold") == 0.6

    def test_summary_levels(self):
        assert summary(eff(), st())["level"] == "pass"
        warn = summary(eff(threshold=0.5), st())
        assert warn["level"] == "warn" and "coco update" in (warn["hint"] or "")
        assert "官方默认值覆盖" in warn["message"], warn["message"]
        info = summary(eff(threshold=0.6), st())
        assert info["level"] == "info", "经纪人自己的设置不该报 WARN"
        assert "--force" in (info["hint"] or "")

    def test_summary_mixed_keeps_custom_and_warns_reclaimable(self):
        s = summary(eff(threshold=0.5, protect=35), st())
        assert s["level"] == "warn"
        assert "自定义" in s["message"] and "保留" in s["message"]


class TestCatchUpMissedDefault:
    """漏跑不补（2026-09-23 拍板"机器没开定时任务就不跑了"）

    官方默认 catch_up_missed=True：机器关机/网关没起时错过的提醒会在下次启动后补发一条
    （如下午三点收到 09:00 的早报）。Coco 的标准值是 False。
    """

    def test_standard_and_official_cover_the_key(self):
        assert STANDARD["cron.catch_up_missed"] is False
        from coco_config_align import OFFICIAL_DEFAULTS

        assert True in OFFICIAL_DEFAULTS["cron.catch_up_missed"]

    def test_official_true_is_reclaimed(self):
        _a, reclaimable, custom = classify(eff(catch_up=True), st())
        assert {k: r for k, _v, r in reclaimable}.get("cron.catch_up_missed") == "official-default"
        assert not custom

    def test_code_default_is_catch_up(self):
        """官方代码默认确实会补跑（我们只是把它按标准值拉成 False）"""
        from hermes_cli.config_defaults import DEFAULT_CONFIG

        assert DEFAULT_CONFIG["cron"]["catch_up_missed"] is True
