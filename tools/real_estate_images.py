"""
Coco 房产工具 - 房源图片管理

口径（2026-09-26 收口）：
- `images` **数组与逗号串都认**（复用 `agent.real_estate_input.as_comma_text`）；
- **已售 / 已租房源也能加图、查图**（资料补录/查看不是面向客户的产出），只给 `warnings` 说明状态；
- 回执说清**新增了几张、几张已在库里**（不按输入条数报）；
- 没给图（空串/空数组/只有逗号）→ 中文提示，不当成"成功了但加了 0 张"；
- 看起来是本地路径但本机找不到 → 一句 `warnings`（不拦：可能是链接，或图还没传完）。
"""
import json
import os

from tools.registry import registry
from agent.real_estate_input import as_comma_text, norm_id
from agent.real_estate_media import archive_images
from tools.real_estate_property import _STATUS_LABELS, unavailable_property_note


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


def _split_images(value) -> list:
    """图片串 → 列表（数组写法与逗号串都认，元素去空白、丢空项）"""
    return [x.strip() for x in (as_comma_text(value) or '').split(',') if x.strip()]


def _status_warning(p) -> list:
    """已售/已租房源照常加图、查图，但如实说明状态（不拦）"""
    status = p.get('status')
    if status in ('sold', 'rented'):
        return [f"这套房源现在是「{_STATUS_LABELS.get(status, status)}」，图片照样记上了。"]
    return []


def add_property_images(property_id: int, images: str, task_id: str = None) -> str:
    """为房源添加图片（多个用逗号分隔，数组写法也认；已售/已租也能加）

    同一张不会重复加；回执给"新增 N 张／M 张已存在／共 K 张"。
    """
    property_id, problem = norm_id(property_id, '房源编号')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    incoming = _split_images(images)
    if not incoming:
        return json.dumps({"success": False, "error": (
            "没说要加哪些图：把图片路径或链接给我（多个用逗号分隔；一串路径直接发给我也行）")},
            ensure_ascii=False)
    # 「本机没找到」按**归档前**的原始路径判断（归档会换路径，之后的路径一定存在，会漏掉这条提示）
    missing = [i for i in incoming if '://' not in i and not os.path.exists(i)]
    # 照片先归档再入库：网关缓存目录里的文件 24 小时后会被自动清理（见 agent/real_estate_media.py）
    incoming, _archive = archive_images(','.join(incoming))
    incoming = _split_images(incoming)
    db = _get_db()
    p = db.get_property(property_id)          # 已售/已租也要能加（资料补录）
    if p is None:
        error, _label = unavailable_property_note(property_id, None, action="加图片")
        return json.dumps({"success": False, "error": error}, ensure_ascii=False)

    merged = _split_images(p.get('images'))
    added = []
    for img in incoming:
        if img not in merged:
            merged.append(img)
            added.append(img)
    skipped = len(incoming) - len(added)
    result = db.update_property(property_id, images=','.join(merged)) if added else p

    warnings = _status_warning(p)
    if missing:
        shown = '、'.join(missing[:3]) + ('…' if len(missing) > 3 else '')
        warnings.append(f"这些本地文件我在本机没找到：{shown}（图片链接不受影响；"
                        f"如果确实在本机，核对一下路径 —— 海报要用的照片必须是本机能读到的文件）")
    if added:
        message = (f"已添加 {len(added)} 张图片"
                   + (f"（另有 {skipped} 张已在库里，没有重复加）" if skipped else "")
                   + f"，共 {len(merged)} 张。")
    else:
        message = f"{skipped} 张图片都已经在库里了，没有重复添加，共 {len(merged)} 张。"
    payload = {
        "success": True,
        "property": result,
        "added_count": len(added),
        "skipped_count": skipped,
        "count": len(merged),
        "message": message,
    }
    if warnings:
        payload["warnings"] = warnings
    return json.dumps(payload, ensure_ascii=False)


def list_property_images(property_id: int, task_id: str = None) -> str:
    """查看房源图片列表（**已售/已租房源也能查**；没有图片时如实说明，不说成"房源不存在"）"""
    property_id, problem = norm_id(property_id, '房源编号')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    db = _get_db()
    p = db.get_property(property_id)          # 已售/已租也要能查（原先只查在售 → 成交后图就查不到了）
    if p is None:
        error, _label = unavailable_property_note(property_id, None, action="查图片")
        return json.dumps({"success": False, "error": error}, ensure_ascii=False)
    images = _split_images(p.get('images'))
    count = len(images)
    if count:
        shown = '、'.join(images[:3]) + ('…' if count > 3 else '')
        message = (f"这套房源有 {count} 张图片：{shown}（要发出去，用 MEDIA:<路径> 直接发图）")
    else:
        message = "这套房源还没有图片：要加图跟我说一声（把图片路径或链接发我就行）。"
    payload = {
        "success": True,
        "property_id": property_id,
        "images": images,
        "count": count,
        "message": message,
    }
    warnings = _status_warning(p)
    if warnings:
        payload["warnings"] = [w.replace("图片照样记上了", "图片照常给你") for w in warnings]
    return json.dumps(payload, ensure_ascii=False)


registry.register(
    name="add_property_images",
    toolset="real_estate",
    schema={"name": "add_property_images", "description": "给房源加图片（资料补录，**已售/已租的房源也能加**）。多个用逗号分隔，**数组写法也认**；同一张不会重复加。回执会说清新增了几张、几张已在库里；本地文件在本机找不到时会提醒（图片链接不受影响 —— 海报要用的照片必须是本机能读到的文件）", "parameters": {
        "type": "object",
        "properties": {
            "property_id": {"type": "integer", "description": "房源编号（房源列表或详情里的编号，纯数字）"},
            "images": {"type": "string", "description": "图片路径或链接，多个用逗号分隔（如 /root/1.jpg,/root/2.jpg）；数组写法也认（[\"a.jpg\",\"b.jpg\"] 这类）"},
        },
        "required": ["property_id", "images"],
    }},
    handler=lambda args, **kw: add_property_images(**args),
)

registry.register(
    name="list_property_images",
    toolset="real_estate",
    schema={"name": "list_property_images", "description": "查看某套房源的图片列表（**已售/已租的房源也能查**）：返回图片路径与张数，并说清一共有几张；**没有图片时会如实说明**（不会说成「房源不存在」）。要把图发出去，用 MEDIA:<路径> 直接发", "parameters": {
        "type": "object",
        "properties": {"property_id": {"type": "integer", "description": "房源编号（房源列表或详情里的编号，纯数字）"}},
        "required": ["property_id"],
    }},
    handler=lambda args, **kw: list_property_images(**args),
)
