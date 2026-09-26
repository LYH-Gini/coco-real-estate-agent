"""
Coco 房产工具 - 经营报告导出
周报/月报：客户、房源、带看、成交全维度汇总（**本期与累计分开写**）
"""
import json
from datetime import datetime, timedelta

from agent.real_estate_period import norm_period, period_window
from tools.registry import registry

# 周期口径（2026-09-26 拍板 B 方案）：周报 = 近 7 天、月报 = 近 30 天，都是**滚动窗口**
# （不是自然周/自然月）—— 所以标题、段落名与工具描述里一律写明"近 N 天"与具体日期区间，
# 别让"周报"三个字被读成自然周（t74a 实测：标题写"周报"、数字却是累计值，经纪人会当真）。
# 别名表与窗口计算在共用件 `agent/real_estate_period.py`（一处定义，别在别处再写一套）。
_PERIODS = {
    "week": ("周报", "近 7 天"),
    "month": ("月报", "近 30 天"),
}
_PERIOD_ALLOWED = ("week", "month")
_PERIOD_HINT = "周期只认「周报」或「月报」（也可以说「本周/这周」「本月/近 30 天」）"
OVERDUE_SHOW = 10       # 报告里最多列 10 条逾期明细，超出要说清还有几位没列


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


def _norm_period(value):
    """周期归一（走共用件，只认周/月两档）→ (键, None) 或 (None, 中文提示)"""
    return norm_period(value, allowed=_PERIOD_ALLOWED, hint=_PERIOD_HINT)


def generate_report(period: str = "week", task_id: str = None) -> str:
    """生成经营报告（周报=近 7 天 / 月报=近 30 天），Markdown 格式

    **本期与累计分开写**（2026-09-26 改，F337–F345）：标题与段落名写明"近 N 天：X月X日 ~ X月X日"，
    本期数字（新增客户 / 本期完成的带看 / 新开成交单）与累计数字（在跟客户 / 在册成交 / 总带看）
    各占一段 —— 此前全篇是累计值却挂在"周报"标题下，经纪人会把累计当本期业绩。
    逾期清单最多列 10 条（超出说清还有几位，不假装列全）；没有已看的带看时不报"兴趣率偏低"。
    """
    key, hint = _norm_period(period)
    if hint:
        return json.dumps({"success": False, "error": hint}, ensure_ascii=False)

    name = _PERIODS[key][0]
    now = datetime.now()
    start, range_text, span_label = period_window(key, now)
    today = now.strftime("%m-%d")
    title = f"{name}（{span_label}：{range_text}）"

    # 客户/房源统计
    db = _get_db()
    stats = db.get_stats()
    # 带看统计 / 成交统计 / 本期统计：失败时不能静默当成 0（报告会显示成"本周 0 次带看"误导判断）
    warnings = []
    try:
        viewing_stats = db.viewing_stats()
    except Exception as exc:
        viewing_stats = {}
        warnings.append(f"带看统计获取失败（{type(exc).__name__}: {exc}）")
    try:
        deal_stats = db.deal_stats()
    except Exception as exc:
        deal_stats = {}
        warnings.append(f"成交统计获取失败（{type(exc).__name__}: {exc}）")
    try:
        period_stats = db.period_stats(start)
    except Exception as exc:
        period_stats = {}
        warnings.append(f"本期统计获取失败（{type(exc).__name__}: {exc}）")
    overdue = db.get_overdue()
    labels = db.get_customer_labels([f.get("customer_id") for f in overdue])

    tier_counts = stats.get("tier_counts", {})
    total_customers = stats.get("total_customers", 0)
    closed_customers = stats.get("closed_customers", 0)
    no_customers = total_customers + closed_customers == 0

    lines = [f"# Coco 经营{title}", ""]
    if no_customers:
        lines.append("⚠️ 库里还没有客户，先登记客户再看报告。")
        lines.append("")

    # ---- 客户概况：时点值（截至今天）；"本期新增"放在下面单独一段 ----
    lines.append(f"## 客户概况（截至 {today}）")
    lines.append("")
    # 这一段的数字都是**时点值**（此刻库里什么样）；"本期新增"放在下一段，别混在一行里
    lines.append(f"- 在跟客户：{total_customers} 位｜已关闭：{closed_customers} 位")
    lines.append(f"- S级：{tier_counts.get('S', 0)} | A级：{tier_counts.get('A', 0)} | "
                 f"B级：{tier_counts.get('B', 0)} | C级：{tier_counts.get('C', 0)}")
    lines.append(f"- 在售房源：{stats.get('available_properties', 0)} 套")
    lines.append("")

    # ---- 本期：近 N 天内的活动 ----
    lines.append(f"## 本期（{span_label}：{range_text}）")
    lines.append("")
    if period_stats:
        done = period_stats.get("viewings_done", 0)
        interested = period_stats.get("viewings_interested", 0)
        lines.append(f"- 新增客户：{period_stats.get('new_customers', 0)} 位")
        lines.append(f"- 新增房源：{period_stats.get('new_properties', 0)} 套（含已售已租）")
        if done:
            lines.append(f"- 带看：完成 {done} 次，感兴趣 {interested} 位，"
                         f"兴趣率 {round(interested / done * 100, 1)}%（按本期已看 {done} 次算）")
        else:
            lines.append("- 带看：本期还没有已看的带看")
        lines.append(f"- 新开成交单：{period_stats.get('new_deals', 0)} 单")
    else:
        lines.append("- 本期数据这次没取到（见下面的数据缺口），建议稍后重跑")
    lines.append("")

    # ---- 累计：截至今天的全量 ----
    lines.append(f"## 累计（截至 {today}）")
    lines.append("")
    total_view = viewing_stats.get("total_viewings", 0)
    done_view = viewing_stats.get("done", 0)
    if done_view:
        lines.append(f"- 带看：共 {total_view} 次（已完成 {done_view} 次，待带看 "
                     f"{viewing_stats.get('scheduled', 0)} 次，已取消 {viewing_stats.get('cancelled', 0)} 次），"
                     f"其中 {viewing_stats.get('interested', 0)} 位客户感兴趣，"
                     f"兴趣率 {viewing_stats.get('interest_rate', 0)}%")
    else:
        lines.append(f"- 带看：共 {total_view} 次（还没有已看的带看，兴趣率暂无）")
    stages = deal_stats.get("stages", {})
    lines.append(f"- 成交：在册 {deal_stats.get('total_deals', 0)} 单 — 定金 {stages.get('deposit', 0)} / "
                 f"签约 {stages.get('signing', 0)} / 贷款 {stages.get('loan', 0)} / "
                 f"过户 {stages.get('transfer', 0)} / 交房 {stages.get('finalized', 0)}")
    lines.append("")

    # ---- 逾期跟进：最多列 10 条，超出说清还有几位 ----
    overdue_total = len(overdue)
    if no_customers:
        lines.append("## 逾期跟进")
        lines.append("")
        lines.append("- 库里还没有客户")
    elif not overdue_total:
        lines.append("## 逾期跟进")
        lines.append("")
        lines.append("- 暂无逾期跟进")
    else:
        if overdue_total > OVERDUE_SHOW:
            lines.append(f"## 逾期跟进（{overdue_total} 位，这里列逾期最久的 {OVERDUE_SHOW} 位）")
        else:
            lines.append(f"## 逾期跟进（{overdue_total} 位）")
        lines.append("")
        for f in overdue[:OVERDUE_SHOW]:
            cid = f.get("customer_id")
            label = labels.get(cid) or {}
            who = label.get("name") or f"已删除客户（id={cid}）"
            tier = f"{label.get('tier')}级，" if label.get("tier") else ""
            lines.append(f"- {who}（{tier}原定 {(f.get('next_date') or '')[:10]}）")
        if overdue_total > OVERDUE_SHOW:
            lines.append(f"- 还有 {overdue_total - OVERDUE_SHOW} 位没列出来，要全部就说一声，我生成一份清单发你")
    lines.append("")

    # ---- 总结：累计一行、本期一行，口径写在句子里 ----
    lines.append("## 总结")
    lines.append("")
    lines.append(f"截至 {today} 累计：在跟客户 {total_customers} 位，带看 {total_view} 次，"
                 f"在册成交 {deal_stats.get('total_deals', 0)} 单。")
    if period_stats:
        lines.append(f"{span_label}（{range_text}）：新增客户 {period_stats.get('new_customers', 0)} 位，"
                     f"完成带看 {period_stats.get('viewings_done', 0)} 次，"
                     f"新开成交单 {period_stats.get('new_deals', 0)} 单。")
    interest_rate = viewing_stats.get("interest_rate")
    # 没有已看的带看时不报"兴趣率偏低"（t74a/t74b 实测：空库与"刚建档还没带看"都会被误报）
    if done_view and interest_rate is not None and interest_rate < 30:
        lines.append(f"提示：带看兴趣率偏低（{interest_rate}%），建议复盘带看房源匹配度和客户需求沟通。")
    if tier_counts.get("S", 0) > 0 and overdue_total > 0:
        lines.append("注意：存在逾期跟进，S级客户务必 2 天内完成跟进。")
    if no_customers:
        lines.append("提示：先登记客户和房源，报告才有内容可看。")

    if warnings:
        lines.append("")
        lines.append("⚠️ 数据缺口：" + "；".join(warnings) + "（这几项统计失败，报告里的相关数字可能偏低，建议稍后重跑）")
    report = "\n".join(lines)
    out = {"success": True, "period": key, "title": title, "report": report}
    if warnings:
        out["warning_stats"] = "；".join(warnings)
    return json.dumps(out, ensure_ascii=False)


registry.register(
    name="generate_report",
    toolset="real_estate",
    schema={"name": "generate_report",
            "description": "生成经营报告（周报=近 7 天、月报=近 30 天）的 Markdown：客户、房源、带看、成交、逾期汇总，本期数字与累计数字分开写（不含房源明细，要看明细用 search_property / list_deals）",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": ["week", "month"],
                               "description": "报告周期：周报（近 7 天）或月报（近 30 天），默认周报；也认「本周/这周」「本月/近 30 天」这类说法"},
                },
            }},
    handler=lambda args, **kw: generate_report(**args),
)
