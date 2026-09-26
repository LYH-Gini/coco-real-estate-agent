#!/usr/bin/env python3
"""Coco 房产智能体 - 数据清理向导（coco data-clean 的实现体）

用途：把"为了不再看到"而标记成已成交/已关闭的数据真正清掉，或把误标的状态恢复。
安全设计（照备份/恢复/卸载那套口径）：
  · 默认先预演，动手前自动整库备份，备份失败即中止（可用 --no-backup 跳过，不推荐）
  · 有关联带看/成交/跟进/需求变更/转介绍的记录默认跳过（保护历史），--force 才连历史一起删
  · 非交互环境不给档位参数时直接报错（不挂着等输入）；--yes 供自动化
  · 结束打印"清理前 / 清理后 / 备份文件位置"，出问题可用 coco restore 回滚
"""
import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from backup_db import DatabaseBackup, _load_db_config  # noqa: E402

MAX_LISTED = 20  # 预演最多逐条列多少条，其余只报条数


def _load_db():
    """按 .env.db 里的连接串初始化数据库（与其它脚本同源）。"""
    url = _load_db_config()
    if not url:
        print("读取不到数据库连接信息（.env.db 缺失）。请先跑 coco check 体检。", file=sys.stderr)
        sys.exit(1)
    os.environ["DATABASE_URL"] = url
    from agent.real_estate_db import init_real_estate_db
    init_real_estate_db(url)
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


def _overview(db):
    """当前库内规模（清理前/后打印用）"""
    from agent.real_estate_db import Customer, Property
    with db.get_session() as s:
        prop_by_status = {row[0]: row[1] for row in s.query(Property.status, _count_col(Property)).group_by(Property.status).all()}
        cust_by_status = {row[0]: row[1] for row in s.query(Customer.status, _count_col(Customer)).group_by(Customer.status).all()}
    props = sum(prop_by_status.values())
    customers = sum(cust_by_status.values())
    tracking = customers - cust_by_status.get("closed", 0)
    return {
        "properties": props,
        "available": prop_by_status.get("available", 0),
        "sold": prop_by_status.get("sold", 0),
        "rented": prop_by_status.get("rented", 0),
        "customers": customers,
        "tracking": tracking,
        "closed": cust_by_status.get("closed", 0),
    }


def _count_col(model):
    from sqlalchemy import func
    return func.count(model.id)


def _print_overview(label, stat):
    print(f"{label}：房源 {stat['properties']} 套（在售 {stat['available']} / 已售 {stat['sold']} / 已租 {stat['rented']}）；"
          f"客户 {stat['customers']} 位（在跟 {stat['tracking']} / 已关闭 {stat['closed']}）")


def _describe(entry, bullet=True):
    related = entry.get("related", {})
    parts = [f"{_RELATED_LABELS[k]}{v}条" for k, v in related.items() if v]
    tail = ("；关联：" + "、".join(parts)) if parts else ""
    prefix = "  · " if bullet else ""
    if entry["kind"] == "property":
        return f"{prefix}房源 #{entry['id']} {entry['title']}（{_STATUS_LABELS.get(entry['status'], entry['status'])}）{tail}"
    return f"{prefix}客户 #{entry['id']} {entry['name']}（{_STATUS_LABELS.get(entry['status'], entry['status'])}）{tail}"


_RELATED_LABELS = {
    "followups": "跟进", "viewings": "带看", "deals": "成交",
    "changes": "需求变更", "referrals": "转介绍", "price_history": "调价",
}
_STATUS_LABELS = {"sold": "已售", "rented": "已租", "closed": "已关闭", "available": "在售", "active": "在跟"}


def _print_preview(preview, force=False):
    entries = preview["entries"]
    if not entries:
        print("没有符合条件的数据，无需清理。")
        return False
    print(f"\n符合条件的数据共 {preview['matched']} 条：")
    for entry in entries[:MAX_LISTED]:
        print(_describe(entry))
    if len(entries) > MAX_LISTED:
        print(f"  …… 另有 {len(entries) - MAX_LISTED} 条未逐条列出")
    if preview["skipped"]:
        if force:
            print(f"\n其中 {preview['skipped']} 条有关联历史，因为带了 --force，会连历史一起删：")
        else:
            print(f"\n会被跳过（有关联历史，需 --force 才连历史一起删）：{preview['skipped']} 条")
        for entry in preview["skipped_entries"][:MAX_LISTED]:
            print(f"{_describe(entry)} —— {entry['skip_reason']}")
        if preview["skipped"] > MAX_LISTED:
            print(f"  …… 另有 {preview['skipped'] - MAX_LISTED} 条跳过未列出")
    return True


def _confirm(message):
    print(f"\n{message}")
    print("Type 'yes' to confirm: ", end="")
    try:
        answer = input()
    except EOFError:
        answer = ""
    if answer.strip().lower() != "yes":
        print("已取消，未做任何修改。")
        return False
    return True


def _auto_backup():
    """动手前整库备份；备份失败即中止（除非显式 --no-backup）。"""
    print("\n正在备份数据库……")
    try:
        manager = DatabaseBackup()
        ok = manager.backup(force=True)
    except Exception as exc:  # 备份链路任何异常都不允许继续删数据
        print(f"备份失败（{exc}），已中止，未做任何修改。", file=sys.stderr)
        sys.exit(1)
    if not ok:
        print("备份失败，已中止，未做任何修改。请先跑 coco check 体检。", file=sys.stderr)
        sys.exit(1)
    backups = manager.list_backups()
    latest = backups[-1]["filename"] if backups else "(未取到文件名)"
    print(f"备份完成：{manager.backup_dir / latest}")
    return manager.backup_dir / latest


def _run_delete(db, kind, before, force, assume_yes, mode, no_backup, statuses=None):
    before_stat = _overview(db)
    if mode == "archive":
        return _run_archive(db, kind, before, assume_yes, no_backup, before_stat, statuses)
    preview = db.purge_preview(kind=kind, statuses=statuses, before=before)
    if not _print_preview(preview, force=force):
        return 0
    if preview["deletable"] == 0 and not force:
        print("没有被清理的对象（剩下的都有关联历史，需人工决定）。")
        return 0
    if not assume_yes and not _confirm(
            f"即将彻底删除 {preview['deletable']} 条（连同 {preview['related_total']} 条关联记录），"
            f"跳过 {preview['skipped']} 条；此操作不可逆。"):
        return 0
    backup_path = None
    if not no_backup:
        backup_path = _auto_backup()
    result = db.purge_data(kind=kind, statuses=statuses, before=before, mode=mode,
                           dry_run=False, force=force)
    _print_result(result)
    _print_after(before_stat, db, backup_path)
    return 0


def _run_archive(db, kind, before, assume_yes, no_backup, before_stat, statuses=None):
    """archive 档：只把状态标成已成交/已关闭，不删除任何记录"""
    preview = db.purge_data(kind=kind, statuses=statuses, before=before, mode="archive", dry_run=True)
    entries = preview["entries"]
    if not entries:
        print("没有符合条件的数据，无需标记。")
        return 0
    print(f"\n会被标记的数据共 {len(entries)} 条：")
    for entry in entries[:MAX_LISTED]:
        print(f"{_describe(entry)} → {_STATUS_LABELS.get(entry['new_status'], entry['new_status'])}")
    if len(entries) > MAX_LISTED:
        print(f"  …… 另有 {len(entries) - MAX_LISTED} 条未逐条列出")
    print("\n说明：只改状态，不删除任何记录；标记后这些数据不再出现在在售/在跟列表与匹配里。")
    if not assume_yes and not _confirm(f"确认把以上 {len(entries)} 条标成已成交/已关闭？"):
        return 0
    backup_path = None
    if not no_backup:
        backup_path = _auto_backup()
    result = db.purge_data(kind=kind, statuses=statuses, before=before, mode="archive", dry_run=False)
    print(f"\n已标记 {result['archived']} 条为已成交/已关闭。")
    _print_after(before_stat, db, backup_path)
    return 0


def _print_result(result):
    print()
    print(f"已清理：彻底删除 {result['deleted']} 条（连同 {result['deleted_related']} 条关联记录），"
          f"跳过 {result['skipped_count']} 条。")
    if result["skipped_count"]:
        for entry in result["skipped_entries"][:MAX_LISTED]:
            print(f"  跳过：{_describe(entry, bullet=False)} —— {entry['skip_reason']}")


def _print_after(before_stat, db, backup_path):
    after_stat = _overview(db)
    print()
    _print_overview("清理前", before_stat)
    _print_overview("清理后", after_stat)
    if backup_path:
        print(f"\n出问题可回滚：coco restore --file {backup_path}")


def _run_restore(db, kind, before, assume_yes, no_backup, statuses=None):
    before_stat = _overview(db)
    preview = db.restore_status(kind=kind, statuses=statuses, before=before, dry_run=True)
    entries = preview["entries"]
    if not entries:
        print("没有被标记的数据，无需恢复。")
        return 0
    print(f"\n会被恢复为在售/在跟的数据共 {len(entries)} 条：")
    for entry in entries[:MAX_LISTED]:
        name = entry.get("title") or entry.get("name")
        print(f"  · {entry['kind']} #{entry['id']} {name}")
    if len(entries) > MAX_LISTED:
        print(f"  …… 另有 {len(entries) - MAX_LISTED} 条未逐条列出")
    print("\n说明：恢复只改状态（房源→在售、客户→在跟），不新增也不删除任何记录；"
          "原始状态没有留痕，无法只恢复其中一部分。")
    if not assume_yes and not _confirm(f"确认把以上 {len(entries)} 条恢复为在售/在跟？"):
        return 0
    backup_path = None
    if not no_backup:
        backup_path = _auto_backup()
    result = db.restore_status(kind=kind, statuses=statuses, before=before, dry_run=False)
    print(f"\n已恢复 {result['restored']} 条为在售/在跟。")
    _print_after(before_stat, db, backup_path)
    return 0


def _menu_choice(db):
    print("数据清理向导（在服务器终端运行）")
    print()
    _print_overview("当前库内", _overview(db))
    print("""
1) 预演：只列出会被清理的数据，不做任何修改（建议先跑这档）
2) 清理已关闭的客户（彻底删除；有关联跟进/带看/成交的会跳过并说明原因）
3) 清理已售/已租的房源（彻底删除；有关联记录的会跳过并说明原因）
4) 两类都清理
5) 恢复误标状态：已售/已租 → 在售，已关闭 → 在跟（只改状态，不删数据）
6) 取消""")
    print()
    try:
        choice = input("选择序号 [1-6]: ").strip()
    except EOFError:
        choice = ""
    return choice


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="coco data-clean",
        description="数据清理：清理已关闭客户 / 已成交房源，或恢复误标的状态（先预演，动手前自动备份）",
    )
    parser.add_argument("--kind", choices=["property", "customer", "all"], default=None,
                        help="清理对象（不给则进交互菜单）")
    parser.add_argument("--before", default=None, help="只处理该日期（YYYY-MM-DD）之前录入的数据")
    parser.add_argument("--mode", choices=["delete", "archive"], default="delete",
                        help="delete 彻底删除（默认）/ archive 只把状态标成已成交、已关闭")
    parser.add_argument("--statuses", default=None,
                        help="要处理的状态（逗号分隔），默认：房源 sold,rented；客户 closed")
    parser.add_argument("--restore", action="store_true", help="恢复误标状态（不删除数据）")
    parser.add_argument("--force", action="store_true", help="连关联的带看/成交/跟进一起删（默认跳过）")
    parser.add_argument("--dry-run", "-n", action="store_true", help="只列出会做什么，不做任何修改")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过确认（自动化用）")
    parser.add_argument("--no-backup", action="store_true", help="跳过清理前的自动备份（不推荐）")
    args = parser.parse_args(argv)

    interactive = sys.stdin.isatty()
    action_given = args.restore or args.kind is not None or args.dry_run

    if not interactive and not action_given:
        print("数据清理向导需要交互终端。非交互环境请显式给出动作，例如：", file=sys.stderr)
        print("  coco data-clean --dry-run", file=sys.stderr)
        print("  coco data-clean --kind all --before 2026-09-01 --yes", file=sys.stderr)
        print("  coco data-clean --restore --yes", file=sys.stderr)
        return 2

    db = _load_db()
    statuses = [s.strip() for s in args.statuses.split(",") if s.strip()] if args.statuses else None

    if args.dry_run:
        if args.restore:
            preview = db.restore_status(kind=args.kind or "all", statuses=statuses,
                                        before=args.before, dry_run=True)
            print(f"预演：会把 {preview['restored']} 条恢复为在售/在跟（未做任何修改）。")
            for entry in preview["entries"][:MAX_LISTED]:
                print(f"  · {entry['kind']} #{entry['id']} {entry.get('title') or entry.get('name')}")
            return 0
        preview = db.purge_preview(kind=args.kind or "all", statuses=statuses, before=args.before)
        _print_preview(preview)
        print(f"\n预演：会彻底删除 {preview['deletable']} 条（连同 {preview['related_total']} 条关联记录），"
              f"跳过 {preview['skipped']} 条（未做任何修改）。")
        return 0

    if action_given:
        if args.restore:
            return _run_restore(db, args.kind or "all", args.before, args.yes, args.no_backup, statuses)
        return _run_delete(db, args.kind, args.before, args.force, args.yes, args.mode,
                           args.no_backup, statuses)

    choice = _menu_choice(db)
    if choice == "1":
        preview = db.purge_preview(kind="all", statuses=statuses, before=args.before)
        _print_preview(preview)
        print("\n（预演结束，未做任何修改。要执行请重新运行并选择 2/3/4）")
        return 0
    if choice in ("2", "3", "4"):
        kind = {"2": "customer", "3": "property", "4": "all"}[choice]
        return _run_delete(db, kind, args.before, args.force, args.yes, args.mode,
                           args.no_backup, statuses)
    if choice == "5":
        return _run_restore(db, "all", args.before, args.yes, args.no_backup, statuses)
    print("已取消，未做任何修改。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
