"""
Coco 房产工具 - 竞品对比与客户意向度
"""
import json
from tools.registry import registry
from tools.real_estate_property import _property_display
from agent.real_estate_input import clamp_limit, norm_id, norm_tier
from agent.real_estate_money import fmt_budget, fmt_wan


# 条数口径：对比默认 5/上限 20（一次列太多没意义）；列表默认 20/上限 200（与其它列表同一套）
_COMPARE_LIMIT_DEFAULT = 5
_COMPARE_LIMIT_MAX = 20
_LIST_LIMIT_DEFAULT = 20
_LIST_LIMIT_MAX = 200

# 竞品来源标签：同小区不足时补同区域，两个来源必须能分辨（原先混在一张表里无标注）
_COMPARE_SCOPE_LABELS = {'same_community': '同小区', 'same_district': '同区域'}

# 意向度权重（**唯一实现**：两处计分都调 `_score_intent`，别在别处再写一份）
_TIER_BASE = {'S': 40, 'A': 25, 'B': 15, 'C': 5}
_TIER_BASE_FALLBACK = 5          # 等级没填/认不出：按最低档给基础分，不猜
_VIEWING_EACH = 15
_VIEWING_CAP = 30
_RECENT_FOLLOWUP_BONUS = 15
_BUDGET_BONUS = 10
_DEAL_SCORE = 100


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


def _budget_label(budget_min, budget_max):
    """预算展示（走共用 `fmt_budget`：≥1 万按万、低于 1 万按元）；一头都没填给 None。"""
    if budget_min and budget_max:
        return f"{fmt_budget(budget_min)}-{fmt_budget(budget_max)}"
    if budget_min or budget_max:
        return fmt_budget(budget_min or budget_max)
    return None


def _score_intent(comp):
    """把分项算成 0–100 分（**唯一实现**：`intent_score` 与 `list_intent_scores` 都调它）。

    权重：等级基础分 S/A/B/C = 40/25/15/5（等级没填按最低档 5）+ 已完成带看每次 15
    （最多 30）+ 近 7 天有**人为**跟进 15（带看档 3 自动写的两条不算，见
    `agent/real_estate_db.py::_only_human_followups`）+ 预算上下限都填 10；已成交直接 100（不再累加）。

    两个必须守住的口径：
    - **分项要能加回总分**（每项都写清加了几分，经纪人能自己核账）；
    - **缺数据不等于低意向**：没有任何带看/跟进/成交时给 `data_sufficient=False` 与
      `score_note`，说明"这个分数只反映登记等级" —— 新客户不能因为没记录就被当成冷客户。
    """
    tier = (comp.get('tier') or '').upper() or None
    base = _TIER_BASE.get(tier, _TIER_BASE_FALLBACK)
    score = base
    breakdown = [f"等级{tier}基础分 +{base}" if tier in _TIER_BASE
                 else f"等级没填（按最低档） +{base}"]

    viewing_count = comp.get('viewing_count') or 0
    viewing_score = min(viewing_count * _VIEWING_EACH, _VIEWING_CAP)
    if viewing_score:
        score += viewing_score
        breakdown.append(f"带看{viewing_count}次 +{viewing_score}")

    recent_fu = comp.get('recent_followups') or 0
    if recent_fu:
        score += _RECENT_FOLLOWUP_BONUS
        breakdown.append(f"近7天跟进{recent_fu}次 +{_RECENT_FOLLOWUP_BONUS}")

    budget_min, budget_max = comp.get('budget_min'), comp.get('budget_max')
    if budget_min and budget_max:
        score += _BUDGET_BONUS
        breakdown.append(f"预算明确 +{_BUDGET_BONUS}")

    deal_count = comp.get('deal_count') or 0
    if deal_count:
        score = _DEAL_SCORE
        breakdown = ["已成交直接 100 分（不再累加）"]

    score = min(score, _DEAL_SCORE)
    data_sufficient = bool(viewing_count or recent_fu or deal_count)
    note = None
    if not data_sufficient:
        note = (f"这位客户还没有带看或跟进记录，数据不足、分数仅供参考：{score} 分只反映登记等级"
                f"（{tier or '未填'}），不能当成「不感兴趣」")
    last_fu = comp.get('last_followup_at')
    return {
        'customer_id': comp.get('customer_id'), 'customer_name': comp.get('customer_name'),
        'tier': comp.get('tier'), 'status': comp.get('status'),
        'score': score, 'breakdown': breakdown,
        'viewing_count': viewing_count, 'recent_followups': recent_fu,
        'deal_count': deal_count, 'budget': [budget_min, budget_max],
        'budget_label': _budget_label(budget_min, budget_max),
        'data_sufficient': data_sufficient, 'score_note': note,
        'last_followup_at': last_fu.isoformat() if last_fu else None,
    }


def _intent_message(intent):
    """给经纪人看的那句话：总分 + 每一分的来历（数据不足时如实说明）。"""
    message = f"意向度 {intent.get('score')} 分：" + "、".join(intent.get('breakdown') or [])
    if intent.get('score_note'):
        message += f"｜{intent['score_note']}"
    return message


def _compare_message(target, competitors, total, avg_label, avg_scope, sample):
    """给经纪人看的那句话：附近几套可比、这套多少钱、竞品什么价、这类房的行情是多少。"""
    what = target.get('property_type_label')
    where = target.get('community') or target.get('district') or '同区域'
    price_bits = [f"这套 {target.get('price_label')} / {target.get('area_label')}㎡"]
    if target.get('unit_price_label'):
        price_bits.append(target['unit_price_label'])
    if not competitors:
        parts = [f"{where}附近没有在售的{what}可比", " / ".join(price_bits)]
    else:
        n_comm = sum(1 for c in competitors if c.get('scope') == 'same_community')
        n_dist = len(competitors) - n_comm
        scope_desc = "、".join(x for x in (f"同小区 {n_comm} 套" if n_comm else "",
                                           f"同区域其它小区 {n_dist} 套" if n_dist else "") if x)
        listed = "、".join(str(c.get('price_label')) for c in competitors[:3])
        parts = [f"{where}附近在售{what}共 {total} 套", " / ".join(price_bits),
                 f"这里列了 {len(competitors)} 套（{scope_desc}）：{listed}"]
    if avg_label:
        parts.append(f"{avg_scope} {avg_label}（{sample} 套样本）")
    return "；".join(parts) + "。"


def compare_property(property_id: int, limit: int = 5, task_id: str = None) -> str:
    """同小区/同区域竞品对比（只比同类型：卖比卖、租比租）"""
    property_id, problem = norm_id(property_id, '房源编号')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    limit = clamp_limit(limit, _COMPARE_LIMIT_DEFAULT, _COMPARE_LIMIT_MAX)
    db = _get_db()
    result = db.compare_properties(property_id, limit)
    if result is None:
        return json.dumps({"success": False, "error": "房源不存在"}, ensure_ascii=False)

    target = _property_display(result.get('target'))
    competitors = [dict(_property_display(c), scope=c.get('scope'),
                        scope_label=_COMPARE_SCOPE_LABELS.get(c.get('scope'), c.get('scope')))
                   for c in (result.get('competitors') or [])]
    total = result.get('competitor_total') or 0
    is_rental = target.get('property_type') == 'rental'
    avg = result.get('district_avg_price')
    sample = result.get('avg_sample') or 0
    # 均价口径必须自己说清楚：是"哪个区域 + 哪种类型"的均价；房源没填区域时如实说这是全库口径
    if result.get('avg_is_global'):
        avg_scope = f"全库在售{target.get('property_type_label')}均价（这套房源没填区域）"
    else:
        avg_scope = f"{result.get('avg_district')}在售{target.get('property_type_label')}均价"
    avg_label = None if avg is None else (f"{float(avg):.0f}元/月" if is_rental else fmt_wan(avg))
    # 均价不含这套自己（比的是"邻居什么价"），口径写进 scope 里，免得被当成"含自己在内的均价"
    avg_scope = f"{avg_scope}（不含这套）" if avg_label else avg_scope

    warnings = []
    if target.get('status') != 'available':
        warnings.append(f"这套房源现在是「{target.get('status_label')}」，竞品取的是在售房源，只能当参考")
    if not target.get('community') and not target.get('district'):
        warnings.append("这套房源没填小区也没填区域，找不到同小区/同区域的竞品，先补上区域再对比")
    elif not target.get('community'):
        warnings.append("这套房源没填小区，只能按同区域找竞品")

    comparison = {
        'target': target, 'competitors': competitors,
        'count': len(competitors), 'total': total, 'truncated': total > len(competitors),
        'district_avg_price': avg, 'avg_label': avg_label, 'avg_scope': avg_scope,
        'avg_sample': sample, 'target_unit_price': result.get('target_unit_price'),
    }
    out = {"success": True, "comparison": comparison,
           "message": _compare_message(target, competitors, total, avg_label, avg_scope, sample)}
    if warnings:
        out['warnings'] = warnings
    return json.dumps(out, ensure_ascii=False)


def intent_score(customer_id: int, task_id: str = None) -> str:
    """客户意向度评分（0-100，含每一分的来历）"""
    customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    db = _get_db()
    comps = db.intent_components(customer_id=customer_id)
    if not comps:
        return json.dumps({"success": False, "error": "客户不存在"}, ensure_ascii=False)
    intent = _score_intent(comps[0])
    return json.dumps({"success": True, "intent": intent, "message": _intent_message(intent)},
                      ensure_ascii=False)


def list_intent_scores(tier: str = None, limit: int = _LIST_LIMIT_DEFAULT, task_id: str = None) -> str:
    """客户意向度排名（对全库在跟客户统一算分后取前 N）"""
    if tier not in (None, ''):
        tier_value, ok = norm_tier(tier)
        if not ok:
            return json.dumps({"success": False, "error":
                               f"客户等级筛选没能识别：收到的是「{tier}」。等级只能是 S / A / B / C"},
                              ensure_ascii=False)
        tier = tier_value
    else:
        tier = None
    limit = clamp_limit(limit, _LIST_LIMIT_DEFAULT, _LIST_LIMIT_MAX)
    db = _get_db()
    # 一次聚合拿回**全部在跟客户**的分项（原先先取"最新 limit 位"再逐位算分：
    # 名单被截断 + 每位 4~5 次查询，200 位 = 801 次 SQL）
    scored = []
    failed = []
    for comp in db.intent_components(tier=tier):
        try:
            scored.append(_score_intent(comp))
        except Exception as exc:
            # 单个客户算分失败不能悄悄跳过：否则排名少人，经纪人以为这些客户不在库里
            failed.append(f"{comp.get('customer_name') or comp.get('customer_id')}"
                          f"（{type(exc).__name__}）")
    # 排序：分数降序 → 同分按最近跟进时间（新在前）→ 再按客户编号降序。
    # 三级键写死是为了**顺序可复现**：并入新客户、换库、换数据库都不能让同分客户跳来跳去。
    scored.sort(key=lambda x: (x.get('score') or 0,
                               x.get('last_followup_at') or '',
                               x.get('customer_id') or 0), reverse=True)
    total = len(scored)
    top = scored[:limit]
    insufficient = [x for x in top if not x.get('data_sufficient')]
    scope = f"{tier}级在跟客户" if tier else "在跟客户"
    out = {"success": True, "rankings": top, "count": len(top), "total": total,
           "truncated": total > len(top), "insufficient_count": len(insufficient)}
    if total == 0:
        out["message"] = (f"库里还没有{tier}级在跟客户，先登记客户再来排名" if tier
                          else "库里还没有在跟客户，先登记客户再来排名")
    else:
        parts = [f"按意向度排了前 {len(top)} 位（共 {total} 位{scope}）"]
        if out["truncated"]:
            parts.append(f"还有 {total - len(top)} 位没列出来")
        if insufficient:
            parts.append(f"其中 {len(insufficient)} 位还没有带看或跟进记录，分数仅供参考")
        out["message"] = "；".join(parts) + "。"
    if failed:
        out["warning_scores"] = f"{len(failed)} 位客户意向评分计算失败，未计入排名：" + "、".join(failed[:5])
    return json.dumps(out, ensure_ascii=False)


registry.register(
    name="compare_property",
    toolset="real_estate",
    schema={"name": "compare_property", "description":
            "竞品对比：把一套房源与同小区、同区域的在售房源比价格、面积、单价"
            "（卖房比卖房、租房比租房，只比同类型）。返回目标房源、竞品明细（标明是同一个小区"
            "还是同区域）、该区域同类型在售均价与样本套数；目标房源已售或已租时会提醒。"
            "默认列 5 套、最多 20 套，被截断会说明。", "parameters": {
        "type": "object",
        "properties": {
            "property_id": {"type": "integer",
                            "description": "房源编号（数字，如 12；可在房源列表里查）"},
            "limit": {"type": "integer", "description": "对比条数（默认 5，最多 20；传 0/负数/非数字按默认 5）"},
        },
        "required": ["property_id"],
    }},
    handler=lambda args, **kw: compare_property(**args),
)

registry.register(
    name="intent_score",
    toolset="real_estate",
    schema={"name": "intent_score", "description":
            "客户意向度评分（0-100）：按登记等级、已完成带看次数、近 7 天跟进、预算是否明确逐项加分"
            "（等级 S/A/B/C = 40/25/15/5 分，带看每次 +15 最多 +30，近 7 天有跟进 +15，"
            "预算上下限都填 +10，已成交直接 100）。返回总分和每一分的来历；客户还没有跟进或带看"
            "记录时会标注「数据不足、分数仅供参考」，别当成客户不感兴趣。要看多位客户的排序用"
            " list_intent_scores。", "parameters": {
        "type": "object",
        "properties": {"customer_id": {"type": "integer",
                                       "description": "客户编号（数字，如 12；可在客户列表里查）"}},
        "required": ["customer_id"],
    }},
    handler=lambda args, **kw: intent_score(**args),
)

registry.register(
    name="list_intent_scores",
    toolset="real_estate",
    schema={"name": "list_intent_scores", "description":
            "客户意向度排名：对全库在跟客户统一算分后从高到低列出（默认前 20 位，最多 200 位）。"
            "同分按最近跟进时间、再按客户编号排序。会说明共几位在跟客户、这里列了几位；"
            "没有跟进或带看记录的客户单独标注。要看某一位的分数来历用 intent_score。",
            "parameters": {
        "type": "object",
        "properties": {
            "tier": {"type": "string", "enum": ["S", "A", "B", "C"],
                     "description": "只看某个等级（S/A/B/C，可不传）"},
            "limit": {"type": "integer", "description": "返回条数（默认 20，最多 200；传 0/负数/非数字按默认 20）"},
        },
    }},
    handler=lambda args, **kw: list_intent_scores(**args),
)
