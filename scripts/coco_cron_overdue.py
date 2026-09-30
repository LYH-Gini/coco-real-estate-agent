"""Coco 逾期跟进哨兵（纯脚本型定时任务，不叫模型、不烧 token）

设计要点（2026-09-23 拍板）：
- 每天只跑 2 次（10:00 / 17:00），**同一客户当天只提醒一次**，不再每 30 分钟重复念同一批人；
- 有"新的逾期"或"S 级提醒过仍未动"才输出，否则**空输出 = 静默**（不投递、不叫模型）；
- 只提醒、不执行：具体跟进动作由经纪人自己做，脚本不代替他做任何事。
"""
import sys
from datetime import datetime

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from coco_cron_common import (  # noqa: E402
    clip, ensure_import_path, get_db, load_state, parse_dt, report_failure, save_state, today_str,
)

_STATE_NAME = "overdue"
_TIER_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3}


def _followed_up_today(db, customer_id, today: str) -> bool:
    """该客户今天有没有新的跟进记录（记了就不再升级提醒）"""
    try:
        for row in db.get_followups(customer_id, limit=3):
            created = parse_dt(row.get("created_at"))
            if created and created.date().isoformat() == today:
                return True
    except Exception:
        pass
    return False


def collect_overdue(db, now: datetime = None, before=None) -> list:
    """逾期客户明细（按客户取最新一条跟进，与 db.get_overdue 同口径）

    before: 截止时间；传"明天零点"即"今天要跟进或已逾期"（早报用）
    """
    now = now or datetime.now()
    today = today_str(now)
    items = []
    for row in db.get_overdue(before=before):
        customer_id = row.get("customer_id")
        if customer_id is None:
            continue
        customer = {}
        try:
            customer = db.get_customer(customer_id) or {}
        except Exception:
            customer = {}
        next_date = parse_dt(row.get("next_date"))
        days = (now.date() - next_date.date()).days if next_date else 0
        items.append({
            "customer_id": customer_id,
            "name": customer.get("name") or f"客户{customer_id}",
            "tier": customer.get("tier") or "C",
            "days": max(days, 0),
            "content": clip(row.get("content")),
            "followed_today": _followed_up_today(db, customer_id, today),
        })
    return items


def plan_message(items: list, state: dict, now: datetime = None) -> tuple:
    """算出这次要发什么（纯函数，供测试直接调用）

    返回 (message, new_state)：message 为 None 表示这次不该说话（静默）。
    """
    now = now or datetime.now()
    today = today_str(now)
    if state.get("date") != today:  # 跨天重置，只保留当天去重
        state = {"date": today, "reminded": {}, "escalated": []}
    reminded = state.get("reminded") or {}
    escalated = list(state.get("escalated") or [])

    fresh = [i for i in items if str(i["customer_id"]) not in reminded]
    fresh.sort(key=lambda i: (_TIER_ORDER.get(i["tier"], 9), -i["days"]))
    escalate = [
        i for i in items
        if str(i["customer_id"]) in reminded
        and i["tier"] == "S"
        and not i["followed_today"]
        and i["customer_id"] not in escalated
    ]

    new_state = {
        "date": today,
        "reminded": dict(reminded, **{str(i["customer_id"]): i["days"] for i in fresh}),
        "escalated": escalated + [i["customer_id"] for i in escalate],
    }
    if not fresh and not escalate:
        return None, new_state

    first_of_day = not reminded
    if fresh:
        head = "⏰ 逾期跟进提醒 · {} 位".format(len(fresh)) if first_of_day \
            else "⏰ 逾期跟进提醒 · 新增 {} 位".format(len(fresh))
    else:
        head = "⏰ 逾期跟进提醒"
    lines = [head]
    for i in fresh:
        when = "今天到期" if i["days"] == 0 else f"逾期 {i['days']} 天"
        line = f"· {i['name']}（{i['tier']}级）{when}"
        if i["content"]:
            line += f" ｜ 上次：{i['content']}"
        lines.append(line)
    for i in escalate:
        lines.append(f"⚠️ {i['name']}（S级）今天提醒过仍无跟进记录，优先处理")
    if fresh:
        lines.append("跟完记一条跟进，这个提醒就不会再出现。")
    return "\n".join(lines), new_state


def main() -> int:
    ensure_import_path()
    now = datetime.now()
    try:
        items = collect_overdue(get_db(), now)
    except Exception as exc:
        # 哨兵坏掉必须让人看见：日志留全，对外只给一句人话（不静默）
        print(report_failure(_STATE_NAME, "逾期检查", exc))
        return 0

    state = load_state(_STATE_NAME)
    message, new_state = plan_message(items, state, now)
    save_state(_STATE_NAME, new_state)
    if message:
        print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
