"""
Coco 房产工具 - 房源管理
"""
import json
import re

from agent.real_estate_display import (OWNER_KEY_MISMATCH_WARNING, attach_key_warning, mask_contacts,
                                       safe_contact)
from agent.real_estate_money import (fmt_budget, fmt_delta, fmt_price, fmt_unit_price,
                                     fmt_wan)
from agent.real_estate_input import (as_comma_text, clamp_limit, cn_number, norm_customer_type,
                                     norm_date, norm_id, norm_money)
from agent.real_estate_media import archive_images
from tools.registry import registry


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


def add_property(
    title: str, price: int, area: float,
    community: str = None, district: str = None, address: str = None,
    rooms: int = None, halls: int = None, bathrooms: int = None,
    floor: str = None, orientation: str = None,
    renovation: str = None, year_built: int = None,
    has_elevator: int = None, parking: int = 0,
    property_type: str = "second_hand",
    tags: str = None, images: str = None,
    image_paths: str = None, agent_id: str = None,
    tenant_requirements: str = None,
    owner_name: str = None, owner_phone: str = None, owner_wechat: str = None,
    viewing_note: str = None,
    exclusive_until: str = None,
    force: bool = False, task_id: str = None,
) -> str:
    """添加新房源
    
    property_type: new(一手房) / second_hand(二手房) / rental(租房)
    images: 图片链接或标识（逗号分隔）
    image_paths: 本地图片文件路径（逗号分隔），优先于 images 合并存储
    viewing_note: 看房方式（如 钥匙在门店/需提前预约），房源详情与业主查询里会显示
    exclusive_until: 独家委托到期日（如 2026-12-31；填了这套房会进独家到期清单）
    force=True 跳过房源查重强制新增（仅当确认是不同期数/楼栋而要保留同名时用，默认 False）。
    """
    db = _get_db()
    # 「一串多个值」的参数先归一：模型常把 image_paths/tags 当数组传（数组与逗号串都认）
    images, image_paths, tags = as_comma_text(images), as_comma_text(image_paths), as_comma_text(tags)
    # 入口归一与基础校验（2026-09-24 加）：模型有时会把经纪人的原话直接传下来（"185万""一百二十平"），
    # 也可能传空标题/非法类型 —— 这里统一换算成 元 / ㎡ 并挡住脏数据，认不出的给中文提示，不静默入库。
    title = (title or '').strip()
    if not title:
        return json.dumps({"success": False, "error": (
            "房源标题是空的：请给一个能认出是哪套房的标题（小区名 + 楼栋/房号）")}, ensure_ascii=False)
    normalized = {}
    price_value = norm_money(price)
    if price_value is None:
        return json.dumps({"success": False, "error": (
            f"价格没能识别：收到的是「{price}」。请按元给数字（如 185万 记作 1850000；出租月租 2200 就写 2200）")},
            ensure_ascii=False)
    if price_value != price:
        normalized['price'] = f"{price} → {price_value}元"
    area_value = _norm_area_value(area)
    if area_value is None or area_value <= 0:
        return json.dumps({"success": False, "error": (
            f"面积没能识别或不是正数：收到的是「{area}」。请给平方米数字（如 128.5 或 128平）")},
            ensure_ascii=False)
    if area_value != area:
        normalized['area'] = f"{area} → {area_value}㎡"
    if property_type not in ("new", "second_hand", "rental"):
        normalized['property_type'] = f"类型「{property_type}」不认识，已按二手房记（要改就说一声）"
        property_type = "second_hand"
    price, area = price_value, area_value
    # 合并 images 和 image_paths，并**先归档**：网关缓存目录里的文件 24 小时后会被自动清理
    # （见 agent/real_estate_media.py），所以照片要在进库前就搬进不会被清理的目录。
    img_list = []
    for src in (images, image_paths):
        if src:
            img_list.extend([x.strip() for x in src.split(',') if x.strip()])
    merged_images, image_archive = archive_images(','.join(img_list) if img_list else None)
    # 录入前查重（2026-08-29 要求：跟客户一致，重复就不录入；2026-09-21 改按身份要素判定）
    suspected = None
    if not force:
        dup, suspected = db.find_property_conflict(title=title, area=area, property_type=property_type)
        if dup:
            incoming = {"price": price, "area": area, "community": community, "district": district,
                        "address": address, "rooms": rooms, "halls": halls, "bathrooms": bathrooms,
                        "floor": floor, "orientation": orientation, "renovation": renovation,
                        "year_built": year_built, "has_elevator": has_elevator, "parking": parking,
                        "property_type": property_type, "tags": tags,
                        "tenant_requirements": tenant_requirements}
            payload = {
                "success": False, "duplicate": True, "existing_property": dup,
                "merge_preview": _merge_preview(dup, incoming),
                "options": _MERGE_OPTIONS,
                "error": (f"该房源已存在（id={dup['id']} {dup['title']}，{dup.get('price')}元 {dup.get('area')}平），"
                          f"本次未重复录入。请经纪人选：① 合并更新（用新值覆盖）"
                          f"② 只补空缺（保留已有值，只补没填的）③ 这其实是另一套（另建一条）。"),
            }
            # 这次带来的新照片：已经归档留住了（不入库也不会 24 小时后消失），但**没有**记到房源上 ——
            # 必须如实说明，否则经纪人以为照片已经录进去了
            if image_archive["archived"]:
                payload["image_archive"] = image_archive
                payload["note_image_archive"] = (
                    "这次带来的新照片已先存进长期目录（不会被自动清理），"
                    "但**没有**记到这套房源上：要加就说「加图」。")
            return json.dumps(payload, ensure_ascii=False)
    inferred = {}
    if floor is None:
        guess, why = infer_floor(title, address)
        if guess:
            floor, inferred["floor"] = guess, f"{guess}（按{why}推断，不对请直接纠正）"
    exclusive_until_dt, date_problem = norm_date(exclusive_until, '独家委托到期日')
    if date_problem:
        return json.dumps({"success": False, "error": date_problem}, ensure_ascii=False)
    result = db.add_property(
        title=title, price=price, area=area, community=community,
        district=district, address=address,
        rooms=rooms, halls=halls, bathrooms=bathrooms,
        floor=_norm_floor(floor), orientation=_norm_orientation(orientation),
        renovation=renovation, year_built=year_built,
        has_elevator=has_elevator, parking=parking, property_type=property_type,
        tags=tags, images=merged_images, agent_id=agent_id,
        tenant_requirements=tenant_requirements, viewing_note=viewing_note,
        exclusive_until=exclusive_until_dt,
    )
    # 租客要求入库后同步到返回结果
    if tenant_requirements:
        result['tenant_requirements'] = tenant_requirements
    # 业主信息一步关联：找到/新建房东(电话加密)并挂到房源 owner_id
    # 业主信息一步关联：失败要如实告知经纪人（原先静默吞掉，经纪人以为登记好了）
    owner = None
    owner_warning = None
    owner_note = None
    if owner_name or owner_phone:
        try:
            owner, owner_info = db.link_owner_to_property(result['id'], name=owner_name, phone=owner_phone,
                                                          wechat=owner_wechat, return_info=True)
            if owner_info.get('created') and owner_info.get('same_name_exists'):
                owner_note = (f"库内已有同名房东（另一个号码），这次的号码库里没有，已按新号码另记一位房东。"
                              f"如果其实是同一个人，说一声我把它并过去。")
        except Exception as exc:
            owner = None
            owner_warning = f"业主信息没登记上：{type(exc).__name__}: {exc}（房源已录入，回头单独跟我说「补业主」即可）"
    # 房源反匹配：自动扫描匹配到的客户（失败同样要如实说，不能悄悄跳过）
    match_warning = None
    try:
        matched = db.match_customers_for_property(result['id'])
    except Exception as exc:
        matched = []
        match_warning = f"自动匹配客户失败：{type(exc).__name__}: {exc}（可稍后重跑匹配）"
    # 同名提示（2026-08-13 加）：防重复录入——同标题在售房源已存在时提醒经纪人确认
    #（真实案例：雅居乐金沙湾/保利中央海岸/恒大美丽沙均录入两条价格、区域冲突的记录）
    duplicate_warning = None
    try:
        # 排除刚插入的这套房源自身 id（避免"每套房都提示同名1条"的误报——2026-08-29 修复）
        same_title = [d for d in db.search_properties(title=title, limit=10)
                      if d.get('title') == title and d.get('id') != result.get('id')]
        if same_title:
            d0 = same_title[0]
            duplicate_warning = (
                f"库内已有同名在售房源 {len(same_title)} 条（如 id={d0['id']} {d0['title']} "
                f"{d0['price']}元 {d0.get('district') or '区域未填'}），"
                f"请确认是否为不同期数/楼栋，避免重复录入"
            )
    except Exception:
        duplicate_warning = None
    response = {"success": True, "property": result}
    # 疑似重复（2026-09-21 加）：小区同一个 + 面积同口径，但标题里房号不全 → 只提示，不拦录入
    if suspected:
        response["suspected_duplicate"] = {
            "id": suspected["id"], "title": suspected["title"],
            "price": suspected.get("price"), "area": suspected.get("area"),
            "reason": suspected.get("reason"),
            "hint": "疑似同一套，请让经纪人确认是不是同一套",
        }
    if inferred:
        response["inferred"] = inferred
        response["note_inferred"] = ("上列字段是系统按房号自动推断的，请如实转述依据并请经纪人核对"
                                     "（不对就直接说新值）")
    if owner:
        response["owner"] = owner
    if owner_note:
        response["owner_note"] = owner_note
    if normalized:
        response["normalized"] = normalized
        response["note_normalized"] = "上面这些值是我按经纪人的说法换算/归一的，请如实转述并请他核对"
    # 归档明细只在「有照片没归档成功」时给：缺文件/复制失败都要让上层能如实说明，不静默
    if image_archive["missing"] or image_archive["failed"]:
        response["image_archive"] = image_archive
    if owner_warning:
        response["warning_owner"] = owner_warning
    if match_warning:
        response["warning_match"] = match_warning
    if duplicate_warning:
        response["duplicate_warning"] = duplicate_warning
    if matched:
        response["matched_customers"] = matched
        response["message"] = f"房源已添加，有 {len(matched)} 位客户可能感兴趣"
    return json.dumps(response, ensure_ascii=False)


def _norm_area_value(value):
    """把「128.5㎡ / 一百二十平 / 约128平 / 128」这类写法换算成平方米（float）；认不出返回 None"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    for junk in ('建筑面积', '平方米', '平米', '平方', '㎡', '平', '米', '约', '大约', '左右', ' '):
        text = text.replace(junk, '')
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        num = cn_number(text)
        return float(num) if num is not None else None


def _blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (int, float)):
        return v == 0
    return False


# 命中重复时给 Coco 的"合并预览"用到的字段与中文名
_MERGE_PREVIEW_FIELDS = {
    "price": "价格", "area": "面积", "community": "小区", "district": "区域", "address": "地址",
    "rooms": "室", "halls": "厅", "bathrooms": "卫", "floor": "楼层", "orientation": "朝向",
    "renovation": "装修", "year_built": "建成年份", "has_elevator": "电梯", "parking": "车位",
    "property_type": "类型", "tags": "标签", "tenant_requirements": "租客要求",
}
_MERGE_OPTIONS = [
    "① 合并更新（用你这次说的字段更新，库里其他信息保留）",
    "② 只补空缺（库里已有的一律不动，只补缺的字段）",
    "③ 确实是另一套 → 强制新增",
]


def _merge_preview(existing: dict, incoming: dict) -> dict:
    """命中重复时给 Coco 的合并预览：库里独有的（会保留）/ 这次与库里不同的（建议更新）"""
    will_keep, will_update, same = {}, {}, []
    for key, label in _MERGE_PREVIEW_FIELDS.items():
        new_val, old_val = incoming.get(key), existing.get(key)
        if _blank(new_val):
            if not _blank(old_val):
                will_keep[label] = old_val
        elif _blank(old_val):
            will_update[label] = new_val
        elif str(old_val) != str(new_val):
            will_update[label] = {"库里": old_val, "这次": new_val}
        else:
            same.append(label)
    extras = [name for name, flag in (("业主信息", existing.get("owner_id")),
                                      ("房源图片", (existing.get("images") or "").strip()))
              if flag]
    return {"will_keep": will_keep, "will_update": will_update, "same": same,
            "existing_extras": extras}


def update_property(
    property_id: int, title: str = None, price: int = None,
    area: float = None, status: str = None,
    community: str = None, district: str = None, renovation: str = None,
    rooms: int = None, halls: int = None, bathrooms: int = None,
    floor: str = None, orientation: str = None, address: str = None,
    year_built: int = None, has_elevator: int = None, parking: int = None,
    tags: str = None, fill_missing_only: bool = False,
    property_type: str = None, tenant_requirements: str = None,
    owner_name: str = None, owner_phone: str = None, owner_wechat: str = None,
    viewing_note: str = None, exclusive_until: str = None,
    task_id: str = None,
) -> str:
    """更新房源信息（可同时补充业主联系方式：owner_name/owner_phone/owner_wechat，自动登记房东并关联）

    也支持补录/修改：户型(rooms/halls/bathrooms)、楼层(floor)、朝向(orientation)、
    详细地址(address)、建造年份(year_built)、电梯(has_elevator)、车位(parking)、标签(tags)、
    房源类型(property_type，录错时可纠正)、租客要求(tenant_requirements)、看房方式(viewing_note)、独家委托到期日(exclusive_until)。
    楼层没传时，只在**库里还没有楼层**的情况下按房号补一个（不会覆盖已有值）。
    fill_missing_only=True 时**只补空缺**：库里已有值的字段一律不动（合并重复房源时用这一档）。
    """
    property_id, problem = norm_id(property_id, '房源编号')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    db = _get_db()
    # 「一串多个值」的参数先归一：模型常把 tags 当数组传（数组与逗号串都认）
    tags = as_comma_text(tags)
    # 入参归一与校验（2026-09-24 加，与 add_property 同一套）：经纪人原话（"185万"）先换算成元/㎡，
    # 认不出的、非正面积的、非法状态/类型都挡在写库之前 —— 避免把库里已有数据改成坏值。
    if price is not None:
        _price = norm_money(price)
        if _price is None:
            return json.dumps({"success": False, "error": (
                f"价格没能识别：收到的是「{price}」。请按元给数字（如 185万 记作 1850000；出租月租 2200 就写 2200）")},
                ensure_ascii=False)
        price = _price
    if area is not None:
        _area = _norm_area_value(area)
        if _area is None or _area <= 0:
            return json.dumps({"success": False, "error": (
                f"面积没能识别或不是正数：收到的是「{area}」。请给平方米数字（如 128.5 或 128平）")},
                ensure_ascii=False)
        area = _area
    if status is not None and status not in ("available", "sold", "rented"):
        return json.dumps({"success": False, "error": (
            f"状态「{status}」不认识：只能是 available（在售）/ sold（已售）/ rented（已租）")},
            ensure_ascii=False)
    if property_type is not None and property_type not in ("new", "second_hand", "rental"):
        return json.dumps({"success": False, "error": (
            f"房源类型「{property_type}」不认识：只能是 new（一手房）/ second_hand（二手房）/ rental（出租）")},
            ensure_ascii=False)
    inferred = {}
    if floor is None:
        _old = db.get_available_property(property_id) or db.get_property(property_id) or {}
        if _blank(_old.get("floor")):
            guess, why = infer_floor(title or _old.get("title"), address or _old.get("address"))
            if guess:
                floor, inferred["floor"] = guess, f"{guess}（按{why}推断，不对请直接纠正）"
    exclusive_until_dt, date_problem = norm_date(exclusive_until, '独家委托到期日')
    if date_problem:
        return json.dumps({"success": False, "error": date_problem}, ensure_ascii=False)
    kwargs = {k: v for k, v in {
        'title': title, 'price': price, 'area': area, 'status': status,
        'community': community, 'district': district, 'renovation': renovation,
        'rooms': rooms, 'halls': halls, 'bathrooms': bathrooms,
        'floor': _norm_floor(floor), 'orientation': _norm_orientation(orientation),
        'address': address, 'year_built': year_built,
        'has_elevator': has_elevator, 'parking': parking, 'tags': tags,
        'property_type': property_type, 'tenant_requirements': tenant_requirements,
        'viewing_note': viewing_note,
        'exclusive_until': exclusive_until_dt,
    }.items() if v is not None}
    kept_existing = {}
    if fill_missing_only and kwargs:
        current = db.get_available_property(property_id) or db.get_property(property_id) or {}
        kept_existing = {k: current.get(k) for k in kwargs if not _blank(current.get(k))}
        kwargs = {k: v for k, v in kwargs.items() if _blank(current.get(k))}
    result = db.update_property(property_id, **kwargs)
    if not result:
        return json.dumps({"success": False, "error": "房源不存在"}, ensure_ascii=False)
    owner = None
    owner_warning = None
    owner_note = None
    if owner_name or owner_phone:
        try:
            owner, owner_info = db.link_owner_to_property(property_id, name=owner_name, phone=owner_phone,
                                                          wechat=owner_wechat, return_info=True)
            if owner_info.get('created') and owner_info.get('same_name_exists'):
                owner_note = (f"库内已有同名房东（另一个号码），这次的号码库里没有，已按新号码另记一位房东。"
                              f"如果其实是同一个人，说一声我把它并过去。")
        except Exception as exc:
            owner = None
            owner_warning = f"业主信息登记失败：{type(exc).__name__}: {exc}（房源已更新，可重试补录业主）"
    response = {"success": True, "property": result}
    if inferred:
        response["inferred"] = inferred
        response["note_inferred"] = ("上列字段是系统按房号自动推断的，请如实转述依据并请经纪人核对"
                                     "（不对就直接说新值）")
    if kept_existing:
        response["kept_existing"] = kept_existing
        response["note_kept"] = ("这些字段库里已有值，本次只补了空缺、没有覆盖；"
                                 "要用新值覆盖就跟我说一声。")
    if owner:
        response["owner"] = owner
    if owner_note:
        response["owner_note"] = owner_note
    if owner_warning:
        response["warning_owner"] = owner_warning
    return json.dumps(response, ensure_ascii=False)


_SEARCH_LIMIT_DEFAULT = 20
_SEARCH_LIMIT_MAX = 200
_SEARCH_STATUS_VALUES = ("available", "sold", "rented", "all")
_SEARCH_SORT_VALUES = ("latest", "price_asc", "price_desc")
_SEARCH_SORT_LABEL = {"latest": "最新录入优先", "price_asc": "总价从低到高", "price_desc": "总价从高到低"}


def search_property(
    min_price: int = None, max_price: int = None,
    min_area: float = None, max_area: float = None,
    rooms: int = None, district: str = None,
    renovation: str = None, property_type: str = None,
    title: str = None, status: str = None, sort: str = None,
    limit: int = _SEARCH_LIMIT_DEFAULT, task_id: str = None,
) -> str:
    """搜索房源（支持按标题关键词、价格、面积、户型、区域、类型、状态筛选）
    
    title: 标题关键词（模糊匹配，如"华庭"可匹配滨海华庭）
    property_type: new(一手房) / second_hand(二手房) / rental(租房)
    status: available(在售，默认) / sold(已售) / rented(已租) / all(全部)
    sort: latest(最新录入优先，默认) / price_asc(总价低到高) / price_desc(总价高到低)
    limit: 本次返回条数（默认 20，最多 200；传 0/负数按默认 20 处理，不会放大返回量）
    """
    db = _get_db()
    if status is not None and status not in _SEARCH_STATUS_VALUES:
        return json.dumps({"success": False, "error": (
            f"状态「{status}」不认识：只能是 available（在售）/ sold（已售）/ rented（已租）/ all（全部）")},
            ensure_ascii=False)
    if sort is not None and sort not in _SEARCH_SORT_VALUES:
        return json.dumps({"success": False, "error": (
            f"排序「{sort}」不认识：只能是 latest（最新录入优先）/ price_asc（总价低到高）/ price_desc（总价高到低）")},
            ensure_ascii=False)
    limit = clamp_limit(limit, _SEARCH_LIMIT_DEFAULT, _SEARCH_LIMIT_MAX)
    filters = {}
    if min_price: filters['min_price'] = min_price
    if max_price: filters['max_price'] = max_price
    if min_area: filters['min_area'] = min_area
    if max_area: filters['max_area'] = max_area
    if rooms: filters['rooms'] = rooms
    if district: filters['district'] = district
    if renovation: filters['renovation'] = renovation
    if property_type: filters['property_type'] = property_type
    if title: filters['title'] = title
    filters['limit'] = limit
    filters['status'] = status or 'available'
    filters['sort'] = sort or 'latest'
    filters['with_total'] = True
    result, total = db.search_properties(**filters)
    response = {"success": True, "properties": result, "count": len(result), "total": total,
                "truncated": bool(total and total > len(result))}
    if response["truncated"]:
        response["message"] = (f"共匹配 {total} 套房源，这里列最近 {len(result)} 套"
                               f"（{_SEARCH_SORT_LABEL[filters['sort']]}）。要我多列，"
                               f"或再收窄条件（价格/面积/区域）都行。")
    return json.dumps(response, ensure_ascii=False)


_TYPE_LABELS = {"new": "一手房", "second_hand": "二手房", "rental": "租房"}

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_FLOOR_PRESETS = ("负一层", "负二层", "底层", "顶层", "低楼层", "中楼层", "高楼层")


def _cn_to_int(text: str):
    """把「十六」「二十」「三」这类中文数字转成整数（支持 1~99，认不出返回 None）"""
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        return tens * 10 + ones if (head or tail) else None
    return _CN_DIGITS.get(text)


def _norm_floor(value):
    """楼层归一：16楼/十六楼/16F → 16层；5/18层 → 5层（共18层）；低/中/高楼层、顶层、底层原样保留。

    为什么要归一：经纪人写法五花八门，不统一的话海报上会出现「3楼/三楼/03F」，
    筛选「中高楼层」也会漏。认不出的写法**原样保留**，绝不臆造。
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("Ｆ", "F").replace("f", "F").replace("樓", "楼")
    if s in _FLOOR_PRESETS:
        return s
    m = re.match(r"^(\d{1,3})\s*[/／]\s*(\d{1,3})\s*(?:楼|层|F)?$", s)       # 5/18层
    if m:
        return f"{int(m.group(1))}层（共{int(m.group(2))}层）"
    m = re.match(r"^(?:共)?(\d{1,3})\s*(?:楼|层|F)$", s)                       # 16楼 / 16层 / 16F
    if m:
        return f"{int(m.group(1))}层"
    m = re.match(r"^(?:共)?(\d{1,3})\s*层?\s*[/／]\s*共?\s*(\d{1,3})\s*层?$", s)  # 16层/共18层
    if m:
        return f"{int(m.group(1))}层（共{int(m.group(2))}层）"
    m = re.match(r"^([一二三四五六七八九十两]{1,3})楼$", s)                        # 十六楼
    if m:
        n = _cn_to_int(m.group(1))
        return f"{n}层" if n else s
    return s


_ROOM_NO_RE = re.compile(r"(?:(?:号楼|栋|幢|座|单元|室|房)\s*)?(\d{3,4})(?!\d)")
_NOT_ROOM_SUFFIX = ("平", "㎡", "万", "元", "年", "月", "日", "%", "层", "楼", "米")


def infer_floor(title, address=None):
    """从房号推断楼层（L2 依据）。返回 (值, 依据) 或 (None, None)。

    依据：国内住宅房号普遍是「楼层+户号」——3 位取首位（301→3层），4 位取前两位（1602→16层）。
    只认「楼栋/单元/室之后」或「整串末尾」的 3~4 位数字，并做三重排除：
    ① 后面紧跟 平/㎡/万/元/年/月/日/%/层/楼/米 的不是房号（面积、价格、年份、别的楼层写法）；
    ② 推得的楼层必须在 1~60 之间（年份 2015 这类直接被挡掉）；
    ③ 从末尾往前找第一个成立的，避免标题中部的干扰数字。
    推不出来就返回 (None, None) —— **绝不臆造**。
    """
    for text in (title, address):
        if not text:
            continue
        norm = str(text).replace("／", "/").replace("　", " ").strip()
        # 先看有没有明写（顶楼/高层/低楼层…）—— 这比房号推断更可靠
        for kw, val in (("顶楼", "顶层"), ("顶层", "顶层"), ("高楼层", "高楼层"),
                        ("中楼层", "中楼层"), ("低楼层", "低楼层"), ("底层", "底层")):
            if kw in norm:
                return val, f"标题/地址里的「{kw}」"
        cands = list(_ROOM_NO_RE.finditer(norm))
        for m in reversed(cands):
            room = m.group(1)
            tail = norm[m.end():m.end() + 2]
            if any(tail.startswith(sfx) for sfx in _NOT_ROOM_SUFFIX):
                continue
            if len(room) == 4:
                floor = int(room[:2])
            else:
                floor = int(room[0])
            if 1 <= floor <= 60:
                return f"{floor}层", f"房号 {room}"
    return None, None


def _norm_orientation(value):
    """朝向归一：朝北/北向/北面/北向一线 → 北；南北通 → 南北通透；认不出的原样保留。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace(" ", "").replace("　", "")
    s = re.sub(r"^(朝|面向|正向|向|正)", "", s)
    stripped = re.sub(r"(向|面|方向)$", "", s)
    if stripped:          # 剥完不能是空串（"一线看海"这类描述不该被剥没）
        s = stripped
    if s in ("南北通", "南北"):
        return "南北通透"
    if s in ("东西通", "东西"):
        return "东西通透"
    dirs = ("东南北", "西南北", "东南", "西南", "东北", "西北", "南", "北", "东", "西")
    for d in dirs:
        if s == d:
            return d
    return s
_STATUS_LABELS = {"available": "在售", "sold": "已售", "rented": "已租"}


def _fmt_field(value) -> str:
    return str(value) if value not in (None, "") else "未录入"


def _fmt_yesno(value) -> str:
    """1/0 → 有/无；**None 是"还没确认"，不能当成"无"**（电梯录入已改成留空，见 _missing_poster_info 同族）"""
    if value in (None, ""):
        return "未录入"
    return "有" if value else "无"


# 房源不能用时的说法：sold / rented / 其它状态，各自的"不能做什么"由调用方给
_PROPERTY_UNAVAILABLE_ACTION = {"sold": "发在售文案", "rented": "发在租文案"}


def unavailable_property_note(property_id, prop=None, action=None):
    """房源不能用的中文说明（**一处定义**：文案 / 海报 / 九宫格 / 口播稿都走它）

    三种情况分开说 —— 不存在 / 已售 / 已租。原先是同一句「房源不存在或不在售」，
    经纪人看不出这套其实在库、只是已售（2026-09-26 文案那轮先修，海报同族）。

    `prop`：调用方已经查过的房源 dict；`None` = 库里没有这个编号。
    `action`：这次想做什么（如「出海报」）。传 None 时按状态取默认（已售→发在售文案、已租→发在租文案）。
    返回 `(给经纪人看的话, 结构化状态标签或 None)`。
    """
    if not prop:
        return f"没有编号 {property_id} 的房源，先在房源列表里核对一下编号。", None
    status = prop.get('status')
    label = _STATUS_LABELS.get(status, status or '未知状态')
    if action is None:
        action = _PROPERTY_UNAVAILABLE_ACTION.get(status, "发布")
    phrase = {'sold': '已经售出', 'rented': '已经出租'}.get(status, f"现在状态是{label}")
    name = prop.get('title') or ''
    who = f"编号 {property_id} 的房源" + (f"（{name}）" if name else "")
    return f"{who}{phrase}，不能{action}。如果它已经回到在售，跟我说一声我把状态改回来。", label


def _fmt_area(value) -> str:
    """面积展示：整数就显示整数（100㎡），有小数才带小数（128.5㎡）"""
    if value in (None, ""):
        return "未录入"
    area = float(value)
    return f"{area:.0f}" if area == int(area) else f"{area:g}"


def _prop_brief(prop: dict) -> dict:
    """候选列表用的房源简短信息（编号/标题/价格/面积/单价/类型/状态）"""
    return {
        "id": prop.get("id"), "title": prop.get("title"),
        "price": prop.get("price"), "area": prop.get("area"),
        "district": prop.get("district"), "property_type": prop.get("property_type"),
        "status": prop.get("status"), "unit_price": prop.get("unit_price"),
    }


def _property_display(prop: dict) -> dict:
    """房源在"对比/列表"里怎么念（**只补不替换**：原值一律保留给机器读）

    - 价格：「150万」/「2500元/月」（走共用 `fmt_price`，出租不再是"0万"）
    - 面积：整数不带 `.0`（`90㎡`）；单价补单位（出租 `元/㎡/月`、其它 `元/㎡`）
    - 状态/类型：补中文（原值保留，认不出回落原样）
    """
    item = dict(prop or {})
    item["price_label"] = fmt_price(item)
    item["area_label"] = _fmt_area(item.get("area"))
    unit = item.get("unit_price")
    if unit:
        suffix = "元/㎡/月" if item.get("property_type") == "rental" else "元/㎡"
        item["unit_price_label"] = f"{fmt_unit_price(unit)}{suffix}"
    else:
        item["unit_price_label"] = None
    item["status_label"] = _STATUS_LABELS.get(item.get("status"), item.get("status") or "未录入")
    item["property_type_label"] = _TYPE_LABELS.get(item.get("property_type"),
                                                   item.get("property_type") or "未录入")
    return item


def _detail_message(prop: dict, owner, image_count: int, history: list) -> str:
    """房源详情的人类可读摘要（模型照抄即可，避免它自己拼表时漏字段）"""
    lines = [f"【房源】{prop.get('title')}（编号 {prop.get('id')}）"]
    unit_price = prop.get("unit_price")
    # 单价单位随口径走：出租算出来的是"每平米月租"，写法必须与列表/对比里的 _property_display 一致
    unit_suffix = "元/㎡/月" if prop.get("property_type") == "rental" else "元/㎡"
    unit_part = (f"单价 {fmt_unit_price(unit_price)}{unit_suffix}" if unit_price
                 else "单价 面积缺失，无法计算")
    lines.append(f"总价 {fmt_price(prop)} | 面积 {_fmt_area(prop.get('area'))}㎡ | {unit_part}")
    lines.append(
        f"类型 {_TYPE_LABELS.get(prop.get('property_type'), prop.get('property_type') or '未录入')}"
        f" | 状态 {_STATUS_LABELS.get(prop.get('status'), prop.get('status') or '未录入')}"
    )
    lines.append(
        "户型 " + (f"{prop['rooms']}室{prop.get('halls') or 0}厅{prop.get('bathrooms') or 0}卫"
                   if prop.get("rooms") else "未录入")
        + f" | 楼层 {_fmt_field(prop.get('floor'))} | 朝向 {_fmt_field(prop.get('orientation'))}"
        + f" | 装修 {_fmt_field(prop.get('renovation'))} | 年份 {_fmt_field(prop.get('year_built'))}"
        + f" | 电梯 {_fmt_yesno(prop.get('has_elevator'))}"
        + f" | 车位 {_fmt_yesno(prop.get('parking'))}"
    )
    region = " ".join(x for x in [prop.get("district"), prop.get("community"), prop.get("address")] if x)
    if region:
        lines.append(f"区域 {region}")
    if prop.get("tenant_requirements"):
        lines.append(f"租客要求 {prop['tenant_requirements']}")
    if owner:
        line = (f"【业主】{owner.get('name') or '未填姓名'}，"
                f"电话 {safe_contact(owner.get('phone')) or '未录入'}")
        _wechat = safe_contact(owner.get("wechat"))
        if _wechat:
            line += f"，微信 {_wechat}"
        if prop.get("viewing_note"):
            line += f"，看房方式 {prop['viewing_note']}"
        lines.append(line)
    else:
        lines.append("【业主】该房源还没录业主信息（跟我说「补业主 + 姓名/电话」我来登记）")
    lines.append(f"【图片】{image_count} 张" if image_count else "【图片】未关联图片")
    if history:
        h = history[0]
        lines.append(f"【调价】最近一次 调至 {fmt_price({'price': h.get('new_price'), 'property_type': prop.get('property_type')})}"
                     f"（原 {fmt_price({'price': h.get('old_price'), 'property_type': prop.get('property_type')})}）")
    return "\n".join(lines)


def get_property_detail(property_id: int = None, title: str = None, task_id: str = None) -> str:
    """房源详情（一次给全）：房源全部字段 + 单价 + 业主 + 图片 + 调价记录。

    按编号或标题定位**唯一一套**房源；标题查不到 → not_found + 最接近的候选（不返回单套数据）；
    标题命中多套 → ambiguous + 候选列表。目的是让"问某套房详情"有确定答案，既不漏业主段，
    也不会拿别的房源顶替。
    """
    db = _get_db()
    if property_id:
        # 编号形态归一（2026-09-25）：原先直接 int(property_id)，传 'abc' 这类文本会抛
        # ValueError（上层看到"执行失败"会转向自己编答案）
        pid, problem = norm_id(property_id, '房源编号')
        if problem:
            return json.dumps({"success": False, "error": problem + "。"}, ensure_ascii=False)
        prop = db.get_property(pid)
        if not prop:
            return json.dumps({"success": False, "not_found": True,
                               "error": f"没有编号为 {property_id} 的房源"}, ensure_ascii=False)
    else:
        title = (title or "").strip()
        if not title:
            return json.dumps({"success": False, "error": "请提供房源编号或标题"},
                              ensure_ascii=False)
        found = db.find_property_by_title(title)
        hits = found["exact"] or found["contains"]
        if not hits:
            candidates = db.similar_properties_by_title(title)
            error = f"库里没有找到标题为「{title}」的房源。"
            if candidates:
                error += "最接近的是下面这几套，请确认是哪一套（把编号告诉我）："
            return json.dumps({
                "success": False, "not_found": True,
                "candidates": [_prop_brief(c) for c in candidates],
                "error": error,
            }, ensure_ascii=False)
        if len(hits) > 1:
            return json.dumps({
                "success": False, "ambiguous": True,
                "candidates": [_prop_brief(h) for h in hits[:5]],
                "error": f"标题「{title}」命中 {len(hits)} 套房源，请告诉我要看哪一套（把编号告诉我）",
            }, ensure_ascii=False)
        prop = hits[0]

    pid = prop["id"]
    rows = db.get_property_owners([pid])
    owner = rows[0].get("owner") if rows else None
    image_count = len([x for x in (prop.get("images") or "").split(",") if x.strip()])
    history = db.get_price_history(pid, limit=3)
    # 联系方式展示防御（2026-09-25 收口）：message 那句一直走了 safe_contact，**结构体漏了** ——
    # 密钥不一致时 owner 里就是 gAAAA…，照同一套过一遍再交出去（房东批 F101 之后又一个同族漏点）。
    masked_fields = []
    if owner:
        owner, masked_fields = mask_contacts(owner)
    response = {
        "success": True,
        "property": prop,
        "owner": owner,
        "images": {"count": image_count},
        "price_history": history,
        "message": _detail_message(prop, owner, image_count, history),
    }
    return json.dumps(attach_key_warning(response, masked_fields, OWNER_KEY_MISMATCH_WARNING),
                      ensure_ascii=False)


def match_property(customer_id: int, top_n: int = 5, task_id: str = None) -> str:
    """根据客户需求智能匹配最合适的房源"""
    customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    db = _get_db()
    customer = db.get_customer(customer_id)
    if not customer:
        return json.dumps({"success": False, "error": "客户不存在"}, ensure_ascii=False)
    # 已有交易记录的客户不再推送房源（真实案例 2026-08-11：过户完成仍被推荐）
    if db.customer_has_deal(customer_id):
        return json.dumps({
            "success": True, "customer": customer.get('name'),
            "customer_tier": customer.get('tier'),
            "matched": False,
            "reason": "客户已有交易记录（进行中或已完成），不再推送房源；除非经纪人明确说明客户还需购房",
            "matches": [],
        }, ensure_ascii=False)
    matches = db.match_property(customer_id, top_n)
    return json.dumps({
        "success": True, "customer": customer.get('name'),
        "customer_tier": customer.get('tier'),
        "matched": True,
        "total_properties": db.count_available_properties(),
        # perfect_match 必须给到模型（提示词/操作手册都要求"只有 perfect_match=true 才能标完全匹配"，
        # 2026-09-24 修：此前白名单把它过滤掉了，模型只能自己猜口径）；unit_price 用于单价展示
        "matches": [{k: v for k, v in m.items() if k in ('id','title','community','price','area','rooms','halls',
                                                         'district','score','match_reasons','perfect_match','unit_price')}
                    for m in matches],
    }, ensure_ascii=False)


def batch_match_report(
    customer_type: str = None, tier: str = None, district: str = None,
    top_n: int = 1, task_id: str = None,
) -> str:
    """批量匹配汇报：为全部客户（或按类型/等级/区域筛选）生成逐客户匹配明细与汇总

    每个客户必有一行（无匹配显式标注"无匹配"），汇总统计由代码生成，禁止自行口算。
    customer_type: buy_new(买一手房) / buy_second_hand(买二手房) / rent(租房)；
                   unspecified=未细分（经纪人没说买新房还是买二手房的客户）
    tier: S/A/B/C
    district: 区域筛选（如"美兰区"或"美兰"）
    top_n: 每个客户展示的最佳房源数（默认 1）
    """
    db = _get_db()
    if customer_type:
        ctype, ok = norm_customer_type(customer_type)
        if not ok:
            return json.dumps({"success": False, "error": (
                f"客户类型筛选没能识别：收到的是「{customer_type}」。请用 buy_new(买一手房) / "
                f"buy_second_hand(买二手房) / rent(租房) / unspecified(未细分)")}, ensure_ascii=False)
        customer_type = ctype
    result = db.match_all_customers(top_n=top_n, customer_type=customer_type,
                                    tier=tier, district=district)
    return json.dumps({
        "success": True,
        "total_properties": db.count_available_properties(),
        "summary": result['summary'],
        "customers": result['customers'],
    }, ensure_ascii=False)


def property_stats(task_id: str = None) -> str:
    """获取房源统计数据"""
    db = _get_db()
    stats = db.get_stats()
    return json.dumps({"success": True, "stats": stats}, ensure_ascii=False)


TOOLS = [
    {"name": "add_property", "description": "添加新房源（支持业主信息：owner_name/owner_phone/owner_wechat 会自动登记房东并关联此房源、电话加密；出租房源可传 tenant_requirements 租客要求，匹配租客时用于筛选）", "parameters": {
        "type": "object", "properties": {
            "title": {"type": "string", "description": "房源标题"},
            "price": {"type": "integer", "description": "价格（元）：二手房/一手房总价如 4000000=400万；出租月租如 1000=1000元/月"},
            "area": {"type": "number", "description": "面积（㎡）"},
            "community": {"type": "string", "description": "小区名"},
            "district": {"type": "string", "description": "区域"},
            "rooms": {"type": "integer", "description": "室数"},
            "halls": {"type": "integer", "description": "厅数"},
            "renovation": {"type": "string", "enum": ["毛坯", "简装", "精装"], "description": "装修状态"},
            "property_type": {"type": "string", "enum": ["new", "second_hand", "rental"], "description": "房源类型：new(一手房)/second_hand(二手房)/rental(租房)"},
            "images": {"type": "string", "description": "房源图片，多个用逗号分隔（URL或本地路径）"},
            "image_paths": {"type": "string", "description": "经纪人消息中附带的图片本地路径，多个用逗号分隔，与 images 合并存入房源"},
            "tenant_requirements": {"type": "string", "description": "出租房源的租客要求（如不吸烟/办居住证/学生优先），多个用逗号或顿号分隔，匹配租客时会用于过滤"},
            "owner_name": {"type": "string", "description": "业主（房东）姓名。填了即自动登记房东并关联此房源"},
            "owner_phone": {"type": "string", "description": "业主（房东）手机号，加密存储"},
            "owner_wechat": {"type": "string", "description": "业主（房东）微信号，加密存储"},
            "viewing_note": {"type": "string", "description": "看房方式（如 钥匙在门店/需提前预约），房源详情与业主查询里会显示"},
            "exclusive_until": {"type": "string", "description": "独家委托到期日（如 2026-12-31；也认 明天/后天/周三/下周三/3天后 这类相对说法；填了这套房会进独家到期清单，到期前是谈续约/调价的窗口）"},
            "address": {"type": "string", "description": "详细地址（楼栋门牌等，如 7号楼2单元301）"},
            "bathrooms": {"type": "integer", "description": "卫数"},
            "floor": {"type": "string", "description": "楼层。经纪人怎么说都行（3楼/十六楼/16F/5/18层/低楼层/中楼层/高楼层/顶层），系统会归一成「16层」「5层（共18层）」这类写法"},
            "orientation": {"type": "string", "description": "朝向。如 南/北/东/西/东南/西南/东北/西北/南北通透（朝南、南向、南北通都会归一）"},
            "year_built": {"type": "integer", "description": "建造年份，如 2015"},
            "has_elevator": {"type": "integer", "description": "有无电梯：1=有，0=无；经纪人没提到就别传（留空＝还没确认，不会当成「有」）"},
            "parking": {"type": "integer", "description": "有无车位：1=有，0=无（默认 0）"},
            "tags": {"type": "string", "description": "特色标签，多个用逗号分隔，如 学区房,地铁房,精装修"},
            "force": {"type": "boolean", "description": "默认 false。true=跳过房源查重强制新增（仅当确认是不同期数/楼栋而要保留同名时用）"},
        }, "required": ["title", "price", "area"],
    }, "handler": lambda args, **kw: add_property(**args)},
    {"name": "update_property", "description": "更新房源信息（可补充业主联系方式：owner_name/owner_phone/owner_wechat，自动登记房东并关联）", "parameters": {
        "type": "object", "properties": {
            "property_id": {"type": "integer"}, "title": {"type": "string"},
            "price": {"type": "integer"}, "area": {"type": "number"},
            "status": {"type": "string", "enum": ["available", "sold", "rented"]},
            "community": {"type": "string", "description": "小区名（录错时可改）"},
            "district": {"type": "string", "description": "区域（录错时可改）"},
            "renovation": {"type": "string", "enum": ["毛坯", "简装", "精装", "豪装"], "description": "装修状态（可改）"},
            "rooms": {"type": "integer", "description": "室数"}, "halls": {"type": "integer", "description": "厅数"},
            "bathrooms": {"type": "integer", "description": "卫数"},
            "floor": {"type": "string", "description": "楼层（补录或修改；3楼/十六楼/16F/中楼层 都会归一成「16层」这类写法）"},
            "orientation": {"type": "string", "description": "朝向（补录或修改；朝北/北向 → 北；南北通 → 南北通透）"},
            "address": {"type": "string", "description": "详细地址"},
            "year_built": {"type": "integer", "description": "建造年份"},
            "has_elevator": {"type": "integer", "description": "有无电梯：1=有/0=无"},
            "parking": {"type": "integer", "description": "有无车位：1=有/0=无"},
            "tags": {"type": "string", "description": "特色标签，多个用逗号分隔"},
            "owner_name": {"type": "string", "description": "业主姓名。填了即自动登记房东并关联此房源"},
            "owner_phone": {"type": "string", "description": "业主手机号，加密存储"},
            "viewing_note": {"type": "string", "description": "看房方式（如 钥匙在门店/需提前预约），房源详情与业主查询里会显示"},
            "exclusive_until": {"type": "string", "description": "独家委托到期日（如 2026-12-31；也认 明天/后天/周三/下周三/3天后 这类相对说法；会进独家到期清单）"},
            "owner_wechat": {"type": "string", "description": "业主微信号，加密存储"},
            "property_type": {"type": "string", "enum": ["new", "second_hand", "rental"], "description": "房源类型（录错时可纠正）：new(一手房)/second_hand(二手房)/rental(出租)"},
            "tenant_requirements": {"type": "string", "description": "出租房源的租客要求（如不吸烟/办居住证/学生优先），可修改"},
            "fill_missing_only": {"type": "boolean", "description": "True=只补空缺：库里已有值的字段一律不动（合并重复房源时用这一档）"}
        }, "required": ["property_id"],
    }, "handler": lambda args, **kw: update_property(**args)},
    {"name": "search_property", "description": "搜索房源（可按标题关键词/价格/面积/户型/区域/类型/状态筛选，默认只看在售、按最新录入优先）", "parameters": {
        "type": "object", "properties": {
            "title": {"type": "string", "description": "标题关键词（模糊匹配，如华庭可匹配滨海华庭）"},
            "min_price": {"type": "integer", "description": "最低价（元）"}, "max_price": {"type": "integer", "description": "最高价（元）"},
            "min_area": {"type": "number"}, "max_area": {"type": "number"},
            "rooms": {"type": "integer"}, "district": {"type": "string"},
            "renovation": {"type": "string"},
            "property_type": {"type": "string", "enum": ["new", "second_hand", "rental"], "description": "房源类型筛选：new(一手房)/second_hand(二手房)/rental(租房)"},
            "status": {"type": "string", "enum": ["available", "sold", "rented", "all"], "description": "房源状态：available(在售，默认)/sold(已售)/rented(已租)/all(全部)"},
            "sort": {"type": "string", "enum": ["latest", "price_asc", "price_desc"], "description": "排序：latest(最新录入优先，默认)/price_asc(总价低到高)/price_desc(总价高到低)"},
            "limit": {"type": "integer", "description": "本次返回条数（默认 20，最多 200）"},
        },
    }, "handler": lambda args, **kw: search_property(**args)},
    {"name": "match_property", "description": "根据客户需求智能匹配房源", "parameters": {
        "type": "object", "properties": {
            "customer_id": {"type": "integer"}, "top_n": {"type": "integer"},
        }, "required": ["customer_id"],
    }, "handler": lambda args, **kw: match_property(**args)},
    {"name": "property_stats", "description": "获取房源统计数据", "parameters": {
        "type": "object", "properties": {},
    }, "handler": lambda args, **kw: property_stats()},
    {"name": "batch_match_report", "description": "批量匹配汇报：为全部客户（或按类型/等级/区域筛选）生成逐客户匹配明细与汇总，每个客户一行（无匹配显式标注），完全匹配/接近匹配/无匹配由代码判定，汇总数字由代码统计，禁止自行口算", "parameters": {
        "type": "object", "properties": {
            "customer_type": {"type": "string", "enum": ["buy_new", "buy_second_hand", "rent", "unspecified"], "description": "客户类型筛选：buy_new买一手房/buy_second_hand买二手房/rent租房/unspecified未细分"},
            "tier": {"type": "string", "enum": ["S", "A", "B", "C"], "description": "客户等级筛选"},
            "district": {"type": "string", "description": "区域筛选，如 美兰区 或 美兰"},
            "top_n": {"type": "integer", "description": "每个客户展示的最佳房源数，默认1"},
        },
    }, "handler": lambda args, **kw: batch_match_report(**args)},
]

def get_property_form(task_id: str = None) -> str:
    """获取房源录入模板"""
    form = """【房源录入表】

- 房源标题：（必填，如"望京新城精装三居"）
- 售价：（元，必填，如 4000000=400万；出租房填月租，如 1000=1000元/月）
- 面积：（㎡，必填）
- 小区名称：
- 所在区域：
- 详细地址：
- 户型：室 / 厅 / 卫
- 楼层：
- 朝向：（南/北/东南/南北通透等）
- 装修：（毛坯/简装/精装/豪装）
- 建造年份：
- 有无电梯：（有/无）
- 车位：（有/无）
- 房源类型：（一手房/二手房/租房）
- 业主（房东）姓名 / 电话 / 微信：（可选；填了系统会自动登记房东并关联此房源，电话加密保存）
-- 租客要求：（仅出租房源，如"不吸烟、禁养宠物/学生优先"）
-- 看房方式：（如"钥匙在门店""需提前一天预约"）
-- 独家委托到期日：（如 2026-12-31；填了会进独家到期清单）
- 特色标签：（如"学区房""地铁房"，多个用逗号分隔）
- 房源图片：（可直接在消息中发送图片，会自动关联）"""
    return json.dumps({"success": True, "form": form}, ensure_ascii=False)


registry.register(
    name="get_property_form",
    toolset="real_estate",
    schema={"name": "get_property_form", "description": "房源登记/录入时获取标准表单模板，按模板逐项收集房源信息。当经纪人要求登记房源、录入房源、添加房源、新建房源时，必须调用此工具，禁止自行编造录入格式。", "parameters": {
        "type": "object",
        "properties": {},
    }},
    handler=lambda args, **kw: get_property_form(**args),
)

registry.register(
    name="add_property",
    toolset="real_estate",
    schema={"name": "add_property", "description": "添加新房源（支持业主信息：owner_name/owner_phone/owner_wechat 会自动登记房东并关联此房源、电话加密；出租房源可传 tenant_requirements 租客要求，匹配租客时用于筛选）", "parameters": TOOLS[0]["parameters"]},
    handler=TOOLS[0]["handler"],
)
registry.register(
    name="update_property",
    toolset="real_estate",
    schema={"name": "update_property", "description": "更新/纠正房源信息：价格、面积、状态（在售/已售/已租）、户型、楼层、朝向、标签、小区、区域、类型（一手/二手/出租，录错可纠正）、租客要求、业主联系方式（自动登记房东并关联）；fill_missing_only=true 时只补空缺不覆盖", "parameters": TOOLS[1]["parameters"]},
    handler=TOOLS[1]["handler"],
)
registry.register(
    name="search_property",
    toolset="real_estate",
    schema={"name": "search_property", "description": "搜索房源（可按标题关键词/价格/面积/户型/区域/类型/状态筛选，默认只看在售、按最新录入优先）。返回 total=匹配总数、count=本次返回条数、truncated，被截断时给 message", "parameters": TOOLS[2]["parameters"]},
    handler=TOOLS[2]["handler"],
)
registry.register(
    name="match_property",
    toolset="real_estate",
    schema={"name": "match_property", "description": "智能匹配房源", "parameters": TOOLS[3]["parameters"]},
    handler=TOOLS[3]["handler"],
)
registry.register(
    name="property_stats",
    toolset="real_estate",
    schema={"name": "property_stats", "description": "房源与客户统计概览：客户（在跟数/已关闭数/各等级分布）、房源（总数、在售、已售、已租，以及在售按类型分布 new=一手房/second_hand=二手房/rental=出租）、逾期跟进数。经纪人问\"我有多少套房\"\"多少套在出租\"\"卖了几套\"\"客户多少个\"时用本工具", "parameters": TOOLS[4]["parameters"]},
    handler=TOOLS[4]["handler"],
)
registry.register(
    name="batch_match_report",
    toolset="real_estate",
    schema={"name": "batch_match_report", "description": "批量匹配汇报：为全部客户（或按类型/等级/区域筛选）生成逐客户匹配明细与汇总，每个客户一行（无匹配显式标注），完全匹配/接近匹配/无匹配由代码判定，汇总数字由代码统计，禁止自行口算", "parameters": TOOLS[5]["parameters"]},
    handler=TOOLS[5]["handler"],
)
registry.register(
    name="get_property_detail",
    toolset="real_estate",
    schema={"name": "get_property_detail", "description": "房源详情（一次给全）：按 房源编号(property_id) 或 标题(title) 查**单套**房源的完整资料——全部字段 + 单价(元/㎡；出租为 元/㎡/月，按总价÷面积自动计算) + 业主（姓名/电话/微信/看房方式）+ 图片数量 + 调价记录。经纪人问'某套房源的详细信息/详情/资料/这套房什么情况/XX栋XX房给我看看'时必须用本工具（不要用 search_property 自己拼表，否则容易漏业主段）。标题查不到会返回 not_found 与最接近的候选（绝不返回别的房源充当答案）；命中多套会返回候选列表，需先让经纪人确认编号。", "parameters": {
        "type": "object",
        "properties": {
            "property_id": {"type": "integer", "description": "房源编号（与标题二选一，优先）"},
            "title": {"type": "string", "description": "房源标题（如 262栋1009）；精确匹配优先，其次包含匹配"},
        },
    }},
    handler=lambda args, **kw: get_property_detail(**args),
)


def deduplicate_properties(dry_run: bool = True, keep: str = None, keep_id: int = None,
                           merge: bool = False, task_id: str = None) -> str:
    """房源去重：按「小区 + 房号 + 面积」找出重复房源（标题写法不同也能认出）

    dry_run=True（默认）只统计不删除；dry_run=False 执行删除。
    保留项：默认 **在售 > 在租 > 已售**，再比信息完整度（业主/图片/租客要求权重更高），最后取最早 id；
    也可 keep='richest'（只看信息完整度）、keep='earliest'（旧行为）、keep_id=指定保留哪条。
    merge=True 执行前**先把被删那条的独有信息并到保留项**（只补空缺、不覆盖），再删除。
    有关联带看/成交/跟进的一律跳过；带业主/图片而保留项没有、又没开 merge 的也跳过。
    """
    if keep_id is not None:
        keep_id, problem = norm_id(keep_id, '房源编号')
        if problem:
            return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    db = _get_db()
    result = db.remove_duplicate_properties(dry_run=dry_run, keep=keep, keep_id=keep_id, merge=merge)
    if result.get('dry_run'):
        message = (
            f"发现 {result['duplicate_groups']} 组重复房源，共 {result['duplicate_total']} 条可清理。"
            f"下面逐组列出明细，你确认后我再动手。"
        )
    else:
        message = f"已清理 {len(result['removable'])} 条重复房源（{result['duplicate_groups']} 组）"
        if result.get('merged_count'):
            message += f"，其中 {result['merged_count']} 条先把独有信息（业主/图片等）并到保留项再删除"
        if result.get('skipped'):
            message += f"；跳过 {len(result['skipped'])} 条（原因见 skipped）"
        message += "。每个保留项的完整信息我下面都列出来了，你核对无误我再执行删除。"
    return json.dumps({"success": True, "result": result, "message": message}, ensure_ascii=False)


registry.register(
    name="deduplicate_properties",
    toolset="real_estate",
    schema={"name": "deduplicate_properties", "description": "房源去重：按小区+房号+面积找出重复房源（标题写法不同也能认出）。默认保留 在售>在租>已售、信息最完整的那条。dry_run=True只统计，dry_run=False执行删除；merge=True时先把被删项的独有信息并到保留项再删。dry_run 返回后请逐组把两条记录（编号/标题/价格/面积/状态/有无业主）列给经纪人，并给三个选项 ① 合并（把独有信息并到保留项再删，信息不丢）② 只删不并 ③ 先不动；等他明确确认再调 deduplicate_properties(dry_run=false, merge=true/false)。", "parameters": {
        "type": "object",
        "properties": {
            "dry_run": {"type": "boolean", "description": "True只统计不删除（默认），False执行删除"},
            "keep": {"type": "string", "enum": ["available", "richest", "earliest"], "description": "保留哪条：available=在售优先（默认）、richest=信息最全、earliest=最早录入"},
            "keep_id": {"type": "integer", "description": "直接指定该组保留哪个房源编号（指定后该组不再按优先级挑）"},
            "merge": {"type": "boolean", "description": "True=先把被删那条的独有信息（业主/图片/租客要求等）并到保留项再删除（只补空缺、不覆盖）"},
        },
    }},
    handler=lambda args, **kw: deduplicate_properties(**args),
)


_PRICE_HISTORY_LIMIT_DEFAULT = 20
_PRICE_HISTORY_LIMIT_MAX = 200


def price_history(property_id: int, limit: int = _PRICE_HISTORY_LIMIT_DEFAULT, task_id: str = None) -> str:
    """查询房源调价历史（limit 最多返回多少条明细；次数与累计变动始终按**全部**调价统计）"""
    property_id, problem = norm_id(property_id, '房源编号')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    db = _get_db()
    # 房源不存在要和"没调过价"区分开（2026-09-24）：否则模型会对不存在的房源说"这套房没调过价"
    prop = db.get_property(property_id)
    if not prop:
        return json.dumps({"success": False, "not_found": True,
                           "error": f"没有编号为 {property_id} 的房源"}, ensure_ascii=False)
    # limit 边界（2026-09-24）：传 0/负数/非数字一律按默认 20，不再出现"0 条却报没调过价"或全量拉取
    limit = clamp_limit(limit, _PRICE_HISTORY_LIMIT_DEFAULT, _PRICE_HISTORY_LIMIT_MAX)
    history = db.get_price_history(property_id, limit)
    summary = db.get_price_history_summary(property_id)
    if not summary['count']:
        return json.dumps({"success": True, "message": "该房源暂无调价记录", "history": []}, ensure_ascii=False)
    message = f"共 {summary['count']} 次调价，累计变动 {fmt_delta(summary['change'], 1)}"
    if summary['count'] > len(history):
        message += f"（下面列出最近 {len(history)} 次明细）"
    return json.dumps({"success": True, "message": message, "history": history,
                       "total_changes": summary['count']}, ensure_ascii=False)


_PRICE_DROP_MAX_ALERTS = 50          # 一次最多列多少套房（多了模型也读不完、还烧 token）
_PRICE_DROP_MAX_CUSTOMERS = 5        # 每套房最多列多少位可联系客户（按最接近成交排序）


def price_drop_alerts(days: int = 7, task_id: str = None) -> str:
    """降价提醒：扫描近期降价房源，反匹配"预算差一点够得着"的客户，输出联系建议"""
    db = _get_db()
    # 2026-09-18 修：原先遍历 1 万套房、对每套各查一次调价历史 + 一次客户反匹配（N+1 很慢且漏房源）。
    # 现在先用一次 SQL 取出"近期降过价的在售房源"，再只对这些房源做客户反匹配。
    # 2026-09-24 修：① 扫描不再写死条数（原先 200 条上限，300 套房降价会静默漏 100 套）；
    # ② 客户池一次取出，不再对每套房各查一次（50 套 × 1000 客户原先要 30 秒）；
    # ③ 输出设上限（否则一次返回 7MB 明细，模型上下文会被撑爆）。
    drops = db.recent_price_drops(days=days)
    if not drops:
        return json.dumps({"success": True, "message": f"近{days}天没有房源降价", "alerts": []}, ensure_ascii=False)
    pool = db.customers_for_drop_pool()
    alerts = []
    for drop in drops:
        customers = db.find_customers_for_price_drop(drop["property_id"], days=days, pool=pool,
                                                     limit=_PRICE_DROP_MAX_CUSTOMERS)
        if not customers:
            continue
        alerts.append({
            "property_id": drop["property_id"],
            "title": drop["title"],
            "old_price": drop["old_price"],
            "new_price": drop["new_price"],
            "drop_amount": drop["drop_amount"],
            "matched_customers": customers,
        })
    if not alerts:
        return json.dumps({
            "success": True, "alerts": [], "drops_found": len(drops),
            "message": f"近{days}天有 {len(drops)} 套房源降价，但没有预算够得着的客户",
        }, ensure_ascii=False)
    shown = alerts[:_PRICE_DROP_MAX_ALERTS]
    total_hits = sum(len(a["matched_customers"]) for a in alerts)
    head = f"📢 近{days}天降价提醒：{len(alerts)} 套房降价可捞回客户，共列出 {total_hits} 位（每套列最接近成交的 {_PRICE_DROP_MAX_CUSTOMERS} 位）"
    if len(alerts) > len(shown):
        head += f"；这里列前 {len(shown)} 套"
    lines = [head]
    for a in shown:
        lines.append(f"\n· {a['title']}（ID:{a['property_id']}）降价 {fmt_wan(a['drop_amount'] or 0, 0)}"
                     f" → 现价 {fmt_wan(a['new_price'], 0)}")
        for c in a["matched_customers"]:
            afford = "现在够得着" if c["now_affordable"] else "还差一点"
            lines.append(f"   → {c['name']}（{c['tier']}级，预算上限{fmt_budget(c['budget_max'])}，"
                         f"上次差{fmt_wan(c['gap'], 0)}，{afford}）建议联系")
    return json.dumps({
        "success": True,
        "summary": f"{len(alerts)}套降价、{total_hits}位可捞回客户",
        "drops_found": len(drops),
        "truncated": len(alerts) > len(shown),
        "alerts": shown,
        "message": "\n".join(lines),
    }, ensure_ascii=False)


registry.register(
    name="price_history",
    toolset="real_estate",
    schema={"name": "price_history", "description": "查询房源调价历史（每次价格变动的记录）", "parameters": {
        "type": "object",
        "properties": {
            "property_id": {"type": "integer", "description": "房源ID"},
            "limit": {"type": "integer", "description": "返回条数（默认20）"},
        },
        "required": ["property_id"],
    }},
    handler=lambda args, **kw: price_history(**args),
)

registry.register(
    name="price_drop_alerts",
    toolset="real_estate",
    schema={"name": "price_drop_alerts", "description": "降价提醒：扫描近期降价房源，找出预算刚够得着的客户并生成联系建议", "parameters": {
        "type": "object",
        "properties": {
            "days": {"type": "integer", "description": "扫描近几天的调价（默认7天）"},
        },
    }},
    handler=lambda args, **kw: price_drop_alerts(**args),
)


_FIND_ALT_LIMIT_DEFAULT = 5
_FIND_ALT_LIMIT_MAX = 20


def find_alternatives(property_id: int, limit: int = _FIND_ALT_LIMIT_DEFAULT, task_id: str = None) -> str:
    """一键平替：客户看中的房被抢/下架时，按贴近度找替代房源（同用途内）"""
    property_id, problem = norm_id(property_id, '房源编号')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    db = _get_db()
    # 原房源不存在要和"没有够贴近的替代"区分开（2026-09-24）
    if not db.get_property(property_id):
        return json.dumps({"success": False, "not_found": True,
                           "error": f"没有编号为 {property_id} 的房源"}, ensure_ascii=False)
    # limit 边界（2026-09-24）：传 0/负数/非数字一律按默认 5，并设上限 20（大库里一次吐几百套会撑爆上下文）
    limit = clamp_limit(limit, _FIND_ALT_LIMIT_DEFAULT, _FIND_ALT_LIMIT_MAX)
    alts = db.find_alternatives(property_id, limit)
    if not alts:
        return json.dumps({"success": True, "message": "暂无贴近度足够的替代房源，建议扩大区域或预算范围", "alternatives": []}, ensure_ascii=False)
    lines = [f"🔁 找到 {len(alts)} 套平替方案（按贴近度排序）"]
    for a in alts:
        diff = a.get("diff_price") or 0
        diff_str = (f"{'贵' if diff > 0 else '便宜'}{fmt_wan(abs(diff), 0)}" if diff else "同价")
        lines.append(f"\n· {a['title']}（ID:{a['id']}）{fmt_wan(a['price'], 0)}（{diff_str}）"
                     f"{a['area']}㎡ {a['rooms'] or '?'}室")
        lines.append(f"  贴近度: {a['match_level']}分")
    return json.dumps({
        "success": True,
        "alternatives": alts,
        "message": "\n".join(lines),
    }, ensure_ascii=False)


registry.register(
    name="find_alternatives",
    toolset="real_estate",
    schema={"name": "find_alternatives", "description": "一键平替：客户看中的房源被抢/下架时，按同小区/同户型/同价位找替代清单", "parameters": {
        "type": "object",
        "properties": {
            "property_id": {"type": "integer", "description": "原房源ID"},
            "limit": {"type": "integer", "description": "最多返回几套（默认 5，最多 20）"},
        },
        "required": ["property_id"],
    }},
    handler=lambda args, **kw: find_alternatives(**args),
)
