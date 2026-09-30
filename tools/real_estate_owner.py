"""
Coco 房产工具 - 房东（业主）委托管理
房东登记、名下房源组合、独家委托到期提醒
身份证号与手机号同一套口径：加密存储全号（列表与详情都给完整号），变更留痕只留脱敏串
"""
import json
from datetime import datetime

from agent.real_estate_display import (OWNER_KEY_MISMATCH_WARNING, PERSON_KEY_MISMATCH_WARNING,
                                       attach_key_warning, dedup_key_mismatch_payload,
                                       mask_contacts, safe_contact)
from agent.real_estate_input import (CUSTOMER_TYPE_LABELS, clamp_limit, clean_text, clip_text, norm_id,
                                     norm_id_number, norm_phone)
from agent.real_estate_money import fmt_budget, fmt_price
from tools.real_estate_property import _STATUS_LABELS
from tools.registry import registry


def _get_db():
    import os
    from agent.real_estate_db import RealEstateDB
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        raise RuntimeError("未配置 DATABASE_URL 环境变量，拒绝初始化数据库。")
    return RealEstateDB(database_url)


FIND_LIMIT_DEFAULT = 20   # 按姓名查人：客户/业主两张表各自默认返回条数（与客户列表同口径）
EXCLUSIVE_LIMIT_DEFAULT = 20   # 独家到期清单默认返回条数

ID_FULL_MISSING_NOTE_ONE = (
    "这位房东的完整身份证号不在库里（登记早于身份证改为加密存储的版本，当时只保存了脱敏号）；"
    "需要完整号就把他证件号发我一次，我补录进去。")
ID_FULL_MISSING_NOTE_MANY = (
    "这 {n} 位房东的完整身份证号不在库里（登记早于身份证改为加密存储的版本）；"
    "需要谁的完整号，把证件号发我一次即可补录。")


def _mask_id(id_number: str) -> str:
    """身份证脱敏：保留前4后4，中间打星"""
    id_number = (id_number or '').strip()
    if len(id_number) < 8:
        return '*' * len(id_number)
    return id_number[:4] + '*' * (len(id_number) - 8) + id_number[-4:]


# re_owners 的列宽：PostgreSQL 上 varchar 超长会让整次登记失败（sqlite 不拦），入库前按列宽截断
NAME_MAX = 100
TRUST_NOTE_MAX = 200
# 列表分页口径（与客户侧 list_customers 同一套：默认 50、上限 200、≤0 与非数字按默认）
_LIST_LIMIT_DEFAULT = 50
_LIST_LIMIT_MAX = 200


def _norm_days(value, default=30):
    """天数窗口归一 → 非数字/None 按默认 30；**0 保留原义**（今天到期及已过期）；负数按 0

    与 `clamp_limit` 的"≤0 按默认"不同：days 是时间窗口，0 有明确含义（只看已到期/今天到期的）。
    """
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(value, 0)


def add_owner(name: str, phone: str = None, wechat: str = None,
              id_number: str = None, trust_note: str = None,
              notes: str = None, force: bool = False, task_id: str = None) -> str:
    """登记房东（业主）。

    登记前按手机号查重（没给手机号时按微信号），命中给出已有房东、不重复建档；
    确认是另一个人（或同一个人的另一个号）时用 force=True 跳过查重。
    姓名不参与判重：同名不同号是两个人，都要能建。
    """
    name = (name or '').strip()
    if not name:
        return json.dumps({"success": False, "error": "房东姓名不能为空"}, ensure_ascii=False)
    warnings = []
    name, _clipped = clip_text(name, NAME_MAX)
    if _clipped:
        warnings.append("房东姓名" + _clipped + "。")
    trust_note, _clipped = clip_text(clean_text(trust_note), TRUST_NOTE_MAX)
    if _clipped:
        warnings.append("信任度备注" + _clipped + "（其余内容可放进备注里）。")
    phone = norm_phone(phone)
    wechat = clean_text(wechat)
    notes = clean_text(notes)
    db = _get_db()

    if not force and (phone or wechat):
        dup, warn = db.find_duplicate_owner(phone=phone, wechat=wechat)
        if warn:
            # 密钥不一致防御：不强行判重；对经纪人只说人话，系统口径进 note_for_model
            return json.dumps(dedup_key_mismatch_payload(warn), ensure_ascii=False)
        if dup:
            identical = (dup.get('name') == name
                         and all(clean_text(dup.get(k)) == v for k, v in
                                 (('phone', phone), ('wechat', wechat),
                                  ('trust_note', trust_note), ('notes', notes)) if v is not None))
            tail = ("同号同名，不用重复登记。" if identical else
                    "如果其实是另一个人（或同一个人的另一个号），跟我说一声我另建一条。")
            return json.dumps({
                "success": False, "duplicate": True, "identical": identical,
                "existing_owner": dup,
                "error": (f"该房东已在库里（id={dup.get('id')} {dup.get('name')}，"
                          f"电话 {safe_contact(dup.get('phone')) or '未填'}）。" + tail),
            }, ensure_ascii=False)

    id_number = norm_id_number(id_number)
    owner = db.add_owner(
        name=name, phone=phone, wechat=wechat,
        id_number=id_number,
        id_masked=_mask_id(id_number) if id_number else None,
        trust_note=trust_note, notes=notes,
    )
    privacy = "（身份证已加密存储）" if id_number else ""
    payload = {"success": True, "message": f"房东 {name} 已登记{privacy}", "owner": owner}
    if warnings:
        payload["warnings"] = warnings
    return json.dumps(payload, ensure_ascii=False)


# 房东可改字段的中文名（回显"改了哪几项"用）
_OWNER_FIELD_LABELS = {'name': '姓名', 'phone': '手机号', 'wechat': '微信号',
                       'id_number': '身份证', 'id_masked': '身份证',
                       'trust_note': '信任度备注', 'notes': '备注'}


def update_owner(owner_id: int, name: str = None, phone: str = None, wechat: str = None,
                 id_number: str = None, trust_note: str = None, notes: str = None,
                 task_id: str = None) -> str:
    """修改房东（业主）资料：姓名 / 手机号 / 微信号 / 身份证 / 信任度备注 / 备注

    改手机号或微信号前**先查重（排除自己）**：命中另一位房东就拦下给提示，不悄悄改
    （合并两位房东是另一件事，本轮只提示）。每次改动写一条变更留痕，加密字段只留掩码。
    名下的房源关联不受影响（房源挂的是 owner_id）。
    """
    owner_id, problem = norm_id(owner_id, '房东编号', '，可在房东列表里查')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if all(v is None or str(v).strip() == ''
           for v in (name, phone, wechat, id_number, trust_note, notes)):
        return json.dumps({"success": False, "error": "没看到要改的内容 —— 说一下改哪一项？"
                          "（姓名/手机号/微信号/身份证/信任度备注/备注）"}, ensure_ascii=False)
    db = _get_db()
    existing = db.get_owner(owner_id)
    if not existing:
        return json.dumps({"success": False, "error": "房东不存在，请先在房东列表里核对编号"},
                          ensure_ascii=False)
    warnings = []
    updates = {}
    if name is not None and str(name).strip():
        name, clipped = clip_text(str(name).strip(), NAME_MAX)
        if clipped:
            warnings.append("房东姓名" + clipped + "。")
        updates['name'] = name
    if phone is not None and str(phone).strip():
        updates['phone'] = norm_phone(phone)
    if wechat is not None and str(wechat).strip():
        updates['wechat'] = clean_text(wechat)
    if id_number is not None and str(id_number).strip():
        id_number = norm_id_number(id_number)
        updates['id_number'] = id_number
        updates['id_masked'] = _mask_id(id_number)
    if trust_note is not None:
        trust_note, clipped = clip_text(clean_text(trust_note), TRUST_NOTE_MAX)
        if clipped:
            warnings.append("信任度备注" + clipped + "（其余内容可放进备注里）。")
        updates['trust_note'] = trust_note
    if notes is not None:
        updates['notes'] = clean_text(notes)
    if not updates:
        return json.dumps({"success": False, "error": "没看到要改的内容 —— 说一下改哪一项？"
                          "（姓名/手机号/微信号/身份证/信任度备注/备注）"}, ensure_ascii=False)

    # 改联系方式先查重（排除自己）：命中另一位房东 → 拦下给提示，不悄悄改
    if 'phone' in updates or 'wechat' in updates:
        dup, warn = db.find_duplicate_owner(phone=updates.get('phone'),
                                            wechat=updates.get('wechat'),
                                            exclude_id=owner_id)
        if warn:
            return json.dumps(dedup_key_mismatch_payload(warn), ensure_ascii=False)
        if dup:
            return json.dumps({
                "success": False, "duplicate": True, "existing_owner": dup,
                "error": (f"这个号码已经是另一位房东（id={dup.get('id')} {dup.get('name')}，"
                          f"电话 {safe_contact(dup.get('phone')) or '未填'}）在用。"
                          f"如果其实是同一个人，说一声我把他并过来；如果是另一个人，请核对号码。"),
            }, ensure_ascii=False)

    result = db.update_owner(owner_id, **updates)
    if not result:
        return json.dumps({"success": False, "error": "房东不存在，请先在房东列表里核对编号"},
                          ensure_ascii=False)
    changed = "、".join(dict.fromkeys(_OWNER_FIELD_LABELS.get(k, k) for k in updates))
    result, masked = mask_contacts(result)
    payload = {"success": True,
               "message": f"房东 {result.get('name')} 的资料已更新（{changed}）",
               "owner": result}
    if warnings:
        payload["warnings"] = warnings
    return json.dumps(attach_key_warning(payload, masked, OWNER_KEY_MISMATCH_WARNING),
                      ensure_ascii=False)


registry.register(
    name="update_owner",
    toolset="real_estate",
    schema={"name": "update_owner", "description": "修改房东（业主）资料：姓名/手机号/微信号/身份证/信任度备注/备注。改名或改号前会先查重（排除自己）—— 命中别的房东会提示核对，不会悄悄改。返回 owner 与本次改动的字段清单。", "parameters": {
        "type": "object",
        "properties": {
            "owner_id": {"type": "integer", "description": "房东ID（数字，如 12；不确定就先列房东列表查）"},
            "name": {"type": "string", "description": "姓名（要改成什么）"},
            "phone": {"type": "string", "description": "手机号（改写法的号也算改；改前会查重）"},
            "wechat": {"type": "string", "description": "微信号（加密存储；改前会查重）"},
            "id_number": {"type": "string", "description": "身份证号（加密存储）"},
            "trust_note": {"type": "string", "description": "信任度备注（如 价格坚挺/可议价）"},
            "notes": {"type": "string", "description": "备注"},
        }, "required": ["owner_id"],
    }},
    handler=lambda args, **kw: update_owner(**args),
)


def get_property_owners(property_ids: list = None, task_id: str = None) -> str:
    """按房源 ID 批量查询业主信息（房源→业主反向查询，最多 3 套）。

    实测现象：让 Coco "把这套/几套房源的业主信息给我"，Coco 只能做模糊工具搜索，
    找不到就兜底报"均未录入业主信息"。本工具补上反向能力：给房源ID → 返回该房源关联业主
    的姓名/电话/微信/看房方式；房源未关联业主则 owner=None，如实说明。
    联系方式给完整号码（经纪人本人是唯一接收方，脱敏只会挡住他自己）。
    """
    db = _get_db()
    if not property_ids:
        return json.dumps({"success": False, "error": "请提供房源 ID 列表（property_ids）"},
                          ensure_ascii=False)
    if not isinstance(property_ids, list):
        property_ids = [property_ids]
    if len(property_ids) > 3:
        return json.dumps({"success": False,
                           "error": "一次最多查询 3 套房源，请分批查询"},
                          ensure_ascii=False)
    # 编号形态归一（2026-09-25）：数组元素也要过 —— 传 'abc' 会崩、传 true 会命中 id=1（张冠李戴）
    ids = []
    for item in property_ids:
        pid, problem = norm_id(item, '房源编号')
        if problem:
            return json.dumps({"success": False, "error": problem + "。"}, ensure_ascii=False)
        ids.append(pid)
    rows = db.get_property_owners(ids)
    # 联系方式展示防御（2026-09-25 修：原先只有 message 那句走了 _safe_contact，**结构体没走**，
    # Coco 读结构化数据时拿到的是 gAAAA…；warning 也改成共用函数以便带 cipher_fields）
    masked_fields = []
    for r in rows:
        if r.get('owner'):
            r['owner'], row_masked = mask_contacts(r['owner'])
            masked_fields.extend(row_masked)
    lines = []
    for r in rows:
        o = r.get('owner')
        if not o:
            lines.append(f"· {r['title']}（房源编号 {r['id']}）：未录入业主信息")
            continue
        phone = o.get('phone')
        wechat = o.get('wechat')
        oid_number = o.get('id_number')
        view = f"，看房方式: {r.get('viewing_note')}" if r.get('viewing_note') else ""
        lines.append(
            f"· {r['title']}（房源编号 {r['id']}）：业主 {o.get('name')}，"
            f"电话 {phone or '未录'}"
            + (f"，微信 {wechat}" if wechat else "")
            + (f"，身份证 {oid_number}" if oid_number else "")
            + view)
    found_ids = {r.get('id') for r in rows}
    not_found = [pid for pid in ids if pid not in found_ids]
    if not_found:
        lines.append("编号 " + "、".join(str(i) for i in not_found) + " 没有对应房源")
    payload = {
        "success": True,
        "count": len(rows),
        "not_found": not_found,
        "properties": rows,
        "message": "\n".join(lines),
    }
    return json.dumps(attach_key_warning(payload, masked_fields, OWNER_KEY_MISMATCH_WARNING), ensure_ascii=False)


def find_person_by_name(name: str = None, limit: int = None, task_id: str = None) -> str:
    """按姓名同时查客户和业主（两边都给），电话/微信给完整号码。

    limit: 每张表返回条数（默认 20，最多 200；传 0/负数/非数字按默认 20）。
    返回里 count_customers/count_owners 是本次条数，total_customers/total_owners 是符合条件的
    总数，truncated 表示是否被截断（被截断时 message 里会说明）。
    """
    db = _get_db()
    if not (name or '').strip():
        return json.dumps({"success": False, "error": "请提供要查询的姓名"},
                          ensure_ascii=False)
    limit = clamp_limit(limit, FIND_LIMIT_DEFAULT, _LIST_LIMIT_MAX)
    result = db.find_person_by_name(name, limit=limit)
    customers = result.get('customers', [])
    owners = result.get('owners', [])
    total_customers = result.get('total_customers', len(customers))
    total_owners = result.get('total_owners', len(owners))
    truncated = total_customers > len(customers) or total_owners > len(owners)
    # 联系方式展示防御（2026-09-25）：**结构体也要过**（原先只有 message 那句走了 _safe_contact，
    # Coco 读结构化数据时拿到的是 gAAAA…）；客户段与业主段各用自己那套掩码函数。
    masked_fields = []
    for c in customers:
        _, row_masked = mask_contacts(c)
        masked_fields.extend(row_masked)
    for o in owners:
        _, row_masked = mask_contacts(o)
        masked_fields.extend(row_masked)
    lines = [f"按姓名「{name}」检索到 客户 {total_customers} 人 / 业主 {total_owners} 人："]
    if truncated:
        lines.append(f"共命中 客户 {total_customers} 人 / 业主 {total_owners} 人，"
                     f"这里各列 客户 {len(customers)} / 业主 {len(owners)} 位（姓名含关键词即命中）。"
                     f"要看得更全就把关键词写长一点，或跟我说一声我多列一些。")
    for c in customers:
        budget = '-'.join(filter(None, [str(fmt_budget(c.get('budget_min')) or ''),
                                        str(fmt_budget(c.get('budget_max')) or '')])) or '-'
        lines.append(f"\n【客户】{c.get('name')}（客户编号 {c.get('id')}）"
                     f"电话 {c.get('phone') or '未录'} | 微信 {c.get('wechat') or '未录'} | "
                     f"等级 {c.get('tier') or '-'} | "
                     f"类型 {CUSTOMER_TYPE_LABELS.get(c.get('customer_type'), c.get('customer_type') or '-')} | 预算 {budget} | "
                     f"意向 {c.get('location') or '-'} {c.get('layout_pref') or ''}")
    for o in owners:
        lines.append(f"\n【业主】{o.get('name')}（业主编号 {o.get('id')}）"
                     f"电话 {o.get('phone') or '未录'} | 微信 {o.get('wechat') or '未录'} | "
                     f"证件 {o.get('id_number') or o.get('id_masked') or '-'} | "
                     f"信任度 {o.get('trust_note') or '-'}")
    if not customers and not owners:
        lines.append("\n客户表和业主表均无此人。可能未登记；如需新建客户请提供电话及需求，业主可先登记。")
    return json.dumps(
        attach_key_warning({
            "success": True,
            "name": name,
            "customers": customers,
            "owners": owners,
            "count_customers": len(customers),
            "count_owners": len(owners),
            "total_customers": total_customers,
            "total_owners": total_owners,
            "truncated": truncated,
            "message": "\n".join(lines),
        }, masked_fields, PERSON_KEY_MISMATCH_WARNING),
        ensure_ascii=False)


def get_owner(owner_id: int, task_id: str = None) -> str:
    """查询房东详情"""
    oid, problem = norm_id(owner_id, '房东编号', '，可在房东列表里查')
    if problem:
        return json.dumps({"success": False, "error": problem + "。"}, ensure_ascii=False)
    db = _get_db()
    owner = db.get_owner(oid)
    if not owner:
        return json.dumps({"success": False, "error": "房东不存在"}, ensure_ascii=False)
    # 联系方式展示防御（2026-09-25 加，与客户详情 F46 同口径）：密钥不一致时读出来是密文，
    # 绝不能把 gAAAA… 当房东电话说给经纪人，也不能默默咽掉（给 warning 让 Coco 如实转述）。
    owner, masked = mask_contacts(owner)
    payload = {"success": True, "owner": owner}
    if not owner.get('id_number') and owner.get('id_masked'):
        payload["note_id_full_missing"] = ID_FULL_MISSING_NOTE_ONE
    return json.dumps(attach_key_warning(payload, masked, OWNER_KEY_MISMATCH_WARNING),
                      ensure_ascii=False)


def list_owners(limit: int = 50, task_id: str = None) -> str:
    """房东列表（默认最新登记优先）

    limit: 返回条数（默认 50，最多 200；传 0/负数/非数字按默认 50 处理）。
    返回 count=本次条数、total=房东总数、truncated=是否被截断（被截断时给一句说明）。
    """
    limit = clamp_limit(limit, _LIST_LIMIT_DEFAULT, _LIST_LIMIT_MAX)
    db = _get_db()
    owners, total = db.list_owners(limit=limit, with_total=True)
    # 联系方式展示防御（2026-09-25 加，与 get_owner / 客户列表 F53 同口径）
    masked_fields = []
    for row in owners:
        _, row_masked = mask_contacts(row)
        masked_fields.extend(row_masked)
    payload = {"success": True, "owners": owners, "count": len(owners), "total": total,
               "truncated": bool(total > len(owners))}
    legacy = [o for o in owners if not o.get('id_number') and o.get('id_masked')]
    if legacy:
        payload["note_id_full_missing"] = ID_FULL_MISSING_NOTE_MANY.format(n=len(legacy))
    if payload["truncated"]:
        payload["message"] = (f"共 {total} 位房东，这里列最近 {len(owners)} 位（最新登记优先）。"
                              f"要我多列就说一声。")
    return json.dumps(attach_key_warning(payload, masked_fields, OWNER_KEY_MISMATCH_WARNING), ensure_ascii=False)


def owner_portfolio(owner_id: int, limit: int = None, task_id: str = None) -> str:
    """房东名下房源组合：房源明细（默认最新登记优先）+ 在售/成交统计（统计按名下全部房源算）

    limit: 返回条数（默认 50，最多 200；传 0/负数/非数字按默认 50）。
    """
    oid, problem = norm_id(owner_id, '房东编号', '，可在房东列表里查')
    if problem:
        return json.dumps({"success": False, "error": problem + "。"}, ensure_ascii=False)
    limit = clamp_limit(limit, _LIST_LIMIT_DEFAULT, _LIST_LIMIT_MAX)
    db = _get_db()
    result = db.owner_portfolio(oid, limit=limit)
    if not result:
        return json.dumps({"success": False, "error": "房东不存在"}, ensure_ascii=False)
    # 联系方式展示防御（2026-09-25 加，与 get_owner / list_owners 同口径）
    result['owner'], masked = mask_contacts(result['owner'])
    stats = result['stats']
    shown = result['properties']
    truncated = stats['total'] > len(shown)
    if stats['total']:
        lines = [f"房东 {result['owner']['name']} 名下 {stats['total']} 套房"
                 f"（在售 {stats['available']} / 已成交 {stats['dealed']}）"]
    else:
        lines = [f"房东 {result['owner']['name']} 名下暂无房源"]
    if truncated:
        lines.append(f"共 {stats['total']} 套房源，这里列出最新登记的 {len(shown)} 套（最新登记优先）。"
                     f"要我多列就说一声。")
    for p in shown:
        # 展示口径复用共用实现：fmt_price（出租 → 元/月）/ _STATUS_LABELS（在售/已售/已租）
        viewing = f"，看房方式: {p['viewing_note']}" if p.get("viewing_note") else ""
        status = _STATUS_LABELS.get(p.get('status'), p.get('status') or '未录入')
        lines.append(f"\n· {p['title']}（房源编号 {p['id']}）{fmt_price(p)} [{status}]{viewing}")
    payload = {
        "success": True, **result,
        "count": len(shown), "truncated": truncated,
        "message": "\n".join(lines),
    }
    return json.dumps(attach_key_warning(payload, masked, OWNER_KEY_MISMATCH_WARNING), ensure_ascii=False)


def exclusive_expiring(days: int = 30, limit: int = None, task_id: str = None) -> str:
    """独家委托到期清单：到期是重新谈委托或建议调价的天然时机

    days: 未来几天内到期（默认 30；**传 0 表示只看今天到期及已过期的**；非数字/None 按默认 30，负数按 0）。
    limit: 返回条数（默认 20，最多 200；传 0/负数/非数字按默认 20）。
    返回 count(本次)/total(窗口内全部)/truncated；空结果会说明"库里有没有登记过独家委托"。
    """
    days = _norm_days(days)
    limit = clamp_limit(limit, EXCLUSIVE_LIMIT_DEFAULT, _LIST_LIMIT_MAX)
    db = _get_db()
    items, total = db.exclusive_expiring(days, limit=limit)
    if not items:
        summary = db.exclusive_summary()
        if not summary.get('registered'):
            note = "（库里还没有登记过独家委托到期日——登记或更新房源时填「独家委托到期日」即可）"
        else:
            nearest = summary.get('nearest_days')
            when = "已过期" if nearest is not None and nearest < 0 else f"还有 {nearest} 天到期"
            note = f"（库里共登记 {summary['registered']} 套独家委托，最近的一套{when}）"
        return json.dumps({"success": True, "items": [], "count": 0, "total": 0, "truncated": False,
                           "message": f"未来 {days} 天内无独家委托到期{note}"}, ensure_ascii=False)
    truncated = total > len(items)
    lines = [f"独家委托到期提醒（{days} 天内 {total} 套）"]
    if truncated:
        lines.append(f"共 {total} 套独家委托在未来 {days} 天内到期，这里列出最急的 {len(items)} 套。"
                     f"要我多列就说一声。")
    for it in items:
        when = "已过期" if it['days_remaining'] < 0 else f"还有 {it['days_remaining']} 天到期"
        lines.append(f"\n· {it['title']}（房源编号 {it['id']}）{fmt_price(it)} — {when}")
    lines.append("\n到期前是重新谈委托条件或建议调价的窗口")
    return json.dumps({
        "success": True, "items": items,
        "count": len(items), "total": total, "truncated": truncated,
        "message": "\n".join(lines),
    }, ensure_ascii=False)


TOOLS = [
    {
        "name": "add_owner",
        "description": "登记房东（业主）。可登记姓名、手机号、微信号、身份证号（加密存储）、信任度备注、备注。登记前按手机号查重，号已在库里会提示已有房东、不重复建档（确认是另一个人时用 force=true）。手机号写法不限（138 0013 8000 / +86 138-0013-8000 都会归一）",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "房东姓名"},
                "phone": {"type": "string", "description": "手机号（加密存储，写法不限，会自动归一）"},
                "wechat": {"type": "string", "description": "微信号（加密存储）"},
                "id_number": {"type": "string", "description": "身份证号（加密存储；列表与详情都给完整号）"},
                "trust_note": {"type": "string", "description": "信任度备注（如 配合带看/价格坚挺，最多 200 字）"},
                "notes": {"type": "string", "description": "备注"},
                "force": {"type": "boolean", "description": "默认 false。true=跳过房东查重强制新增（同一个人的另一个号、或确认是另一个人时用）"},
            },
            "required": ["name"],
        },
        "handler": lambda args, **kw: add_owner(**args),
    },
    {
        "name": "get_owner",
        "description": "查房东详情：姓名、手机号、微信号、身份证号、信任度备注、备注、登记时间；联系方式与身份证号给完整值（经纪人本人是唯一接收方）。房东不存在或编号写错会如实说明；登记早于身份证改为加密存储的房东只有脱敏号，会如实说明需要补录。用于经纪人问\"这位房东的详细资料/电话号码\"",
        "parameters": {
            "type": "object",
            "properties": {"owner_id": {"type": "integer", "description": "房东ID（数字，如 12；不确定就先列房东列表查）"}},
            "required": ["owner_id"],
        },
        "handler": lambda args, **kw: get_owner(**args),
    },
    {
        "name": "list_owners",
        "description": "房东列表（默认最新登记优先）：姓名、手机号、微信号、身份证号、信任度备注、备注、登记时间；联系方式与身份证号给完整值。返回 count=本次条数、total=房东总数、truncated=是否被截断。用于经纪人问\"有哪些房东/给我房东名单\"",
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "返回条数（默认 50，最多 200；传 0/负数/非数字按默认 50）"}},
        },
        "handler": lambda args, **kw: list_owners(**args),
    },
    {
        "name": "owner_portfolio",
        "description": "房东名下房源组合（默认最新登记优先）：名下房源明细（标题/价格/状态/看房方式，出租按月租说）+ 在售·成交统计（统计按名下全部房源算）。房东不存在或编号写错会如实说明。用于经纪人问\"这位房东名下有哪些房/在售几套\"",
        "parameters": {
            "type": "object",
            "properties": {
                "owner_id": {"type": "integer", "description": "房东ID（数字，如 12；不确定就先列房东列表查）"},
                "limit": {"type": "integer", "description": "返回条数（默认 50，最多 200；传 0/负数/非数字按默认 50）—— 统计数字始终按名下全部房源计算"},
            },
            "required": ["owner_id"],
        },
        "handler": lambda args, **kw: owner_portfolio(**args),
    },
    {
        "name": "get_property_owners",
        "description": "按房源ID批量查业主信息（房源→业主反向查询，最多3套）。返回每套房源关联业主的姓名/电话（完整号码）/微信/身份证号/看房方式；房源未关联业主则如实说明。用于经纪人问'这套/这几套房源的业主是谁/业主联系方式'",
        "parameters": {
            "type": "object",
            "properties": {"property_ids": {"type": "array", "items": {"type": "integer"},
                                            "description": "房源ID列表（最多3个）"}},
            "required": ["property_ids"],
        },
        "handler": lambda args, **kw: get_property_owners(**args),
    },
    {
        "name": "find_person_by_name",
        "description": "按姓名同时查客户和业主（两边都给），电话/微信/身份证号给完整值。用于经纪人问'某人/某先生/某女士的详细信息'（对方可能是客户=买家租客，也可能是业主=房东），按姓名子串匹配（通配符按字面处理），找到哪类报哪类，同名两边都有则分别列出；两表都无则如实说明。返回 count/total/truncated（客户与业主两表各自）。严禁用psql直接连库",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "姓名（子串匹配即命中，如「欧阳」能查到「欧阳先生」）"},
                "limit": {"type": "integer", "description": "每张表返回条数（默认 20，最多 200；传 0/负数/非数字按默认 20）—— 客户与业主两张表各自计"},
            },
            "required": ["name"],
        },
        "handler": lambda args, **kw: find_person_by_name(**args),
    },
    {
        "name": "exclusive_expiring",
        "description": "独家委托到期清单：到期前是重新谈委托条件或建议调价的窗口",
        "parameters": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "未来几天内到期（默认 30；传 0 表示只看今天到期及已过期的）"},
                "limit": {"type": "integer", "description": "返回条数（默认 20，最多 200；传 0/负数/非数字按默认 20）"},
            },
        },
        "handler": lambda args, **kw: exclusive_expiring(**args),
    },
]

for tool in TOOLS:
    registry.register(
        name=tool["name"],
        toolset="real_estate",
        schema={"name": tool["name"], "description": tool["description"], "parameters": tool["parameters"]},
        handler=tool["handler"],
    )
