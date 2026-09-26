"""
Coco 房产工具 - 生日/节日提醒
"""
import json
import re
from datetime import date, datetime, timedelta

from tools.registry import registry
from agent.real_estate_display import attach_key_warning, mask_contacts
from agent.real_estate_input import norm_id


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


def _month_day_text(birthday) -> str:
    """库里生日有两种写法（YYYY-MM-DD 与 MM-DD），统一给「月-日」可读形式；认不出的原样返回。"""
    text = str(birthday or "").strip().replace("/", "-")
    if not text:
        return ""
    parts = [p for p in text.split("-") if p != ""]
    if len(parts) >= 2:
        try:
            return f"{int(parts[-2]):02d}-{int(parts[-1]):02d}"
        except ValueError:
            return text
    return text


def _brief(rows):
    """精简行：只给提醒需要的字段（不发联系方式、备注、预算这些私料）"""
    return [{
        "id": c.get("id"),
        "name": c.get("name") or "未填姓名",
        "tier": c.get("tier") or "",
        "birthday": _month_day_text(c.get("birthday")),
    } for c in rows]


def _names_text(rows) -> str:
    return "、".join(f"{r['name']}（{r['tier']} 级）" if r["tier"] else r["name"] for r in rows)


def _message(today, tomorrow, with_birthday_total, any_customer) -> str:
    """给经纪人看的那句话（四种情形分开说，空态不与"没人过生日"混为一谈）"""
    if not today and not tomorrow:
        if not any_customer:
            return "库里还没有客户，先登记客户和生日，之后我会提前一天提醒你。"
        return f"今天和明天都没有客户过生日（库里有 {with_birthday_total} 位客户录了生日）。"
    parts = []
    if today:
        parts.append(f"今天 {len(today)} 位客户过生日：{_names_text(today)}")
    else:
        parts.append("今天没有客户过生日")
    if tomorrow:
        parts.append(f"明天 {len(tomorrow)} 位：{_names_text(tomorrow)}")
    else:
        parts.append("明天没有客户过生日")
    tail = "要不要我帮你拟一条生日祝福？" if today else "要不要提前准备祝福？"
    return "；".join(parts) + f"。{tail}"


def birthday_check(task_id: str = None) -> str:
    """查今天/明天过生日的客户（只含在跟客户、且已录入生日的客户）"""
    db = _get_db()
    now = datetime.now()
    tmr = now + timedelta(days=1)
    today_rows = db.get_birthday_customers(month=now.month, day=now.day) or []
    tmr_rows = db.get_birthday_customers(month=tmr.month, day=tmr.day) or []

    # 读路径密文防御：即使不返回联系方式，也要如实说明"库里的联系方式读不出来"，
    # 免得模型以为一切正常（2026-09-27 实测：原实现把密文当电话直接交出去）。
    masked = []
    for row in list(today_rows) + list(tmr_rows):
        _, hit = mask_contacts(dict(row))
        masked += hit

    today = _brief(today_rows)
    tomorrow = _brief(tmr_rows)
    total_with_birthday = len(db.get_birthday_customers() or [])
    any_customer = bool(db.count_customers())

    payload = {
        "success": True,
        "today_birthdays": today,
        "tomorrow_birthdays": tomorrow,
        "count_today": len(today),
        "count_tomorrow": len(tomorrow),
        "with_birthday_total": total_with_birthday,
        "message": _message(today, tomorrow, total_with_birthday, any_customer),
    }
    return json.dumps(attach_key_warning(payload, masked), ensure_ascii=False)


_BIRTHDAY_FORMS = "1988-05-06、1988/5/6，只记得月日就写 05-06"


def _brief_value(value) -> str:
    """把参数值说成一句人话（布尔说「是/否」，别的截断），避免把内部类型名念给经纪人。"""
    text = "是/否" if isinstance(value, bool) else str(value)
    return " ".join(text.split())[:20]


def _parse_birthday(value, today=None):
    """归一生日写法 → ("YYYY-MM-DD" | "MM-DD", None)，认不出/不合理 → (None, 中文提示)

    认这些写法：`1988-05-06` / `1988/5/6` / `1990年5月6日` / `19880506` / `05-06`（只记得月日）。
    不合理的一律拦下并说清原因（未来日期、2 月 30 日这类不存在的日子）—— 生日写错会让提醒
    在一整年里挑错日子提醒人。
    """
    today = today or date.today()
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None, f"生日要写日期，收到的是「{_brief_value(value)}」这类值。可以这样写：{_BIRTHDAY_FORMS}。"
    raw = " ".join(str(value).split())
    if not raw:
        return None, f"生日没填。可以这样写：{_BIRTHDAY_FORMS}。"
    norm = (raw.replace("年", "-").replace("月", "-").replace("日", "")
               .replace("/", "-").replace(".", "-").replace(" ", ""))
    parts = [p for p in norm.split("-") if p != ""]
    if len(parts) == 1 and parts[0].isdigit() and len(parts[0]) == 8:
        parts = [parts[0][:4], parts[0][4:6], parts[0][6:]]
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        y, m, d = (int(p) for p in parts)
        try:
            when = date(y, m, d)
        except ValueError:
            return None, f"{y:04d}-{m:02d}-{d:02d} 这一天不存在，请核对后再发我一次。"
        if when > today:
            return None, (f"生日不能是未来的日期：收到的是 {when.isoformat()}"
                          f"（今天是 {today.isoformat()}）。核对年份后再发我一次。")
        return f"{y:04d}-{m:02d}-{d:02d}", None
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        m, d = (int(p) for p in parts)
        try:
            date(2000, m, d)          # 2000 是闰年，2-29 也能过
        except ValueError:
            return None, f"{m:02d}-{d:02d} 不是一个存在的月日，请核对后再发我一次。"
        return f"{m:02d}-{d:02d}", None
    return None, f"生日没认出来：收到的是「{raw}」。可以这样写：{_BIRTHDAY_FORMS}。"


def update_birthday(customer_id: int, birthday: str, task_id: str = None) -> str:
    """设置客户生日（多种写法自动归一；只记得月日也可以）"""
    customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    normalized, problem = _parse_birthday(birthday)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    db = _get_db()
    result = db.update_customer(customer_id, birthday=normalized)
    if not result:
        return json.dumps({"success": False, "error": "客户不存在"}, ensure_ascii=False)
    # 联系方式展示防御（2026-09-25）：返回体里带着整行客户资料，密钥不一致时
    # 结构体里的 phone/wechat 就是密文（update_customer/update_tier 早就做了，这里也补上）
    result, masked = mask_contacts(result)
    raw = " ".join(str(birthday).split())
    notes = []
    if normalized != raw:
        notes.append(f"收到的是 {raw}，已按 {normalized} 记下。")
    if len(normalized) == 5:
        notes.append(f"已记下生日 {normalized}（只填了月日，年份留空；提醒照常）。")
    else:
        notes.append(f"已设置客户生日 {normalized}（生日提醒会在当天和前一天提示你）。")
    if (result.get("status") or "") == "closed":
        notes.append("注意：这位客户状态是「已关闭」，生日提醒不会包含他（要恢复提醒就把状态改回在跟）。")
    return json.dumps(attach_key_warning(
        {"success": True, "customer": result, "message": "".join(notes)},
        masked), ensure_ascii=False)


registry.register(
    name="birthday_check",
    toolset="real_estate",
    schema={"name": "birthday_check", "description": (
        "查今天/明天过生日的客户（只含在跟客户、且已录入生日的客户；提前一天提醒用）。"
        "生日以库里录入的为准，不要按年龄或星座推测。"), "parameters": {
        "type": "object", "properties": {},
    }},
    handler=lambda args, **kw: birthday_check(),
)

registry.register(
    name="update_birthday",
    toolset="real_estate",
    schema={"name": "update_birthday", "description": (
        "设置客户生日（支持 1988-05-06、1988/5/6，只记得月日就写 05-06）。"
        "设置后生日提醒会在当天与前一天提示；已关闭的客户不计入提醒。"), "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "integer", "description": "客户编号（可在客户列表里查）"},
            "birthday": {"type": "string", "description": "生日：1988-05-06 / 1988/5/6 / 05-06（只记得月日）"},
        },
        "required": ["customer_id", "birthday"],
    }},
    handler=lambda args, **kw: update_birthday(**args),
)
