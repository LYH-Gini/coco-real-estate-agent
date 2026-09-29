"""
Coco 房产工具 - 客户管理
"""
import json

from agent.real_estate_display import (attach_key_warning, dedup_key_mismatch_payload,
                                       mask_contacts, safe_contact)
from agent.real_estate_money import fmt_budget, fmt_wan
from agent.real_estate_input import (STAGES, STAGE_LABELS, clamp_limit, clean_tags,
                                     money_limit_problem, norm_birthday, norm_customer_type,
                                     norm_id, norm_id_number, norm_money, norm_phone,
                                     norm_stage, norm_tags,
                                     norm_tier, stage_options_text)
from tools.registry import registry

# "够不着"的硬冲突理由：匹配结果全是这些时，不能说"有 N 套可能符合需求"
_HARD_CONFLICT_REASONS = ("超预算", "区域不符", "类型不符")

# 客户状态（与建档/列表口径一致）：在跟 / 暂缓 / 已关闭
_STATUS_VALUES = ("active", "paused", "closed")
_STATUS_LABELS = {"active": "在跟", "paused": "暂缓", "closed": "已关闭"}
_STATUS_ALIASES = {"在跟": "active", "跟进中": "active", "活跃": "active",
                   "暂缓": "paused", "搁置": "paused", "暂停": "paused",
                   "关闭": "closed", "已关闭": "closed"}

# 变更历史条数（≤0 按默认，避免 limit=0 谎报"没变更"、负数变成拉全量）
_CHANGE_LIMIT_DEFAULT = 20
_CHANGE_LIMIT_MAX = 200

# 客户列表条数（同上：≤0/非数字按默认，且有上限 —— 列表最容易把上下文撑爆）
_LIST_LIMIT_DEFAULT = 20
_LIST_LIMIT_MAX = 200


def _norm_status(value):
    """客户状态归一 → (规范值或 None, 是否认得)"""
    if value is None:
        return None, True
    if not isinstance(value, str):
        return None, False
    key = value.strip().lower()
    if key in _STATUS_VALUES:
        return key, True
    alias = _STATUS_ALIASES.get(value.strip())
    return (alias, True) if alias else (None, False)


def _fail(message: str) -> str:
    return json.dumps({"success": False, "error": message}, ensure_ascii=False)


def _with_cn_labels(customer: dict) -> dict:
    """补中文阶段名与状态名：返回体里原本只有英文键（lead / active），模型只能自己翻 ——
    实测一轮翻成"线索期"、另一轮干脆把 lead、active 念给了经纪人（2026-09-29）。
    老键照留（消费方在用），这里只补不改。"""
    if not isinstance(customer, dict):
        return customer
    stage = customer.get("stage")
    if stage:
        customer.setdefault("stage_label", STAGE_LABELS.get(stage, stage))
    status = customer.get("status")
    if status:
        customer.setdefault("status_label", _STATUS_LABELS.get(status, status))
    return customer


def _stage_before(new_stage, old_stage):
    """新阶段是否比原阶段更靠前（回退）；拿不到原阶段时不算回退"""
    if not new_stage or not old_stage or new_stage not in STAGES or old_stage not in STAGES:
        return False
    return STAGES.index(new_stage) < STAGES.index(old_stage)


def _tier_error(raw_tier):
    """等级非法时的同一句提示（add_customer / update_customer / update_tier / 列表筛选共用）"""
    return f"客户等级没能识别：收到的是「{raw_tier}」。等级只能是 S / A / B / C"


def _contact_conflict(label, dup, warn):
    """改联系方式前的查重：命中别人已在用的号/微信就给两条可执行路径（与建档同口径）"""
    if warn:
        return json.dumps(dedup_key_mismatch_payload(warn), ensure_ascii=False)
    if not dup:
        return None
    return json.dumps({
        "success": False, "duplicate": True, "existing_customer": dup,
        "error": (f"{label}已经是客户「{dup['name']}」（id={dup['id']}）在用。"
                  f"如果是同一个人，我直接更新他的资料；如果是另一个人，请核对号码后跟我说一声。"),
    }, ensure_ascii=False)


def _match_message(matches, budget_max):
    """给自动匹配配一句可读的话：

    有"够得着"的（无硬冲突）就报数量；一套都够不着时改口径如实说"暂无符合需求的房源"，
    不能把 30 倍超预算的房子说成"可能符合需求"。
    """
    qualified = [m for m in matches
                 if not any(r in _HARD_CONFLICT_REASONS for r in (m.get('match_reasons') or []))]
    if qualified:
        return f"客户已添加，有 {len(qualified)} 套房源符合需求"
    parts = []
    for m in matches[:2]:
        price = m.get('price')
        price_txt = fmt_wan(price, 0) if isinstance(price, (int, float)) and price else "价格未知"
        reasons = [r for r in (m.get('match_reasons') or []) if r in _HARD_CONFLICT_REASONS]
        if '超预算' in reasons and isinstance(price, (int, float)) and isinstance(budget_max, (int, float)) \
                and price > budget_max:
            extra = f"，超出预算 {fmt_wan(price - budget_max, 0)}"
        elif reasons:
            extra = "，" + "、".join(reasons)
        else:
            extra = ""
        parts.append(f"{m.get('title') or '房源'} {price_txt}{extra}")
    return f"客户已添加。库里暂无符合需求的房源，最接近的 {len(matches)} 套仅供参考（{'；'.join(parts)}）"


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


def add_customer(
    name: str,
    phone: str = None,
    wechat: str = None,
    id_number: str = None,
    tier: str = 'C',
    budget_min: int = None,
    budget_max: int = None,
    area_pref: str = None,
    layout_pref: str = None,
    location: str = None,
    renovation: str = None,
    notes: str = None,
    source: str = None,
    customer_type: str = None,
    birthday: str = None,
    force: bool = False,
    task_id: str = None,
) -> str:
    """添加新客户到系统

    customer_type: buy_new(买一手房) / buy_second_hand(买二手房) / rent(租房)；
                   没确认是买新房还是买二手房时不传（按"未细分"登记，匹配时不限类型）。
    force=True 跳过客户查重强制新增（仅当确认要新增重复客户时才用，默认 False）。
    """
    db = _get_db()

    # 入参归一与基础校验（2026-09-24 加，与房源录入同一套口径）：模型会把经纪人的原话
    # 直接传下来（预算"300万"、生日"5月20日"），能认就换算，认不出给中文提示，绝不静默
    # 把文本存进库——文本预算会让这套客户的匹配直接崩，批量匹配整批跟着崩。
    name = name.strip() if isinstance(name, str) else name
    if not name:
        return _fail("客户姓名不能为空，请告诉我这位客户怎么称呼")

    ctype, ok = norm_customer_type(customer_type)
    if not ok:
        return _fail(f"客户类型没能识别：收到的是「{customer_type}」。请用 buy_new(买一手房) / "
                     f"buy_second_hand(买二手房) / rent(租房)；不确定是买新房还是买二手房就先不传")

    tier_value, ok = norm_tier(tier)
    if not ok:
        return _fail(_tier_error(tier))

    warnings = []
    for field_name, label in (('budget_min', '预算下限'), ('budget_max', '预算上限')):
        raw = budget_min if field_name == 'budget_min' else budget_max
        if raw is None:
            continue
        value = norm_money(raw)
        if value is None:
            return _fail(f"{label}没能识别：收到的是「{raw}」。请按元给数字（300万 记作 3000000）")
        if value < 0:
            return _fail(f"{label}不能是负数：收到的是「{raw}」")
        problem = money_limit_problem(value, label)
        if problem:
            return _fail(problem)
        if field_name == 'budget_min':
            budget_min = value
        else:
            budget_max = value
    if budget_min is not None and budget_max is not None and budget_min > budget_max:
        warnings.append(f"预算下限 {fmt_budget(budget_min)} 大于上限 {fmt_budget(budget_max)}，"
                        f"已按原样登记，请核对哪个写反了")

    raw_birthday = birthday
    birthday, ok = norm_birthday(birthday)
    if not ok:
        return _fail(f"生日没能识别：收到的是「{raw_birthday}」。请用 1990-05-20 或 05-20 这类写法")

    phone = norm_phone(phone)

    if not force:
        dup, warn = db.find_duplicate_customer(
            phone=phone, wechat=wechat, name=name, customer_type=ctype)
        if warn:
            # 密钥不一致防御：不强行判重；对经纪人只说人话，系统口径进 note_for_model
            return json.dumps(dedup_key_mismatch_payload(warn), ensure_ascii=False)
        if dup:
            # 判断本次录入与已存在客户的关键字段是否完全一致（2026-08-30 加）
            identical = True
            for k, new_v in [('name', name), ('phone', phone), ('budget_min', budget_min),
                             ('budget_max', budget_max), ('area_pref', area_pref),
                             ('layout_pref', layout_pref), ('location', location),
                             ('customer_type', customer_type), ('source', source)]:
                if new_v is None:
                    continue  # 本次未提供的字段不参与一致判断
                if str(dup.get(k)) != str(new_v):
                    identical = False
                    break
            msg = ("信息完全一致，无需重复登记。" if identical else
                   "信息有差异：是同一个人的话，我直接更新他的资料；"
                   "确实是另一个人，我另建一条档案。请先说一声。")
            return json.dumps({
                "success": False, "duplicate": True, "identical": identical, "existing_customer": dup,
                "error": (f"该客户已存在（id={dup['id']} {dup['name']}，"
                          f"手机 {safe_contact(dup.get('phone')) or '未填'}）。" + msg),
            }, ensure_ascii=False)
    result = db.add_customer(
        name=name, phone=phone, wechat=wechat, id_number=norm_id_number(id_number),
        tier=tier_value,
        budget_min=budget_min, budget_max=budget_max,
        area_pref=area_pref, layout_pref=layout_pref,
        location=location, renovation=renovation,
        notes=notes, source=source, customer_type=ctype,
        birthday=birthday,
    )
    # 录入后自动匹配（2026-08-29 加）：新客户 → 自动找匹配房源，随返回主动报告
    matched_properties = []
    match_warning = None
    try:
        matched_properties = db.match_property(result['id'], top_n=5)
    except Exception as exc:
        # 失败不能悄悄咽掉：否则界面显示"无匹配"，经纪人以为库里没合适房源
        matched_properties = []
        match_warning = f"自动匹配房源失败：{type(exc).__name__}: {exc}（可稍后重跑匹配）"
    response = {"success": True, "customer": _with_cn_labels(result)}
    if matched_properties:
        response["matched_properties"] = matched_properties
        response["message"] = _match_message(matched_properties, budget_max)
    if warnings:
        response["warnings"] = warnings
    if match_warning:
        response["warning_match"] = match_warning
    return json.dumps(response, ensure_ascii=False)


def update_customer(
    customer_id: int,
    name: str = None,
    phone: str = None,
    wechat: str = None,
    id_number: str = None,
    tier: str = None,
    budget_min: int = None,
    budget_max: int = None,
    area_pref: str = None,
    layout_pref: str = None,
    location: str = None,
    renovation: str = None,
    notes: str = None,
    status: str = None,
    source: str = None,
    customer_type: str = None,
    birthday: str = None,
    task_id: str = None,
) -> str:
    """更新客户信息（自动记录变更历史；预算大幅下调时给出需求漂移预警）

    customer_type: buy_new(买一手房) / buy_second_hand(买二手房) / rent(租房)；
                   建档时没说清、后来确认了，用这个参数补上（改类型同样留痕）。
    status: active(在跟) / paused(暂缓) / closed(已关闭)——"这个客户不跟了"就传 closed。
    """
    customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return _fail(problem)
    db = _get_db()
    old = db.get_customer(customer_id)
    if old is None:
        return json.dumps({"success": False, "error": "客户不存在"}, ensure_ascii=False)

    # 与 add_customer 同一套入参归一与校验（2026-09-24 加）：文本预算存进库会让匹配崩，
    # 乱写的等级会撞数据库约束崩，生日乱写会让生日提醒静默漏人。
    warnings = []
    if name is not None:
        name = name.strip() if isinstance(name, str) else name
        if not name:
            return _fail("客户姓名不能为空")
    if tier is not None:
        raw_tier = tier
        tier, ok = norm_tier(tier)
        if not ok:
            return _fail(_tier_error(raw_tier))
    if status is not None:
        raw_status = status
        status, ok = _norm_status(status)
        if not ok:
            return _fail(f"客户状态没能识别：收到的是「{raw_status}」。只能是 "
                         f"active(在跟) / paused(暂缓) / closed(已关闭)")
    if customer_type is not None:
        raw_type = customer_type
        customer_type, ok = norm_customer_type(customer_type)
        if not ok:
            return _fail(f"客户类型没能识别：收到的是「{raw_type}」。请用 buy_new(买一手房) / "
                         f"buy_second_hand(买二手房) / rent(租房) / unspecified(未细分)")
    for field_name, label in (('budget_min', '预算下限'), ('budget_max', '预算上限')):
        raw = budget_min if field_name == 'budget_min' else budget_max
        if raw is None:
            continue
        value = norm_money(raw)
        if value is None:
            return _fail(f"{label}没能识别：收到的是「{raw}」。请按元给数字（300万 记作 3000000）")
        if value < 0:
            return _fail(f"{label}不能是负数：收到的是「{raw}」")
        problem = money_limit_problem(value, label)
        if problem:
            return _fail(problem)
        if field_name == 'budget_min':
            budget_min = value
        else:
            budget_max = value
    eff_min = budget_min if budget_min is not None else norm_money(old.get('budget_min'))
    eff_max = budget_max if budget_max is not None else norm_money(old.get('budget_max'))
    if eff_min is not None and eff_max is not None and eff_min > eff_max:
        warnings.append(f"预算下限 {fmt_budget(eff_min)} 大于上限 {fmt_budget(eff_max)}，"
                        f"已按原样登记，请核对哪个写反了")
    if birthday is not None:
        raw_birthday = birthday
        birthday, ok = norm_birthday(birthday)
        if not ok:
            return _fail(f"生日没能识别：收到的是「{raw_birthday}」。请用 1990-05-20 或 05-20 这类写法")
    phone = norm_phone(phone)

    # 改联系方式先查重（2026-09-24 加）：建档有查重、改号这条路没有，等于从后门制造重复客户，
    # 之后用这个号再建档就会认错人。手机号与微信分开查（各查各的）。
    if phone is not None:
        dup, warn = db.find_duplicate_customer(phone=phone, exclude_id=customer_id)
        conflict = _contact_conflict("这个手机号", dup, warn)
        if conflict:
            return conflict
    if wechat is not None:
        dup, warn = db.find_duplicate_customer(wechat=wechat, exclude_id=customer_id)
        conflict = _contact_conflict("这个微信号", dup, warn)
        if conflict:
            return conflict

    kwargs = {k: v for k, v in {
        'name': name, 'phone': phone, 'wechat': wechat,
        'id_number': norm_id_number(id_number), 'tier': tier,
        'budget_min': budget_min, 'budget_max': budget_max,
        'area_pref': area_pref, 'layout_pref': layout_pref,
        'location': location, 'renovation': renovation,
        'notes': notes, 'status': status, 'source': source, 'birthday': birthday,
        'customer_type': customer_type,
    }.items() if v is not None}
    result = db.update_customer(customer_id, **kwargs)

    # 需求漂移预警：预算上限下调 >=30% → 客户可能转向更便宜的房子
    alerts = []
    if 'budget_max' in kwargs and old.get('budget_max'):
        old_max = norm_money(old.get('budget_max'))
        new_max = norm_money(kwargs.get('budget_max'))
        if old_max and new_max and old_max > 0 and new_max < old_max * 0.7:
            drop_pct = round((old_max - new_max) / old_max * 100)
            alerts.append({
                'type': 'budget_drift',
                'level': 'warning',
                'message': f"预算上限从 {fmt_budget(old_max)} 下调到 {fmt_budget(new_max)}（降 {drop_pct}%），"
                           f"客户很可能在别处看到了更便宜的房子，建议主动联系确认需求变化。",
            })
    if 'location' in kwargs and old.get('location') and kwargs['location'] != old.get('location'):
        alerts.append({
            'type': 'location_change',
            'level': 'info',
            'message': f"意向区域从「{old['location']}」变更为「{kwargs['location']}」，留意需求方向变化。",
        })

    result, masked = mask_contacts(result)
    response = {"success": True, "customer": _with_cn_labels(result)}
    if warnings:
        response['warnings'] = warnings
    if alerts:
        response['alerts'] = alerts
    return json.dumps(attach_key_warning(response, masked), ensure_ascii=False)


# 变更历史展示口径（2026-09-24 加，F76）：历史里原本只有英文键与裸数字，
# 模型/经纪人得自己翻 —— 每条补 field_label + old_display/new_display，原始三字段保留不动。
_CHANGE_FIELD_LABELS = {
    "name": "姓名", "phone": "手机号", "wechat": "微信", "id_number": "身份证",
    "tier": "客户等级",
    "status": "客户状态", "stage": "生命周期阶段",
    "budget_min": "预算下限", "budget_max": "预算上限",
    "area_pref": "面积偏好", "layout_pref": "户型偏好", "location": "意向区域",
    "renovation": "装修偏好", "notes": "备注", "source": "客户来源",
    "customer_type": "客户类型", "birthday": "生日", "tags": "标签",
}
_CHANGE_STATUS_LABELS = _STATUS_LABELS  # 变更历史与客户回执共用一张表（一处定义）
_CHANGE_TYPE_LABELS = {"buy_new": "买一手房", "buy_second_hand": "买二手房", "rent": "租房",
                       "unspecified": "未细分", "buy": "未细分"}

_DISPLAY_MAX_LEN = 40


def _display_change_value(field, value):
    """把留痕里的原始值翻成人话（不改动原值，只用于展示）"""
    if value is None or value == "":
        return "（未填）"
    text = str(value)
    if field in ("budget_min", "budget_max"):
        try:
            yuan = float(text)
        except ValueError:
            return text
        # 留痕口径**刻意不同**：变更历史要精确到 0.01 万并去掉尾零（5000 元 → "0.5万"），
        # 与"说给经纪人听"的 fmt_budget（<1 万按元说）不是一回事，别硬套。
        wan = yuan / 10000
        wan_text = f"{wan:.2f}".rstrip("0").rstrip(".")
        return f"{wan_text}万"
    if field == "status":
        return _CHANGE_STATUS_LABELS.get(text, text)
    if field == "stage":
        return STAGE_LABELS.get(text, text)
    if field == "customer_type":
        return _CHANGE_TYPE_LABELS.get(text, text)
    if "****" in text:          # 联系方式留痕是掩码，原样展示
        return text
    return text if len(text) <= _DISPLAY_MAX_LEN else text[:_DISPLAY_MAX_LEN] + "…"


def customer_change_history(customer_id: int, limit: int = _CHANGE_LIMIT_DEFAULT,
                            task_id: str = None) -> str:
    """查询客户需求变更历史（预算/区域/户型/等级/状态等字段的变更记录）

    limit: 本次返回条数（默认 20，最多 200；传 0/负数按默认 20 处理，不会谎报"没有变更"、
           也不会被放大成拉全量）
    """
    customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return _fail(problem)
    db = _get_db()
    customer = db.get_customer(customer_id)
    if not customer:
        return json.dumps({"success": False, "error": "客户不存在"}, ensure_ascii=False)
    limit = clamp_limit(limit, _CHANGE_LIMIT_DEFAULT, _CHANGE_LIMIT_MAX)
    changes, total = db.get_customer_changes(customer_id, limit=limit, with_total=True)
    for ch in changes:
        ch["field_label"] = _CHANGE_FIELD_LABELS.get(ch.get("field"), ch.get("field"))
        ch["old_display"] = _display_change_value(ch.get("field"), ch.get("old_value"))
        ch["new_display"] = _display_change_value(ch.get("field"), ch.get("new_value"))
    payload = {
        "success": True, "customer_id": customer_id,
        "customer_name": customer.get('name'),
        "changes": changes, "count": len(changes), "total": total,
        "truncated": bool(total and total > len(changes)),
    }
    if payload["truncated"]:
        payload["message"] = (f"共 {total} 条变更，这里列最近 {len(changes)} 条（最新在前）。"
                              f"要看更早的跟我说一声。")
    return json.dumps(payload, ensure_ascii=False)


def get_customer(customer_id: int, task_id: str = None) -> str:
    """获取客户详情（联系方式、身份证号、等级、预算、偏好、来源、标签、阶段、状态、生日、备注、时间）"""
    customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return _fail(problem)
    db = _get_db()
    result = db.get_customer(customer_id)
    if not result:
        return json.dumps({"success": False, "error": "客户不存在"}, ensure_ascii=False)
    # 联系方式展示防御（2026-09-24 加，与房源详情 F16 同口径）：密钥不一致时读出来是密文，
    # 绝不能把 gAAAA… 当客户手机号说给经纪人，也不能默默咽掉（要给 warning 让 Coco 如实转述）。
    result, masked = mask_contacts(result)
    return json.dumps(attach_key_warning({"success": True, "customer": _with_cn_labels(result)}, masked),
                      ensure_ascii=False)


def list_customers(tier: str = None, status: str = None, customer_type: str = None, limit: int = 20,
                   include_closed: bool = False, tag: str = None, task_id: str = None) -> str:
    """列出客户列表（默认只列在跟客户：活跃 + 暂缓，按最新录入优先）

    customer_type: buy_new(买一手房) / buy_second_hand(买二手房) / rent(租房)；
                   unspecified=未细分（经纪人没说买新房还是买二手房的客户）。
    status: active(在跟) / paused(暂缓) / closed(已关闭) —— 传了就只列该状态。
    include_closed=True 才把已关闭客户一并列出（默认不列：关掉的客户不再跟进，
    混在列表与数量里会让数字越用越虚）。
    limit: 本次返回条数（默认 20，最多 200；传 0/负数/非数字按默认 20 处理）。
    tag: 按标签筛（精确匹配单个标签；多个标签用逗号分隔表示"同时包含"；写法会归一）。
    返回 total=符合条件的总数、count=本次返回条数、truncated=是否被截断。
    """
    db = _get_db()
    if customer_type:
        ctype, ok = norm_customer_type(customer_type)
        if not ok:
            return _fail(f"客户类型筛选没能识别：收到的是「{customer_type}」。请用 buy_new(买一手房) / "
                         f"buy_second_hand(买二手房) / rent(租房) / unspecified(未细分)")
        customer_type = ctype
    if tier:
        tier_value, ok = norm_tier(tier)
        if not ok:
            return _fail(f"客户等级筛选没能识别：收到的是「{tier}」。等级只能是 S / A / B / C")
        tier = tier_value
    if status is not None:
        raw_status = status
        status, ok = _norm_status(status)
        if not ok:
            return _fail(f"客户状态筛选没能识别：收到的是「{raw_status}」。只能是 "
                         f"active(在跟) / paused(暂缓) / closed(已关闭)")
    limit = clamp_limit(limit, _LIST_LIMIT_DEFAULT, _LIST_LIMIT_MAX)

    tag_values = []
    if tag is not None:
        # 与 status/customer_type/tier 三个筛选参数同一口径：给了就校验，空值/超长给中文提示，
        # 不静默当成"没筛"（否则上层以为筛过了，实际拿到了全量）
        tag_values, problem = norm_tags(tag)
        if problem:
            return _fail(f"标签没能识别：{problem}。请给一个具体的标签名（如 学区房、急售）")
    result, total = db.list_customers(tier=tier, status=status, customer_type=customer_type,
                                      limit=limit, include_closed=include_closed, with_total=True,
                                      tag=tag if tag_values else None)
    # 联系方式展示防御（2026-09-24 加，与 get_customer / 房源详情 F16 同口径）
    masked_fields = []
    for row in result:
        _, row_masked = mask_contacts(row)
        masked_fields.extend(row_masked)
    scope = "按指定状态" if status else ("含已关闭" if include_closed else "在跟客户（活跃+暂缓）")
    if tag_values:
        scope += f"，含标签：{'、'.join(tag_values)}"
    total = total if total is not None else len(result)
    response = {"success": True, "customers": [_with_cn_labels(r) for r in result],
                "count": len(result), "total": total,
                "truncated": bool(total > len(result)), "count_scope": scope}
    if response["truncated"]:
        response["message"] = (f"共 {total} 位{scope}，这里列最近 {len(result)} 位（最新录入优先）。"
                               f"要我多列，或再收窄条件（等级/类型/标签）都行。")
    return json.dumps(attach_key_warning(response, masked_fields), ensure_ascii=False)


def update_tier(customer_id: int, tier: str, task_id: str = None) -> str:
    """调整客户等级（S高意向/A有需求/B培养/C初步接触）；每次调整记入变更历史"""
    customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return _fail(problem)
    tier_value, ok = norm_tier(tier)
    if not ok:
        return _fail(_tier_error(tier))
    db = _get_db()
    result = db.update_customer(customer_id, tier=tier_value)
    if not result:
        return json.dumps({"success": False, "error": "客户不存在"}, ensure_ascii=False)
    result, masked = mask_contacts(result)
    payload = {"success": True, "customer": _with_cn_labels(result), "message": f"已将客户等级调整为 {tier_value}"}
    return json.dumps(attach_key_warning(payload, masked), ensure_ascii=False)


def customer_stats(task_id: str = None) -> str:
    """获取经营统计（客户数按"在跟"口径；含各等级/各类型/活跃暂缓分布；房源四维；逾期跟进数）"""
    db = _get_db()
    stats = db.get_stats()
    return json.dumps({"success": True, "stats": stats}, ensure_ascii=False)


TOOLS = [
    {"name": "add_customer", "description": "添加新客户到系统（自动查重：手机号>微信>姓名+客户类型；建档后自动匹配房源并随返回报告）", "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "客户姓名"},
            "phone": {"type": "string", "description": "手机号"},
            "wechat": {"type": "string", "description": "微信号"},
            "id_number": {"type": "string", "description": "身份证号（加密存储；网签、贷款要用到，留空也行）"},
            "tier": {"type": "string", "enum": ["S", "A", "B", "C"], "description": "客户等级（仅经纪人明确告知等级时才传；未告知则不传，默认 C 级）"},
            "budget_min": {"type": "integer", "description": "预算下限（元，如 300万=3000000）"},
            "budget_max": {"type": "integer", "description": "预算上限（元）"},
            "area_pref": {"type": "string", "description": "面积偏好，如 80-120"},
            "layout_pref": {"type": "string", "description": "户型偏好，如 3室2厅"},
            "location": {"type": "string", "description": "意向区域"},
            "renovation": {"type": "string", "description": "装修偏好"},
            "notes": {"type": "string", "description": "备注"},
            "source": {"type": "string", "description": "客户来源"},
            "customer_type": {"type": "string", "enum": ["buy_new", "buy_second_hand", "rent"], "description": "客户类型：buy_new(买一手房)/buy_second_hand(买二手房)/rent(租房)。经纪人没说清买新房还是买二手房就不要传，按未细分登记（匹配时不限类型）"},
            "birthday": {"type": "string", "description": "客户生日 YYYY-MM-DD"},
            "force": {"type": "boolean", "description": "默认 false。true=跳过客户查重强制新增（仅当确认要新增重复客户时才用）"},
        },
        "required": ["name"],
    }, "handler": lambda args, **kw: add_customer(**args)},
    {"name": "update_customer", "description": "更新客户信息。注意：仅当经纪人明确要求调整客户等级时才传 tier 参数，否则不要传、不要自行修改客户等级", "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "integer", "description": "客户ID"},
            "name": {"type": "string"}, "phone": {"type": "string"},
            "tier": {"type": "string", "enum": ["S", "A", "B", "C"], "description": "客户等级（仅经纪人明确要求调整等级时传，禁止自行修改）"},
            "budget_min": {"type": "integer"}, "budget_max": {"type": "integer"},
            "area_pref": {"type": "string"}, "layout_pref": {"type": "string"},
            "location": {"type": "string"}, "renovation": {"type": "string"},
            "notes": {"type": "string"}, "status": {"type": "string", "enum": ["active", "paused", "closed"], "description": "客户状态：active在跟/paused暂缓/closed已关闭（经纪人说不跟了就传 closed）"},
            "customer_type": {"type": "string", "enum": ["buy_new", "buy_second_hand", "rent", "unspecified"], "description": "客户类型：buy_new买一手房/buy_second_hand买二手房/rent租房/unspecified未细分（建档时没说清、后来确认了用它补上）"},
            "source": {"type": "string", "description": "客户来源（如 抖音/贝壳/安居客/转介绍/门店/58/其他）"},
            "wechat": {"type": "string", "description": "客户微信号（加密存储；建档后补录或修改都用这个参数）"},
            "id_number": {"type": "string", "description": "客户身份证号（加密存储；建档后补录或修改都用这个参数）"},
            "birthday": {"type": "string", "description": "客户生日，格式 MM-DD 或 YYYY-MM-DD"},
        },
        "required": ["customer_id"],
    }, "handler": lambda args, **kw: update_customer(**args)},
    {"name": "get_customer", "description": "获取某位客户的完整资料（联系方式、身份证号、等级、预算区间、面积/户型偏好、意向区域、装修偏好、来源、标签、生命周期阶段、在跟/已关闭状态、生日、备注、建档与更新时间）。按客户编号查，编号来自建档或客户列表。", "parameters": {
        "type": "object", "properties": {"customer_id": {"type": "integer"}}, "required": ["customer_id"],
    }, "handler": lambda args, **kw: get_customer(**args)},
    {"name": "list_customers", "description": "列出客户列表（默认只列在跟客户：活跃+暂缓，按最新录入优先；可按等级/客户类型/状态/**标签**筛选；已关闭客户默认不列，要看需传 include_closed=true 或 status=\"closed\"）。返回 total=符合条件的总数、count=本次返回条数、truncated", "parameters": {
        "type": "object", "properties": {
            "tier": {"type": "string", "enum": ["S", "A", "B", "C"]},
            "customer_type": {"type": "string", "enum": ["buy_new", "buy_second_hand", "rent", "unspecified"], "description": "客户类型筛选：buy_new买一手房/buy_second_hand买二手房/rent租房/unspecified未细分（经纪人没确认买新房还是买二手房的客户）"},
            "status": {"type": "string", "enum": ["active", "paused", "closed"], "description": "按状态筛选（不传默认不列已关闭客户）"},
            "include_closed": {"type": "boolean", "description": "是否把已关闭客户一起列出（默认 false）"},
            "tag": {"type": "string", "description": "按标签筛：精确匹配单个标签；多个标签用逗号分隔表示同时包含（写法会归一，如 学区房、急售）"},
            "limit": {"type": "integer", "description": "本次返回条数，默认20，最多200（传0/负数按默认20）"},
        },
    }, "handler": lambda args, **kw: list_customers(**args)},
    {"name": "update_tier", "description": "调整客户等级（S=高意向 2 天内跟进 / A=有需求 5 天内 / B=培养 / C=初步接触）。只在经纪人明确要求调级时使用；每次调整都会记入变更历史。", "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "integer"},
            "tier": {"type": "string", "enum": ["S", "A", "B", "C"]},
        },
        "required": ["customer_id", "tier"],
    }, "handler": lambda args, **kw: update_tier(**args)},
    {"name": "customer_stats", "description": "获取经营统计：客户数（在跟口径，已关闭单列）、各等级客户数、各类型客户数、活跃/暂缓分布、房源统计（总数/在售/已售/已租/在售按类型）、逾期跟进数。客户来源分布用 channel_stats。", "parameters": {
        "type": "object", "properties": {},
    }, "handler": lambda args, **kw: customer_stats()},
]

registry.register(
    name="add_customer",
    toolset="real_estate",
    schema={"name": "add_customer", "description": "添加新客户到系统", "parameters": TOOLS[0]["parameters"]},
    handler=TOOLS[0]["handler"],
)
registry.register(
    name="update_customer",
    toolset="real_estate",
    schema={"name": "update_customer", "description": "更新客户信息（自动记录变更历史；预算大幅下调≥30%时返回需求漂移预警；改手机号/微信会先查重防撞号）", "parameters": TOOLS[1]["parameters"]},
    handler=TOOLS[1]["handler"],
)
registry.register(
    name="customer_change_history",
    toolset="real_estate",
    schema={"name": "customer_change_history", "description": "查询客户需求变更历史（预算/区域/户型/等级/状态/阶段/标签等字段的变更记录，最新的在前）。每条含 field_label 中文名与 old_display/new_display 可读值；返回 total=变更总条数、count=本次返回条数、truncated", "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "integer", "description": "客户ID"},
            "limit": {"type": "integer", "description": "返回条数，默认20，最多200（传0或负数按默认20）"},
        },
        "required": ["customer_id"],
    }},
    handler=lambda args, **kw: customer_change_history(**args),
)
registry.register(
    name="get_customer",
    toolset="real_estate",
    schema={"name": "get_customer", "description": "获取某位客户的完整资料（联系方式、身份证号、等级、预算区间、面积/户型偏好、意向区域、装修偏好、来源、标签、生命周期阶段、在跟/已关闭状态、生日、备注、建档与更新时间）。按客户编号查，编号来自建档或客户列表。", "parameters": TOOLS[2]["parameters"]},
    handler=TOOLS[2]["handler"],
)
registry.register(
    name="list_customers",
    toolset="real_estate",
    schema={"name": "list_customers", "description": "列出客户列表（默认只列在跟客户：活跃+暂缓，按最新录入优先；可按等级/客户类型/状态/**标签**筛选；已关闭客户默认不列，要看需传 include_closed=true 或 status=\"closed\"）。返回 total=符合条件的总数、count=本次返回条数、truncated", "parameters": TOOLS[3]["parameters"]},
    handler=TOOLS[3]["handler"],
)
registry.register(
    name="update_tier",
    toolset="real_estate",
    schema={"name": "update_tier", "description": "调整客户等级（S=高意向 2 天内跟进 / A=有需求 5 天内 / B=培养 / C=初步接触）。只在经纪人明确要求调级时使用；每次调整都会记入变更历史。", "parameters": TOOLS[4]["parameters"]},
    handler=TOOLS[4]["handler"],
)
registry.register(
    name="customer_stats",
    toolset="real_estate",
    schema={"name": "customer_stats", "description": "获取经营统计：客户数（在跟口径，已关闭单列）、各等级客户数、各类型客户数、活跃/暂缓分布、房源统计（总数/在售/已售/已租/在售按类型）、逾期跟进数。客户来源分布用 channel_stats。", "parameters": TOOLS[5]["parameters"]},
    handler=TOOLS[5]["handler"],
)


def add_customer_tag(
    customer_id: int,
    tag: str,
    task_id: str = None,
) -> str:
    """添加客户标签（支持一次传多个；重复的不会重复添加，写法会归一）"""
    customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return _fail(problem)
    db = _get_db()
    customer = db.get_customer(customer_id)
    if not customer:
        return json.dumps({"success": False, "error": "客户不存在"}, ensure_ascii=False)
    new_tags, problem = norm_tags(tag)
    if problem:
        return _fail(f"标签没能识别：{problem}。请给一个具体的标签名（如 学区房、急售）")
    tag_list = clean_tags(customer.get('tags'))
    added = [t for t in new_tags if t not in tag_list]
    already = [t for t in new_tags if t in tag_list]
    if added:
        db.update_customer(customer_id, tags=','.join(tag_list + added))
        tag_list = tag_list + added
    if added:
        message = f"已添加标签：{'、'.join(added)}"
        if already:
            message += f"；{'、'.join(already)} 已有（未重复添加）"
    else:
        message = f"这些标签客户已经有了：{'、'.join(already)}（未重复添加）"
    return json.dumps({"success": True, "message": message, "tags": tag_list,
                       "added": added, "already": already}, ensure_ascii=False)


def remove_customer_tag(
    customer_id: int,
    tag: str,
    task_id: str = None,
) -> str:
    """移除客户标签（写法会归一：库里带空格的旧标签也能按规范写法删掉）"""
    customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return _fail(problem)
    db = _get_db()
    customer = db.get_customer(customer_id)
    if not customer:
        return json.dumps({"success": False, "error": "客户不存在"}, ensure_ascii=False)
    targets, problem = norm_tags(tag)
    if problem:
        return _fail(f"标签没能识别：{problem}。请给一个具体的标签名（如 学区房、急售）")
    tag_list = clean_tags(customer.get('tags'))
    removed = [t for t in targets if t in tag_list]
    if not removed:
        if not tag_list:
            return json.dumps({"success": False, "message": "该客户还没有标签",
                               "tags": [], "error": "该客户还没有标签"}, ensure_ascii=False)
        return json.dumps({"success": False, "tags": tag_list,
                           "error": f"客户没有这个标签（现有：{'、'.join(tag_list)}）"},
                          ensure_ascii=False)
    rest = [t for t in tag_list if t not in removed]
    db.update_customer(customer_id, tags=','.join(rest))
    return json.dumps({"success": True, "message": f"已移除标签：{'、'.join(removed)}",
                       "tags": rest, "removed": removed}, ensure_ascii=False)


def list_customer_tags(
    customer_id: int,
    task_id: str = None,
) -> str:
    """查看客户标签"""
    customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return _fail(problem)
    db = _get_db()
    customer = db.get_customer(customer_id)
    if not customer:
        return json.dumps({"success": False, "error": "客户不存在"}, ensure_ascii=False)
    tag_list = clean_tags(customer.get('tags'))
    return json.dumps({"success": True, "customer_id": customer_id, "tags": tag_list,
                       "count": len(tag_list)}, ensure_ascii=False)


registry.register(
    name="add_customer_tag",
    toolset="real_estate",
    schema={"name": "add_customer_tag", "description": "给客户打标签（支持一次传多个，用逗号/顿号分隔；重复的标签不会重复添加）。标签用于按特征筛客户、批量跟进。", "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "integer", "description": "客户ID"},
            "tag": {"type": "string", "description": "标签名称"},
        },
        "required": ["customer_id", "tag"],
    }},
    handler=lambda args, **kw: add_customer_tag(**args),
)

registry.register(
    name="remove_customer_tag",
    toolset="real_estate",
    schema={"name": "remove_customer_tag", "description": "移除客户某个标签（会归一写法：带空格/全角写法的同一个标签也能删掉）。", "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "integer", "description": "客户ID"},
            "tag": {"type": "string", "description": "标签名称"},
        },
        "required": ["customer_id", "tag"],
    }},
    handler=lambda args, **kw: remove_customer_tag(**args),
)

registry.register(
    name="list_customer_tags",
    toolset="real_estate",
    schema={"name": "list_customer_tags", "description": "查看某位客户的全部标签。", "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "integer", "description": "客户ID"},
        },
        "required": ["customer_id"],
    }},
    handler=lambda args, **kw: list_customer_tags(**args),
)


def get_customer_form(task_id: str = None) -> str:
    """获取客户录入模板（栏位与 add_customer 的 schema 逐项对齐）"""
    form = """【客户录入表】

- 客户姓名：（必填）
- 客户电话：（加密保存；留空也行，可以后补）
- 客户微信：（加密保存）
- 客户身份证：（加密保存；网签、贷款要用到，留空也行，可以后补）
- 客户类型：(买一手房) / (买二手房) / (租房)；不确定买新房还是买二手房就先不填，按"未细分"登记
- 预算范围：（元，如 3000000-5000000）
- 面积偏好：（如 80-120㎡）
- 户型需求：（如 3室2厅）
- 意向区域：
- 装修偏好：（毛坯/简装/精装）
- 客户来源：（安居客/贝壳/抖音/转介绍/门店/58/其他）
- 客户等级：（S/A/B/C，不确定就不填，默认 C 级）
- 客户生日：（可选，如 1990-05-20 或 05-20；填了到时自动提醒）
- 客户情况 / 备注：
- 下次回访日期：（可选，YYYY-MM-DD；登记后记一条跟进并带上这个日期，到点会提醒）

温馨提示：只有姓名是硬性必填。其余字段你没有的可以后续再补。比如只给姓名 + 电话 + 预算，我就能直接建档并自动跑匹配。"""
    return json.dumps({"success": True, "form": form}, ensure_ascii=False)


registry.register(
    name="get_customer_form",
    toolset="real_estate",
    schema={"name": "get_customer_form", "description": "客户登记/录入时获取标准表单模板，按模板逐项收集客户信息。当经纪人要求登记客户、录入客户、新建客户资料时，必须调用此工具，禁止自行编造录入格式。", "parameters": {
        "type": "object",
        "properties": {},
    }},
    handler=lambda args, **kw: get_customer_form(**args),
)


def update_customer_stage(customer_id: int, stage: str, task_id: str = None) -> str:
    """更新客户生命周期阶段，自动写变更历史

    stage: lead(潜在) / interested(意向) / strong(强意向) / viewed(已看房) /
           negotiating(谈判) / dealing(成交中) / maintain(售后维护) / lost(流失)；
           中文说法与大小写都认。
    """
    customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return _fail(problem)
    db = _get_db()
    stage_value, ok = norm_stage(stage)
    if not ok:
        return _fail(f"客户阶段没能识别：收到的是「{stage}」。可用阶段：{stage_options_text()}")
    old = db.get_customer(customer_id)
    if not old:
        return json.dumps({"success": False, "error": "客户不存在"}, ensure_ascii=False)
    try:
        updated = db.update_stage(customer_id, stage_value)
    except ValueError:
        # 理论上到不了（上面已归一），兜住数据库层的英文异常
        return _fail(f"客户阶段没能识别：收到的是「{stage}」。可用阶段：{stage_options_text()}")
    if not updated:
        return json.dumps({"success": False, "error": "客户不存在"}, ensure_ascii=False)

    # 阶段与状态/成交是两套维度，这里只如实提示、不替经纪人做决定（2026-09-24 定的口径）
    warnings = []
    if old.get('status') == 'closed':
        warnings.append("该客户已关闭，改阶段前请确认是否要重新打开")
    if db.customer_has_deal(customer_id) and _stage_before(stage_value, old.get('stage')):
        warnings.append("该客户有成交记录，阶段回退请确认")
    if stage_value == 'lost' and old.get('status') != 'closed':
        warnings.append("该客户状态仍是「在跟」；如果不再跟进了，说一声我把他改成已关闭")

    updated, masked = mask_contacts(updated)
    payload = {
        "success": True,
        "message": f"{updated['name']} 生命周期阶段已更新为: {STAGE_LABELS[stage_value]}",
        "customer": _with_cn_labels(updated),
    }
    if warnings:
        payload["warnings"] = warnings
    return json.dumps(attach_key_warning(payload, masked), ensure_ascii=False)


registry.register(
    name="update_customer_stage",
    toolset="real_estate",
    schema={"name": "update_customer_stage", "description": "更新客户生命周期阶段（潜在/意向/强意向/已看房/谈判/成交中/售后维护/流失），自动记录变更历史", "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "integer", "description": "客户ID"},
            "stage": {"type": "string", "enum": ["lead", "interested", "strong", "viewed", "negotiating", "dealing", "maintain", "lost"], "description": "目标阶段"},
        },
        "required": ["customer_id", "stage"],
    }},
    handler=lambda args, **kw: update_customer_stage(**args),
)


def add_referral(referrer_customer_id: int, referred_name: str,
                 referred_phone: str = None, reward_note: str = None,
                 task_id: str = None) -> str:
    """登记转介绍：老客户介绍新客，自动建新客户档案并标记来源为转介绍

    登记前会认人（2026-09-24 加）：被介绍人若已在库里 → 复用他的档案，不新建重复客户；
    被介绍人手机号等于介绍人自己 → 拦下；同一介绍人已登记过同一位被介绍人 → 提示，不重复登记。
    """
    referrer_customer_id, problem = norm_id(referrer_customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return _fail(problem)
    db = _get_db()
    referred_name = (referred_name or '').strip()
    if not referred_name:
        return json.dumps({"success": False, "error": "被介绍人姓名不能为空"}, ensure_ascii=False)
    referrer = db.get_customer(referrer_customer_id)
    if not referrer:
        return json.dumps({"success": False, "error": "介绍人客户不存在"}, ensure_ascii=False)

    phone = norm_phone(referred_phone)
    if phone and norm_phone(referrer.get('phone')) == phone:
        return _fail("被介绍人不能是介绍人自己（手机号与介绍人相同），请核对是谁介绍的")

    # 认人：给了手机号就只按手机号认（与建档/改号同口径，不退回按姓名合并）；
    # 没给手机号才按姓名找（同名不同人时只提示、不合并）
    existing, match_note = None, None
    if phone:
        existing, _warn = db.find_duplicate_customer(phone=phone)
        if existing:
            match_note = f"这位客户已在库里（id={existing['id']} {existing['name']}，手机号相同），已挂到该档案，未新建重复客户"
    else:
        existing = db.find_customer_by_name(referred_name)
        if existing:
            match_note = (f"库里已有同名客户（id={existing['id']}），已挂到该档案；"
                          f"如果其实是两个人，请带上手机号重新登记")

    referred_customer_id = existing['id'] if existing else None
    if referred_customer_id and db.has_referral(referrer_customer_id, referred_customer_id):
        return json.dumps({
            "success": False, "duplicate": True,
            "referred_customer_id": referred_customer_id,
            "error": (f"{referrer['name']} 已经把这位客户（id={referred_customer_id}）登记为转介绍了，"
                      f"本次未重复登记，避免贡献榜重复计数"),
        }, ensure_ascii=False)

    r = db.add_referral(referrer_customer_id=referrer_customer_id,
                        referred_name=referred_name, referred_phone=phone,
                        referred_customer_id=referred_customer_id,
                        reward_note=reward_note)
    message = (f"转介绍已登记：{referrer['name']} 介绍了 {referred_name}，"
               f"{'已挂到库里已有档案' if existing else '新客户档案已建（来源: 转介绍）'}。"
               f"成交后别忘了答谢 {referrer['name']}")
    payload = {"success": True, "message": message, "referral": r}
    if match_note:
        payload["note"] = match_note
        payload["reused_existing_customer"] = bool(existing)
    return json.dumps(payload, ensure_ascii=False)


def referral_stats(limit: int = _LIST_LIMIT_DEFAULT, task_id: str = None) -> str:
    """转介绍贡献榜：谁介绍了几个客户、其中几个（人）已成交

    limit: 榜单条数（默认 20，最多 200；传 0/负数/非数字按默认 20 处理）。
    返回 total=介绍人总数、count=本次返回条数、truncated=是否被截断。
    """
    db = _get_db()
    limit = clamp_limit(limit, _LIST_LIMIT_DEFAULT, _LIST_LIMIT_MAX)
    board = db.referral_stats(limit=limit)
    total = db.count_referrers()
    payload = {"success": True, "leaderboard": board, "count": len(board), "total": total,
               "truncated": bool(total > len(board))}
    if not board:
        payload["message"] = "暂无转介绍记录"
        return json.dumps(payload, ensure_ascii=False)
    lines = ["转介绍贡献榜"]
    for i, row in enumerate(board, 1):
        tier_txt = f"（{row['tier']}级）" if row.get('tier') else ""
        lines.append(f"{i}. {row['referrer_name']}{tier_txt}："
                     f"介绍 {row['referrals']} 人，其中 {row['deals_from_referrals']} 人成交")
    payload["message"] = "\n".join(lines)
    if payload["truncated"]:
        payload["message"] += (f"\n共 {total} 位介绍人，这里列前 {len(board)} 位。"
                               f"要我列全就说一声。")
    return json.dumps(payload, ensure_ascii=False)


registry.register(
    name="add_referral",
    toolset="real_estate",
    schema={"name": "add_referral", "description": "登记转介绍：老客户介绍新客，自动建新客户档案（来源:转介绍），成交后可答谢介绍人", "parameters": {
        "type": "object",
        "properties": {
            "referrer_customer_id": {"type": "integer", "description": "介绍人（老客户）ID"},
            "referred_name": {"type": "string", "description": "被介绍人姓名"},
            "referred_phone": {"type": "string", "description": "被介绍人手机号（加密存储）"},
            "reward_note": {"type": "string", "description": "酬谢备注"},
        },
        "required": ["referrer_customer_id", "referred_name"],
    }},
    handler=lambda args, **kw: add_referral(**args),
)

registry.register(
    name="referral_stats",
    toolset="real_estate",
    schema={"name": "referral_stats", "description": "转介绍贡献榜：按介绍人数排序，含每位介绍人介绍了多少人、其中多少人已成交；返回 total=介绍人总数、count=本次返回条数、truncated", "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "榜单条数，默认20，最多200（传0/负数按默认20）"},
        },
    }},
    handler=lambda args, **kw: referral_stats(**args),
)
