"""Coco 运行时配置对齐（scripts/coco_config_align.py）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import coco_config_align as align  # noqa: E402


def test_standard_values_are_the_agreed_ones():
    """拍板的标准值，改动必须是有意的。"""
    assert align.STANDARD == {
        "agent.max_turns": 500,
        "compression.threshold": 0.8,
        "compression.protect_last_n": 40,
        "compression.hygiene_hard_message_limit": 5000,
        "timezone": "Asia/Shanghai",
        "display.language": "zh",   # 2026-09-23 拍板：界面语言中文
        "cron.catch_up_missed": False,  # 2026-09-23 拍板：机器没开就不补跑
        "approvals.destructive_slash_confirm": False,
        "sessions.retention_days": 3650,   # 2026-09-26 拍板：会话正文不再 90 天自动删
        "display.platforms.feishu.tool_progress": "off",   # 2026-09-27 拍板：飞书不显示工具进展行
        "display.memory_notifications": "off",   # 2026-09-30 拍板：不在对话里播报自我改进
    }


def test_dig_reads_nested_and_missing():
    cfg = {"agent": {"max_turns": 150}, "compression": {"threshold": 0.5}}
    assert align.dig(cfg, "agent.max_turns") == 150
    assert align.dig(cfg, "compression.threshold") == 0.5
    assert align.dig(cfg, "compression.protect_last_n") is None
    assert align.dig(cfg, "timezone", default="") == ""
    assert align.dig({"agent": "broken"}, "agent.max_turns") is None


def test_diffs_flags_official_defaults():
    """官方向导写的 config.yaml（150 / 0.5 / 20）必须被判定为不一致。"""
    eff = dict(align.STANDARD)      # 以标准值为基准再覆盖，STANDARD 加键不必改本函数
    eff.update({
        "agent.max_turns": 150,
        "compression.threshold": 0.5,
        "compression.protect_last_n": 20,
        "timezone": "UTC",
        "display.language": "en",   # 官方向导写回的语言默认值
        "cron.catch_up_missed": True,  # 官方默认：错过会补发一次
    })
    bad = dict((k, (g, w)) for k, g, w in align.diffs(eff))
    assert set(bad) == {
        "agent.max_turns",
        "compression.threshold",
        "compression.protect_last_n",
        "timezone",
        "display.language",
        "cron.catch_up_missed",
    }
    assert bad["agent.max_turns"] == (150, 500)


def test_diffs_ok_when_aligned():
    eff = dict(align.STANDARD)
    eff["compression.threshold"] = 0.8000000000000001  # 浮点写法差异不算偏差
    assert align.diffs(eff) == []


def test_check_mode_reports_mismatch(monkeypatch, capsys):
    monkeypatch.setattr(align, "effective_values", lambda: {"agent.max_turns": 150})
    rc = align.main(["--check"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "agent.max_turns" in out


def test_check_mode_passes_when_aligned(monkeypatch, capsys):
    eff = dict(align.STANDARD)  # 以标准值为基准，STANDARD 加键不必改本用例
    monkeypatch.setattr(align, "effective_values", lambda: eff)
    assert align.main(["--check"]) == 0
    assert capsys.readouterr().out == ""


def test_apply_is_noop_when_aligned(monkeypatch):
    eff = dict(align.STANDARD)  # 以标准值为基准，STANDARD 加键不必改本用例
    monkeypatch.setattr(align, "effective_values", lambda: eff)
    assert align.apply() == []


def test_main_never_raises_on_broken_config(monkeypatch, capsys):
    def boom():
        raise RuntimeError("配置读不到")

    monkeypatch.setattr(align, "effective_values", boom)
    assert align.main([]) == 2
    assert "配置对齐未完成" in capsys.readouterr().out


def test_apply_backs_up_config(monkeypatch, tmp_path):
    """改配置前必须留一份 .coco-bak 备份。"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("agent:\n  max_turns: 150\n", encoding="utf-8")
    import hermes_cli.config as hcfg

    monkeypatch.setattr(hcfg, "get_config_path", lambda: cfg)
    monkeypatch.setattr(hcfg, "set_config_value", lambda key, value: None)
    monkeypatch.setattr(align, "_read_state", lambda: {"written": {}})
    monkeypatch.setattr(align, "_write_state", lambda d: None)
    monkeypatch.setattr(align, "effective_values", lambda: {"agent.max_turns": 150})
    monkeypatch.setattr(align, "plan", lambda eff=None, state=None: ([("agent.max_turns", 150, 500)], []))

    changed = align.apply()
    assert changed == [("agent.max_turns", 150, 500)]
    assert (tmp_path / "config.yaml.coco-bak").is_file()


# ---------------- "经纪人改过就不动"（2026-09-19 拍板） ----------------
def _eff(**over):
    base = dict(align.STANDARD)   # 以标准值为基准：新增标准键不必改本函数
    base.update(over)
    return base


def test_plan_first_run_aligns_everything():
    """没有状态文件（首次对齐，含存量实例）→ 一律对齐（修好"设定失效"）"""
    eff = _eff(**{"agent.max_turns": 150, "compression.threshold": 0.5, "timezone": "UTC"})
    to_align, preserved = align.plan(eff, state={})
    assert {k for k, _g, _w in to_align} == {"agent.max_turns", "compression.threshold", "timezone"}
    assert preserved == []


def test_plan_preserves_broker_customisation():
    """有状态文件 + 当前值既不是标准值也不是我们写的值 → 判定为经纪人改的，保留"""
    eff = _eff(**{"agent.max_turns": 300})
    state = {"written": dict(align.STANDARD)}
    to_align, preserved = align.plan(eff, state)
    assert to_align == []
    assert preserved == [("agent.max_turns", 300)]


def test_plan_follows_our_standard_upgrade():
    """当前值 == 我们上次写的值（说明经纪人没动）→ 我们的标准升级了，跟着对齐"""
    eff = _eff(**{"agent.max_turns": 400})
    state = {"written": {"agent.max_turns": 400}}
    to_align, preserved = align.plan(eff, state)
    assert to_align == [("agent.max_turns", 400, 500)]
    assert preserved == []


def test_plan_fills_missing_value():
    """键缺失 → 补上标准值（不算"经纪人改过"）"""
    eff = _eff(**{"timezone": None})
    state = {"written": {"agent.max_turns": 500}}
    to_align, preserved = align.plan(eff, state)
    assert ("timezone", None, "Asia/Shanghai") in to_align
    assert preserved == []


def test_apply_force_overrides_preservation(monkeypatch, tmp_path):
    """COCO_FORCE_CONFIG=1 → 无视保护，强制拉回标准值"""
    written = {}

    class _Cfg:
        @staticmethod
        def get_config_path():
            return tmp_path / "config.yaml"

    monkeypatch.setenv("COCO_FORCE_CONFIG", "1")
    monkeypatch.setattr(align, "effective_values", lambda: _eff(**{"agent.max_turns": 300}))
    monkeypatch.setattr(align, "_read_state", lambda: {"written": {"agent.max_turns": 500}})
    monkeypatch.setattr(align, "_write_state", lambda d: written.update(d))
    monkeypatch.setattr(align, "_backup_config", lambda: None)
    import hermes_cli.config as hcfg
    monkeypatch.setattr(hcfg, "set_config_value", lambda key, value: None)
    changed = align.apply()
    assert changed == [("agent.max_turns", 300, 500)]


def test_apply_preserves_without_force(monkeypatch, tmp_path):
    """不加 force 时经纪人自改的值不动，且会打印已保留"""
    monkeypatch.delenv("COCO_FORCE_CONFIG", raising=False)
    monkeypatch.setattr(align, "effective_values", lambda: _eff(**{"agent.max_turns": 300}))
    monkeypatch.setattr(align, "_read_state", lambda: {"written": {"agent.max_turns": 500}})
    monkeypatch.setattr(align, "_write_state", lambda d: None)
    monkeypatch.setattr(align, "_backup_config", lambda: None)
    import hermes_cli.config as hcfg
    called = []
    monkeypatch.setattr(hcfg, "set_config_value", lambda key, value: called.append(key))
    assert align.apply() == []
    assert called == []


def test_memory_notifications_defaults_off_and_is_reclaimable():
    """自我改进播报默认关（2026-09-30 拍板：经纪人不该看到「技能…已完成补丁更新」）

    两头都要守：① 代码默认值 = off（新装即生效）；② 官方默认 on 要进 OFFICIAL_DEFAULTS，
    否则官方流程把它写回 on 时会被当成"经纪人自己改的"保留下来（就是那次"永远修不好的 WARN"）。
    """
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["display"]["memory_notifications"] == "off"
    assert align.STANDARD["display.memory_notifications"] == "off"
    assert "on" in align.OFFICIAL_DEFAULTS["display.memory_notifications"]
