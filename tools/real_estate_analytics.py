"""
Coco 房产工具 - 数据分析
业绩看板、渠道统计、市场简报
"""
import json
from datetime import datetime

from agent.real_estate_period import norm_period, period_window
from tools.registry import registry

# 业绩看板的四个档（口径与经营报告同一处：`agent/real_estate_period.py` 的滚动窗口）
_DASH_PERIOD_ALLOWED = ("week", "month", "quarter", "year")
_DASH_PERIOD_NAMES = {"week": "本周", "month": "本月", "quarter": "本季度", "year": "今年"}
_DASH_PERIOD_HINT = "周期只认「本周」「本月」「本季度」「今年」（也可以说 近 7 天 / 近 30 天 / 近 90 天 / 近 365 天）"
OVERDUE_SHOW = 5        # 看板里最多列 5 位逾期客户，超出要说清共几位


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


def performance_dashboard(
    period: str = "month",
    task_id: str = None,
) -> str:
    """业绩看板：客户/房源的时点数据 + 本期（近 N 天）进展 + 逾期跟进

    2026-09-26 改（F346–F349）：`period` 此前**只被原样回显**、数字全是累计值（传 week 与传 year
    的结果一模一样），经纪人传「本季度」会以为拿到季度业绩 → 现在四个档**真的统计**
    （滚动窗口：近 7 / 30 / 90 / 365 天，口径在 `agent/real_estate_period.py` 一处），
    并在 `本周期` 里写明区间；逾期客户给**客户名与等级**、只列最急的 5 位并说清共几位；
    空库给空态说法；补一句中文 `message`（键名沿用中文，见 HANDOFF 第 35 条「能中文就中文」）。
    """
    key, hint = norm_period(period, allowed=_DASH_PERIOD_ALLOWED, hint=_DASH_PERIOD_HINT)
    if hint:
        return json.dumps({"success": False, "error": hint}, ensure_ascii=False)
    period_name = _DASH_PERIOD_NAMES[key]
    start, range_text, span_label = period_window(key)

    db = _get_db()
    stats = db.get_stats()

    # 获取各等级客户数
    tier_counts = stats.get('tier_counts', {})

    # 获取逾期跟进（每客户只看最新一条人为跟进是否过期；本工具只读，不降级）
    overdue = db.get_overdue()
    labels = db.get_customer_labels([f.get('customer_id') for f in overdue])

    warnings = []
    try:
        period_stats = db.period_stats(start)
    except Exception as exc:
        period_stats = {}
        warnings.append(f"本期数据这次没取到（{type(exc).__name__}）")

    total_customers = stats.get('total_customers', 0)
    closed_customers = stats.get('closed_customers', 0)
    no_customers = total_customers + closed_customers == 0

    shown = overdue[:OVERDUE_SHOW]
    overdue_rows = []
    for f in shown:
        cid = f.get('customer_id')
        label = labels.get(cid) or {}
        who = label.get('name') or f"已删除客户（id={cid}）"
        tier = f"{label.get('tier')}级" if label.get('tier') else ''
        overdue_rows.append(f"{who}（{tier}）" if tier else who)

    result = {
        "统计周期": period_name,
        "本周期": f"{span_label}（{range_text}）",
        "客户总数": total_customers,
        "各等级客户": {
            "S级（高意向）": tier_counts.get('S', 0),
            "A级（有需求）": tier_counts.get('A', 0),
            "B级（培养中）": tier_counts.get('B', 0),
            "C级（初步接触）": tier_counts.get('C', 0),
        },
        "在售房源": stats.get('available_properties', 0),
        "逾期跟进": len(overdue),
        "逾期客户": overdue_rows,
    }
    if period_stats:
        result["本期新增客户"] = period_stats.get('new_customers', 0)
        result["本期新增房源"] = period_stats.get('new_properties', 0)
        result["本期完成带看"] = period_stats.get('viewings_done', 0)
        result["本期新开成交单"] = period_stats.get('new_deals', 0)

    # 给经纪人看的那句话：全中文，替他把口径与"这里只列了几位"说清
    if no_customers:
        result["说明"] = "库里还没有客户，先登记客户再看数据"
        message = "库里还没有客户，先登记客户再看数据"
    else:
        parts = [f"{period_name}（{span_label}：{range_text}）",
                 f"在跟客户 {total_customers} 位（S级 {tier_counts.get('S', 0)} / "
                 f"A级 {tier_counts.get('A', 0)} / B级 {tier_counts.get('B', 0)} / "
                 f"C级 {tier_counts.get('C', 0)}），在售房源 {stats.get('available_properties', 0)} 套"]
        if period_stats:
            parts.append(f"本期新增客户 {period_stats.get('new_customers', 0)} 位、"
                         f"新增房源 {period_stats.get('new_properties', 0)} 套、"
                         f"完成带看 {period_stats.get('viewings_done', 0)} 次、"
                         f"新开成交单 {period_stats.get('new_deals', 0)} 单")
        if not overdue:
            result["逾期客户说明"] = "暂无逾期跟进"
            parts.append("暂无逾期跟进")
        elif len(overdue) > OVERDUE_SHOW:
            result["逾期客户说明"] = f"这里列逾期最久的 {OVERDUE_SHOW} 位，共 {len(overdue)} 位"
            parts.append(f"逾期跟进 {len(overdue)} 位（这里列逾期最久的 {OVERDUE_SHOW} 位）")
        else:
            result["逾期客户说明"] = f"共 {len(overdue)} 位逾期，已全部列出"
            parts.append(f"逾期跟进 {len(overdue)} 位")
        message = "；".join(parts) + "。"

    out = {"success": True, "dashboard": result, "message": message}
    if warnings:
        out["warnings"] = warnings
        out["message"] = message + "（本期数据这次没取到，稍后我可以再试一次）"
    return json.dumps(out, ensure_ascii=False)


registry.register(
    name="performance_dashboard",
    toolset="real_estate",
    schema={"name": "performance_dashboard",
            "description": "业绩看板：客户与房源的时点数据（在跟客户、各等级、在售房源）+ 本期（近 N 天）新增客户/新增房源/完成带看/新开成交单 + 逾期跟进数与最急的几条",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": ["week", "month", "quarter", "year"],
                               "description": "统计周期：本周（近 7 天）/本月（近 30 天）/本季度（近 90 天）/今年（近 365 天），默认本月"},
                },
            }},
    handler=lambda args, **kw: performance_dashboard(**args),
)

def channel_stats(task_id: str = None) -> str:
    """渠道线索统计：按客户来源分组统计客户数、S/A/B/C分级、成交数、成交率

    2026-09-26 改（F350–F352）：
    ① 来源先**归并同义写法**（实测「贝壳找房」与「贝壳」原先是两行、各 100% 成交率，
       据此判断投放性价比会看错），并在回执里如实说明合并了哪些写法、还有哪些写法没合并；
    ② `成交率 = 该渠道有成交的客户数 ÷ 该渠道在跟客户数` —— 这句口径写进描述与给经纪人的话里
       （`deals` 是"有成交的客户数"，不是成交单数，别让人读成千分之一）；
    ③ 空库分开说；④ 补合计字段与中文 message。
    """
    from agent.real_estate_db import CHANNEL_ALIASES, UNSPECIFIED_SOURCE

    db = _get_db()
    channels = db.get_channel_stats()
    customers_total = sum(c.get('customers', 0) for c in channels)
    closed_total = sum(c.get('closed', 0) for c in channels)
    merged = [(c['source'], c.get('spellings') or []) for c in channels if c.get('spellings')]
    known = set(CHANNEL_ALIASES.values()) - {UNSPECIFIED_SOURCE}
    unmerged = sorted(s for s in (c['source'] for c in channels)
                      if s not in known and s != UNSPECIFIED_SOURCE)

    out = {
        "success": True,
        "channels": channels,
        "total_channels": len(channels),
        "渠道数": len(channels),
        "在跟客户合计": customers_total,
        "已关闭合计": closed_total,
    }
    if not channels:
        out["message"] = "库里还没有客户，先登记客户再看渠道数据"
    else:
        message = ("各渠道线索量与成交率一览（成交率 = 该渠道有成交的客户数 ÷ 该渠道在跟客户数），"
                   "可据此判断广告投放性价比")
        if merged:
            message += "；已合并同一渠道的不同写法：" + "、".join(
                f"{'、'.join(spellings)}→{src}" for src, spellings in merged)
        if unmerged:
            message += (f"；另有 {len(unmerged)} 种来源写法没合并（{'、'.join(unmerged)}），"
                        f"如果它们其实是同一个渠道，跟我说一声我就并进去")
        out["message"] = message
    return json.dumps(out, ensure_ascii=False)


registry.register(
    name="channel_stats",
    toolset="real_estate",
    schema={"name": "channel_stats",
            "description": "渠道线索统计：按客户来源分组，给各渠道的在跟客户数、S/A/B/C 分级、有成交的客户数与客户成交率（同一渠道的不同写法已合并；来源没填的算「未填写」；渠道按客户数从多到少排）",
            "parameters": {
                "type": "object",
                "properties": {},
            }},
    handler=lambda args, **kw: channel_stats(**args),
)


def market_brief(city: str = None, district: str = None, task_id: str = None) -> str:
    """市场行情简报（**内部用**）：自家真实盘况 + 联网行情（标注来源与检索日期）+ 行动建议

    2026-09-26 改（F353–F358）：
    ① **联网那条从来没成功过** —— 原先 `from hermes_tools import web_search` 是 execute_code 沙箱里才有的模块，
       工具进程里必然 ModuleNotFoundError，每次都回「联网检索暂不可用（No module named 'hermes_tools'）」
       还把那句英文报错念给经纪人 → 改用 `tools.web_tools.web_search_tool`（真检索；失败只说
       "这次没查到、稍后再试"，**不编内容、不露内部报错**）；来源标注补上检索日期与检索词；
    ② 「本周」全篇没写日期区间 → 按共用的周期口径写明（`近 7 天：09-19 ~ 09-26`）；
    ③ 描述写着"可直接转发朋友圈/客户群"，正文却是内部盘况与建议 → 描述改"内部用"，正文注明；
    ④ 空库照样给「在售房源偏少：联系房东补盘」→ 空库与"本期为空"分开说；
    ⑤ 带看转化率的分母口径写明（现在是"近 7 天**已完成**的带看"，见 F360）。
    """
    db = _get_db()
    start, range_text, span_label = period_window('week')
    today = datetime.now().strftime('%m-%d')

    # ① 自家盘况（真实统计）
    _brief_warning = None
    no_library = False
    try:
        with db.get_session() as s:
            from agent.real_estate_db import Customer, Property, Viewing, Deal
            new_props = s.query(Property).filter(Property.created_at >= start).count()
            avail = s.query(Property).filter(Property.status == 'available').count()
            viewings_week = s.query(Viewing).filter(Viewing.viewing_time >= start).count()
            viewings_done_week = s.query(Viewing).filter(
                Viewing.viewing_time >= start, Viewing.status == 'done').count()
            deals_week = s.query(Deal).filter(Deal.created_at >= start).count()
            no_library = not s.query(Property).count() and not s.query(Customer).count()
    except Exception as exc:
        # 同上：失败要标出来，不能假装是 0
        new_props = avail = viewings_week = deals_week = viewings_done_week = None
        _brief_warning = f"自家盘况统计失败（{type(exc).__name__}: {exc}），下面的数字不可信"

    own_section = [f"一、自家盘况（系统数据，{span_label}：{range_text}）"]
    if _brief_warning:
        own_section += [f"· 本周新增房源: 统计失败 套", f"· 当前在售: 统计失败 套",
                        "· 本周带看: 统计失败 次", "· 本周成交: 统计失败 单"]
    elif no_library:
        # 空库不能给"在售偏少、联系房东补盘"这种建议（会让人以为盘子里有房）
        own_section.append("· 库里还没有客户和房源，先登记房源再看盘况")
    else:
        # 带看转化率的分母是**本周已完成的带看**（2026-09-26 拍板改，F360）：
        # 原先分母是"本周全部带看"（含还没看的预约与已取消的）——实测把带看全设成未来预约、
        # 本周一场没看时，它照样报 67%，那个比例没有业务含义；两个原始数一并给出，便于对账。
        if viewings_done_week:
            conversion = f"{deals_week / viewings_done_week * 100:.0f}%"
        else:
            conversion = "暂无数据"
        conversion_note = (f"带看转化率 {conversion} = 本周新开成交单 ÷ 本周已完成带看"
                          if viewings_done_week else
                          f"带看转化率暂无数据（本周还没有已完成的带看）")
        own_section += [
            f"· 本周新增房源: {new_props} 套",
            f"· 当前在售: {avail} 套",
            f"· 本周带看: {viewings_week} 场（已完成 {viewings_done_week} 场）",
            f"· 本周成交: {deals_week} 单（{conversion_note}）",
        ]
        if viewings_done_week and deals_week > viewings_done_week:
            own_section.append("· 带看转化率超过 100%：说明有成交来自更早的带看（不是本周看的）")
        if not any((new_props, viewings_week, deals_week)):
            own_section.append(f"· {span_label}没有新增房源、带看与成交（库里现有数据都在更早）")

    # ② 联网行情（标注来源与检索日期；失败不阻塞，也不编内容）
    query = f"{city} 楼市 最新政策 房价" + (f" {district}" if district else "") if city else ""
    news_section = []
    if not city:
        news_section = ["二、市场动态（来源：联网检索）",
                        "· 未指定城市，这次跳过联网行情（告诉我城市名就能查）"]
    else:
        news_items = []
        try:
            from tools.web_tools import web_search_tool
            res = json.loads(web_search_tool(query, limit=5))
            items = ((res.get("data") or {}).get("web") or []) if res.get("success") else []
            for it in items[:3]:
                title = (it.get("title") or "").strip()
                if title:
                    news_items.append(f"· {title}")
                    if it.get("url"):
                        news_items.append(f"  {it['url']}")
        except Exception:
            news_items = []
        if news_items:
            news_section = [f"二、市场动态（来源：联网检索，检索日期 {today}；检索词「{query}」）"] + news_items
        else:
            news_section = [f"二、市场动态（来源：联网检索，检索日期 {today}）",
                            "· 这次没查到相关动态（检索没返回结果），稍后我可以再试一次"]

    # ③ 行动建议（基于自家数据生成；这段是内部参考，别直接转发）
    advice = ["三、本周行动建议（内部参考，别直接转发给客户）"]
    if _brief_warning:
        advice.append("· 自家盘况统计这次没取到，本周建议先按人工判断（我稍后再试一次）")
    elif no_library:
        advice.append("· 库里还没有房源，先登记房源（有房才有得卖）")
    else:
        if deals_week == 0 and viewings_week > 0:
            advice.append("· 有带看无成交：回访本周带看客户，优先推进最接近成交的")
        if avail is not None and avail < 10:
            advice.append("· 在售房源偏少：联系房东补盘，优先谈委托快到期的房东续期")
    high = db.churn_risk_customers(min_risk=60)
    if high:
        advice.append(f"· {len(high)} 位客户流失风险高危，建议优先挽回（要我拉名单就说一声）")
    if len(advice) == 1:
        advice.append("· 节奏健康，按日常跟进计划执行即可")

    report = "\n".join(own_section + news_section + advice)
    out = {
        "success": True,
        "city": city, "district": district,
        "统计区间": f"{span_label}（{range_text}）",
        "stats": {"new_listings": new_props, "available": avail,
                  "viewings": viewings_week, "deals": deals_week},
        "message": report,
    }
    if _brief_warning:
        out["warning_stats"] = _brief_warning
    return json.dumps(out, ensure_ascii=False)


registry.register(
    name="market_brief",
    toolset="real_estate",
    schema={"name": "market_brief",
            "description": "市场行情简报（内部用）：自家盘况（近 7 天新增/在售/带看/成交）+ 联网行情（标注来源与检索日期，查不到就如实说）+ 本周行动建议。正文含门店经营信息，别直接转发给客户",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名（如 海口），填了才会联网检索当地楼市动态"},
                    "district": {"type": "string", "description": "区域（可选，填了会一起带去检索）"},
                },
            }},
    handler=lambda args, **kw: market_brief(**args),
)
