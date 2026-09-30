"""Coco 早报数据收集（09:00，脚本给数据、模型只负责按板块成文）

设计要点（2026-09-23 拍板）：
- 只列"今天要做什么"：要跟进的客户、今天的带看、已录入生日的客户、成交节点；
- **没有的板块由脚本明确写"无"，模型不许编造**（尤其是生日：只列出已录入生日的客户，
  没录入的不出现，也不许按年龄/星座推测）；
- 数字全部来自代码，不让模型口算。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coco_cron_common import ensure_import_path, get_db, report_failure  # noqa: E402
from coco_cron_overdue import collect_overdue  # noqa: E402

_DEAL_STAGE_LABEL = {
    "deposit": "定金", "signing": "签约", "loan": "贷款", "transfer": "过户",
}
_DEAL_NEXT_ACTION = {
    "deposit": "签约", "signing": "贷款申请", "loan": "过户", "transfer": "交房",
}
_STALL_DAYS = 7  # 成交单阶段停留超过这个天数才算"卡住了"


def collect_viewings(db, start, end) -> list:
    """时间段内的带看，补上客户名与房源标题（供早报/收工小结用）"""
    out = []
    for v in db.viewings_between(start, end):
        try:
            customer = db.get_customer(v.get("customer_id")) or {}
            prop = db.get_property(v.get("property_id")) or {}
        except Exception:
            customer, prop = {}, {}
        when = v.get("viewing_time") or ""
        out.append({
            "time": when[11:16] if len(when) >= 16 else "",
            "customer": customer.get("name") or f"客户{v.get('customer_id')}",
            "tier": customer.get("tier") or "",
            "property": prop.get("title") or f"房源{v.get('property_id')}",
            "status": v.get("status") or "",
            "result": v.get("result") or "",
        })
    return out


def collect_birthdays(db, now: datetime) -> list:
    """今明两天的生日客户（只含已录入生日的客户，未录入的不会出现）"""
    out = []
    for offset, label in ((0, "今天"), (1, "明天")):
        day = now.date() + timedelta(days=offset)
        try:
            rows = db.get_birthday_customers(month=day.month, day=day.day) or []
        except Exception:
            rows = []
        for c in rows:
            out.append({"when": label, "name": c.get("name"), "tier": c.get("tier") or ""})
    return out


def collect_deal_nodes(db, now: datetime) -> list:
    """成交节点：阶段卡住超 7 天的单，或近期到期的交房/过户约定"""
    out = []
    try:
        deals = db.list_deals(limit=100)
    except Exception:
        return out
    soon = now.date() + timedelta(days=3)
    for d in deals:
        stage = d.get("stage")
        if stage == "finalized":
            continue
        try:
            customer = db.get_customer(d.get("customer_id")) or {}
        except Exception:
            customer = {}
        name = customer.get("name") or f"客户{d.get('customer_id')}"
        stall = None
        updated = d.get("updated_at")
        if updated:
            try:
                stall = (now.date() - datetime.fromisoformat(str(updated)).date()).days
            except Exception:
                stall = None
        for field, label in (("finalize_date", "交房"), ("transfer_date", "过户")):
            raw = d.get(field)
            if not raw:
                continue
            try:
                due = datetime.fromisoformat(str(raw)).date()
            except Exception:
                continue
            if due <= soon:
                out.append({"name": name, "text": f"{label}约定 {due.isoformat()}（{_DEAL_STAGE_LABEL.get(stage, stage)}阶段）"})
        if stall is not None and stall >= _STALL_DAYS:
            out.append({
                "name": name,
                "text": f"{_DEAL_STAGE_LABEL.get(stage, stage)}阶段已 {stall} 天未推进 → 该办"
                        f"{_DEAL_NEXT_ACTION.get(stage, '下一步')}",
            })
    return out


def build_data(db, now: datetime = None) -> str:
    """把早报四块数据排成文本（空块明确写"无"，不给模型留编造空间）"""
    now = now or datetime.now()
    today_start = datetime(now.year, now.month, now.day)
    tomorrow_start = today_start + timedelta(days=1)
    lines = []

    followups = collect_overdue(db, now, before=tomorrow_start)
    lines.append("【今天要跟进】")
    if followups:
        for i in followups:
            when = "今天到期" if i["days"] == 0 else f"逾期 {i['days']} 天"
            line = f"· {i['name']}（{i['tier']}级）{when}"
            if i["content"]:
                line += f" ｜ 上次：{i['content']}"
            lines.append(line)
    else:
        lines.append("无")

    viewings = collect_viewings(db, today_start, tomorrow_start)
    lines.append("【今天的带看】")
    if viewings:
        for v in viewings:
            lines.append(f"· {v['time']} {v['customer']} × {v['property']}")
    else:
        lines.append("无")

    birthdays = collect_birthdays(db, now)
    lines.append("【生日（仅已录入生日的客户）】")
    if birthdays:
        for b in birthdays:
            lines.append(f"· {b['when']} {b['name']} 生日")
    else:
        lines.append("无（库里没有已录入生日的客户）")

    nodes = collect_deal_nodes(db, now)
    lines.append("【成交节点】")
    if nodes:
        for n in nodes:
            lines.append(f"· {n['name']}：{n['text']}")
    else:
        lines.append("无")

    return "\n".join(lines)


def main() -> int:
    ensure_import_path()
    try:
        print(build_data(get_db(), datetime.now()))
    except Exception as exc:
        print(report_failure("daily", "早报", exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
