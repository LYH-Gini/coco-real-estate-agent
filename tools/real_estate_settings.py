"""
Coco 房产工具 - 经纪人配置（品牌/公司名）
2026-08-12 加：海报品牌必须来自经纪人真实告知的公司名，禁止用默认值硬凑。
"""
import json
import os
import re

from agent.real_estate_input import clip_text, norm_phone
from tools.registry import registry

_CARD_LABELS = {"name": "姓名", "phone": "电话", "wechat": "微信", "company": "公司/门店名"}
# 海报排版的容量上限（超出截断并说明，绝不静默丢字）
_CARD_LIMITS = {"name": 10, "phone": 20, "wechat": 30, "company": 30}
_CARD_SETTINGS = {"name": "agent_name", "phone": "agent_phone",
                  "wechat": "agent_wechat", "company": "brand_name"}
_QR_SETTING = "agent_qr_image"      # 经纪人自己给的微信二维码图片（归档后的路径）
_WECHAT_PREFIX = re.compile(r"^(微信号|微信|vx|VX|wx|WX)\s*[:：]?\s*")
_PHONE_LIKE = re.compile(r"[\d\-]{7,20}")


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


_WHITESPACE = re.compile(r"\s+")
_BRAND_LIMIT = 30          # 海报品牌栏能放下的字数（与名片里的公司名同一上限）


def _prep_text(value, label, limit):
    """文本参数整理（品牌名与名片里的公司名共用）→ (值, 错误说明, 截断说明)

    一处规则：非文本给中文提示（`True` 这类值不能让它崩在 `.strip()` 上）、去首尾空白、
    **折叠内部空白与换行**（海报排版与回执都会被换行带歪）、按栏位字数截断。
    """
    if value is None:
        return None, None, None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        got = "是/否" if isinstance(value, bool) else "非文字内容"
        return None, f"{label}要填文字，收到的是「{got}」这类值。", None
    text = _WHITESPACE.sub(" ", str(value)).strip()
    if not text:
        return None, None, None
    text, clip_note = clip_text(text, limit)
    return text, None, clip_note


def save_agent_brand(brand_name: str, task_id: str = None) -> str:
    """保存经纪人公司/门店品牌名（海报品牌栏用）"""
    text, problem, clip_note = _prep_text(brand_name, "品牌名", _BRAND_LIMIT)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if not text:
        return json.dumps({"success": False, "error": "品牌名没填。可以这样发我：宇恒房产。"},
                          ensure_ascii=False)
    db = _get_db()
    db.set_setting('brand_name', text)      # 与名片里的公司名是同一个键
    message = f"品牌名已保存：{text}（海报品牌栏会显示它）。"
    if clip_note:
        message += f"品牌名太长，已按前 {_BRAND_LIMIT} 字记下：「{text}」。"
    return json.dumps({"success": True, "brand_name": text, "message": message}, ensure_ascii=False)


def get_agent_brand(task_id: str = None) -> str:
    """查看已保存的公司/门店品牌名（海报品牌栏用）"""
    db = _get_db()
    brand = (db.get_setting('brand_name') or '').strip()
    payload = {"success": True, "brand_name": brand or None, "configured": bool(brand)}
    if not brand:
        payload["message"] = "还没配置公司/门店名，海报品牌栏会空着。把公司名发我就能存下来。"
        payload["note_for_model"] = "品牌未配置：需要时问经纪人要公司/门店名，不要编。"
        return json.dumps(payload, ensure_ascii=False)
    payload["message"] = f"品牌名：{brand}（海报品牌栏会显示它）。"
    if len(brand) > _BRAND_LIMIT:
        payload["warnings"] = [
            f"品牌名有 {len(brand)} 字，超过海报栏位（{_BRAND_LIMIT} 字），"
            "出图时可能显示不全；重新发我一次会自动截断。"]
    return json.dumps(payload, ensure_ascii=False)


def get_brand_or_none() -> str:
    """供海报工具内部调用：返回品牌名或空字符串（不输出 JSON）"""
    try:
        db = _get_db()
        return db.get_setting('brand_name') or ''
    except Exception:
        return ''


def _archive_qr_image(value):
    """把经纪人发来的二维码图归档到长期目录 → (归档后路径, 错误说明)

    走与房源照片同一套归档：网关缓存目录里的文件 24 小时后会被清理，存路径进去等于丢图。
    文件找不到就给中文说明（不臆造、不静默）。
    """
    from agent.real_estate_media import archive_images

    raw = str(value or "").strip()
    if not raw:
        return None, None
    archived, _detail = archive_images(raw)
    first = (archived or "").split(",")[0].strip()
    if first and os.path.exists(first):
        return first, None
    return None, f"这张二维码图片没找到（收到的是「{raw}」）：把图片重新发一次就行。"


def save_agent_card(name: str = None, phone: str = None, wechat: str = None,
                    company: str = None, qr_image_path: str = None,
                    task_id: str = None) -> str:
    """保存经纪人名片（姓名/电话/微信/公司门店名/微信二维码图），只写入本次提供的字段

    海报用：公司名做品牌栏，姓名/电话/微信做底部名片区，二维码图印在底部（扫了直接加微信）。
    没提供的字段保持原值不动；绝不写默认值或占位符。
    """
    provided = {"name": name, "phone": phone, "wechat": wechat, "company": company}
    db = _get_db()
    saved = {}
    notes = []
    for key in ("name", "phone", "wechat", "company"):
        text, problem, clip_note = _prep_text(provided[key], _CARD_LABELS[key], _CARD_LIMITS[key])
        if problem:
            return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
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
        if clip_note:
            notes.append(f"{_CARD_LABELS[key]}太长，已按前 {_CARD_LIMITS[key]} 字记下：「{text}」。")
        db.set_setting(_CARD_SETTINGS[key], text)
        saved[key] = text
    if qr_image_path:
        qr_path, qr_problem = _archive_qr_image(qr_image_path)
        if qr_problem:
            return json.dumps({"success": False, "error": qr_problem}, ensure_ascii=False)
        db.set_setting(_QR_SETTING, qr_path)
        saved["qr_image"] = qr_path
        notes.append("二维码已存好，以后出海报会印在底部。")
    if not saved:
        return json.dumps({"success": False, "error": "没有可保存的内容（姓名/电话/微信/公司名/二维码图 至少给一项）"},
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


def get_agent_qr_path():
    """供海报等内部调用：经纪人自己给的二维码图片路径；没配置或文件不在就返回 None"""
    try:
        db = _get_db()
        path = (db.get_setting(_QR_SETTING) or '').strip()
    except Exception:
        return None
    return path if path and os.path.exists(path) else None


def get_agent_card(task_id: str = None) -> str:
    """查看经纪人名片与缺失项"""
    card = get_agent_card_or_empty()
    missing = [label for key, label in (('name', '姓名'), ('phone', '电话'),
                                        ('wechat', '微信'), ('company', '公司/门店名')) if not card.get(key)]
    payload = {"success": True, "card": card, "missing": missing,
               "qr_image": bool(get_agent_qr_path())}
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
    schema={"name": "save_agent_brand", "description": (
        "保存经纪人公司/门店品牌名（海报品牌栏用；与名片里的公司名是同一个位置，改一处两边都变）。"
        "经纪人明确告知公司名称后调用。"), "parameters": {
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
    schema={"name": "get_agent_brand", "description": (
        "查看已保存的公司/门店品牌名（生成海报前确认品牌栏有没有值）。"
        "没有就如实说还没配置，并向经纪人要，不要编。"), "parameters": {
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
            "wechat": {"type": "string", "description": "微信号（海报底部显示）"},
            "qr_image_path": {"type": "string", "description": "经纪人自己发的微信二维码图片路径（他把二维码图发过来时传这个；海报会印这张图——不要拿微信号现生成）"},
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
