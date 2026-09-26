"""
Coco 房产工具 - 沟通工具
话术库、消息模板
"""
import json
import re
from tools.registry import registry
from agent.real_estate_script import label_of, norm_scenario, norm_sub_scenario, options_text, type_word

# get_script 真正认的场景（顺序即提示里给经纪人念的清单顺序）——「自定义」不在这里，
# 那是 save_script 的事；中文说法与英文别名由共用件 agent/real_estate_script.py 归一。
SCENARIO_ORDER = ["greeting", "objection_handling", "closing", "follow_up"]

# 话术库
SCRIPTS = {
    "greeting": {
        "first_contact": "您好，我是XX房产的置业顾问。看到您在关注房产信息，不知道您是想买房还是租房呢？我可以根据您的需求帮您推荐合适的房源。",
        "follow_up": "您好，之前您看过的那套房子，现在有个好消息想告诉您。不知道您方便聊聊吗？",
        "after_viewing": "您好，上次带您看的房子感觉怎么样？有什么想法可以跟我说说，我帮您分析分析。",
    },
    "objection_handling": {
        "price_too_high": "理解您的顾虑。这套房子的价格确实是同区域较高的，但它的优势在于：1. 学区对口XX小学；2. 楼层好、采光佳；3. 装修保养好，拎包入住。如果您诚心想要，我可以帮您跟业主谈谈价格。",
        "need_to_consider": "买房确实是大事，您考虑清楚是对的。不过这套房子在同户型里性价比很高，最近看的人也不少。我建议您可以先做个对比，看看其他类似房源的价格和条件，这样心里更有数。",
        "location_not_satisfied": "这个位置确实不是您首选的区域，但您知道吗，这个片区未来有地铁规划，而且现在价格比您理想的区域低了20%左右。从投资角度看，增值空间更大。",
    },
    "closing": {
        "create_urgency": "跟您说实话，这套房子已经有两组客户在谈了。如果您真的喜欢，建议尽快定下来，不然可能就被别人抢先了。",
        "offer_incentive": "如果您今天能定下来，我可以帮您跟公司申请一个额外优惠，比如免一部分中介费或者赠送家电。您看怎么样？",
        "final_reminder": "您考虑得怎么样了？这套房子业主那边也在等回复，如果今天能给个准信，我好帮您争取最好的条件。",
    },
    "follow_up": {
        "weekly_check": "您好，好久没联系了。最近房产市场有些新变化，不知道您还在关注买房的事吗？有什么需要随时跟我说。",
        "holiday_greeting": "XX节快乐！感谢您一直以来的信任。最近有几套不错的房源，要不要我发给您看看？",
        "price_drop": "好消息！您之前关注的那套房子降价了，现在价格更合适了。要不要再去看一看？",
    },
}


# 内置话术是通用版本：里面的数字与地铁/学区说法查不到依据（第九组已定口径）
# → 2026-09-26 拍板「原文保留 + 返回里提醒经纪人按实际房源替换」。
BUILTIN_SCRIPT_NOTE = "这些是内置通用话术；你自己攒的话术在话术库里，按名字取用话术库那条。"
BUILTIN_CONTENT_NOTE = ("这些是内置通用话术，里面的数字（如\"低了20%左右\"）、地铁/学区这类说法，"
                        "请按这套房源的真实情况替换后再发给客户。")

_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def _numbered(labels):
    """子场景中文名列表 → 「①首次联系②再次跟进③带看后回访」"""
    return "".join(f"{_CIRCLED[i] if i < len(_CIRCLED) else str(i + 1) + '.'}{t}"
                   for i, t in enumerate(labels))


def get_script(
    scenario: str,
    sub_scenario: str = None,
    task_id: str = None,
) -> str:
    """获取话术

    参数:
        scenario: 场景（开场白/异议处理/逼定成交/跟进维护，也认 greeting 这类英文写法）
        sub_scenario: 子场景（给了只回那一条；不给就回整个场景的子场景清单）
    """
    key, problem = norm_scenario(scenario, SCENARIO_ORDER)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)

    scripts = SCRIPTS[key]
    scenario_label = label_of(key)

    sub_text = "" if sub_scenario is None else str(sub_scenario).strip()
    if sub_text:
        sub, sub_problem = norm_sub_scenario(sub_scenario, list(scripts), scenario_key=key)
        if sub_problem:
            return json.dumps({"success": False, "error": sub_problem}, ensure_ascii=False)
        body = scripts[sub]
        sub_label = label_of(sub, "sub")
        return json.dumps({
            "success": True,
            "scenario": key,
            "scenario_label": scenario_label,
            "sub_scenario": sub,
            "sub_scenario_label": sub_label,
            "script": body,
            "message": f"「{scenario_label} · {sub_label}」：{body}",
            "note": BUILTIN_SCRIPT_NOTE,
            "content_note": BUILTIN_CONTENT_NOTE,
        }, ensure_ascii=False)

    script_list = [{"sub_scenario": sub, "sub_scenario_label": label_of(sub, "sub"), "script": body}
                   for sub, body in scripts.items()]
    labels = _numbered([item["sub_scenario_label"] for item in script_list])
    return json.dumps({
        "success": True,
        "scenario": key,
        "scenario_label": scenario_label,
        "scripts": scripts,                     # 老字段保留（消费方兼容）
        "script_list": script_list,             # 带中文名的清单，新增
        "message": f"「{scenario_label}」共 {len(script_list)} 条：{labels}。要哪一条跟我说一声，我把原文给你。",
        "note": BUILTIN_SCRIPT_NOTE,
        "content_note": BUILTIN_CONTENT_NOTE,
    }, ensure_ascii=False)


# 消息模板（2026-09-26）：**单位不写在模板里**，由归一后的值自带 ——
# 原先写死 `{price}万`/`{area}㎡`，传 1500000（元）就成了「1500000万」、传「2500元/月」成了「2500元/月万」。
TEMPLATES = {
    "property_recommend": "【房源推荐】\n小区：{community}\n价格：{price}\n户型：{rooms}室{halls}厅\n面积：{area}\n亮点：{highlights}",
    "viewing_reminder": "【看房提醒】\n时间：{time}\n地址：{address}\n联系人：{contact}",
    "follow_up": "【跟进提醒】\n客户：{customer}\n上次沟通：{last_contact}\n待办：{todo}",
    "price_change": "【价格变动】\n房源：{title}\n原价：{old_price}\n现价：{new_price}\n变动：{change}",
    "market_report": "【市场周报】\n区域：{district}\n新增房源：{new_listings}套\n成交：{deals}套\n均价：{avg_price}",
    # 挽回模板：churn_warning 的流失预警已经在引导经纪人「用「长期未联系」挽回模板生成话术」，
    # 但这两个键**此前根本不存在**（跨工具死引用）—— 2026-09-26 补齐，把链路接上。
    "winback_long_absence": "【挽回 · 长期未联系】\n{customer}您好，好久没联系了。最近片区里新上了几套房源，我把符合您条件的挑出来发您看看？预算或区域有变化也跟我说，我按新的条件帮您找。",
    "winback_after_viewing": "【挽回 · 看房后没下文】\n{customer}您好，上次带您看的{property}，您考虑得怎么样了？有什么顾虑直接跟我说，我帮您一条条看；要是那套不合适，我按您的条件再挑几套对比着看。",
}

TEMPLATE_LABELS = {
    "property_recommend": "房源推荐",
    "viewing_reminder": "看房提醒",
    "follow_up": "跟进提醒",
    "price_change": "价格变动",
    "market_report": "市场周报",
    "winback_long_absence": "长期未联系挽回",
    "winback_after_viewing": "看房后没下文挽回",
}

TEMPLATE_ALIASES = {
    "property_recommend": "property_recommend", "房源推荐": "property_recommend",
    "推荐房源": "property_recommend", "房源介绍": "property_recommend", "房源": "property_recommend",
    "viewing_reminder": "viewing_reminder", "看房提醒": "viewing_reminder",
    "带看提醒": "viewing_reminder", "看房通知": "viewing_reminder",
    "follow_up": "follow_up", "跟进提醒": "follow_up", "跟进通知": "follow_up",
    "price_change": "price_change", "价格变动": "price_change",
    "调价通知": "price_change", "降价通知": "price_change",
    "market_report": "market_report", "市场周报": "market_report",
    "周报": "market_report", "市场报告": "market_report",
    "winback_long_absence": "winback_long_absence", "长期未联系挽回": "winback_long_absence",
    "长期未联系": "winback_long_absence", "挽回": "winback_long_absence",
    "winback_after_viewing": "winback_after_viewing", "看房后没下文挽回": "winback_after_viewing",
    "看房后没下文": "winback_after_viewing", "看房后挽回": "winback_after_viewing",
}

# 每个模板要填哪些格子：(变量名, 中文名, 归一类型)
# 类型决定这个值怎么念：money 金额（万/元/元每月）、area 面积、count 个数、room 房间数、text 文本。
TEMPLATE_FIELDS = {
    "property_recommend": [
        ("community", "小区", "text"), ("price", "价格", "money"),
        ("rooms", "户型-室", "room"), ("halls", "户型-厅", "room"),
        ("area", "面积", "area"), ("highlights", "亮点", "text"),
    ],
    "viewing_reminder": [("time", "时间", "text"), ("address", "地址", "text"), ("contact", "联系人", "text")],
    "follow_up": [("customer", "客户", "text"), ("last_contact", "上次沟通", "text"), ("todo", "待办", "text")],
    "price_change": [("title", "房源", "text"), ("old_price", "原价", "money"),
                     ("new_price", "现价", "money"), ("change", "变动", "text")],
    "market_report": [("district", "区域", "text"), ("new_listings", "新增房源", "count"),
                      ("deals", "成交", "count"), ("avg_price", "均价", "money")],
    "winback_long_absence": [("customer", "客户", "text")],
    "winback_after_viewing": [("customer", "客户", "text"), ("property", "房源", "text")],
}

TEMPLATE_ORDER = list(TEMPLATES)

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _parse_number(text):
    """从文本里抠出数字（认千分位与夹带单位）→ float 或 None"""
    m = re.search(r"-?\d+(?:\.\d+)?", str(text).replace(",", "").replace("，", ""))
    return float(m.group()) if m else None


def _num_text(num):
    """数字 → 好念的文本（150.0 → 150、1.2 → 1.2）"""
    return str(int(num)) if float(num).is_integer() else f"{num:g}"


def _norm_money(value):
    """金额归一 → (可念的文本 或 None（按"没填"）, 是否发生了"元→万"的换算)

    口径（一处定义，与房东侧预算同一套）：带「/月」的按月租原样念；带「万」的按万；带「元」的
    不足一万按元说、够一万按万说；纯数字 ≥10000 视为**元**换算成万（回执要说明换算了什么）。
    **0 与负数按"没填"**（沿用零价房源的既有口径）—— 不给客户发一条「0万」的消息。
    """
    if isinstance(value, bool):
        return None, False
    if isinstance(value, (int, float)):
        num = float(value)
        if num <= 0:
            return None, False
        return (f"{_num_text(num / 10000)}万", True) if num >= 10000 else (f"{_num_text(num)}万", False)
    text = str(value).strip()
    if not text:
        return None, False
    if "月" in text:
        num = _parse_number(text)
        return (f"{_num_text(num)}元/月", False) if num is not None else (text, False)
    if "万" in text:
        num = _parse_number(text)
        return (f"{_num_text(num)}万", False) if num is not None else (text, False)
    if "元" in text:
        num = _parse_number(text)
        if num is None:
            return text, False
        if num <= 0:
            return None, False
        return (f"{_num_text(num / 10000)}万", True) if num >= 10000 else (f"{_num_text(num)}元", False)
    num = _parse_number(text)
    if num is None:
        return text, False          # 「面议」「待定」这类原样念，不猜
    if num <= 0:
        return None, False
    return (f"{_num_text(num / 10000)}万", True) if num >= 10000 else (f"{_num_text(num)}万", False)


def _norm_area(value):
    """面积归一 → 「90㎡」/「90.5㎡」（不带 .0；带「㎡」「平」字样不重复）"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if float(value) <= 0 else f"{_num_text(float(value))}㎡"
    text = str(value).strip()
    if not text:
        return None
    num = _parse_number(text)
    if num is None or num <= 0:
        return text
    return f"{_num_text(num)}㎡"


def _norm_small_int(value):
    """房间数/套数归一 → 纯数字串（认「3室2厅」这种整串、也认中文数字）；认不出算"没填" """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _num_text(float(value)) if float(value) > 0 else None
    text = str(value).strip()
    if not text:
        return None
    num = _parse_number(text)
    if num is not None:
        return _num_text(num) if num > 0 else None
    if text in _CN_DIGITS and _CN_DIGITS[text] > 0:
        return str(_CN_DIGITS[text])
    return None


def _norm_text(value):
    """文本类归一 → 去首尾空白的串；空值算"没给"；一串值用「、」连成一句（不打印 Python 的列表样子）"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (list, tuple, set)):
        parts = [str(v).strip() for v in value if str(v).strip()]
        return "、".join(parts) if parts else None
    if isinstance(value, dict):
        return None
    text = str(value).strip()
    return text or None


def _field_text(kind, value):
    """按类型归一一项 → (文本 或 None, 是否元→万换算)"""
    if value is None:                       # 显式 null 按"没给"（不然会念出「None万」）
        return None, False
    if kind == "money":
        return _norm_money(value)
    if kind == "area":
        return _norm_area(value), False
    if kind == "room" or kind == "count":
        return _norm_small_int(value), False
    return _norm_text(value), False


def _template_options():
    return options_text(TEMPLATE_ORDER, labels=TEMPLATE_LABELS)


def use_template(
    template_name: str,
    variables: dict = None,
    task_id: str = None,
) -> str:
    """使用消息模板

    参数:
        template_name: 模板名（中文或英文键；房源推荐/看房提醒/跟进提醒/价格变动/市场周报/两条挽回模板）
        variables: 模板变量（按变量名成对给；缺哪一项会一次列全）
    """
    if isinstance(template_name, str):
        key = TEMPLATE_ALIASES.get(template_name.strip().lower().replace(" ", ""))
    else:
        key = None
    if key is None:
        head = (f"未知模板「{template_name}」。" if isinstance(template_name, str)
                else f"模板名要是文字（如「{TEMPLATE_LABELS[TEMPLATE_ORDER[0]]}」）。")
        return json.dumps({"success": False, "error": f"{head}可用模板：{_template_options()}"},
                          ensure_ascii=False)

    if variables is not None and not isinstance(variables, dict):
        return json.dumps({"success": False, "error": (
            f"模板变量要成对给我（例如：小区=格子小区，价格=150万）。"
            f"这次收到的是{type_word(variables)}，我没法用。")}, ensure_ascii=False)

    variables = variables or {}
    label = TEMPLATE_LABELS.get(key, key)
    fields = TEMPLATE_FIELDS[key]
    values, missing, notes = {}, [], []
    for name, cn, kind in fields:
        if name not in variables:
            missing.append((name, cn))
            continue
        text, converted = _field_text(kind, variables[name])
        if text is None:
            missing.append((name, cn))
            continue
        values[name] = text
        if converted:
            notes.append(f"{cn}「{variables[name]}」按元换成了「{text}」")

    if missing:
        who = "、".join(f"{cn}（{name}）" for name, cn in missing)
        if len(missing) == len(fields):
            error = f"「{label}」需要这些内容：{who}。给我我就生成。"
        else:
            error = f"「{label}」还缺 {len(missing)} 项：{who}。补齐后我再生成。"
        return json.dumps({"success": False, "error": error,
                           "missing": [name for name, _ in missing]}, ensure_ascii=False)

    payload = {
        "success": True,
        "template": key,
        "template_label": label,
        "message": TEMPLATES[key].format(**values),
    }
    if notes:
        payload["note"] = "注：" + "；".join(notes) + "。"
    return json.dumps(payload, ensure_ascii=False)


registry.register(
    name="get_script",
    toolset="real_estate",
    schema={"name": "get_script", "description": "获取内置销售话术的原文（共 12 条：4 个场景 × 3 条）—— 开场白 greeting / 异议处理 objection_handling / 逼定成交 closing / 跟进维护 follow_up。给场景回这个场景下的子场景清单，给子场景回那一条的正文。这是内置话术、改不了；经纪人自己攒的话术在话术库那几个工具里。", "parameters": {
        "type": "object",
        "properties": {
            "scenario": {"type": "string", "enum": ["greeting", "objection_handling", "closing", "follow_up"], "description": "场景：开场白 / 异议处理 / 逼定成交 / 跟进维护（认这几种中文说法，也认英文）"},
            "sub_scenario": {"type": "string", "description": "子场景（可选）：给了只回那一条，不给就回整个场景的子场景清单。可选值随场景不同，返回里会列出来"},
        },
        "required": ["scenario"],
    }},
    handler=lambda args, **kw: get_script(**args),
)

registry.register(
    name="use_template",
    toolset="real_estate",
    schema={"name": "use_template", "description": "按固定格式生成一条发给客户或门店的信息（7 个模板）：房源推荐 / 看房提醒 / 跟进提醒 / 价格变动 / 市场周报 / 长期未联系挽回 / 看房后没下文挽回。只负责把内容填进格式里，不改内容、不查库、不发消息；金额按「万」说（写 150 或 150万 都行，写 2500元/月 就按月租显示，写 1500000 会按元换算成 150万 并在回执里说明），面积不带小数点。缺哪一项会一次列全，不产出半截消息。", "parameters": {
        "type": "object",
        "properties": {
            "template_name": {"type": "string", "enum": ["property_recommend", "viewing_reminder", "follow_up", "price_change", "market_report", "winback_long_absence", "winback_after_viewing"], "description": "模板名：房源推荐 / 看房提醒 / 跟进提醒 / 价格变动 / 市场周报 / 长期未联系挽回 / 看房后没下文挽回（认这几种中文说法，也认英文）"},
            "variables": {"type": "object", "description": "模板变量（按\"变量名→内容\"成对给）。各模板需要什么：房源推荐→community 小区、price 价格、rooms 室、halls 厅、area 面积、highlights 亮点；看房提醒→time 时间、address 地址、contact 联系人；跟进提醒→customer 客户、last_contact 上次沟通、todo 待办；价格变动→title 房源、old_price 原价、new_price 现价、change 变动；市场周报→district 区域、new_listings 新增房源、deals 成交、avg_price 均价；长期未联系挽回→customer 客户；看房后没下文挽回→customer 客户、property 房源。缺的会一次列全"},
        },
        "required": ["template_name"],
    }},
    handler=lambda args, **kw: use_template(**args),
)
