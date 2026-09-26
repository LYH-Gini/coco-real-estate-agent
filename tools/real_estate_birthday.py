"""
Coco 房产工具 - 生日/节日提醒
"""
import json
from datetime import datetime
from tools.registry import registry
from agent.real_estate_display import attach_key_warning, mask_contacts
from agent.real_estate_input import norm_id


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


def birthday_check(task_id: str = None) -> str:
    """检查今天/明天过生日的客户（供定时任务调用）"""
    db = _get_db()
    now = datetime.now()
    today = db.get_birthday_customers(month=now.month, day=now.day)
    # 明天生日（提前提醒）
    tomorrow = now.replace(day=now.day + 1) if now.day < 28 else now
    from datetime import timedelta
    tmr = now + timedelta(days=1)
    upcoming = db.get_birthday_customers(month=tmr.month, day=tmr.day)

    result = {
        "success": True,
        "today_birthdays": today,
        "tomorrow_birthdays": upcoming,
    }
    return json.dumps(result, ensure_ascii=False)


def update_birthday(customer_id: int, birthday: str, task_id: str = None) -> str:
    """设置客户生日（YYYY-MM-DD）"""
    customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    from datetime import datetime as _dt
    try:
        _dt.strptime(birthday.strip(), "%Y-%m-%d")
    except ValueError:
        return json.dumps({"success": False, "error": "生日格式错误，请用 YYYY-MM-DD"}, ensure_ascii=False)
    db = _get_db()
    result = db.update_customer(customer_id, birthday=birthday.strip())
    if result:
        # 联系方式展示防御（2026-09-25）：返回体里带着整行客户资料，密钥不一致时
        # 结构体里的 phone/wechat 就是密文（update_customer/update_tier 早就做了，这里漏了）
        result, masked = mask_contacts(result)
        return json.dumps(attach_key_warning(
            {"success": True, "customer": result, "message": f"已设置客户生日 {birthday}"},
            masked), ensure_ascii=False)
    return json.dumps({"success": False, "error": "客户不存在"}, ensure_ascii=False)


registry.register(
    name="birthday_check",
    toolset="real_estate",
    schema={"name": "birthday_check", "description": "检查今天和明天过生日的客户（定时任务用）", "parameters": {
        "type": "object", "properties": {},
    }},
    handler=lambda args, **kw: birthday_check(),
)

registry.register(
    name="update_birthday",
    toolset="real_estate",
    schema={"name": "update_birthday", "description": "设置客户生日（YYYY-MM-DD）", "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "integer", "description": "客户ID"},
            "birthday": {"type": "string", "description": "生日 YYYY-MM-DD"},
        },
        "required": ["customer_id", "birthday"],
    }},
    handler=lambda args, **kw: update_birthday(**args),
)
