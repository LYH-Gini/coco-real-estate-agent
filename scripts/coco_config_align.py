#!/usr/bin/env python3
"""Coco 运行时配置对齐（幂等）。

为什么需要这个脚本
------------------
Hermes 读配置时，**config.yaml 里显式写过的值优先于代码里的默认值**，而且"新版默认值不会
自动补写进配置文件"。所以：

* 只改代码默认值（config_defaults.py）→ 对**已安装实例无效**（它的 config.yaml 里早就存着旧值）；
* 只写在 config.yaml 里 → 会被重跑向导（官方向导写 `agent.max_turns = 150`）、重装、
  恢复配置冲掉。

两头都靠不住，所以把 Coco 的标准值**显式写进 config.yaml**，安装与更新各跑一次：
新装即正确，老实例更新即对齐，重装后也丢不了。

用法：
    python scripts/coco_config_align.py            # 写入/对齐（幂等，已一致则不动）
    python scripts/coco_config_align.py --check    # 只核对运行时生效值；不一致退出码 1
    python scripts/coco_config_align.py --verbose  # 打印写入过程
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
from pathlib import Path

# Coco 标准运行时配置（2026-09-19 拍板：轮次 500、压缩阈值 0.8、保留最近 40 条、
# 网关卫生 5000（官方默认）、时区北京时间）
STANDARD: "dict[str, object]" = {
    "agent.max_turns": 500,
    "compression.threshold": 0.8,
    "compression.protect_last_n": 40,
    "compression.hygiene_hard_message_limit": 5000,
    "timezone": "Asia/Shanghai",
    # 漏跑不补（2026-09-23 拍板"机器没开定时任务就不跑了"）：官方默认 True，
    # 机器关机/网关没起时错过的提醒会在下次启动后补发一条（如下午 3 点收到早报）。
    "cron.catch_up_missed": False,
    # 界面语言（2026-09-23 拍板=中文）：官方 i18n 的静态文案（未设主页频道提示、
    # 审批提示、会话相关回复等）都按这个值选语言；不设时官方默认 en，用户会看到英文。
    # 注意：只影响官方静态文案，不影响 Coco 的房产话术与模型输出。
    "display.language": "zh",
    # 对话清空类命令（/new、/clear、/reset、/undo）不再弹确认框：经纪人不会输入 /always，
    # 三选一反而卡住「开新会话」；关掉后直接执行（业务数据都在库里，不受影响）。
    "approvals.destructive_slash_confirm": False,
    # 会话记录保留期（2026-09-26 拍板 3650 天）：官方默认 90 天，网关启动时会**真删**
    # 「已结束且 90 天没动静」的会话正文与磁盘转录（hermes_state_maintenance.prune_sessions）。
    # Coco 是单人/少人用，state.db 增长极慢，没必要为了省几 MB 丢掉历史对话。
    "sessions.retention_days": 3650,
}

# 官方 Hermes 的默认值 / 官方设置向导会写进去的值（权威来源：官方 v2026.9.14 的
# hermes_cli/config_defaults.py 与 setup.py / setup_quick.py）。
#
# 为什么要这张表：**被官方流程写回默认值**跟"经纪人自己调过"是两回事。没有这张表时，
# 两者都落在"∉ {标准值, 我们上次写的值}"里 → 一律被当成"经纪人改的"保留 → 而体检又按
# "≠标准"报警、建议重跑 update.sh（重跑还是保留）→ 出现**永远修不好的 WARN**。
# 真实案例：服务器 compression.threshold 被写回官方默认 0.5，体检一直 WARN。
OFFICIAL_DEFAULTS: "dict[str, tuple]" = {
    "agent.max_turns": (150, 90),                  # 官方向导 setup.py=150 / setup_quick.py=90
    "compression.threshold": (0.5,),               # 官方默认 0.50
    "compression.protect_last_n": (20,),           # 官方默认 20
    "compression.hygiene_hard_message_limit": (5000,),
    "timezone": ("", None),
    "display.language": ("en",),                      # 官方默认 en（不设或向导写回时都当官方默认）
    "approvals.destructive_slash_confirm": (True,),   # 官方默认 True（弹确认框）
    "cron.catch_up_missed": (True,),                  # 官方默认 True（错过会补发一次）
    "sessions.retention_days": (90,),                 # 官方默认 90 天（到期真删会话正文）
}


# 人话说明（终端与体检输出用）
LABELS: "dict[str, str]" = {
    "agent.max_turns": "单次任务最大轮次",
    "compression.threshold": "上下文压缩阈值",
    "compression.protect_last_n": "保留最近消息条数",
    "compression.hygiene_hard_message_limit": "网关强制压缩消息上限",
    "timezone": "时区",
    "cron.catch_up_missed": "漏跑不补（机器没开就不发）",
    "display.language": "界面语言",
    "approvals.destructive_slash_confirm": "清空对话类命令的确认框",
    "sessions.retention_days": "会话记录保留天数",
}


def _backup_config() -> None:
    """改配置前留一份备份（config.yaml.coco-bak），出问题可对比/回滚。"""
    from hermes_cli.config import get_config_path

    try:
        cfg = Path(get_config_path())
        if cfg.is_file():
            shutil.copy2(cfg, cfg.with_suffix(cfg.suffix + ".coco-bak"))
    except Exception:
        pass


def dig(cfg, key: str, default=None):
    """按点号路径取值（纯函数，便于单测）。"""
    cur = cfg
    for part in key.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part)
        if cur is None:
            return default
    return cur


def effective_values() -> "dict[str, object]":
    """运行时**实际生效**的值（Hermes load_config：默认值深合并 + 用户配置覆盖）。"""
    from hermes_cli.config import load_config

    cfg = load_config()
    return {key: dig(cfg, key) for key in STANDARD}


def _state_path():
    """对齐状态文件（记录"我们上次写进去的值"），放在用户配置旁边 —— 未跟踪，更新不碰。"""
    from pathlib import Path

    from hermes_cli.config import get_config_path

    return Path(get_config_path()).parent / ".coco_config_aligned.json"


def _read_state() -> dict:
    import json

    try:
        with open(_state_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(written: dict) -> None:
    import json
    from datetime import datetime

    try:
        with open(_state_path(), "w", encoding="utf-8") as fh:
            json.dump({"written": written, "updated_at": datetime.now().isoformat(timespec="seconds")},
                      fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _same(a, b) -> bool:
    """数值容忍比较（0.8 与 0.8000000000000001 视为相同）"""
    if a == b:
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-9
    return False


def _is_official_default(key: str, value) -> bool:
    """这个值是不是官方默认值 / 官方向导会写的值（= 被官方流程覆盖，不是经纪人的自定义）"""
    return any(_same(value, v) for v in OFFICIAL_DEFAULTS.get(key, ()))


def classify(effective: "dict[str, object] | None" = None, state: "dict | None" = None):
    """把每个标准键分三类 —— **唯一判定入口**（plan()/apply()/体检都走这里，避免两套口径打架）。

    返回 (aligned, reclaimable, custom)：
      aligned     : [(key, 值)]           已是标准值
      reclaimable : [(key, 值, 原因)]     应当拉回标准值；原因 ∈ missing / first-run / standard-upgrade / official-default
      custom      : [(key, 值)]          经纪人自己设的值 → 保留（并用 --force 才拉回）
    """
    effective = effective_values() if effective is None else effective
    state = _read_state() if state is None else state
    last_written = dict(state.get("written") or {})
    first_run = not last_written

    aligned, reclaimable, custom = [], [], []
    for key, want in STANDARD.items():
        got = effective.get(key)
        if _same(got, want):
            aligned.append((key, got))
            continue
        if got in (None, ""):
            reclaimable.append((key, got, "missing"))
            continue
        if first_run:
            reclaimable.append((key, got, "first-run"))
            continue
        if _same(got, last_written.get(key)):
            reclaimable.append((key, got, "standard-upgrade"))
            continue
        if _is_official_default(key, got):
            reclaimable.append((key, got, "official-default"))
            continue
        custom.append((key, got))
    return aligned, reclaimable, custom


def summary(effective: "dict[str, object] | None" = None, state: "dict | None" = None) -> "dict":
    """给终端 / 部署体检用的一句话结论（纯函数，便于单测）。

    level=pass  → 已对齐
    level=warn  → 需要拉回标准值（建议有效：跑 update.sh 真的能改）
    level=info  → 是经纪人自己的设置，已保留（**不是故障，不该报 WARN**）
    """
    aligned, reclaimable, custom = classify(effective, state)
    detail_reclaim = "；".join(
        f"{LABELS.get(k, k)} 生效 {g!r} ≠ 标准 {STANDARD[k]!r}"
        + ("（被官方默认值覆盖）" if r == "official-default" else "")
        for k, g, r in reclaimable
    )
    detail_custom = "；".join(f"{LABELS.get(k, k)}={v}" for k, v in custom)

    if not reclaimable and not custom:
        return {"level": "pass", "message": "运行时配置已对齐 Coco 标准", "hint": None}
    if reclaimable and not custom:
        return {"level": "warn", "message": f"配置与 Coco 标准不一致：{detail_reclaim}",
                "hint": "修复：coco update（会自动拉回以上各项）"}
    if custom and not reclaimable:
        return {"level": "info",
                "message": f"检测到你自己调整过的设置，已按「自定义优先」保留：{detail_custom}",
                "hint": "如需拉回标准值：coco update --force"}
    return {"level": "warn",
            "message": f"配置与 Coco 标准不一致：{detail_reclaim}；另有你的自定义设置已保留：{detail_custom}",
            "hint": "修复：coco update（会自动拉回不一致项，自定义项仍保留）"}


def plan(effective: "dict[str, object] | None" = None, state: "dict | None" = None):
    """决定每个键怎么处理。返回 (to_align, preserved)：

    - to_align: [(key, 当前值, 标准值), ...] —— 需要写入标准值
    - preserved: [(key, 当前值), ...] —— **经纪人自己改过，保留不动**

    判定规则（2026-09-19 拍板："经纪人改过就不动"）：
    1. 没有状态文件（首次对齐，含存量实例） → 一律对齐（这是修复"设定失效"的初衷）
    2. 有状态文件：
       - 当前值 == 标准值            → 不动（顺带刷新状态）
       - 当前值 == 我们上次写的值     → 我们在升级标准值 → 对齐
       - 当前值 ∉ {标准值, 上次写的}  → **经纪人改的 → 保留**
       - 当前值缺失/为空             → 对齐（补上）
    """
    _aligned, reclaimable, custom = classify(effective, state)
    to_align = [(key, got, STANDARD[key]) for key, got, _reason in reclaimable]
    preserved = [(key, got) for key, got in custom]
    return to_align, preserved


def diffs(effective: "dict[str, object] | None" = None):
    """返回 [(key, 生效值, 标准值), ...]；已一致则空列表。"""
    effective = effective_values() if effective is None else effective
    out = []
    for key, want in STANDARD.items():
        got = effective.get(key)
        if not _same(got, want):
            out.append((key, got, want))
    return out


def apply(verbose: bool = False, force: bool = False) -> "list[tuple[str, object, object]]":
    """把标准值写入 config.yaml；返回被修正的 [(key, 旧值, 新值), ...]。

    force=True（或 COCO_FORCE_CONFIG=1）：无视"经纪人改过"的保护，一律拉回标准值。
    """
    from hermes_cli.config import set_config_value

    force = force or os.environ.get("COCO_FORCE_CONFIG") == "1"
    state = _read_state()
    written = dict(state.get("written") or {})
    effective = effective_values()

    if force:
        items = diffs(effective)
        preserved = []
    else:
        items, preserved = plan(effective, state)

    changed = []
    if items:
        _backup_config()
    for key, got, want in items:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            set_config_value(key, str(want))
        changed.append((key, got, want))
        if verbose:
            print(buf.getvalue(), end="")
    # 状态文件记录"我们写了什么"，下次才能区分"我们写的"与"经纪人改的"
    for key, _got, want in changed:
        written[key] = want
    for key, want in STANDARD.items():
        if key not in written and _same(effective.get(key), want):
            written[key] = want
    if changed or preserved:
        _write_state(written)
    reclaimed = [(k, g, w) for k, g, w in changed if _is_official_default(k, g)]
    if reclaimed:
        names = "；".join(f"{LABELS.get(k, k)} {g} → {w}" for k, g, w in reclaimed)
        print(f"  已拉回被官方默认值覆盖的设置（不是你改的，官方流程写回）：{names}")
    if preserved:
        names = "；".join(f"{LABELS.get(k, k)}={v}" for k, v in preserved)
        print(f"  已保留你自行调整的设置（未拉回标准值）：{names}")
    return changed


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    check_only = "--check" in argv
    verbose = "--verbose" in argv
    force = "--force" in argv or os.environ.get("COCO_FORCE_CONFIG") == "1"

    try:
        if check_only:
            to_align, preserved = plan()
            for key, got, want in to_align:
                print(f"  {key}: 生效 {got!r} ≠ 标准 {want!r}（{LABELS.get(key, '')}）")
            for key, got in preserved:
                print(f"  {LABELS.get(key, key)}: {got!r}（经纪人自行调整，已保留）")
            return 1 if to_align else 0
        changed = apply(verbose=verbose, force=force)
    except Exception as exc:  # 配置读不到/写不进时不能拖垮安装与更新
        print(f"  提示: 配置对齐未完成（{exc}）")
        return 2

    if changed:
        for key, got, want in changed:
            print(f"  已对齐 {LABELS.get(key, key)}: {got} → {want}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
