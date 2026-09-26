#!/usr/bin/env python3
"""按房号回填房源楼层（历史数据一次性回填；默认只演练，不动库）。

依据与在线录入一致：`tools/real_estate_property.py::infer_floor` —— 房号形如「楼层+户号」
（4 位取前两位 1602→16层；3 位取首位 301→3层），标题里明写「顶楼/高楼层」等优先采用。
**只填空缺，绝不覆盖已有楼层**；推不出依据的一律跳过（不臆造）。

用法（在已装实例的服务器上跑）：
    python3 scripts/backfill_floor_from_title.py --dry-run        # 默认：只打清单
    python3 scripts/backfill_floor_from_title.py --dry-run --limit 20
    python3 scripts/backfill_floor_from_title.py --apply          # 真正写库（建议先跑 dry-run 看过）

说明：脚本走 agent/real_estate_db 与 .env.db 的 DATABASE_URL（与业务同一套配置）；
不需要 ssh / psql，也不绕过加密层（楼层不是加密字段）。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_env_db() -> None:
    """读取安装目录里的 .env.db（与 install.sh / update.sh 一致），已有环境变量则不覆盖。"""
    env_file = Path(os.environ.get("COCO_ENV_FILE") or (REPO_ROOT / ".env.db"))
    if not env_file.is_file():
        return
    for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="按房号回填房源楼层（默认演练）")
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只演练）")
    ap.add_argument("--dry-run", action="store_true", help="显式演练（默认行为）")
    ap.add_argument("--limit", type=int, default=0, help="只看前 N 套（便于抽样）")
    args = ap.parse_args(argv)

    _load_env_db()
    if not os.environ.get("DATABASE_URL"):
        print("  提示：没有 DATABASE_URL（可在安装目录放 .env.db，或用 COCO_ENV_FILE 指向它）")
        return 2

    from agent.real_estate_db import get_real_estate_db
    from tools.real_estate_property import infer_floor

    db = get_real_estate_db()
    # 必须用 search_properties（它走 to_dict，包含 floor 字段）；
    # 不要用 iter_available_properties —— 那是匹配专用的精简投影（不含 floor），
    # 会让人误判"所有房源都缺楼层"，进而覆盖掉已经录好的楼层（实测踩过）。
    props = db.search_properties(limit=200000)

    if args.limit:
        props = props[: args.limit]

    missing, proposals, skipped = 0, [], 0
    for p in props:
        if p.get("floor"):
            continue
        missing += 1
        guess, why = infer_floor(p.get("title"), p.get("address"))
        if guess:
            proposals.append((p.get("id"), p.get("title"), guess, why))
        else:
            skipped += 1

    print(f"  在售房源 {len(props)} 套；缺楼层 {missing} 套")
    print(f"  可推断 {len(proposals)} 套；无依据（跳过）{skipped} 套")
    if proposals:
        print("\n  编号    推断楼层   依据            标题")
        for pid, title, guess, why in proposals[:200]:
            print(f"  {pid:<6}  {guess:<8}  {why:<14}  {str(title)[:34]}")
        if len(proposals) > 200:
            print(f"  …（还有 {len(proposals) - 200} 套，完整清单可加 --limit 分批看）")

    if not args.apply:
        print("\n  演练模式：未改动任何数据。确认清单无误后加 --apply 写库。")
        return 0

    changed = failed = 0
    for pid, _title, guess, _why in proposals:
        try:
            db.update_property(pid, floor=guess)
            changed += 1
        except Exception as exc:  # 单条失败不影响整体
            failed += 1
            print(f"  !! {pid} 回填失败：{type(exc).__name__}: {exc}")
    print(f"\n  已回填 {changed} 套；失败 {failed} 套（只填空缺，未覆盖任何已有楼层）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
