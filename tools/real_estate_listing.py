"""
Coco 房产工具 - 房源发布文案生成
为不同平台生成标准化的房源发布文案

展示口径（2026-09-26 收口）：
- 面积/单价走房源侧 `_property_display`（一处定义）：面积整数不带 `.0`（90㎡）、
  出租单价带 `/月`（41.67元/㎡/月）；价格按文案口径（一位小数，缺价格说"价格待定"）。
- 房源里没填的字段（朝向/装修/楼层/电梯…）一律不出现，也不留空的地区行 —— 不臆造。
- 平台写法认中文与常见别名；认不出给中文可选值清单，绝不静默按默认平台生成。
"""
import json
from functools import partial

from tools.registry import registry
from agent.real_estate_input import norm_id
from agent.real_estate_money import fmt_price
from tools.real_estate_property import _property_display, unavailable_property_note

# 文案口径：整万说整万、非整万保留一位（海报/文案的字要短）；缺价格说"价格待定"
_fmt_price = partial(fmt_price, digits=1, empty="价格待定")

PLATFORM_LABELS = {"friends": "朋友圈", "beike": "贝壳", "anjuke": "安居客", "58": "58同城"}
_PLATFORM_ALIAS = {
    "friends": "friends", "friend": "friends", "朋友圈": "friends", "微信": "friends",
    "weixin": "friends", "wechat": "friends", "moments": "friends", "wx": "friends",
    "beike": "beike", "贝壳": "beike", "贝壳找房": "beike",
    "anjuke": "anjuke", "安居客": "anjuke",
    "58": "58", "58同城": "58", "58.com": "58", "五八": "58",
}
_PLATFORM_OPTIONS = "可以这样说：朋友圈（friends）/ 贝壳（beike）/ 安居客（anjuke）/ 58同城（58）。"


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


def _norm_platform(value):
    """平台写法归一 → (代号, 中文提示)。没给或认不出都给提示，**绝不静默按默认平台生成**"""
    if value is None or str(value).strip() == "":
        return None, "没说要发到哪个平台。" + _PLATFORM_OPTIONS
    key = str(value).strip().lower().replace(" ", "").replace("　", "")
    code = _PLATFORM_ALIAS.get(key)
    if code:
        return code, None
    return None, f"平台没能识别：你说的是「{value}」。" + _PLATFORM_OPTIONS


def _fmt_title(p):
    """标题：房源自己的标题；没有就用小区 + 户型拼，**不出现「?室?厅」这类占位符**"""
    if p.get('title'):
        return p['title']
    layout = f"{p['rooms']}室{p.get('halls') or 0}厅" if p.get('rooms') else ''
    return " ".join(x for x in (p.get('community') or '', layout) if x) or '优质房源'


def _fmt_basic(p):
    """房源基础信息行：**只写库里真有的字段**（没填的不出现，也不写占位符）"""
    parts = []
    if p.get('area') not in (None, ""):
        parts.append(f"面积：{_property_display(p).get('area_label')}㎡")
    if p.get('rooms'):
        parts.append(f"户型：{p.get('rooms')}室{p.get('halls') or 0}厅{p.get('bathrooms') or 1}卫")
    if p.get('orientation'):
        parts.append(f"朝向：{p.get('orientation')}")
    if p.get('floor'):
        parts.append(f"楼层：{p.get('floor')}")
    if p.get('renovation'):
        parts.append(f"装修：{p.get('renovation')}")
    if p.get('year_built'):
        parts.append(f"建成年份：{p.get('year_built')}")
    if p.get('has_elevator') == 1:
        parts.append("有电梯")
    if p.get('parking') == 1:
        parts.append("有车位")
    return "，".join(parts)


def _unavailable_error(property_id: int) -> tuple:
    """房源不在售时怎么说（**共用件一处定义**：不存在 / 已售 / 已租 分开说）"""
    return unavailable_property_note(property_id, _get_db().get_property(property_id))


def generate_listing_copy(property_id: int, platform: str = "friends", task_id: str = None) -> str:
    """生成房源发布文案

    platform: friends(朋友圈) / beike(贝壳) / anjuke(安居客) / 58（中文名与常见别名都认）
    """
    property_id, problem = norm_id(property_id, '房源编号')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    code, plat_problem = _norm_platform(platform)
    if plat_problem:
        return json.dumps({"success": False, "error": plat_problem}, ensure_ascii=False)

    db = _get_db()
    p = db.get_available_property(property_id)
    if p is None:
        error, status_label = _unavailable_error(property_id)
        payload = {"success": False, "error": error}
        if status_label:
            payload["property_status"] = status_label
        return json.dumps(payload, ensure_ascii=False)

    title = _fmt_title(p)
    basic = _fmt_basic(p)
    display = _property_display(p)
    price = _fmt_price(p)
    unit_label = display.get('unit_price_label')      # 已带单位（出租是 元/㎡/月）
    community = p.get('community') or ''
    district = p.get('district') or ''
    address = p.get('address') or ''
    region_line = " ".join(x for x in (district, community) if x)

    if code == "friends":
        mid = [part for part in ((f"📍 {region_line}" if region_line else ''), basic,
                                 f"💰 价格 {price}" + (f"（单价 {unit_label}）" if unit_label else ""))
               if part]
        copy = "\n".join(["🏠 优质房源推荐", "", title] + mid + ["", "感兴趣的私信我，随时约看房！"])
    elif code == "beike":
        head = f"{title}，{region_line}" if region_line else title
        lines = [head, basic,
                 f"价格：{price}" + (f"，单价：{unit_label}" if unit_label else "")]
        if address or community:
            lines.append(f"地址：{address or community}")
        lines.append("真实房源，看房方便，欢迎咨询。")
        copy = "\n".join(x for x in lines if x)
    elif code == "anjuke":
        lines = [f"【{title}】"]
        area_region = "·".join(x for x in (district, community) if x)
        if area_region:
            lines.append(area_region)
        lines.append(basic)
        lines.append(f"价格：{price}" + (f"（{unit_label}）" if unit_label else ""))
        if address or community:
            lines.append(f"地址：{address or community}")
        lines.append("房源真实有效，随时可看，中介费优惠，欢迎来电咨询。")
        copy = "\n".join(x for x in lines if x)
    else:                                             # 58
        head = f"{title}（{community or district}）" if (community or district) else title
        lines = [head, f"【房屋信息】{basic}",
                 f"【价格】{price}" + (f"（单价{unit_label}）" if unit_label else "")]
        loc = address or community or district
        if loc:
            lines.append(f"【位置】{loc}")
        lines.append("【亮点】真实房源，看房方便，价格可谈。")
        copy = "\n".join(x for x in lines if x)

    return json.dumps({"success": True, "platform": code, "copy": copy}, ensure_ascii=False)


registry.register(
    name="generate_listing_copy",
    toolset="real_estate",
    schema={"name": "generate_listing_copy", "description": "生成房源发布文案，可以整段复制发出去。四个渠道写法不同：朋友圈（friends，带表情、口语化）、贝壳（beike，字段式）、安居客（anjuke，带标题框）、58同城（58，分栏）。价格按「150万」或「2500元/月」说，面积不带小数（90㎡）；房源里没填的字段（朝向/装修/楼层等）不会出现在文案里。只能给在售或在租的房源生成，已售/已租会如实说明。", "parameters": {
        "type": "object",
        "properties": {
            "property_id": {"type": "integer", "description": "房源编号（房源列表或详情里的编号，纯数字）"},
            "platform": {"type": "string", "enum": ["friends", "beike", "anjuke", "58"], "description": "发到哪个平台：朋友圈 / 贝壳 / 安居客 / 58同城（代号 friends / beike / anjuke / 58，中英文都认）"},
        },
        "required": ["property_id", "platform"],
    }},
    handler=lambda args, **kw: generate_listing_copy(**args),
)
