"""
Coco 房产工具 - 经纪人配置（品牌/公司名）
2026-08-12 加：海报品牌必须来自经纪人真实告知的公司名，禁止用默认值硬凑。
"""
import json
import re

from agent.real_estate_input import clean_text, clip_text, norm_phone
from tools.registry import registry

_CARD_LABELS = {"name": "姓名", "phone": "电话", "wechat": "微信", "company": "公司/门店名"}
# 海报排版的容量上限（超出截断并说明，绝不静默丢字）
_CARD_LIMITS = {"name": 10, "phone": 20, "wechat": 30, "company": 30}
_CARD_SETTINGS = {"name": "agent_name", "phone": "agent_phone",
                  "wechat": "agent_wechat", "company": "brand_name"}
_WECHAT_PREFIX = re.compile(r"^(微信号|微信|vx|VX|wx|WX)\s*[:：]?\s*")
_PHONE_LIKE = re.compile(r"[\d\-]{7,20}")


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
    provided = {"name": name, "phone": phone, "wechat": wechat, "company": company}
    for key, value in provided.items():
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            got = "是/否" if isinstance(value, bool) else "非文字内容"
            return json.dumps({"success": False,
                               "error": f"{_CARD_LABELS[key]}要填文字，收到的是「{got}」这类值。"},
                              ensure_ascii=False)
    db = _get_db()
    saved = {}
    notes = []
    for key in ("name", "phone", "wechat", "company"):
        text = clean_text(provided[key])
        if not text:
            continue                      # 没提供 / 纯空白：保持原值不动
        if key == "phone":
            normalized = norm_phone(text) or text
            if normalized != text:
                notes.append(f"电话已按 {normalized} 记下。")
            text = normalized
            if not _PHONE_LIKE.fullmatch(text):
                notes.append(f"电话「{text}」看着不像完整手机号，已按原样记下（要改就说一声）。")
        if key == "wechat":
            text = _WECHAT_PREFIX.sub("", text).strip()
        text, clip_note = clip_text(text, _CARD_LIMITS[key])
        if clip_note:
            notes.append(f"{_CARD_LABELS[key]}太长，已按前 {_CARD_LIMITS[key]} 字记下：「{text}」。")
        db.set_setting(_CARD_SETTINGS[key], text)
        saved[key] = text
    if not saved:
        return json.dumps({"success": False, "error": "没有可保存的内容（姓名/电话/微信/公司名 至少给一项）"},
                          ensure_ascii=False)
    card = get_agent_card_or_empty()
    missing = [label for key, label in (('name', '姓名'), ('phone', '电话'),
                                        ('wechat', '微信'), ('company', '公司/门店名')) if not card.get(key)]
    message = "经纪人名片已更新。海报底部会显示姓名/电话/微信，品牌栏显示公司名。"
    if missing:
        message += f"还差：{'、'.join(missing)}（想起来的时候发我就行）。"
    message += "".join(notes)
    payload = {
        "success": True,
        "saved": saved,
        "card": card,
        "still_missing": missing,
        "message": message,
    }
    if missing:
        # 「不要编」是给模型的口径，不能混进给经纪人看的那句话
        payload["note_for_model"] = f"还缺：{'、'.join(missing)} —— 需要时问经纪人要，不要编。"
    return json.dumps(payload, ensure_ascii=False)


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
    payload = {"success": True, "card": card, "missing": missing}
    if not any(card.values()):
        payload["message"] = ("还没配置经纪人名片，海报底部会空着。"
                              "把姓名、电话、微信号、公司门店名发我，我存下来海报就能用。")
        payload["note_for_model"] = "名片未配置：需要时问经纪人要姓名/电话/微信/公司名，不要编。"
    elif missing:
        payload["message"] = (f"经纪人名片还差：{'、'.join(missing)}。"
                              "海报相应位置会空着，想起来发我一下就行。")
        payload["note_for_model"] = f"还缺：{'、'.join(missing)} —— 需要时问经纪人要，不要编。"
    else:
        parts = [card.get("name"), card.get("phone"),
                 f"微信 {card.get('wechat')}" if card.get("wechat") else "", card.get("company")]
        payload["message"] = f"经纪人名片已齐全：{' · '.join(p for p in parts if p)}。海报可以直接出图。"
    # 老库里可能还留着写侧加截断之前的超长值：读侧不截断（读=写，不悄悄改展示），但要提示
    overlong = [(label, len(card[key]), _CARD_LIMITS[key])
                for key, label in _CARD_LABELS.items()
                if card.get(key) and len(card[key]) > _CARD_LIMITS[key]]
    if overlong:
        label, size, cap = overlong[0]
        payload["warnings"] = [
            f"{label}有 {size} 字，超过海报栏位（{cap} 字），出图时可能显示不全；"
            f"重新发我一次{label}会自动截断。"]
    return json.dumps(payload, ensure_ascii=False)


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
    schema={"name": "get_agent_card", "description": (
        "查看已保存的经纪人名片与还缺哪些字段（海报出图前核对信息齐不齐）。"
        "缺项要如实告诉经纪人，不要编。"), "parameters": {
        "type": "object", "properties": {},}},
    handler=lambda args, **kw: get_agent_card(**args),
)
