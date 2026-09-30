"""Coco 周报数据收集（周一 08:30，脚本给数据、模型只负责成文）

设计要点（2026-09-23 拍板）：
- 数字全部来自数据库真实统计（转化漏斗/市场周报这两个工具曾输出"模拟数据"，2026-09-23 已删除）；
- 只在周一早上发一次，给的是"上周发生了什么 + 这周该盯谁"。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coco_cron_common import ensure_import_path, get_db, report_failure  # noqa: E402
from coco_cron_overdue import collect_overdue  # noqa: E402

_TIER_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3}


def _channel_lines(db) -> list:
    """渠道：来客量与成交（真实统计，未填来源归入"未填写"）

    2026-09-26（F359）改文案：原先写「成交 N 单」，而 N 是 `deals`＝**有成交的客户数**
    （实测：某渠道 1 位客户开 2 单时周报写"成交 1 单"，而同一条周报【活动量】段写"新增成交单 3 张"，
    同一份周报两个"成交"含义不同）→ 现在分开说清：多少位客户成交、一共多少张单。
    """
    try:
        channels = db.get_channel_stats() or []
    except Exception:
        return []
    rows = sorted(channels, key=lambda c: c.get("customers", 0), reverse=True)
    return [
        f"· {c.get('source')}：{c.get('customers', 0)} 位客户（其中 {c.get('deals', 0)} 位已成交、"
        f"共 {c.get('成交单数', c.get('deals', 0))} 张单）"
        for c in rows if c.get("customers")
    ]


def build_data(db, now: datetime = None) -> str:
    """周报数据：上周活动量、客户分布、渠道、该盯的人"""
    now = now or datetime.now()
    start = now - timedelta(days=7)
    lines = [f"【统计区间】{start.strftime('%m-%d')} ~ {now.strftime('%m-%d')}（近 7 天）"]

    try:
        counts = db.activity_counts(days=7)
    except Exception:
        counts = {}
    try:
        stats = db.get_stats() or {}
    except Exception:
        stats = {}
    try:
        viewed_customers = len({v.get("customer_id") for v in db.viewings_between(start, now)})
    except Exception:
        viewed_customers = 0

    lines.append("【活动量】")
    lines.append(
        f"· 新增客户 {counts.get('new_customers', 0)} 位 · 新增房源 {counts.get('new_properties', 0)} 套"
        f" · 记录跟进 {counts.get('followups', 0)} 条")
    lines.append(
        f"· 带看 {counts.get('viewings', 0)} 场（{viewed_customers} 位客户，已完成 "
        f"{counts.get('viewings_done', 0)} 场，表示感兴趣 {counts.get('viewings_interested', 0)} 位）")
    lines.append(
        f"· 新增成交单 {counts.get('deals_created', 0)} 张 · 成交单有更新 "
        f"{counts.get('deals_updated', 0)} 张 · 交房 {counts.get('deals_finalized', 0)} 套")

    tier_counts = stats.get("tier_counts", {}) or {}
    lines.append("【客户分布（在跟）】")
    lines.append(
        "· " + " · ".join(f"{t}级 {tier_counts.get(t, 0)} 位" for t in ("S", "A", "B", "C"))
        + f" · 在售房源 {stats.get('available_properties', 0)} 套")

    channels = _channel_lines(db)
    lines.append("【渠道】")
    lines.extend(channels or ["无（客户来源都还没填）"])

    overdue = collect_overdue(db, now)
    stale = []
    try:
        stale = db.get_stale_customers() or []
    except Exception:
        stale = []
    watch = []
    for i in sorted(overdue, key=lambda i: (_TIER_ORDER.get(i["tier"], 9), -i["days"])):
        if i["tier"] in ("S", "A"):
            when = "今天到期" if i["days"] == 0 else f"逾期 {i['days']} 天"
            watch.append(f"· {i['name']}（{i['tier']}级）{when}未跟进")
    for s in sorted(stale, key=lambda s: _TIER_ORDER.get(s.get("tier"), 9))[:5]:
        watch.append(
            f"· {s.get('name')}（{s.get('tier')}级）已 {s.get('days_inactive')} 天没联系"
            f"（预警阈值 {s.get('threshold')} 天）")
    lines.append("【该盯的人】")
    lines.extend(watch or ["无（没有逾期的高意向客户，也没有长时间没联系的客户）"])

    return "\n".join(lines)


def main() -> int:
    ensure_import_path()
    try:
        print(build_data(get_db(), datetime.now()))
    except Exception as exc:
        print(report_failure("weekly", "周报", exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
