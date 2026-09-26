"""
Coco 房产工具 - 成交/交易管理
"""
import json
from datetime import datetime
from tools.registry import registry
from agent.real_estate_input import (STAGE_LABELS as CUSTOMER_STAGE_LABELS, clamp_limit,
                                     clean_text, money_limit_problem, norm_date, norm_id,
                                     norm_money)
from agent.real_estate_money import fmt_wan
from tools.real_estate_followup import norm_followup_time, split_time_part
from tools.real_estate_property import _STATUS_LABELS

# 列表分页口径（与 list_customers/list_owners 同一套：默认 20、上限 200、≤0 与非数字按默认）
_LIST_LIMIT_DEFAULT = 20
_LIST_LIMIT_MAX = 200


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


STAGES = ['deposit', 'signing', 'loan', 'transfer', 'finalized']
STAGE_LABELS = {
    'deposit': '意向金/定金', 'signing': '签约', 'loan': '贷款审批',
    'transfer': '过户', 'finalized': '交房完成',
}
# 阶段 → 该阶段的日期列。**必须走这张表**：终态列在模型里叫 `finalize_date`，
# 按 `f'{stage}_date'` 拼出来的 `finalized_date` 不存在，会被 `update_deal` 的 `hasattr` 静默丢掉
# （2026-09-26 F216：经纪人填的交房日期当场消失，且回执还说推进成功）。
STAGE_DATE_FIELDS = {
    'deposit': 'deposit_date', 'signing': 'signing_date', 'loan': 'loan_date',
    'transfer': 'transfer_date', 'finalized': 'finalize_date',
}
# 经纪人嘴里的阶段说法（与带看状态/跟进类型同一口径：认中文，提示中文在前、英文值括号对照）
STAGE_ALIASES = {
    '定金': 'deposit', '意向金': 'deposit', '付定金': 'deposit', '交定金': 'deposit',
    '意向金/定金': 'deposit',
    '签约': 'signing', '签合同': 'signing', '签了合同': 'signing', '已签约': 'signing',
    '贷款': 'loan', '贷款审批': 'loan', '办贷款': 'loan', '面签': 'loan',
    '过户': 'transfer', '办过户': 'transfer', '过户完成': 'transfer',
    '交房': 'finalized', '交房完成': 'finalized', '结单': 'finalized', '成交完成': 'finalized',
}


def stages_options_text() -> str:
    """阶段的可选说法（中文在前、英文值括号对照，由枚举表生成，别手写一份字符串）"""
    return '、'.join(f'{STAGE_LABELS[s]}({s})' for s in STAGES)


def norm_deal_stage(value):
    """成交阶段归一 → (规范值 或 None, 中文提示 或 None)；没给/空串按"没要求改"返回 (None, None)"""
    if value is None:
        return None, None
    text = str(value).strip()
    if not text:
        return None, None
    key = text.lower()
    if key in STAGE_LABELS:
        return key, None
    if key in STAGE_ALIASES:
        return STAGE_ALIASES[key], None
    return None, f"阶段没能识别：收到的是「{value}」。可以说 {stages_options_text()}"


def _deal_display(row):
    """成交单详情/列表共用的展示口径（**只补不替换**：原值一律保留给机器读）

    - 阶段：补中文名（与列表、与推进工具共用同一张 `STAGE_LABELS`）
    - 金额：补「万」口径（`agent/real_estate_money.py::fmt_wan`；空值不臆造）
    - 日期：五个阶段日期各补 `YYYY-MM-DD`（原值是 ISO，那个 `T` 不该念给经纪人听）
    - 孤儿：客户/房源已被删时给可读标注，而不是 null（契约 20）
    """
    item = dict(row or {})
    stage = item.get('stage')
    item['stage_label'] = STAGE_LABELS.get(stage, stage)
    item['price_label'] = fmt_wan(item['price']) if item.get('price') is not None else None
    item['deposit_label'] = (fmt_wan(item['deposit_amount'])
                             if item.get('deposit_amount') is not None else None)
    for field in STAGE_DATE_FIELDS.values():
        value = item.get(field)
        item[f'{field}_label'] = str(value)[:10] if value else None
    if not item.get('customer_name'):
        item['customer_name'] = f"已删除客户（id={item.get('customer_id')}）"
    if not item.get('property_title'):
        item['property_title'] = f"已删除房源（id={item.get('property_id')}）"
    return item


def _parse_date(value: str, field_name: str):
    """成交/交易里的日期入参 → datetime；认不出抛 ValueError（提示是中文）。

    先按老写法解析（`2026-12-31` / `2026-12-31 10:00` / `2026-12-31T10:00` —— **必须保留**，
    否则是把旧能力改坏），再交给共用的 `norm_date` + `split_time_part` 兜底：于是 `2026/12/31`、
    `2026年12月31日`、`明天 10:00`、`周三`、`3天后` 这些说法也一并认了（与跟进、带看同一套口径，
    「日期+时刻」的拆分只留 `split_time_part` 一处）。
    """
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    date_part, embedded = split_time_part(text)
    parsed, problem = norm_date(date_part, field_name, "2026-12-31")
    if problem:
        raise ValueError(f"{field_name}没能识别：收到的是「{value}」。"
                         f"请用 2026-12-31 这类写法，也认 明天/周三/3天后")
    if embedded:
        time_text, problem = norm_followup_time(embedded, field_name)
        if problem or not time_text:
            raise ValueError(f"{field_name}没能识别：收到的是「{value}」。"
                             f"时间请用 9:30 或 9点30 这类写法")
        hour, minute = (int(part) for part in time_text.split(':'))
        parsed = parsed.replace(hour=hour, minute=minute)
    return parsed


def _norm_amount(value, label, sample):
    """金额入参归一（复用共用的 `norm_money`）→ (元 或 None, 中文提示 或 None)

    经纪人常把原话「185万」丢下来：不归一就会把文本写进整数列（生产库上整单失败，
    SQLite 则是静默存成文本）。
    """
    if value is None:
        return None, None
    amount = norm_money(value)
    if amount is None:
        return None, f"{label}没能识别：收到的是「{value}」。请按元给数字（如 {sample}）"
    return amount, None


def _amount_changed(raw, value):
    """入参写法与归一后的数值是否不同（不同才在回执里说明「按 N 元记的」）"""
    try:
        return float(raw) != float(value)
    except (TypeError, ValueError):
        return True


def start_deal(customer_id: int, property_id: int, price: int = None, deposit_amount: int = None,
               deposit_date: str = None, notes: str = None, task_id: str = None) -> str:
    """创建成交单：录入成交客户、房源、价格、定金，进入交易流程

    副作用（会改数据，回执里必须如实说明）：房源状态改成已售（出租房 → 已租）、客户阶段推进到「成交中」。
    写库前先认人认房（查不到就如实说明，不造孤儿成交单）；同一客户同一房源的**未完结**成交单不重复开。
    """
    customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    property_id, problem = norm_id(property_id, '房源编号')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    db = _get_db()
    customer = db.get_customer(customer_id)
    if not customer:
        return json.dumps({"success": False, "error": (
            f"客户不存在：编号 {customer_id} 没找到这位客户，先在客户列表里核对一下编号")}, ensure_ascii=False)
    prop = db.get_property(property_id)
    if not prop:
        return json.dumps({"success": False, "error": (
            f"房源不存在：编号 {property_id} 没找到这套房，先在房源列表里核对一下编号")}, ensure_ascii=False)

    # 金额：先归一（认「185万」），再挡明显坏值（0/负数、定金高于成交价）
    price_value, problem = _norm_amount(price, '成交价', '400万 记作 4000000')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    deposit_value, problem = _norm_amount(deposit_amount, '定金', '5万 记作 50000')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if price_value is not None and price_value <= 0:
        return json.dumps({"success": False, "error": (
            f"成交价要大于 0：收到的是「{price}」")}, ensure_ascii=False)
    if deposit_value is not None and deposit_value <= 0:
        return json.dumps({"success": False, "error": (
            f"定金要大于 0：收到的是「{deposit_amount}」")}, ensure_ascii=False)
    for _value, _label in ((price_value, '成交价'), (deposit_value, '定金')):
        _problem = money_limit_problem(_value, _label)
        if _problem:
            return json.dumps({"success": False, "error": _problem}, ensure_ascii=False)
    if price_value is not None and deposit_value is not None and deposit_value > price_value:
        return json.dumps({"success": False, "error": (
            f"定金 {fmt_wan(deposit_value)} 比成交价 {fmt_wan(price_value)} 还高，"
            f"请核对一下哪个数写错了")}, ensure_ascii=False)
    try:
        deposit_dt = _parse_date(deposit_date, '定金日期')
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    # 同一客户同一房源的未完结成交单不重复开（已交房完成的可以再开，如实说明）
    existing = db.find_open_deal(customer_id, property_id)
    if existing:
        stage_label = STAGE_LABELS.get(existing.get('stage'), existing.get('stage'))
        return json.dumps({
            "success": True, "deal": existing, "already_started": True,
            "message": (f"这单已经开过了（成交单编号 {existing['id']}，当前阶段：{stage_label}），"
                        f"没有重复创建"),
        }, ensure_ascii=False)

    warnings = []
    if (prop.get('status') or '') in ('sold', 'rented'):
        warnings.append(f"这套房源的状态是{_STATUS_LABELS.get(prop['status'], prop['status'])}，"
                        f"开单前先确认一下")
    if (customer.get('status') or '') == 'closed':
        warnings.append("这位客户已经标记为已关闭，确认还要开单吗")

    kwargs = {'price': price_value, 'deposit_amount': deposit_value, 'notes': notes,
              'deposit_date': deposit_dt}
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    result = db.add_deal(customer_id=customer_id, property_id=property_id, **kwargs)

    # 回执按写库后的**真实状态**说（返回"成功"不等于数据在库，读回来核对一次）
    prop_after = db.get_property(property_id) or {}
    customer_after = db.get_customer(customer_id) or {}
    money_bits = []
    if price_value is not None:
        money_bits.append(f"成交价 {fmt_wan(price_value)}")
    if deposit_value is not None:
        money_bits.append(f"定金 {fmt_wan(deposit_value)}")
    money_text = ('｜' + '｜'.join(money_bits)) if money_bits else ''
    pstat = prop_after.get('status')
    pstat_label = _STATUS_LABELS.get(pstat, pstat)
    if (prop.get('status') or 'available') == 'available':
        status_text = f"该房源已标记为{pstat_label}，不再对外推荐"
    else:
        status_text = f"该房源此前已是「{pstat_label}」，保持不再对外推荐"
    name = customer.get('name')
    stage_from, stage_to = customer.get('stage'), customer_after.get('stage')
    to_label = CUSTOMER_STAGE_LABELS.get(stage_to, stage_to)
    from_label = CUSTOMER_STAGE_LABELS.get(stage_from)
    if stage_to and stage_from == stage_to:
        stage_text = f"{name}的阶段已在「{to_label}」"
    elif from_label:
        stage_text = f"{name}的阶段已从「{from_label}」推进到「{to_label}」"
    else:
        stage_text = f"{name}的阶段已推进到「{to_label}」"
    message = (f"已创建成交单（成交单编号 {result['id']}）：{name} 成交 "
               f"{result.get('property_title')}{money_text}；当前阶段：{STAGE_LABELS['deposit']}。"
               f"{status_text}；{stage_text}")
    norm_bits = []
    if price_value is not None and _amount_changed(price, price_value):
        norm_bits.append(f"成交价「{price}」按 {price_value} 元记的")
    if deposit_value is not None and _amount_changed(deposit_amount, deposit_value):
        norm_bits.append(f"定金「{deposit_amount}」按 {deposit_value} 元记的")
    if norm_bits:
        message += "（" + "；".join(norm_bits) + "）"
    payload = {"success": True, "deal": result, "message": message}
    if warnings:
        payload["warnings"] = warnings
    return json.dumps(payload, ensure_ascii=False)


def advance_deal(deal_id: int, stage: str, date: str = None, notes: str = None,
                 replace_notes: bool = False, task_id: str = None) -> str:
    """推进交易阶段：意向金/定金 → 签约 → 贷款审批 → 过户 → 交房完成

    - 阶段认中文说法（「签约」「交房完成」都行），认不出给中英对照提示；
    - **越级**（跳过中间几步）与**回退**（往回走）不拦，只在 `warnings` 里如实说明；
    - 备注默认**追加**（`[阶段 日期] 备注`，原备注保留），要纠正错记才用 `replace_notes=True` 覆盖；
    - 推进到「交房完成」时，客户阶段自动从「成交中」挪到「售后维护」（已在售后维护、已关闭客户不动），
      走 `update_stage` 留痕并在回执里如实说明。
    """
    deal_id, problem = norm_id(deal_id, '成交单编号', '，可在成交列表里查')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    stage_value, problem = norm_deal_stage(stage)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if not stage_value:
        return json.dumps({"success": False, "error": (
            f"要说一下推进到哪个阶段，比如「签约」({STAGE_LABELS['signing']} 对应 signing)")},
            ensure_ascii=False)
    db = _get_db()
    deal = db.get_deal(deal_id)
    if not deal:
        return json.dumps({"success": False, "error": "成交单不存在"}, ensure_ascii=False)
    before_stage = deal.get('stage')
    kwargs = {'stage': stage_value}
    date_dt = None
    if date:
        try:
            date_dt = _parse_date(date, f'{STAGE_LABELS[stage_value]}日期')
        except ValueError as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
        # 列名按映射表取：终态列叫 finalize_date，用 f'{stage}_date' 拼出来的 finalized_date
        # 在模型上不存在，会被 update_deal 的 hasattr 静默丢掉（经纪人填的日期当场消失）
        kwargs[STAGE_DATE_FIELDS[stage_value]] = date_dt

    warnings = []
    if before_stage in STAGES:
        cur, target = STAGES.index(before_stage), STAGES.index(stage_value)
        if target > cur + 1:
            skipped = '、'.join(STAGE_LABELS[s] for s in STAGES[cur + 1:target])
            warnings.append(f"这一步跳过了「{skipped}」—— 如果是笔误，跟我说一声我改回来")
        elif target < cur:
            warnings.append(f"这一步是从「{STAGE_LABELS[before_stage]}」退回到"
                            f"「{STAGE_LABELS[stage_value]}」—— 如果不是笔误就不用管")

    # 备注默认**追加**（把原备注整段覆盖会让"客户要求留车位"这类信息凭空消失）；
    # 要纠正错记时才显式 replace_notes=True 覆盖。追加带 [阶段 日期] 前缀，免得看不出哪句是哪一步说的。
    note_text = clean_text(notes)
    notes_mode = None
    if note_text:
        if replace_notes or not clean_text(deal.get('notes')):
            kwargs['notes'] = note_text
            notes_mode = 'replace' if replace_notes else 'new'
        else:
            stamp = f"[{STAGE_LABELS[stage_value]}{' ' + date_dt.strftime('%Y-%m-%d') if date_dt else ''}]"
            kwargs['notes'] = f"{deal['notes']}\n{stamp} {note_text}"
            notes_mode = 'append'

    updated = db.update_deal(deal_id, **kwargs)

    # 交房完成 = 这单结掉了 → 客户阶段从「成交中」挪到「售后维护」（已在售后维护的不动，
    # 已关闭客户不动；走 update_stage 留痕，与带看档 3 同一套）
    stage_advance = None
    if stage_value == 'finalized' and updated:
        customer = db.get_customer(updated['customer_id']) or {}
        cur_stage = customer.get('stage')
        if cur_stage != 'maintain' and (customer.get('status') or '') != 'closed':
            db.update_stage(updated['customer_id'], 'maintain')
            stage_advance = {
                'from': cur_stage, 'to': 'maintain',
                'from_label': CUSTOMER_STAGE_LABELS.get(cur_stage, cur_stage or '未设'),
                'to_label': CUSTOMER_STAGE_LABELS['maintain'],
            }

    message = f"交易已推进至：{STAGE_LABELS[stage_value]}（成交单编号 {deal_id}）"
    if date_dt:
        message += f"｜{STAGE_LABELS[stage_value]}日期 {date_dt.strftime('%Y-%m-%d')}"
    if notes_mode == 'append':
        message += "；备注已追加（原备注保留）"
    elif notes_mode == 'replace':
        message += "；备注已按你说的替换"
    if stage_advance:
        message += (f"；{customer.get('name')}的阶段已从「{stage_advance['from_label']}」"
                    f"推进到「{stage_advance['to_label']}」")
    payload = {"success": True, "deal": updated, "message": message}
    if stage_advance:
        payload['stage_advance'] = stage_advance
    if warnings:
        payload['warnings'] = warnings
    return json.dumps(payload, ensure_ascii=False)


def get_deal(deal_id: int, task_id: str = None) -> str:
    """查看一笔成交的详情：客户与房源、金额、当前阶段（中文）、各阶段日期、备注

    客户或房源已被删除（存量数据、外部改库）时给可读标注，不回 null。
    """
    deal_id, problem = norm_id(deal_id, '成交单编号', '，可在成交列表里查')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    db = _get_db()
    result = db.get_deal(deal_id)
    if result:
        return json.dumps({"success": True, "deal": _deal_display(result)}, ensure_ascii=False)
    return json.dumps({"success": False, "error": "成交单不存在"}, ensure_ascii=False)


def list_deals(stage: str = None, customer_id: int = None, limit: int = _LIST_LIMIT_DEFAULT,
               task_id: str = None) -> str:
    """列出成交单：默认最新登记优先，可按阶段（认中文）或某位客户筛选

    返回 `count`(本次条数) / `total`(符合条件总数，SQL 聚合) / `truncated`，被截断时给一句说明；
    空态分两种说法（库里还没有成交单 / 有成交单但筛选没命中）。
    """
    limit = clamp_limit(limit, _LIST_LIMIT_DEFAULT, _LIST_LIMIT_MAX)
    stage_value = None
    if stage is not None:
        stage_value, problem = norm_deal_stage(stage)
        if problem:
            return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if customer_id is not None:
        customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
        if problem:
            return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    db = _get_db()
    if customer_id is not None and not db.get_customer(customer_id):
        return json.dumps({"success": False, "error": (
            f"客户不存在：编号 {customer_id} 没找到这位客户，先在客户列表里核对一下编号")},
            ensure_ascii=False)
    rows, total = db.list_deals(stage=stage_value, customer_id=customer_id, limit=limit,
                                with_total=True)
    deals = [_deal_display(d) for d in rows]
    payload = {"success": True, "deals": deals, "count": len(deals), "total": total,
               "truncated": bool(total and total > len(deals))}
    if not total:
        if customer_id is not None and stage_value is not None:
            payload["message"] = f"这位客户没有「{STAGE_LABELS[stage_value]}」阶段的成交单"
        elif customer_id is not None:
            payload["message"] = "这位客户还没有成交单"
        elif stage_value is not None:
            _, all_total = db.list_deals(limit=1, with_total=True)
            payload["message"] = (f"没有「{STAGE_LABELS[stage_value]}」阶段的成交单"
                                  f"（库里共 {all_total} 单）")
        else:
            payload["message"] = "还没有任何成交单，开单后这里就能看到"
    elif payload["truncated"]:
        payload["message"] = f"共 {total} 单成交，这里列最近 {len(deals)} 单（要我多列就说一声）"
    return json.dumps(payload, ensure_ascii=False)


def deal_stats(task_id: str = None) -> str:
    """成交统计：按当前阶段数各有多少单（含还在推进中的单）

    字段键不变（`total_deals`/`stages`/`finalized`/`stage_labels`，经营报告在读），只是补一句
    把口径说清的话：`total_deals` 是**登记在册**的成交单总数（含还在推进中的），不是"已交房完成"的套数
    （那看 `finalized`）。空库与有数据分开说。
    """
    db = _get_db()
    stats = db.deal_stats()
    stats['stage_labels'] = STAGE_LABELS
    payload = {"success": True, "stats": stats}
    stages = stats.get('stages') or {}
    if not stats.get('total_deals'):
        payload["message"] = "库里还没有成交单，开单后这里就能看到"
    else:
        parts = '、'.join(f'{STAGE_LABELS[s]} {stages.get(s, 0)}' for s in STAGES)
        payload["message"] = (f"当前共 {stats['total_deals']} 单登记在册（{parts}）；"
                              f"其中已交房完成 {stats.get('finalized', 0)} 单。"
                              f"这里的「成交总数」指登记在册的成交单，含还在推进中的。")
    return json.dumps(payload, ensure_ascii=False)


registry.register(
    name="start_deal",
    toolset="real_estate",
    schema={"name": "start_deal", "description": (
        "创建成交单（开单）：把一位客户与一套房源登记成一笔交易，可记成交价与定金。"
        "开单后这套房源会被标记为已售/已租、不再对外推荐，这位客户的阶段会推进到「成交中」；"
        "当前阶段从「意向金/定金」开始，后续往签约/贷款/过户/交房推进。"), "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "integer", "description": "客户编号（数字，可在客户列表里查）"},
            "property_id": {"type": "integer", "description": "房源编号（数字，可在房源列表里查）"},
            "price": {"type": "integer", "description": "成交价（元）：可写 4000000，也可写「400万」；必须是大于 0 的数字"},
            "deposit_amount": {"type": "integer", "description": "定金（元）：可写 50000，也可写「5万」；不给表示还没收定金；必须大于 0 且不高于成交价"},
            "deposit_date": {"type": "string", "description": "定金日期：认 2026-12-31、2026/12/31、2026年12月31日，也认 今天/明天/周三/3天后"},
            "notes": {"type": "string", "description": "备注（如客户要求留车位、贷款银行）"},
        },
        "required": ["customer_id", "property_id"],
    }},
    handler=lambda args, **kw: start_deal(**args),
)

registry.register(
    name="advance_deal",
    toolset="real_estate",
    schema={"name": "advance_deal", "description": (
        "推进成交单的交易阶段：意向金/定金 → 签约 → 贷款审批 → 过户 → 交房完成。"
        "阶段可以直接说中文（「签约」「交房完成」）。跳步（越级）或往回走（回退）都不拦，只在 warnings 里说明；"
        "备注默认追加到原备注后面（带 [阶段 日期] 前缀），要纠正错记才用 replace_notes=True 覆盖。"
        "推进到「交房完成」时，客户阶段会自动从「成交中」挪到「售后维护」（回执里说明）。"), "parameters": {
        "type": "object",
        "properties": {
            "deal_id": {"type": "integer", "description": "成交单编号（数字，可在成交列表里查）"},
            "stage": {"type": "string", "enum": STAGES, "description": "目标阶段：认中文说法（签约/贷款审批/过户/交房完成），也认英文值"},
            "date": {"type": "string", "description": "该阶段日期：认 2026-12-31、2026/12/31、2026年12月31日，也认 今天/明天/周三/3天后"},
            "notes": {"type": "string", "description": "这一步的备注（默认追加到原备注后面，不会覆盖）"},
            "replace_notes": {"type": "boolean", "description": "默认 false。true=用 notes 整段替换原备注（只在纠正写错的备注时用）"},
        },
        "required": ["deal_id", "stage"],
    }},
    handler=lambda args, **kw: advance_deal(**args),
)

registry.register(
    name="get_deal",
    toolset="real_estate",
    schema={"name": "get_deal", "description": (
        "查看一笔成交的详情：客户与房源、成交价与定金、当前阶段（中文）、"
        "意向金/签约/贷款/过户/交房各阶段的日期、备注。客户或房源已被删除时给可读标注。"
        "要看多笔请用成交列表。"), "parameters": {
        "type": "object",
        "properties": {
            "deal_id": {"type": "integer", "description": "成交单编号（数字，可在成交列表里查）"},
        },
        "required": ["deal_id"],
    }},
    handler=lambda args, **kw: get_deal(**args),
)

registry.register(
    name="list_deals",
    toolset="real_estate",
    schema={"name": "list_deals", "description": (
        "列出成交单（默认最新登记优先；默认列 20 条、最多 200 条），可按阶段筛选（认中文，如「签约」）"
        "或只看某位客户的成交单。返回 total=符合条件的总数、count=本次返回条数、truncated；"
        "被截断时会说明。想更快推进某单，用推进交易阶段。"), "parameters": {
        "type": "object",
        "properties": {
            "stage": {"type": "string", "enum": STAGES, "description": "阶段筛选：认中文说法（签约/贷款审批/过户/交房完成），也认英文值"},
            "customer_id": {"type": "integer", "description": "只看这位客户的成交单（可单独用，也可与阶段叠加）"},
            "limit": {"type": "integer", "description": "返回条数（默认 20，最多 200；传 0/负数/非数字按默认 20）"},
        },
    }},
    handler=lambda args, **kw: list_deals(**args),
)

registry.register(
    name="deal_stats",
    toolset="real_estate",
    schema={"name": "deal_stats", "description": (
        "成交统计：按当前阶段数各有多少单（意向金/定金 → 签约 → 贷款审批 → 过户 → 交房完成）。"
        "total_deals 是登记在册的成交单总数（含还在推进中的，不等于已结单数），「已交房完成」看 finalized；"
        "阶段中文名在 stage_labels 里。想看具体单子用成交列表。"), "parameters": {
        "type": "object",
        "properties": {},
    }},
    handler=lambda args, **kw: deal_stats(),
)
