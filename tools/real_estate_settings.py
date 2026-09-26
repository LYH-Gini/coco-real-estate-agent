"""
Coco 房产工具 - 经纪人配置（品牌/公司名）
2026-08-12 加：海报品牌必须来自经纪人真实告知的公司名，禁止用默认值硬凑。
"""
import json
from tools.registry import registry


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


def save_agent_brand(brand_name: str, task_id: str = None) -> str:
    """保存经纪人公司/门店品牌名（海报展示用），返回保存结果"""
    brand_name = (brand_name or '').strip()
    if not brand_name:
        return json.dumps({"success": False, "error": "品牌名称不能为空"}, ensure_ascii=False)
    db = _get_db()
    db.set_setting('brand_name', brand_name)
    return json.dumps({
        "success": True,
        "brand_name": brand_name,
        "message": f"品牌名称已保存：{brand_name}（海报将展示该名称）",
    }, ensure_ascii=False)


def get_agent_brand(task_id: str = None) -> str:
    """获取经纪人已保存的品牌名；未配置返回 None"""
    db = _get_db()
    brand = db.get_setting('brand_name')
    if not brand:
        return json.dumps({"success": False, "error": "品牌未配置，需要先询问经纪人公司名称"}, ensure_ascii=False)
    return json.dumps({"success": True, "brand_name": brand}, ensure_ascii=False)


def get_brand_or_none() -> str:
    """供海报工具内部调用：返回品牌名或空字符串（不输出 JSON）"""
    try:
        db = _get_db()
        return db.get_setting('brand_name') or ''
    except Exception:
        return ''


def save_agent_card(name: str = None, phone: str = None, wechat: str = None,
                    company: str = None, task_id: str = None) -> str:
    """保存经纪人名片（姓名/电话/微信/公司门店名），只写入本次提供的字段

    海报用：公司名做品牌栏，姓名/电话/微信做底部名片区。
    没提供的字段保持原值不动；绝不写默认值或占位符。
    """
    db = _get_db()
    saved = {}
    if name and name.strip():
        db.set_setting('agent_name', name.strip())
        saved['name'] = name.strip()
    if phone and phone.strip():
        db.set_setting('agent_phone', phone.strip())
        saved['phone'] = phone.strip()
    if wechat and wechat.strip():
        db.set_setting('agent_wechat', wechat.strip())
        saved['wechat'] = wechat.strip()
    if company and company.strip():
        db.set_setting('brand_name', company.strip())   # 兼容旧字段：品牌名 = 公司名
        saved['company'] = company.strip()
    if not saved:
        return json.dumps({"success": False, "error": "没有可保存的内容（姓名/电话/微信/公司名 至少给一项）"},
                          ensure_ascii=False)
    card = get_agent_card_or_empty()
    missing = [label for key, label in (('name', '姓名'), ('phone', '电话'),
                                        ('wechat', '微信'), ('company', '公司/门店名')) if not card.get(key)]
    return json.dumps({
        "success": True,
        "saved": saved,
        "card": card,
        "still_missing": missing,
        "message": ("经纪人名片已更新。海报底部将显示姓名/电话/微信，品牌栏显示公司名。"
                    + (f"还缺：{'、'.join(missing)}（需要时问经纪人要，不要编）" if missing else "")),
    }, ensure_ascii=False)


def get_agent_card_or_empty() -> dict:
    """供海报等内部调用：返回名片字典（缺项为空串，不输出 JSON）"""
    out = {'name': '', 'phone': '', 'wechat': '', 'company': ''}
    try:
        db = _get_db()
        for key, setting in (('name', 'agent_name'), ('phone', 'agent_phone'),
                             ('wechat', 'agent_wechat'), ('company', 'brand_name')):
            out[key] = (db.get_setting(setting) or '').strip()
    except Exception:
        pass
    return out


def get_agent_card(task_id: str = None) -> str:
    """查看经纪人名片与缺失项"""
    card = get_agent_card_or_empty()
    missing = [label for key, label in (('name', '姓名'), ('phone', '电话'),
                                        ('wechat', '微信'), ('company', '公司/门店名')) if not card.get(key)]
    return json.dumps({"success": True, "card": card, "missing": missing}, ensure_ascii=False)


registry.register(
    name="save_agent_brand",
    toolset="real_estate",
    schema={"name": "save_agent_brand", "description": "保存经纪人公司/门店品牌名（用于海报等展示），经纪人明确告知公司名称后调用", "parameters": {
        "type": "object",
        "properties": {
            "brand_name": {"type": "string", "description": "公司/门店品牌名，如 宇恒房产"},
        },
        "required": ["brand_name"],
    }},
    handler=lambda args, **kw: save_agent_brand(**args),
)

registry.register(
    name="get_agent_brand",
    toolset="real_estate",
    schema={"name": "get_agent_brand", "description": "获取经纪人已保存的品牌名（生成海报前确认品牌是否已配置）", "parameters": {
        "type": "object",
        "properties": {},
    }},
    handler=lambda args, **kw: get_agent_brand(**args),
)


registry.register(
    name="save_agent_card",
    toolset="real_estate",
    schema={"name": "save_agent_card", "description": "保存经纪人名片（姓名/电话/微信/公司门店名）。海报的品牌栏与名片区用这些信息；只保存经纪人明确提供的内容，其余自动问清后再存", "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "经纪人姓名，如 李经理"},
            "phone": {"type": "string", "description": "联系电话（完整号码）"},
            "wechat": {"type": "string", "description": "微信号（海报二维码内容）"},
            "company": {"type": "string", "description": "公司/门店名称（海报品牌栏，绝不写平台名或虚构名）"},
        },
    }},
    handler=lambda args, **kw: save_agent_card(**args),
)

registry.register(
    name="get_agent_card",
    toolset="real_estate",
    schema={"name": "get_agent_card", "description": "查看已保存的经纪人名片与还缺哪些字段（海报信息齐全校验用）", "parameters": {
        "type": "object", "properties": {},}},
    handler=lambda args, **kw: get_agent_card(**args),
)
