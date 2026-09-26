"""
Coco 房产工具 - 短视频口播脚本生成
抖音/视频号 30 秒卖房口播脚本：开头钩子 + 房源亮点 + 价格钩子 + 行动号召

口径（2026-09-26 收口）：
- **亮点只来自房源里真有的字段**（面积/户型/朝向/装修/楼层/电梯/年份/标签），有几条说几条 ——
  原先不足 3 条时会补「格局方正/采光通透/产权清晰随时看房」这类**编出来的卖点**（口播稿是要念出来的）。
- 不写查不到依据的断言（产权、稀缺性、"基本找不到第二套"、业主诚心卖/租金还能谈）。
- 出租说「月租」、买卖说「总价」；面积走共用件（整数不带 `.0`）。
- 平台认中文与英文代号；**必须说清发哪个**（两个平台语气不同，不替他默认）。
"""
import json
from functools import partial

from tools.registry import registry
from agent.real_estate_input import norm_id
from agent.real_estate_money import fmt_price
from tools.real_estate_property import _fmt_area, unavailable_property_note

# 口播稿口径：整万说整万、非整万保留一位；缺价格说"价格待定"
_fmt_price = partial(fmt_price, digits=1, empty="价格待定")

PLATFORM_LABELS = {"douyin": "抖音", "shipinhao": "视频号"}
_PLATFORM_ALIAS = {
    "douyin": "douyin", "抖音": "douyin", "抖音短视频": "douyin", "抖音视频": "douyin",
    "shipinhao": "shipinhao", "视频号": "shipinhao", "微信视频号": "shipinhao",
    "weixin": "shipinhao", "wechat": "shipinhao",
}
_PLATFORM_OPTIONS = "可以这样说：抖音（douyin）/ 视频号（shipinhao）。"
# 只有这些装修才当卖点念（毛坯/简装念出来反而是减分项）
_RENOVATION_SELLING = ("精装", "豪装", "中装")


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


def _layout_text(p):
    rooms, halls = p.get('rooms'), p.get('halls')
    if rooms and halls:
        return f"{rooms}室{halls}厅"
    if rooms == 1:
        return '开间'
    return ''


def _norm_platform(value):
    """平台写法归一 → (代号, 中文提示)。**必填**，认不出给中文可选值清单"""
    if value is None or str(value).strip() == "":
        return None, "没说要发到哪个平台（抖音还是视频号）。" + _PLATFORM_OPTIONS
    key = str(value).strip().lower().replace(" ", "")
    code = _PLATFORM_ALIAS.get(key)
    if code:
        return code, None
    return None, f"平台没能识别：你说的是「{value}」。" + _PLATFORM_OPTIONS + "两个平台语气不同，告诉我发哪个我按那个写。"


def _highlights(p):
    """可口播的亮点：**只取库里真有的字段**（一条都没有就返回空列表，由上层如实说）"""
    items = []
    area = p.get('area')
    layout = _layout_text(p)
    if area:
        items.append(f"{_fmt_area(area)}㎡{' ' + layout if layout else ''}空间")
    if p.get('orientation'):
        items.append(f"{p['orientation']}朝向")
    if str(p.get('renovation') or '') in _RENOVATION_SELLING:
        items.append(f"{p['renovation']}装修")
    if p.get('floor'):
        items.append(f"{p['floor']}楼层")
    if p.get('has_elevator') == 1:
        items.append('带电梯')
    if p.get('year_built'):
        items.append(f"{p['year_built']}年建成")
    tags = [t.strip() for t in (p.get('tags') or '').replace('，', ',').split(',') if t.strip()]
    for tag in tags:
        if '景观' in tag:
            items.append(tag.split(':', 1)[-1] + '景观')
        else:
            items.append(tag)
    seen, uniq = set(), []
    for it in items:
        if it not in seen:
            seen.add(it)
            uniq.append(it)
    return uniq[:3]


def _highlight_line(highlights) -> str:
    """亮点句：**按实际条数说**，不足三条就说几条，绝不补假话"""
    if not highlights:
        return "这套房源登记的信息还不多，我先按价格和位置给你说重点。"
    if len(highlights) == 1:
        return f"这套房子的亮点：{highlights[0]}。"
    if len(highlights) == 2:
        return f"这套房子的亮点有两个：第一，{highlights[0]}；第二，{highlights[1]}。"
    return (f"这套房子的亮点有三个：第一，{highlights[0]}；第二，{highlights[1]}；"
            f"第三，{highlights[2]}。")


def generate_short_video_script(property_id: int, platform: str = None,
                                task_id: str = None) -> str:
    """生成 30 秒短视频口播脚本

    platform: douyin(抖音，快节奏强悬念) / shipinhao(视频号，接地气重信任) —— **必填**，中文名也认
    结构：0-3s 钩子 → 3-20s 亮点 → 20-25s 价格 → 25-30s 行动号召
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
        error, status_label = unavailable_property_note(
            property_id, db.get_property(property_id), action="写口播稿")
        payload = {"success": False, "error": error}
        if status_label:
            payload["property_status"] = status_label
        return json.dumps(payload, ensure_ascii=False)

    title = p.get('title') or '这套房子'
    price = _fmt_price(p)
    is_rental = p.get('property_type') == 'rental'
    place = p.get('district') or p.get('community') or ''
    layout = _layout_text(p)
    highlights = _highlights(p)
    highlight_line = _highlight_line(highlights)
    price_word = "月租" if is_rental else "总价"

    if code == 'douyin':
        hook = (f"在{place}，{price}就能拿下这套{layout or '房子'}，你敢信？" if place
                else f"{price}就能拿下这套{layout or '房子'}，你敢信？")
        price_hook = f"重点来了，{price_word}只要{price}。"
        cta = "想看房源的评论区扣1，我挨个发资料，手慢无！"
        closing = f"关注我，{place}好房每天更新" if place else "关注我，好房每天更新"
    else:
        hook = (f"今天带大家看一套{place}的房子，{layout or '面积适中'}" if place
                else f"今天带大家看一套房子，{layout or '面积适中'}")
        price_hook = f"这套房{price_word}{price}。"
        cta = "想实地看看的朋友，点个关注私信我，随时带你看房"
        closing = "我是本地中介，房源真实，看房不收费"

    script = (
        f"【0-3秒 钩子】{hook}\n"
        f"【3-20秒 亮点】{highlight_line}\n"
        f"【20-25秒 价格】{price_hook}\n"
        f"【25-30秒 行动号召】{cta}\n"
        f"【结尾】{closing}"
    )

    return json.dumps({
        "success": True,
        "platform": code,
        "property_id": property_id,
        "title": title,
        "duration": "30秒",
        "highlights": highlights,
        "script": script,
        "tip": "录制时语速保持每分钟240-260字，价格和地点放慢强调；可让Coco按此框架润色成更口语化的版本",
    }, ensure_ascii=False)


registry.register(
    name="generate_short_video_script",
    toolset="real_estate",
    schema={"name": "generate_short_video_script", "description": "生成 30 秒短视频口播脚本（抖音 / 视频号）：0-3 秒钩子 → 3-20 秒亮点 → 20-25 秒价格 → 25-30 秒行动号召。**亮点只来自房源里真有的字段**（面积/户型/朝向/装修/楼层/电梯/年份/标签），有几条说几条；房源没填的、或查不到依据的（产权、稀缺性、比价、业主是否诚心卖）不会写进稿子。出租说「月租」、买卖说「总价」。只能给在售或在租房源写稿，已售/已租会如实说明。", "parameters": {
        "type": "object",
        "properties": {
            "property_id": {"type": "integer", "description": "房源编号（房源列表或详情里的编号，纯数字）"},
            "platform": {"type": "string", "enum": ["douyin", "shipinhao"], "description": "发到哪个平台：抖音（douyin，快节奏强悬念）/ 视频号（shipinhao，接地气重信任）；**必填**（两个平台语气不同，中文名也认）"},
        },
        "required": ["property_id", "platform"],
    }},
    handler=lambda args, **kw: generate_short_video_script(**args),
)
