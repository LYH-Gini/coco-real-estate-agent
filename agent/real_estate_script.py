"""话术「场景 / 子场景」的中文名与写法归一 —— 一处定义（2026-09-26）

话术族六个工具都要回答同一个问题：「开场白」到底是哪个档？内置话术（`get_script` 的内存表）
与自建话术库（`re_scripts.scenario`）用的是同一套档位，中文说法、英文别名、大小写都要归一，
认不出时给的中文清单也要一致 —— 各写一张表就会分叉（本项目的日期、金额、渠道来源都分叉过）。

- 场景档位：`greeting` / `objection_handling` / `closing` / `follow_up` / `custom`
- 子场景：每个场景一套（`SUB_SCENARIO_LABELS` 给中文名）
- **提示文案由各工具传入**：各工具认的档位不同（`get_script` 不认 `custom`、`save_script` 认），
  别让共用件替它们决定说什么话（与 `real_estate_period.norm_period` 同一套做法）。
"""
SCENARIO_LABELS = {
    "greeting": "开场白",
    "objection_handling": "异议处理",
    "closing": "逼定成交",
    "follow_up": "跟进维护",
    "custom": "自定义",
}

SUB_SCENARIO_LABELS = {
    "first_contact": "首次联系",
    "follow_up": "再次跟进",
    "after_viewing": "带看后回访",
    "price_too_high": "价格太高",
    "need_to_consider": "需要考虑",
    "location_not_satisfied": "位置不满意",
    "create_urgency": "制造紧迫感",
    "offer_incentive": "优惠激励",
    "final_reminder": "最后提醒",
    "weekly_check": "定期问候",
    "holiday_greeting": "节日问候",
    "price_drop": "降价通知",
}

# 中文/别名 → 档位（键一律小写、不含空格；去空格在 `_clean` 里做）
SCENARIO_ALIASES = {
    "greeting": "greeting", "开场白": "greeting", "开场": "greeting", "开场问候": "greeting",
    "首次接触": "greeting", "打招呼": "greeting",
    "objection_handling": "objection_handling", "异议处理": "objection_handling",
    "处理异议": "objection_handling", "异议": "objection_handling", "应对异议": "objection_handling",
    "closing": "closing", "逼定成交": "closing", "逼定": "closing", "促单": "closing", "成交话术": "closing",
    "follow_up": "follow_up", "跟进维护": "follow_up", "跟进": "follow_up", "维护": "follow_up",
    "回访": "follow_up", "跟进话术": "follow_up",
    "custom": "custom", "自定义": "custom", "其他": "custom", "其它": "custom",
}

# 子场景中文/别名 → 子场景键（同名键在不同场景下含义相同，故做全局表）
SUB_SCENARIO_ALIASES = {
    "first_contact": "first_contact", "首次联系": "first_contact", "第一次联系": "first_contact",
    "初次联系": "first_contact", "开场": "first_contact",
    "follow_up": "follow_up", "再次跟进": "follow_up", "二次跟进": "follow_up", "再跟进": "follow_up",
    "after_viewing": "after_viewing", "带看后回访": "after_viewing", "带看回访": "after_viewing",
    "看房后回访": "after_viewing", "带看后跟进": "after_viewing",
    "price_too_high": "price_too_high", "价格太高": "price_too_high", "嫌贵": "price_too_high",
    "价格贵": "price_too_high", "还价": "price_too_high",
    "need_to_consider": "need_to_consider", "需要考虑": "need_to_consider", "再考虑": "need_to_consider",
    "考虑一下": "need_to_consider", "观望": "need_to_consider",
    "location_not_satisfied": "location_not_satisfied", "位置不满意": "location_not_satisfied",
    "位置不合适": "location_not_satisfied", "区域不满意": "location_not_satisfied",
    "create_urgency": "create_urgency", "制造紧迫感": "create_urgency", "紧迫感": "create_urgency",
    "制造稀缺": "create_urgency",
    "offer_incentive": "offer_incentive", "优惠激励": "offer_incentive", "给优惠": "offer_incentive",
    "优惠": "offer_incentive", "促成交": "offer_incentive",
    "final_reminder": "final_reminder", "最后提醒": "final_reminder", "临门一脚": "final_reminder",
    "催答复": "final_reminder",
    "weekly_check": "weekly_check", "定期问候": "weekly_check", "周问候": "weekly_check",
    "每周问候": "weekly_check", "日常问候": "weekly_check",
    "holiday_greeting": "holiday_greeting", "节日问候": "holiday_greeting", "节日祝福": "holiday_greeting",
    "price_drop": "price_drop", "降价通知": "price_drop", "降价": "price_drop", "价格下调": "price_drop",
}

_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def label_of(key, kind="scenario"):
    """档位键 → 中文名（认不出回落原样，不臆造）

    `kind`：`scenario`（顶层场景）/ `sub`（子场景）—— 两套表里有同名键
    （`follow_up` 既是一个场景、又是「开场白」下的一个子场景），所以必须显式指定看哪张表。
    """
    table = SUB_SCENARIO_LABELS if kind == "sub" else SCENARIO_LABELS
    return table.get(key) or str(key)


def type_word(value):
    """参数类型不对时给的大白话类型名（话术族各工具共用，别再各写一份）"""
    if isinstance(value, str):
        return "文字"
    if isinstance(value, bool):
        return "真假值"
    if isinstance(value, (int, float)):
        return "数字"
    if isinstance(value, (list, tuple, set)):
        return "一串值"
    if isinstance(value, dict):
        return "一份表格"
    return "这种写法"


def _clean(value):
    """归一待比较的写法：去首尾空白、转小写、去掉中间空格（「跟进 维护」也认）"""
    return "" if value is None else str(value).strip().lower().replace(" ", "").replace("　", "")


def options_text(keys, kind="scenario", labels=None):
    """档位清单 → 「①开场白（greeting）②异议处理（objection_handling）」这种可念的串

    `keys` 的顺序即清单顺序（调用方给的顺序就是给经纪人看的顺序）。
    `labels`：自定义中文名表（如消息模板名）；不传就用场景/子场景那两张表。
    """
    table = labels if labels is not None else (SUB_SCENARIO_LABELS if kind == "sub" else SCENARIO_LABELS)
    out = []
    for i, k in enumerate(keys):
        num = _CIRCLED[i] if i < len(_CIRCLED) else f"{i + 1}."
        out.append(f"{num}{table.get(k) or k}（{k}）")
    return "".join(out)


def norm_scenario(value, allowed, label="场景"):
    """场景写法归一 → (档位, None) 或 (None, 中文提示)

    `allowed`：该工具认的档位（顺序即提示里的清单顺序）。
    非文字（列表/字典/数字）与认不出的写法都给同一句中文提示 —— **不崩、不猜**。
    """
    if isinstance(value, str):
        key = SCENARIO_ALIASES.get(_clean(value))
        if key and key in allowed:
            return key, None
        return None, f"未知{label}「{value}」。可用{label}：" + options_text(allowed)
    return None, f"{label}要是文字（如「{label_of(allowed[0])}」）。可用{label}：" + options_text(allowed)


def norm_sub_scenario(value, allowed, scenario_key=None, label="子场景"):
    """子场景写法归一 → (子场景键, None) 或 (None, 中文提示)

    `allowed`：该场景下真正有的子场景（顺序即提示里的清单顺序）；
    `scenario_key`：给提示加个场景前缀（「开场白」下没有…）。
    """
    prefix = f"「{label_of(scenario_key)}」下" if scenario_key else ""
    if isinstance(value, str):
        key = SUB_SCENARIO_ALIASES.get(_clean(value))
        if key and key in allowed:
            return key, None
        return None, f"{prefix}没有「{value}」这个{label}。这个场景有：" + options_text(allowed, "sub")
    return None, f"{label}要是文字（如「{label_of(allowed[0], 'sub')}」）。{prefix}有：" + options_text(allowed, "sub")
