"""经营报表类的「周期」口径 —— 一处定义（2026-09-26）

报表/统计类工具（`generate_report` / `performance_dashboard` / `market_brief` …）都要回答同一个问题：
「本周 / 本月」到底是哪一段？2026-09-26 拍板 **B 方案**：四个档一律走**滚动窗口**（从这一刻往前数 N 天），
**不改自然周/自然月**，但**必须把口径写进标题、段落名与工具描述**（如「近 7 天：09-19 ~ 09-26」）——
只写「周报」会被读成自然周（t74a 实测：标题写周报、数字却是累计值）。

- 周 = 近 7 天、月 = 近 30 天、季度 = 近 90 天、年 = 近 365 天
- 别名表覆盖中文说法（周报/本周/这周/近7天/本月/月报/近30天/本季度/今年…）与英文 key（大小写不敏感、忽略空格）
- **提示文案由各工具传入**：各工具认的档位不同（经营报告只认周/月，业绩看板认四档），
  别让共用件替它们决定说什么话。

新工具要「认周期」就用这两个函数，别各写一套解析 —— 同族分叉过一次就要收（日期、金额、渠道来源都栽过）。
"""
from datetime import datetime, timedelta

PERIOD_DAYS = {"week": 7, "month": 30, "quarter": 90, "year": 365}

PERIOD_ALIASES = {
    # 周
    "week": "week", "weekly": "week", "7天": "week", "七天": "week",
    "周报": "week", "周": "week", "本周": "week", "这周": "week", "这个星期": "week", "这星期": "week",
    "一周": "week", "近一周": "week", "最近一周": "week", "近7天": "week", "最近7天": "week",
    # 月
    "month": "month", "monthly": "month", "30天": "month", "三十天": "month",
    "月报": "month", "月": "month", "本月": "month", "这个月": "month", "这月": "month",
    "一月": "month", "近一月": "month", "最近一月": "month", "近一个月": "month", "最近一个月": "month",
    "近30天": "month", "最近30天": "month",
    # 季度
    "quarter": "quarter", "quarterly": "quarter", "季度": "quarter", "本季度": "quarter",
    "这季度": "quarter", "90天": "quarter", "近90天": "quarter", "最近90天": "quarter",
    "三个月": "quarter", "近三个月": "quarter", "最近三个月": "quarter",
    # 年
    "year": "year", "yearly": "year", "annual": "year", "年": "year", "年度": "year",
    "今年": "year", "本年度": "year", "365天": "year", "近一年": "year", "最近一年": "year",
    "近365天": "year", "最近365天": "year", "12个月": "year",
}


def _clean(value):
    """归一待比较的写法：空值按空串、去首尾空白、转小写、去掉中间空格（「近 30 天」也认）"""
    return "" if value is None else str(value).strip().lower().replace(" ", "")


def norm_period(value, allowed=None, hint=None):
    """周期写法归一 → (键, None) 或 (None, 中文提示)

    `allowed`：该工具认的档位（如 `("week", "month")`）；不传表示四个档都认。
    `hint`：认不出时给经纪人的中文提示（各工具自己写，写清它认哪些说法）。认不出**不猜**。
    """
    key = PERIOD_ALIASES.get(_clean(value))
    if key and (allowed is None or key in allowed):
        return key, None
    return None, hint or "这个周期我没认出来，换一种说法再说一次（如「本周」「本月」）"


def period_window(key, now=None):
    """→ (起始时间, 区间文案「MM-DD ~ MM-DD」, 口径文案「近 N 天」)

    区间文案与口径文案要能直接拼进标题、段落名与描述里（拍板 B 方案要求"把口径写清楚"）。
    """
    days = PERIOD_DAYS[key]
    now = now or datetime.now()
    start = now - timedelta(days=days)
    return start, f'{start.strftime("%m-%d")} ~ {now.strftime("%m-%d")}', f"近 {days} 天"
