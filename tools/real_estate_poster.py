"""
Coco 房产工具 - 房源海报/九宫格生成
一键生成朋友圈海报图（标题+价格+面积+二维码），返回图片路径供飞书直接发送
"""
import glob
import hashlib
import json
import os
import re
from functools import partial

from agent.real_estate_input import norm_id
from tools.real_estate_property import _fmt_area, _prop_brief, unavailable_property_note
from agent.real_estate_money import fmt_price, fmt_unit_price
from tools.registry import registry

# 海报口径：整万说整万、非整万保留一位（画面上字要短）；缺价格说"价格待定"
_fmt_price = partial(fmt_price, digits=1, empty="价格待定")


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


_FONT_CANDIDATES = [
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
    '/System/Library/Fonts/PingFang.ttc',
    'C:/Windows/Fonts/msyh.ttc',
]


def _load_font(size):
    from PIL import ImageFont
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _poster_dir():
    """海报成品目录（独立于网关的图片缓存：缓存目录里的文件 24 小时后会被自动清理）"""
    try:
        from hermes_constants import get_hermes_home

        base = str(get_hermes_home())
    except Exception:  # noqa: BLE001 —— 拿不到框架工具时退回默认家目录，别让出图失败
        base = os.path.expanduser('~/.hermes')
    out = os.path.join(base, 'posters')
    os.makedirs(out, exist_ok=True)
    return out


def _gradient(size, c1, c2):
    """竖版线性渐变背景"""
    from PIL import Image, ImageDraw
    w, h = size
    img = Image.new('RGB', (w, h), c1)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        ratio = y / max(h - 1, 1)
        r = int(c1[0] + (c2[0] - c1[0]) * ratio)
        g = int(c1[1] + (c2[1] - c1[1]) * ratio)
        b = int(c1[2] + (c2[2] - c1[2]) * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    return img


def _type_colors(property_type):
    return {
        'second_hand': ((30, 58, 95), (46, 94, 158)),    # 深蓝 → 蓝
        'new': ((124, 45, 18), (194, 65, 12)),           # 深橙 → 橙
        'rental': ((20, 83, 45), (34, 139, 85)),         # 深绿 → 绿
    }.get(property_type, ((30, 58, 95), (46, 94, 158)))


def _ellipsis(draw, text, font, max_width):
    """按像素宽度截断文本"""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + '…', font=font) > max_width:
        text = text[:-1]
    return text + '…'


# ==================== B 档专业模板（2026-08-12 加） ====================

def _get_brand():
    """品牌名：数据库 re_settings.brand_name 优先，环境变量 COCO_BRAND 兜底；都无返回空"""
    try:
        from tools.real_estate_settings import get_brand_or_none
        db_brand = get_brand_or_none()
        if db_brand:
            return db_brand
    except Exception:
        pass
    return os.getenv('COCO_BRAND', '')


def _load_property_image(p, target_w, target_h):
    """加载房源第一张图片并 cover 裁剪到目标尺寸；无图返回 None"""
    from PIL import Image
    images = [x.strip() for x in (p.get('images') or '').split(',') if x.strip()]
    if not images:
        return None
    try:
        img = Image.open(images[0]).convert('RGB')
    except Exception:
        return None
    # cover 裁剪
    iw, ih = img.size
    scale = max(target_w / iw, target_h / ih)
    nw, nh = int(iw * scale + 0.5), int(ih * scale + 0.5)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - target_w) // 2
    top = (nh - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def _overlay_gradient_mask(img, bottom_dark=True, alpha=150):
    """在图片上叠加竖向渐变蒙版（底部压暗，让文字可读）"""
    from PIL import Image, ImageDraw
    w, h = img.size
    mask = Image.new('L', (1, h), 0)
    md = ImageDraw.Draw(mask)
    if bottom_dark:
        for y in range(h):
            ratio = y / max(h - 1, 1)
            md.point((0, y), fill=int(alpha * ratio))
    else:
        for y in range(h):
            ratio = 1 - y / max(h - 1, 1)
            md.point((0, y), fill=int(alpha * ratio))
    mask = mask.resize((w, h))
    black = Image.new('RGB', (w, h), (0, 0, 0))
    img.paste(black, (0, 0), mask)
    return img


def _rounded_card(size, radius, fill, outline=None, width=0):
    """圆角卡片（带可选描边）"""
    from PIL import Image, ImageDraw
    w, h = size
    card = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=radius, fill=fill,
                        outline=outline, width=width)
    return card


def _draw_qr(img, qr_content, center_x, center_y, size=260, bg_light=True):
    """在 img 上画二维码（居中定位）"""
    if not qr_content:
        return
    try:
        import qrcode
        from PIL import Image
        qr = qrcode.make(qr_content)
        qr = qr.convert('RGB')
        # 白底
        pad = 18
        panel = Image.new('RGB', (size + pad * 2, size + pad * 2), (255, 255, 255))
        panel.paste(qr.resize((size, size)), (pad, pad))
        img.paste(panel, (int(center_x - panel.width / 2), int(center_y - panel.height / 2)))
    except ImportError:
        pass


def _draw_premium(img, draw, p, qr_content):
    """模板1 高端黑金：深黑蓝渐变 + 金色价格 + 细线装饰（一手房）"""
    from PIL import ImageDraw
    W, H = img.size
    gold = (212, 175, 55)
    white = (255, 255, 255)
    soft = (200, 210, 230)

    # 顶部房源大图（0-640）压暗
    photo = _load_property_image(p, W, 640)
    if photo:
        photo = _overlay_gradient_mask(photo, bottom_dark=True, alpha=190)
        img.paste(photo, (0, 0))
    else:
        c1, c2 = _type_colors(p.get('property_type'))
        img.paste(_gradient((W, 640), c1, c2), (0, 0))

    # 类型角标（左上）
    type_label = {'second_hand': '二手房', 'rental': '租房', 'new': '一手房'}.get(p.get('property_type'), '房源')
    f_type = _load_font(30)
    tw = draw.textlength(type_label, font=f_type) + 36
    draw.rounded_rectangle([(40, 40), (40 + tw, 92)], radius=26,
                           fill=(212, 175, 55, 220))
    draw.text((40 + 18, 48), type_label, font=f_type, fill=(20, 20, 25))

    # 品牌（右上）
    f_brand = _load_font(34)
    brand = _get_brand()
    bw = draw.textlength(brand, font=f_brand)
    draw.text((W - 40 - bw, 48), brand, font=f_brand, fill=white)

    # 标题（图片下方）
    f_title = _load_font(56)
    title = _ellipsis(draw, p.get('title') or '优质房源', f_title, W - 100)
    draw.text((50, 700), title, font=f_title, fill=(30, 30, 40))

    # 金色装饰线
    draw.rectangle([(50, 800), (160, 806)], fill=gold)

    # 价格
    price_text = _fmt_price(p)
    f_price = _load_font(120)
    draw.text((50, 830), price_text, font=f_price, fill=gold)
    f_unit = _load_font(34)
    if p.get('unit_price'):
        draw.text((50, 990), f"单价 {fmt_unit_price(p['unit_price'])} 元/㎡", font=f_unit, fill=(120, 125, 140))

    # 信息卡（白色圆角卡片）
    area = p.get('area')
    rooms, halls = p.get('rooms'), p.get('halls')
    layout = f"{rooms}室{halls}厅" if (rooms and halls) else ('开间' if rooms == 1 else '')
    district = p.get('district') or p.get('community') or '位置详情'
    items = [
        ('面积', f"{area}㎡" if area else '-'),
        ('户型', layout or '-'),
        ('区域', district),
    ]
    card = _rounded_card((W - 100, 180), 24, (255, 255, 255))
    img.paste(card, (50, 1050), card)
    f_k = _load_font(30)
    f_v = _load_font(36)
    x = 80
    for k, v in items:
        draw.text((x, 1085), k, font=f_k, fill=(130, 135, 150))
        draw.text((x, 1125), _ellipsis(draw, v, f_v, 260), font=f_v, fill=(40, 40, 50))
        x += 310

    # 标签（金色描边）
    tags = [t.strip() for t in (p.get('tags') or '').split(',') if t.strip()]
    y = 1270
    if tags:
        f_tag = _load_font(30)
        x = 50
        for tag in tags[:4]:
            tw = draw.textlength(tag, font=f_tag) + 36
            draw.rounded_rectangle([(x, y), (x + tw, y + 58)], radius=29,
                                   outline=gold, width=2)
            draw.text((x + 18, y + 12), tag, font=f_tag, fill=(60, 55, 40))
            x += tw + 20

    # 底部：二维码 + 引导语
    if qr_content:
        _draw_qr(img, qr_content, W - 170, H - 150, size=200)
    f_foot = _load_font(34)
    draw.text((50, H - 220), "真实房源 · 随时约看", font=f_foot, fill=(110, 115, 130))


def _draw_modern(img, draw, p, qr_content):
    """模板2 现代白卡：白/浅灰背景 + 圆角卡片 + 清爽灰调（二手房）"""
    from PIL import Image, ImageDraw
    W, H = img.size
    dark = (40, 45, 55)
    gray = (130, 135, 145)
    accent = (52, 120, 246)
    white = (255, 255, 255)
    bg = (245, 247, 250)

    # 背景浅灰
    img.paste(Image.new('RGB', (W, H), bg), (0, 0))

    # 顶部大图（0-560）白色圆角卡片包裹
    photo = _load_property_image(p, W - 60, 500)
    if photo:
        photo = _overlay_gradient_mask(photo, bottom_dark=True, alpha=140)
        card = _rounded_card((W - 60, 500), 28, (255, 255, 255))
        img.paste(card, (30, 30), card)
        img.paste(photo, (30, 30), card)
    else:
        c1, c2 = _type_colors(p.get('property_type'))
        photo2 = _gradient((W - 60, 500), c1, c2)
        card = _rounded_card((W - 60, 500), 28, (255, 255, 255))
        img.paste(card, (30, 30), card)
        img.paste(photo2, (30, 30), card)

    # 类型角标
    type_label = {'second_hand': '二手房', 'rental': '租房', 'new': '一手房'}.get(p.get('property_type'), '房源')
    f_type = _load_font(28)
    tw = draw.textlength(type_label, font=f_type) + 30
    draw.rounded_rectangle([(52, 52), (52 + tw, 96)], radius=22, fill=accent)
    draw.text((52 + 15, 60), type_label, font=f_type, fill=white)

    # 标题
    f_title = _load_font(52)
    title = _ellipsis(draw, p.get('title') or '优质房源', f_title, W - 100)
    draw.text((50, 590), title, font=f_title, fill=dark)

    # 价格（accent 蓝）
    price_text = _fmt_price(p)
    f_price = _load_font(110)
    draw.text((50, 660), price_text, font=f_price, fill=accent)
    f_unit = _load_font(32)
    if p.get('unit_price'):
        draw.text((50, 800), f"单价 {fmt_unit_price(p['unit_price'])} 元/㎡", font=f_unit, fill=gray)

    # 信息卡（三列白卡）
    area = p.get('area')
    rooms, halls = p.get('rooms'), p.get('halls')
    layout = f"{rooms}室{halls}厅" if (rooms and halls) else ('开间' if rooms == 1 else '')
    district = p.get('district') or p.get('community') or '位置详情'
    items = [
        ('面积', f"{area}㎡" if area else '-'),
        ('户型', layout or '-'),
        ('区域', district),
    ]
    f_k = _load_font(28)
    f_v = _load_font(32)
    x = 50
    for k, v in items:
        card = _rounded_card((300, 130), 20, white)
        img.paste(card, (x, 850), card)
        draw.text((x + 22, 880), k, font=f_k, fill=gray)
        draw.text((x + 22, 915), _ellipsis(draw, v, f_v, 250), font=f_v, fill=dark)
        x += 320

    # 标签（浅蓝底圆角）
    tags = [t.strip() for t in (p.get('tags') or '').split(',') if t.strip()]
    y = 1020
    if tags:
        f_tag = _load_font(28)
        x = 50
        for tag in tags[:4]:
            tw = draw.textlength(tag, font=f_tag) + 32
            draw.rounded_rectangle([(x, y), (x + tw, y + 54)], radius=27,
                                   fill=(232, 240, 255))
            draw.text((x + 16, y + 11), tag, font=f_tag, fill=accent)
            x += tw + 18

    # 底部品牌 + 二维码
    f_brand = _load_font(30)
    brand = _get_brand()
    draw.text((50, H - 130), brand, font=f_brand, fill=gray)
    f_foot = _load_font(30)
    draw.text((50, H - 80), "真实房源 · 随时约看", font=f_foot, fill=gray)
    if qr_content:
        _draw_qr(img, qr_content, W - 140, H - 120, size=170)


def _draw_vibrant(img, draw, p, qr_content):
    """模板3 活力橙红：橙红渐变 + 大号促销价签 + 行动号召（出租/快节奏）"""
    from PIL import ImageDraw
    W, H = img.size
    white = (255, 255, 255)
    soft = (255, 225, 215)
    red = (232, 65, 24)

    # 顶部大图 + 渐变
    photo = _load_property_image(p, W, 620)
    if photo:
        photo = _overlay_gradient_mask(photo, bottom_dark=True, alpha=170)
        img.paste(photo, (0, 0))
    else:
        c1, c2 = ((214, 69, 28), (255, 140, 60))
        img.paste(_gradient((W, 620), c1, c2), (0, 0))

    # 类型角标
    type_label = {'second_hand': '二手房', 'rental': '租房', 'new': '一手房'}.get(p.get('property_type'), '房源')
    f_type = _load_font(30)
    tw = draw.textlength(type_label, font=f_type) + 36
    draw.rounded_rectangle([(40, 40), (40 + tw, 92)], radius=26, fill=red)
    draw.text((40 + 18, 48), type_label, font=f_type, fill=white)

    # 品牌
    f_brand = _load_font(34)
    brand = _get_brand()
    bw = draw.textlength(brand, font=f_brand)
    draw.text((W - 40 - bw, 48), brand, font=f_brand, fill=white)

    # 标题
    f_title = _load_font(56)
    title = _ellipsis(draw, p.get('title') or '优质房源', f_title, W - 100)
    draw.text((50, 680), title, font=f_title, fill=(40, 30, 25))

    # 价格（橙红大价签）
    price_text = _fmt_price(p)
    f_price = _load_font(130)
    draw.text((50, 760), price_text, font=f_price, fill=red)
    f_unit = _load_font(34)
    if p.get('unit_price'):
        draw.text((50, 930), f"单价 {fmt_unit_price(p['unit_price'])} 元/㎡", font=f_unit, fill=(140, 90, 70))

    # 信息卡（半透明白卡片）
    area = p.get('area')
    rooms, halls = p.get('rooms'), p.get('halls')
    layout = f"{rooms}室{halls}厅" if (rooms and halls) else ('开间' if rooms == 1 else '')
    district = p.get('district') or p.get('community') or '位置详情'
    items = [
        ('面积', f"{area}㎡" if area else '-'),
        ('户型', layout or '-'),
        ('区域', district),
    ]
    f_k = _load_font(30)
    f_v = _load_font(34)
    x = 50
    for k, v in items:
        card = _rounded_card((300, 120), 20, (255, 255, 255, 230))
        img.paste(card, (x, 1000), card)
        draw.text((x + 22, 1025), k, font=f_k, fill=(150, 100, 80))
        draw.text((x + 22, 1060), _ellipsis(draw, v, f_v, 250), font=f_v, fill=(60, 40, 30))
        x += 320

    # 标签（橙红描边）
    tags = [t.strip() for t in (p.get('tags') or '').split(',') if t.strip()]
    y = 1160
    if tags:
        f_tag = _load_font(28)
        x = 50
        for tag in tags[:4]:
            tw = draw.textlength(tag, font=f_tag) + 32
            draw.rounded_rectangle([(x, y), (x + tw, y + 54)], radius=27,
                                   outline=red, width=2)
            draw.text((x + 16, y + 11), tag, font=f_tag, fill=red)
            x += tw + 18

    # 底部行动号召 + 二维码
    f_cta = _load_font(44)
    draw.text((50, H - 260), "🏠 好房不等人 速约看房", font=f_cta, fill=red)
    if qr_content:
        _draw_qr(img, qr_content, W - 150, H - 130, size=190)
    f_foot = _load_font(30)
    draw.text((50, H - 80), _get_brand() + " · 真实房源", font=f_foot, fill=(140, 90, 70))


# ---------------- 经纪人名片与信息齐全校验（2026-09-19 加） ----------------
_FOOTER_TEXT = "房源信息以实际看房为准"


def _agent_card() -> dict:
    """读取经纪人名片（姓名/电话/微信/公司名）；未配置的字段为空串"""
    try:
        from tools.real_estate_settings import get_agent_card_or_empty

        return get_agent_card_or_empty()
    except Exception:
        return {}


def _property_photo(p) -> str:
    """房源第一张可用照片路径；没有则空串"""
    images = [x.strip() for x in str(p.get('images') or '').split(',') if x.strip()]
    for path in images:
        if os.path.exists(path):
            return path
    return ''


POSTER_TEMPLATES = [
    # shows_room_no：这一款会不会把「房源标识行」（含房号）印在图上 —— 会印才需要问经纪人
    {"code": "A", "name": "红金促销", "desc": "红金配色、促销感强；不需要照片也能出图；会印房源标识行（含房号）",
     "need_photo": False, "shows_room_no": True},
    {"code": "B", "name": "极简高级", "desc": "米白极简、适合有房源照片的房；会显示楼层/朝向两栏，不印房号",
     "need_photo": True, "shows_room_no": False},
    {"code": "CUSTOM", "name": "参考图风格（自定义款）",
     "desc": "按经纪人发的参考海报图提取的风格渲染：可选版式/配色/字体气质/显示哪些信息/装饰；会印房源标识行（含房号）",
     "need_photo": False, "shows_room_no": True},
]


def _template_shows_room_no(code: str) -> bool:
    return any(t["code"] == code and t.get("shows_room_no") for t in POSTER_TEMPLATES)
TEMPLATE_CHOICES = "；".join(f"{t['code']} {t['name']}（{t['desc']}）" for t in POSTER_TEMPLATES) + "；auto（你看着办，按房源特征自动挑）"
ROOM_NO_MODES = {
    "full": "写完整房号（如 7号楼2单元1602）",
    "unit": "只写楼栋单元、不写具体房号（如 7号楼2单元）",
    "none": "不写房号，只显示小区名（如 海阔天空）",
}
_ROOM_NO_ALIAS = {"完整": "full", "全": "full", "full": "full", "写完整": "full",
                  "楼栋": "unit", "只写楼栋": "unit", "单元": "unit", "unit": "unit",
                  "不写": "none", "不要": "none", "隐藏": "none", "none": "none"}


def _norm_room_no_mode(value):
    """把经纪人的说法归一成 full / unit / none；认不出返回 None（继续追问）。"""
    if value is None:
        return None
    key = str(value).strip().lower().replace(" ", "")
    return _ROOM_NO_ALIAS.get(key)


def _poster_display_title(p, room_no_mode: str = "full") -> str:
    """海报上显示的房源标题：按房号档位掩码（unit/none 档不显示具体房号）。

    渲染前**兜底**：即便上游把带房号的标题塞进来，也不会把具体房号印到图上；
    **掩完没有可显示的字时不回退原串**（那等于没掩），退回小区名或「优质房源」。
    """
    from tools.real_estate_poster_svg import mask_room_no

    raw = p.get('title') or '优质房源'
    return mask_room_no(raw, room_no_mode, community=p.get('community')) or '优质房源'


def _missing_poster_info(p, card, need_photo: bool, need_floor: bool = False,
                         need_orientation: bool = False, need_room_no: bool = False,
                         room_no_mode: str = None) -> list:
    """出图前的信息齐全校验：返回缺失项清单（空列表 = 信息齐全，可以出图）

    need_floor / need_orientation：所选模板会显示这两栏（目前是 B 极简高级款）。
    **先问清再出图**，别等图做完了才发现两栏是「—」（2026-09-21 要求）。
    """
    miss = []
    if not (p.get('title') or p.get('community')):
        miss.append("房源名称/房号（如 262栋1009）")
    if not p.get('area'):
        miss.append("建筑面积（㎡）")
    if not p.get('price'):
        miss.append("价格（总价或月租）")
    if not card.get('company'):
        miss.append("您的公司/门店名称（海报品牌栏显示，不会写任何平台或虚构名称）")
    if not (card.get('name') or card.get('phone') or card.get('wechat')):
        miss.append("您的联系方式（姓名 / 电话 / 微信 至少一项，海报名片区使用）")
    if need_photo:
        miss.append("房源照片（所选模板需要照片；也可以改用不需要照片的模板）")
    if need_floor and not p.get('floor'):
        miss.append("楼层（如 11层；标题或地址里带房号如 301/1602 时系统会自动按房号推断，不必您提供）")
    if need_orientation and not p.get('orientation'):
        miss.append("朝向（如 朝南 / 南北通透；房号推不出朝向，需要您告知）")
    if need_room_no and not room_no_mode:
        miss.append("海报上要不要写房号（三选一：" + " / ".join(ROOM_NO_MODES.values()) + "）")
    return miss


# 兜底营销词：**不对房源事实做任何断言**（原先的「业主诚售/仅此一套/开发商直售/拎包入住/今日可看」
# 五句在房源里都查不到依据，其中「仅此一套」还是绝对化表述 —— 2026-09-26 拍板全部删掉）
_REMARK_WORDS = {
    "second_hand": ["今日主推", "好房推荐"],
    "new": ["新盘在售", "今日主推"],          # 「新盘在售」= 房源类型就是新的，有依据
    "rental": ["月租好房", "今日主推"],
}
_TAG_TITLE_MAX_LEN = 6      # 标签太长（如「地铁1号线步行3分钟」）不做候选，海报大字放不下


def _is_recent_listing(p, days: int = 7) -> bool:
    created = p.get('created_at')
    if not created:
        return False
    from datetime import datetime, timedelta

    try:
        ts = datetime.fromisoformat(str(created).replace('Z', '').replace('T', ' ')[:19])
    except (TypeError, ValueError):
        return False
    return datetime.now() - ts <= timedelta(days=days)


def _title_candidate_items(p) -> list:
    """主标题候选（**每个都带依据**）：先用房源里真有的卖点，不够再用中性营销词补足

    2026-09-26 修（F318/F319）：原先把三个硬编码套话排在最前、再追加标签/装修候选，最后 `[:3]` 截断 ——
    于是「业主诚售/仅此一套/今日可看」这些**没有依据的断言**永远占满名额，标签与装修**从来没进过候选**
    （而描述写着"按房源类型/标签生成"）。现在反过来：有依据的排前面，营销词只做兜底。
    """
    items = []
    tags = [x.strip() for x in str(p.get('tags') or '').replace('，', ',').replace('、', ',').split(',')
            if x.strip()]
    for tag in tags[:2]:
        if len(tag) <= _TAG_TITLE_MAX_LEN:
            items.append({"title": f"{tag}好房", "why": f"来自房源标签「{tag}」"})
    renovation = str(p.get('renovation') or '')
    if renovation in ('精装', '豪装'):
        items.append({"title": f"{renovation}好房", "why": f"来自房源装修「{renovation}」"})
    if '南北通透' in str(p.get('orientation') or ''):
        items.append({"title": "南北通透好房", "why": "来自房源朝向「南北通透」"})
    if str(p.get('viewing_note') or '').strip():
        items.append({"title": "随时可看", "why": "房源里已填看房方式（钥匙/预约）"})
    if _is_recent_listing(p):
        items.append({"title": "新上房源", "why": "近 7 天内录入的房源"})
    for word in _REMARK_WORDS.get(p.get('property_type'), _REMARK_WORDS["second_hand"]):
        items.append({"title": word, "why": "中性营销词（不涉及房源事实）"})

    seen, out = set(), []
    for it in items:
        if it["title"] in seen:
            continue
        seen.add(it["title"])
        out.append(it)
    return out[:3]


def _title_candidates(p) -> list:
    """候选标题数组（海报那边在用）；带依据的明细见 `_title_candidate_items`"""
    return [c["title"] for c in _title_candidate_items(p)]


def _pick_template(p, template: str, photo: str) -> tuple:
    """返回 (模板代号, 选择理由)。**模板为空时返回 (None, ...)，由上层询问经纪人**（2026-09-21 改）。

    传 "auto"（或"你看着办"）才按房源特征自动挑 —— 保留这个逃生口，避免每次都问。
    """
    alias = {"premium": "A", "modern": "B", "vibrant": "A", "promo": "A", "classic": "B",
             "custom": "CUSTOM", "自定义": "CUSTOM", "参考图": "CUSTOM", "style": "CUSTOM"}
    if template and str(template).strip().lower() in ("auto", "你看着办", "随便", "自动"):
        template = ""
    elif template:
        code = alias.get(str(template).lower(), str(template).upper())
        if code in ("A", "B", "CUSTOM"):
            return code, ("按经纪人发的参考图风格渲染" if code == "CUSTOM" else "按指定模板")
        return None, f"模板「{template}」不在模板库里"
    else:
        return None, "未指定模板 —— 先问经纪人要哪一款"
    try:
        area = float(p.get('area') or 0)
    except (TypeError, ValueError):
        area = 0
    ren = str(p.get('renovation') or '')
    if photo and (area >= 110 or ren in ('豪装', '精装')):
        return "B", "房源照片齐全且面积/装修偏高端 → 极简高级款"
    return "A", "促销风主力款（无需照片也能出图）"


def generate_property_poster(property_id: int = None, title: str = None, qr_content: str = None,
                            template: str = None, poster_title: str = None,
                            show_room_no: str = None, style: dict = None,
                            allow_missing: bool = False, task_id: str = None) -> str:
    """生成房源海报（1080x1920）

    property_id 或 title 二选一：传 id 精确匹配；传标题模糊匹配。
    template 可选 A（红金促销）/B（极简高级，需照片）/auto（你看着办，系统按房源特征挑）；
    **不传则不出图，先返回模板库清单让经纪人挑**（2026-09-21 要求）。
    style：template="custom" 时用 —— 从参考图提取的风格参数（layout/palette/font_style/show_fields/decor），
    非法值会被收敛到安全取值并在 notes 里说明（绝不因为参数不对就出空图）。
    show_room_no：海报上要不要写房号 —— full（完整）/unit（只写楼栋单元）/none（只显示小区名）；
    **不传会并入待问清单**（不同经纪人对房号曝光的诉求不同，不能默认替他决定）。
    poster_title：海报主标题文案（先调 suggest_poster_titles 拿候选给经纪人挑）。
    allow_missing=True：经纪人明确说"先出图/信息就这些"时使用，缺的字段留空不编造。
    信息不齐时**不出图**，返回 missing 清单让 Coco 一次问清。
    """
    if property_id is not None:
        property_id, problem = norm_id(property_id, '房源编号')
        if problem:
            return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    db = _get_db()
    p = db.get_available_property(property_id) if property_id is not None else None
    if p is None and title:
        hits = db.find_available_property_by_title(title)
        if len(hits) > 1:
            # 命中多套时**不许猜**（原先静默取第一套 → 可能把别的房源印成海报）
            return json.dumps({
                "success": False, "ambiguous": True,
                "candidates": [_prop_brief(h) for h in hits[:5]],
                "error": (f"标题「{title}」命中 {len(hits)} 套在售房源，请告诉我要出哪一套（把编号给我），"
                          f"我不替你挑 —— 海报印错了收不回来。"),
            }, ensure_ascii=False)
        p = hits[0] if hits else None
    if p is None:
        if property_id is not None:
            error, status_label = unavailable_property_note(
                property_id, db.get_property(property_id), action="出海报")
            payload = {"success": False, "error": error}
            if status_label:
                payload["property_status"] = status_label
            return json.dumps(payload, ensure_ascii=False)
        return json.dumps({"success": False, "error": (
            f"库里没有标题含「{title}」的在售房源，先把房源编号或完整标题给我。")}, ensure_ascii=False)
    property_id = p.get('id')            # 按标题找到的也要回填编号，经纪人才能核对

    card = _agent_card()
    photo = _property_photo(p)
    tpl, reason = _pick_template(p, template, photo)
    need_template = tpl is None
    if need_template and allow_missing:
        tpl, reason = "A", "未指定模板（经纪人同意先出图）→ 用 A 红金促销款"
        need_template = False
    if tpl is None:
        tpl = "A"          # 仅用于判断依赖（照片/楼层/朝向），真正的代号在拿到选择后才会用于渲染
    room_no_mode = _norm_room_no_mode(show_room_no)
    room_no_note = None
    if room_no_mode is None and allow_missing:
        # 没能问过房号档位时，落到**最不曝光**的那一档（口径：不能替他决定，就别默认印完整房号）
        room_no_mode = "unit"
        room_no_note = ("你没说海报上要不要写房号，我按「只写楼栋单元、不写具体房号」出的图；"
                        "要写完整房号跟我说一声，我重出一张。")
    from tools.real_estate_poster_svg import normalize_style

    style_notes: list = []
    style_summary = ""
    if tpl == "CUSTOM":
        style_norm = normalize_style(style, style_notes)
        style_summary = style_norm["summary"]
    else:
        style_norm = None
        if style:
            style_notes.append(f"这次的 style（参考图风格）没生效：模板 {tpl} 不用 style；"
                               f"想按参考图风格出图，请把 template 传 CUSTOM。")
    if tpl == "B" and not photo:
        if allow_missing:
            tpl, reason = "A", "无照片（经纪人同意先出图）→ 改为不需要照片的促销款"
        else:
            _need = (["模板：先用哪一款（" + TEMPLATE_CHOICES + "）"] if need_template else []) \
                + ["房源照片"] + _missing_poster_info(
                    p, card, need_photo=False, need_floor=True, need_orientation=True)
            return json.dumps({
                "success": False,
                "need_photo": True,
                "missing": _need,
                "ask": ("所选模板（B 极简高级款）需要这些信息，请**一次问清后再出图**："
                        + "；".join(_need) + "。若经纪人不想发照片，可改用 A 红金促销款，或说「先出图」我再生成。"),
                "templates_without_photo": ["A"],
            }, ensure_ascii=False)

    # B 款会显示「楼层 / 朝向」两栏 —— 缺了就先问清，不要等出图后再补问
    _need_extras = tpl == "B"
    if tpl == "CUSTOM" and style_norm:
        # 自定义款：显示哪些信息由 show_fields 决定 —— 显示楼层/朝向但库里没有时，也算缺信息
        _need_extras = ("floor" in style_norm["show_fields"] and not p.get("floor")) or \
                       ("orientation" in style_norm["show_fields"] and not p.get("orientation"))
    missing = (_missing_poster_info(p, card, need_photo=False,
                                    need_floor=("floor" in (style_norm or {}).get("show_fields", []) if tpl == "CUSTOM" else _need_extras),
                                    need_orientation=("orientation" in (style_norm or {}).get("show_fields", []) if tpl == "CUSTOM" else _need_extras),
                                    need_room_no=_template_shows_room_no(tpl), room_no_mode=room_no_mode)
               if not allow_missing else [])
    if need_template and not allow_missing:
        missing.insert(0, "模板：用哪一款（" + TEMPLATE_CHOICES + "）")
    cands = _title_candidates(p) if not poster_title else []
    if missing or cands:
        payload = {
            "success": False,
            "ask": ("海报还差这些，请一次问清后再出图（不要臆造、不要用占位符）："
                    + ("①缺信息：" + "、".join(missing) + "；" if missing else "")
                    + ("②主标题候选（发给经纪人挑，或他自己给文案）：" + " / ".join(cands) + "；" if cands else "")
                    + "拿齐后用 poster_title 传标题、必要时先 save_agent_card 存名片，再调用本工具。"
                      "经纪人若明确说「就这些，先出图」，带 allow_missing=true 再调一次。"),
        }
        if need_template:
            payload["need_template"] = True
            payload["templates"] = POSTER_TEMPLATES
        if missing:
            payload["need_info"] = True
            payload["missing"] = missing
        if cands:
            payload["need_title"] = True
            payload["candidates"] = cands
        return json.dumps(payload, ensure_ascii=False)

    qr_path = None
    qr_value = qr_content or card.get('wechat') or ''
    if qr_value:
        qr_path = _make_qr_png(qr_value)

    data = {
        "template": tpl,
        "style": style_norm,
        "room_no_mode": room_no_mode or "full",
        "title": poster_title,
        "subtitle": " · ".join(str(x) for x in [p.get('community'), p.get('district'),
                                               p.get('renovation')] if x),
        "properties": [p],
        "agent": card,
        "qr_path": qr_path,
        "photo_path": photo or None,
        "footer": _FOOTER_TEXT,
    }

    pid = p.get('id') if isinstance(p, dict) else property_id
    out_path = os.path.join(_poster_dir(), f'poster_{pid}_{tpl}.png')
    result = None
    try:
        from tools import real_estate_poster_svg

        result = real_estate_poster_svg.render(data, out_path)
    except Exception as exc:  # noqa: BLE001
        result = {"success": False, "error": f"SVG 引擎不可用：{exc}"}

    notes = []
    if room_no_note:
        notes.append(room_no_note)
    if not result.get("success"):
        # 回落旧 Pillow 引擎（保证任何服务器都能出图）
        notes.append(f"已回落到旧引擎（原因：{result.get('error')}）")
        try:
            path = _render_legacy({**p, "title": _poster_display_title(p, room_no_mode)},
                                  qr_content, tpl)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"success": False, "error": (
                f"海报没出成：本机的 SVG 渲染器与备用引擎都不能用（{exc}）。"
                f"装上 librsvg2-bin 再试，或跟我说一声我换一台机器出图。")}, ensure_ascii=False)
    else:
        path = result["png_path"]

    path = _stamp_and_prune(path)

    if not card.get('company'):
        notes.append("未提供公司名称，海报未显示品牌（不臆造）")
    notes.extend(style_notes)     # CUSTOM 的风格收敛说明 + "非 CUSTOM 传了 style" 这类提醒
    if tpl == "CUSTOM" and not photo:
        notes.append("这套房源还没有照片：已用色块代替大图位")
    return json.dumps({
        "success": True,
        "property_id": property_id,
        "template": tpl,
        "why": reason,
        "style_summary": style_summary,
        "poster_path": path,
        "notes": notes,
        "message": f"海报已生成（模板 {tpl}）：{path}（发送时用 MEDIA:{path} 直接发图）",
    }, ensure_ascii=False)


# 只认自己生成的海报成品 —— 海报目录里可能还放着别的文件，别碰。
# 三种结尾都要认：`.png`（成品）、`.svg`（老命名）、`.png.svg`（渲染引擎写的 SVG 源文件，名字 = 成品名 + .svg）
_POSTER_ARTIFACT_SUFFIXES = (".png.svg", ".png", ".svg")
_POSTER_ARTIFACT_RE = re.compile(
    r"^poster_(?:(?P<pid>\d+)_[A-Za-z]+|(?P<grid>grid))(?:_[0-9a-f]{8})?(?:\.png\.svg|\.png|\.svg)$")
# 每套房各留 5 份成品（跨模板共享这 5 个名额）；九宫格一图多套，单独算一个桶，同样 5 份
_POSTER_KEEP_PER_PROPERTY = 5
_POSTER_KEEP = _POSTER_KEEP_PER_PROPERTY      # 兼容旧名（有测试/脚本按这个名字读）


def _stamp_and_prune(path, keep=_POSTER_KEEP_PER_PROPERTY):
    """给成品名加上内容指纹（同一房源同模板改标题重出图不再复用同一个文件名），并**每套房只留最近 keep 份**

    为什么两件事要一起做：只加指纹会**多留文件**，而图片目录原先没有任何清理 ——
    治了"发送端可能按路径缓存旧图"，却堆出一个磁盘隐患。
    只清理**我们自己生成的海报**（同目录里别的东西，比如二维码、房源照片，绝不能碰）。
    """
    try:
        with open(path, 'rb') as fh:
            digest = hashlib.sha1(fh.read()).hexdigest()[:8]
        base, ext = os.path.splitext(path)
        stamped = base if base.endswith("_" + digest) else f"{base}_{digest}{ext}"
        if stamped != path:
            os.replace(path, stamped)
            svg_old, svg_new = path + ".svg", stamped + ".svg"
            if os.path.exists(svg_old):          # SVG 源文件同样改名，保持 poster_path+".svg" 的老约定
                os.replace(svg_old, svg_new)
        path = stamped
    except Exception:      # noqa: BLE001 —— 改名失败不影响出图，代价只是文件名不带指纹
        pass

    try:
        buckets = {}          # 房源编号（九宫格是 "grid"）→ {成品基名: [成品文件, 它的 .svg]}
        for f in glob.glob(os.path.join(_poster_dir(), "poster_*")):
            name = os.path.basename(f)
            matched = _POSTER_ARTIFACT_RE.match(name)
            if not matched:
                continue
            bucket = matched.group("pid") or "grid"
            base = name
            for suffix in _POSTER_ARTIFACT_SUFFIXES:      # 剥掉后缀（含指纹），PNG 与它的 .svg 归成同一组
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
                    break
            buckets.setdefault(bucket, {}).setdefault(base, []).append(f)
        for groups in buckets.values():
            # 按「成品（.png）自己的时间」定新旧 —— 别用组内最大时间：伴生 .svg 的 mtime 一旦异常
            # （比如被别的工具 touch 过），那一组就会显得"永远最新"，永远轮不到清理（2026-09-27 实测踩到）
            def _newest_png(fs):
                pngs = [f for f in fs if f.endswith(".png")]
                return max(os.path.getmtime(f) for f in (pngs or fs))
            ordered = sorted(groups.values(), key=_newest_png, reverse=True)
            for group in ordered[keep:]:
                for f in group:
                    try:
                        os.remove(f)
                    except OSError:
                        pass
    except Exception:      # noqa: BLE001 —— 清理失败不影响出图
        pass
    return path


def _make_qr_png(content: str):
    """生成二维码 PNG（内容 = 经纪人微信名片）"""
    try:
        from tools import real_estate_poster_svg

        return real_estate_poster_svg.make_qr_png(
            content, os.path.join(_poster_dir(), 'poster_qr.png'))
    except Exception:
        return None


def _render_legacy(p, qr_content, tpl: str) -> str:
    """旧 Pillow 引擎兜底（rsvg 缺失/渲染失败时使用）"""
    from PIL import Image, ImageDraw

    template = {"A": "vibrant", "B": "modern"}.get(tpl, "modern")
    W, H = 1080, 1440
    img = Image.new('RGB', (W, H), (240, 244, 250))
    draw = ImageDraw.Draw(img)
    if template == 'vibrant':
        _draw_vibrant(img, draw, p, qr_content)
    else:
        _draw_modern(img, draw, p, qr_content)
    pid = p.get('id') if isinstance(p, dict) else None
    path = os.path.join(_poster_dir(), f'poster_{pid}_{template}.png')
    img.save(path)
    return path


def suggest_poster_titles(property_id: int = None, title: str = None, task_id: str = None) -> str:
    """给经纪人挑的海报主标题候选（2~3 个，**优先用房源里真有的卖点**）"""
    if property_id is not None:
        property_id, problem = norm_id(property_id, '房源编号')
        if problem:
            return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    db = _get_db()
    p = db.get_available_property(property_id) if property_id is not None else None
    if p is None and title:
        hits = db.find_available_property_by_title(title)
        if len(hits) > 1:
            # 命中多套时**不许猜**（与海报同一条口径：起错标题要重做海报）
            return json.dumps({
                "success": False, "ambiguous": True,
                "properties": [_prop_brief(h) for h in hits[:5]],
                "error": (f"标题「{title}」命中 {len(hits)} 套在售房源，请告诉我要给哪一套起标题（把编号给我），"
                          f"我不替你挑。"),
            }, ensure_ascii=False)
        p = hits[0] if hits else None
    if p is None:
        if property_id is not None:
            error, status_label = unavailable_property_note(
                property_id, db.get_property(property_id), action="给海报起标题")
            payload = {"success": False, "error": error}
            if status_label:
                payload["property_status"] = status_label
            return json.dumps(payload, ensure_ascii=False)
        if title:
            return json.dumps({"success": False, "error": (
                f"库里没有标题含「{title}」的在售房源，先把房源编号或完整标题给我。")}, ensure_ascii=False)
        return json.dumps({"success": False, "error": (
            "请给房源编号或标题（编号最准）：我先定位到唯一那套房源，再给你 2~3 个海报主标题候选。")},
            ensure_ascii=False)
    items = _title_candidate_items(p)
    return json.dumps({
        "success": True,
        "property_id": p.get('id'),
        "candidates": [c["title"] for c in items],
        "candidates_detail": items,
        "ask": "把候选标题发给经纪人挑一个（他也可以自己给文案），选定后用 poster_title 传给 generate_property_poster。",
    }, ensure_ascii=False)


# 九宫格（旧版 3x3 拼图，保留兼容；2026-09-26 收口口径与形状，**版式原样保留**）
_GRID_TITLE_SIZES = (38, 34, 30, 26, 22)


def _fit_cell_title(draw, text, max_width):
    """格子里的小区/房号标题：先**自动缩字号**（38→22），仍放不下才截断，且**保尾部**

    为什么保尾部：unit/full 档要显示的信息（楼栋/单元/房号）都在标题尾部 —— 实测
    `格子小区 3号楼1602 急售` 从尾部截断后成了 `格子小区 3号楼…`，经纪人特意选的"完整房号"反而看不到。
    """
    for size in _GRID_TITLE_SIZES:
        font = _load_font(size)
        if draw.textlength(text, font=font) <= max_width:
            return text, font
    font = _load_font(_GRID_TITLE_SIZES[-1])
    while text and draw.textlength('…' + text, font=font) > max_width:
        text = text[1:]
    return '…' + text, font


def generate_poster_grid(property_ids: str, show_room_no: str = "unit",
                         task_id: str = None) -> str:
    """生成朋友圈九宫格大图（3x3 拼图，最多 9 套房源）

    property_ids: 房源编号列表 —— **数组与逗号串都认**（`[1,2,3]` 或 `"1,2,3"`），最多 9 个；
                  重复的编号每套只画一格（并在回执里说明）。
    show_room_no: full / unit / none —— 每格标题里的房号显示方式，**默认 unit**
    （批量图默认不逐个曝光具体房号；经纪人要显示完整房号时显式传 full）。
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return json.dumps({"success": False, "error": "缺少 Pillow 依赖，请执行 pip install Pillow"}, ensure_ascii=False)

    db = _get_db()
    # 编号形态（2026-09-25）：schema 声明 property_ids 是数组，历史实现却按逗号串 `.split(',')` ——
    # 模型照 schema 传数组时直接崩（AttributeError），只有传字符串才跑得通。两种写法都认，
    # 每个编号过 norm_id（认不出的给中文提示，而不是"房源不存在"）。
    raw_ids = (property_ids if isinstance(property_ids, (list, tuple))
               else str(property_ids).split(','))
    ids = []
    for item in raw_ids:
        if isinstance(item, str) and not item.strip():
            continue
        value, problem = norm_id(item, '房源编号')
        if problem:
            return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
        ids.append(value)
    if not ids:
        return json.dumps({"success": False, "error": "请提供房源ID列表（逗号分隔，最多9个）"}, ensure_ascii=False)

    # 房号档位：认不出就**不许静默按默认出图**（原先 `or "unit"` 把乱值悄悄当成 unit）
    mode = _norm_room_no_mode(show_room_no)
    if mode is None:
        if show_room_no in (None, ""):
            mode = "unit"                      # 没给 = 用默认档（默认 unit 是文档承诺的行为）
        else:
            return json.dumps({"success": False, "error": (
                f"房号写法没能识别：你说的是「{show_room_no}」。可以这样说：full 写完整房号（如 7号楼2单元1602）/ "
                f"unit 只写楼栋单元（如 7号楼2单元）（这是默认）/ none 不写房号、只显示小区名。")},
                ensure_ascii=False)

    warnings = []
    deduped = []
    for pid in ids:
        if pid not in deduped:
            deduped.append(pid)
    if len(deduped) != len(ids):
        dup = sorted({p for p in ids if ids.count(p) > 1})
        warnings.append(f"编号 {'、'.join(str(x) for x in dup)} 给了两次，每套只画一格。")

    total = len(deduped)
    ids = deduped[:9]                          # 九宫格最多 9 格
    dropped = deduped[9:]
    if dropped:
        warnings.append(f"九宫格最多 9 格，这次只画了前 9 套；没画上的："
                        f"{'、'.join(str(x) for x in dropped)}（要这些的话，分开再出一张就行）。")

    by_id = {i: q for i in ids if (q := db.get_available_property(i)) is not None}
    unavailable = []
    for pid in [i for i in ids if i not in by_id]:
        text, label = unavailable_property_note(pid, db.get_property(pid), action="做九宫格")
        unavailable.append({"property_id": pid, "status": label or "不存在", "error": text})
    if unavailable:
        # 不存在 / 已售 / 已租 分开说（原先一律「房源不存在或不在售：[…]」）
        return json.dumps({
            "success": False,
            "error": "；".join(x["error"] for x in unavailable),
            "unavailable": unavailable,
        }, ensure_ascii=False)

    cell, gap = 360, 0
    grid = Image.new('RGB', (cell * 3, cell * 3), (240, 244, 250))
    draw = ImageDraw.Draw(grid)
    f_price = _load_font(44)
    f_area = _load_font(30)

    for idx, pid in enumerate(ids):
        p = by_id[pid]
        cx, cy = (idx % 3) * cell, (idx // 3) * cell
        c1, c2 = _type_colors(p.get('property_type'))
        card = _gradient((cell, cell), c1, c2)
        d = ImageDraw.Draw(card)
        title_text, f_title = _fit_cell_title(d, _poster_display_title(p, mode), cell - 40)
        d.text((20, 20), title_text, font=f_title, fill=(255, 255, 255))
        d.text((20, 130), _fmt_price(p), font=f_price, fill=(255, 255, 255))
        area = f"{_fmt_area(p.get('area'))}㎡" if p.get('area') else ''
        d.text((20, 240), area, font=f_area, fill=(220, 230, 245))
        grid.paste(card, (cx, cy))

    path = os.path.join(_poster_dir(), 'poster_grid.png')
    grid.save(path)
    path = _stamp_and_prune(path)
    payload = {
        "success": True,
        "property_ids": ids,
        "count": len(ids),
        "total": total,
        "truncated": bool(dropped),
        "grid_path": path,
        "message": (f"九宫格已生成（{len(ids)} 格）" + ("；" + "；".join(warnings) if warnings else "")
                    + f"：{path}（发送时用 MEDIA:{path} 直接发图）"),
    }
    if warnings:
        payload["warnings"] = warnings
    return json.dumps(payload, ensure_ascii=False)


registry.register(
    name="generate_property_poster",
    toolset="real_estate",
    schema={"name": "generate_property_poster", "description": "生成房源海报图（1080x1920）。**出图前必须先把信息问齐**（模板选哪款、房号要不要写、B 款还需要照片+楼层+朝向）：B 极简高级款需要 照片+楼层+朝向，缺任一项都会拒绝出图并返回 missing 清单（一次问清，不要先出图再补问）；楼层若标题/地址带房号会自动推断。未提供主标题会返回 2~3 个候选让经纪人挑。模板 A 红金促销/B 极简高级(需照片)，不传自动选。返回图片路径，用 MEDIA:路径 发送", "parameters": {
        "type": "object",
        "properties": {
            "property_id": {"type": "integer", "description": "房源ID（与 title 二选一，优先用 ID）"},
            "title": {"type": "string", "description": "房源标题关键词（与 property_id 二选一，模糊匹配）"},
            "poster_title": {"type": "string", "description": "海报主标题文案（先用 suggest_poster_titles 拿候选给经纪人挑）"},
            "template": {"type": "string", "enum": ["A", "B", "CUSTOM", "auto"], "description": "A 红金促销（不需要照片）/B 极简高级（需要照片）/CUSTOM 参考图风格（经纪人发了参考海报图，用 style 传提取到的风格）/auto（经纪人让你看着办时用，按房源特征自动挑）。**不传则不出图，会返回模板库清单让你先问经纪人选哪款**"},
              "style": {"type": "object", "description": "template=CUSTOM 时必填：从经纪人发的参考海报图里提取到的风格（拿不准就别填，系统会用默认）", "properties": {
                  "layout": {"type": "string", "enum": ["hero_top", "minimal", "split"], "description": "版式：hero_top 大图在上信息在下 / minimal 极简留白 / split 左右分栏"},
                  "palette": {"type": "string", "enum": ["red_gold", "black_gold", "cream", "navy", "green", "orange", "pink", "grey"], "description": "配色：红金/黑金/米白/藏蓝/墨绿/橙红/粉紫/灰白（也可传自定义色值（bg 底色 / accent 主色，形如 #RRGGBB））"},
                  "font_style": {"type": "string", "enum": ["serif", "sans"], "description": "字体气质：serif 衬线（稳重高级）/ sans 黑体（醒目促销）"},
                  "show_fields": {"type": "array", "items": {"type": "string", "enum": ["price", "unit_price", "area", "layout", "floor", "orientation", "tags", "community"]}, "description": "海报上显示哪些信息（参考图信息少就少显示）"},
                  "decor": {"type": "string", "enum": ["rounded_soft", "sharp", "bordered"], "description": "装饰：圆角柔和 / 直角硬朗 / 描边款"}}},
              "show_room_no": {"type": "string", "enum": ["full", "unit", "none"], "description": "海报上要不要写房号：full 写完整（如 7号楼2单元1602）/unit 只写楼栋单元（如 7号楼2单元）/none 不写、只显示小区名。**不传会并入待问清单先问经纪人**——不同经纪人对房号曝光的诉求不同，不要替他决定"},
            "qr_content": {"type": "string", "description": "可选：二维码内容；不传则用经纪人名片里的微信号（微信名片）"},
            "allow_missing": {"type": "boolean", "description": "仅当经纪人明确说「就这些，先出图」时传 true；缺的字段留空，不编造"},
        },
    }},
    handler=lambda args, **kw: generate_property_poster(**args),
)

registry.register(
    name="suggest_poster_titles",
    toolset="real_estate",
    schema={"name": "suggest_poster_titles", "description": "给房产海报出 2~3 个主标题候选：**优先用房源里真有的卖点**（标签、装修、朝向、已填的看房方式、近 7 天新上），不够再用中性营销词补足；**不写「业主诚售/仅此一套」这类房源里查不到的断言**。每个候选在 candidates_detail 里带 why（依据），可用来向经纪人解释。把候选发给经纪人挑（他也可以自己给文案），选定后用 poster_title 传给 generate_property_poster", "parameters": {
        "type": "object",
        "properties": {
            "property_id": {"type": "integer", "description": "房源编号（与 title 二选一，编号最准）"},
            "title": {"type": "string", "description": "房源标题关键词（与 property_id 二选一；命中多套会让经纪人确认）"},
        },
    }},
    handler=lambda args, **kw: suggest_poster_titles(**args),
)

registry.register(
    name="generate_poster_grid",
    toolset="real_estate",
    schema={"name": "generate_poster_grid", "description": "生成朋友圈九宫格大图（3x3 拼图，最多 9 套房源；超过 9 套只画前 9 套并说明没画上哪些）。每格显示 小区/房号 + 价格 + 面积，房号写法由 show_room_no 决定（默认只到楼栋单元，不逐个曝光具体房号）。返回图片路径，发消息时用 MEDIA:路径 发送图片", "parameters": {
        "type": "object",
        "properties": {
            "property_ids": {"type": "string", "description": "房源编号列表，最多 9 个（如 1,2,3,4,5,6,7,8,9；数组写法也认）"},
              "show_room_no": {"type": "string", "enum": ["full", "unit", "none"], "description": "每格标题里的房号写法：full 写完整房号（如 7号楼2单元1602）/ unit 只写楼栋单元（如 7号楼2单元，默认）/ none 不写房号、只显示小区名"},
        },
        "required": ["property_ids"],
    }},
    handler=lambda args, **kw: generate_poster_grid(**args),
)
