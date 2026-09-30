"""Coco 收工小结数据收集（20:30，脚本给数据、模型只负责按板块成文）

设计要点（2026-09-23 拍板）：
- 让经纪人睡前收口：今天做了什么、今天该做没做的、明天第一件事；
- 数字全部来自代码统计，不让模型口算；
- 收工小结**不静默**（哪怕今天什么都没做，也要如实说"今天没有跟进记录"）。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coco_cron_common import ensure_import_path, get_db, report_failure  # noqa: E402
from coco_cron_daily import collect_viewings  # noqa: E402
from coco_cron_overdue import collect_overdue  # noqa: E402

_TIER_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3}


def build_data(db, now: datetime = None) -> str:
    """收工小结三块数据：今天做了什么 / 该做没做 / 明天第一件事"""
    now = now or datetime.now()
    today_start = datetime(now.year, now.month, now.day)
    tomorrow_start = today_start + timedelta(days=1)
    lines = []

    try:
        counts = db.activity_counts(days=1)
    except Exception:
        counts = {}
    viewings = collect_viewings(db, today_start, tomorrow_start)
    done = [v for v in viewings if v["status"] == "done"]

    lines.append("【今天做了什么】")
    parts = []
    if counts.get("followups"):
        parts.append(f"记录跟进 {counts['followups']} 条")
    if viewings:
        parts.append(f"带看 {len(viewings)} 场（已完成 {len(done)} 场）")
    if counts.get("viewings_interested"):
        parts.append(f"客户表示感兴趣 {counts['viewings_interested']} 位")
    if counts.get("deals_updated"):
        parts.append(f"成交单有更新 {counts['deals_updated']} 张")
    if counts.get("new_customers"):
        parts.append(f"新增客户 {counts['new_customers']} 位")
    if counts.get("new_properties"):
        parts.append(f"新增房源 {counts['new_properties']} 套")
    lines.append("· " + " · ".join(parts) if parts else "· 今天库里没有任何跟进/带看/成交记录")

    pending = [i for i in collect_overdue(db, now) if not i["followed_today"]]
    pending.sort(key=lambda i: (_TIER_ORDER.get(i["tier"], 9), -i["days"]))
    lines.append("【今天该做没做的】")
    if pending:
        for i in pending[:5]:
            when = "今天到期" if i["days"] == 0 else f"逾期 {i['days']} 天"
            lines.append(f"· {i['name']}（{i['tier']}级）{when}仍无跟进记录")
    else:
        lines.append("无（逾期客户今天都跟过了）")

    lines.append("【明天第一件事】")
    first = []
    if pending:
        top = pending[0]
        first.append(f"{top['name']}（{top['tier']}级）要跟进"
                     + (f"，上次：{top['content']}" if top["content"] else ""))
    try:
        tomorrow_viewings = collect_viewings(db, tomorrow_start, tomorrow_start + timedelta(days=1))
    except Exception:
        tomorrow_viewings = []
    for v in tomorrow_viewings:
        first.append(f"{v['time']} 带看：{v['customer']} × {v['property']}")
    if first:
        lines.extend(f"· {t}" for t in first)
    else:
        lines.append("无（没有逾期客户，也没有预约的带看）")

    return "\n".join(lines)


def main() -> int:
    ensure_import_path()
    try:
        print(build_data(get_db(), datetime.now()))
    except Exception as exc:
        print(report_failure("dayend", "收工小结", exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
