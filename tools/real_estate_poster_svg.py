"""
Coco 房产海报 - SVG 渲染引擎（2026-09-19 新增，v2 版式）

为什么：Pillow 画不出渐变字、描边、投影这类"海报感"元素；改用 SVG 描述版式 +
rsvg-convert 渲染，设计自由度接近网页，依赖却很轻（librsvg 约 7MB）。

设计约束：
- 画幅 1080x1920
- 海报上**只显示经纪人自己的公司/门店名**，绝不出现"Coco"字样；公司名没拿到就不显示品牌
- 页脚固定一句「房源信息以实际看房为准」
- 二维码内容 = 经纪人微信名片
- 两款模板：A 红金促销（有照片/无照片两版）、B 极简高级（需照片）
  （2026-09-19 评审：B 款效果好；C 清单款做的差，已删除，待以后重新设计）

版式原则（v2 修版）：竖直方向按"块"排布 —— 先算每块高度，再把剩余空间**均分到块间间距**，
保证既不重叠也不出现大空洞；所有文本按列宽量宽后自适应字号/截断。
"""
from __future__ import annotations

import base64
import re
import html
import os
import shutil
import subprocess
import tempfile

from agent.real_estate_money import fmt_price
from tools.real_estate_property import _fmt_area

W, H = 1080, 1920
MARGIN = 70                      # 左右安全边
CONTENT_W = W - MARGIN * 2       # 940

FONTS = {
    "title_heavy": ["Alibaba PuHuiTi Heavy", "Noto Sans CJK SC"],      # 大标题/数字
    "title_promo": ["Alibaba PuHuiTi Heavy", "YouSheBiaoTiHei", "Noto Sans CJK SC"],
    "title_serif": ["Noto Serif CJK SC", "zcool-gdh", "Noto Sans CJK SC"],  # 中式庄重
    "title_list": ["Smiley Sans", "PangMenZhengDao", "Noto Sans CJK SC"],    # 动感
    "number": ["Alibaba PuHuiTi Heavy", "Noto Sans CJK SC"],
    "body": ["Noto Sans CJK SC", "MiSans"],
}
_MEASURE_FILES = {
    "title_heavy": ["/usr/local/share/fonts/coco/Alibaba-PuHuiTi-Heavy.ttf",
                    "/usr/local/share/fonts/coco/NotoSansCJKsc-Black.otf"],
    "body": ["/usr/local/share/fonts/coco/NotoSansCJKsc-Regular.otf",
             "/usr/local/share/fonts/coco/Alibaba-PuHuiTi-Bold.ttf"],
}


_ROOM_TAIL_RE = re.compile(r"[\s,，、\-]*(\d{3,4})(?:\s*(?:室|号|房))?\s*$")


def _room_no_match(text):
    """在**原文**里定位房号：与房源判重共用同一套正则与防护表（面积/价格/楼层不会被误抹）

    `agent/real_estate_db` 的 `parse_property_identity` 是同一套规则，但它把文本归一化过、
    只回值不回位置；这里要按位置删字，所以用同一批正则/防护表在原文上扫。
    """
    from agent.real_estate_db import (_ROOM_AFTER_MARKER_RE, _ROOM_PREFIX_BLOCK, _ROOM_RE,
                                      _ROOM_TAIL_BLOCK)

    m = _ROOM_AFTER_MARKER_RE.search(text)
    if m:
        return m
    for m in _ROOM_RE.finditer(text):
        after = text[m.end():].lstrip()[:1]
        if after and (after in _ROOM_TAIL_BLOCK or after in ".．"):
            continue                       # 后面跟着 平/㎡/万/元/层… 或小数点 → 是面积/价格
        if any(k in text[max(0, m.start() - 3):m.start()] for k in _ROOM_PREFIX_BLOCK):
            continue                       # 前面是 租/价/费/面积 → 不是房号
        return m
    return None


def strip_room_no(text) -> str:
    """去掉房号（**任意位置**，不只是末尾）：「海阔天空7号楼2单元1602 急售」→「海阔天空7号楼2单元 急售」。

    2026-09-26 修：原先只去**末尾**房号，实测「房号在中段/后面还有字」的三种写法
    （`…1602 急售`、`1602 南北通透三居`、`1602室 急售`）都能照印到海报上 —— unit/none 档形同虚设。
    """
    s = str(text or "").strip()
    if not s:
        return ""
    m = _room_no_match(s)
    if m is None:
        return s
    start, end = m.start(1), m.end(1)              # 只吃房号本身，别把「单元/号楼」一起吃掉
    if end < len(s) and s[end] in "室房号":        # 连「室/房」一起吃掉
        end += 1
    head, tail = s[:start].strip(), s[end:].strip()
    if head and tail:
        return re.sub(r"\s{2,}", " ", head + " " + tail)
    return head or tail


def mask_room_no(text, mode="full", community=None) -> str:
    """按房号档位掩码一段文字（**主标题与房源标识行共用这一处**）

    - `full`：原样返回；
    - `unit`：去掉房号、保留楼栋/单元与其余文字；**掩完若连楼栋/单元都不剩**，退回小区名
      （例如「1602室 急售」→ 没有定位信息 → 用小区名，绝不退回原串）；
    - `none`：只留小区名（没有小区名时用掩码后的文字）。

    `community`：调用方拿到的小区名（房源字段），优先于从文字里解析出来的。
    """
    from agent.real_estate_db import parse_property_identity

    s = str(text or "").strip()
    mode = str(mode or "full").lower()
    if mode in ("", "full") or not s:
        return s
    if _room_no_match(s) is None:
        return s                                   # 本来就没房号 → 原样（别丢经纪人自己写的字）
    masked = strip_room_no(s)
    if mode in ("none", "no", "hide"):
        return community or parse_property_identity(s).get("community") or masked
    ident = parse_property_identity(masked)
    if not (ident.get("building") or ident.get("unit")):
        # 掩完没有楼栋/单元信息了 → 说明原本只有房号，别把光秃秃的尾巴当标题
        return community or parse_property_identity(s).get("community") or masked
    return masked


def _code_line_for(d, p) -> str:
    """按 room_no_mode 决定海报上那一行房源标识怎么显示。

    full（默认）: 完整，如「7号楼2单元1602」
    unit        : 只到楼栋/单元，如「7号楼2单元」
    none        : 只显示小区名，如「海阔天空」（没有小区名时才退回掩码后的标题）
    """
    mode = str(d.get("room_no_mode") or "full").lower()
    raw = d.get("code_line") or _identity_line(p) or p.get("title") or p.get("community") or "房源"
    return mask_room_no(raw, mode, community=p.get("community")) or "房源"


def _identity_line(p) -> str:
    """房源标识行：小区 + 楼栋/单元/房号（由身份要素拼）。

    不直接印整条标题：标题里带着面积/户型/价格（改价后旧价会跟着上海报），而且太长会被截断。
    拼不出楼栋/房号时只给小区名 —— 认不出来的不编。
    """
    from agent.real_estate_db import parse_property_identity

    ident = parse_property_identity(p.get("title") or p.get("address") or "")
    community = p.get("community") or ident.get("community") or ""
    seg = "".join([f"{ident['building']}号楼" if ident.get("building") else "",
                   f"{ident['unit']}单元" if ident.get("unit") else "",
                   str(ident["room"]) if ident.get("room") else ""])
    return " ".join(x for x in (community, seg) if x)


def _esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def _measure(text: str, size: int, role: str = "body") -> float:
    try:
        from PIL import ImageFont

        for path in _MEASURE_FILES.get(role, []) + _MEASURE_FILES["body"]:
            if os.path.exists(path):
                return float(ImageFont.truetype(path, size).getlength(text))
    except Exception:
        pass
    return sum(size if ord(ch) > 0x2E80 else size * 0.55 for ch in text)


def _fit(text: str, max_width: float, size: int, role: str = "body", tail: str = "…") -> str:
    text = str(text if text is not None else "")
    if _measure(text, size, role) <= max_width:
        return text
    out = ""
    for ch in text:
        if _measure(out + ch + tail, size, role) > max_width:
            break
        out += ch
    return out + tail


def _auto_size(text: str, max_width: float, base: int, role: str = "body", floor: int = 28) -> int:
    """自适应字号：尽量用 base，放不下就逐级缩小到 floor"""
    size = base
    while size > floor and _measure(str(text), size, role) > max_width:
        size -= 2
    return size


def _stack(blocks: list, top: float, bottom: float, weights: list | None = None) -> list:
    """竖直排布：blocks=[(height, render_fn(y)) ...]；剩余空间按权重均分到块间间距。
    返回 [render_fn(y), ...] 的新 y 列表（调用方负责把渲染结果拼起来）。"""
    heights = [b[0] for b in blocks]
    n_gaps = max(len(blocks) - 1, 1)
    slack = (bottom - top) - sum(heights)
    weights = weights or [1.0] * n_gaps
    unit = max(slack, 0) / sum(weights) if slack > 0 else 0
    ys, y = [], top
    for i, h in enumerate(heights):
        ys.append(y)
        y += h
        if i < len(heights) - 1:
            y += unit * (weights[i] if i < len(weights) else weights[-1])
    return ys


def _fit_mode(d: dict, has_image: bool) -> str:
    """图片适配：照片默认裁切铺满（slice）；显式指定或户型图用完整显示（meet）"""
    mode = str(d.get("image_fit") or "").lower()
    if mode in ("meet", "slice"):
        return mode
    return "slice" if has_image else "meet"


def _image_size(path: str | None):
    """读取图片宽高（失败返回 (0, 0)）"""
    if not path or not os.path.exists(path):
        return (0, 0)
    try:
        from PIL import Image

        with Image.open(path) as im:
            return im.size
    except Exception:
        return (0, 0)


def _panel_size(box_w: float, box_h: float, path: str | None, fit: str):
    """meet 模式：按图片宽高比收缩面板，避免大块白边（户型图多为竖图）"""
    if fit != "meet":
        return box_w, box_h
    iw, ih = _image_size(path)
    if not iw or not ih:
        return box_w, box_h
    aspect = iw / ih
    if aspect < box_w / box_h:
        h = box_h
        w = max(h * aspect, 360.0)
    else:
        w = box_w
        h = max(w / aspect, 360.0)
    return min(w, box_w), min(h, box_h)


def _img_href(path: str | None) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        ext = os.path.splitext(path)[1].lower().lstrip(".") or "png"
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "png")
        with open(path, "rb") as fh:
            return "data:image/%s;base64,%s" % (mime, base64.b64encode(fh.read()).decode())
    except Exception:
        return "file://" + os.path.abspath(path)


def make_qr_png(content: str, out_path: str) -> str | None:
    """生成二维码 PNG（内容 = 经纪人微信名片）"""
    try:
        import qrcode

        qrcode.make(content).convert("RGB").resize((420, 420)).save(out_path)
        return out_path
    except Exception:
        return None


def _defs(gold=("#F7E3A1", "#D9A93C"), red=("#E03A3A", "#A81018")) -> str:
    return (
        '<defs>'
        '<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%%" stop-color="%s"/><stop offset="100%%" stop-color="%s"/></linearGradient>'
        '<linearGradient id="gold" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%%" stop-color="%s"/><stop offset="52%%" stop-color="%s"/>'
        '<stop offset="100%%" stop-color="#B8862B"/></linearGradient>'
        '<linearGradient id="photoFade" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="45%%" stop-color="#000000" stop-opacity="0"/>'
        '<stop offset="100%%" stop-color="#000000" stop-opacity="0.82"/></linearGradient>'
        '<linearGradient id="topScrim" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%%" stop-color="#000000" stop-opacity="0.55"/>'
        '<stop offset="100%%" stop-color="#000000" stop-opacity="0"/></linearGradient>'
        '<filter id="soft" x="-25%%" y="-25%%" width="150%%" height="150%%">'
        '<feGaussianBlur in="SourceAlpha" stdDeviation="9" result="b"/>'
        '<feOffset in="b" dx="0" dy="7" result="o"/>'
        '<feComponentTransfer in="o" result="s"><feFuncA type="linear" slope="0.3"/></feComponentTransfer>'
        '<feMerge><feMergeNode in="s"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
        '</defs>'
    ) % (red[0], red[1], gold[0], gold[1])


def _brand(company: str, slogan: str = "", color: str = "#FFE9AE", rule: str = "#FFD98A") -> str:
    """顶部品牌栏：只写经纪人公司名；没有公司名就整条不画（绝不写 Coco）"""
    if not company:
        return ""
    out = ('<text x="%d" y="96" font-family="%s" font-weight="700" font-size="42" fill="%s" '
           'letter-spacing="3">%s</text>'
           % (MARGIN, _esc(FONTS["title_heavy"][0]), color, _esc(company)))
    if slogan:
        out += ('<text x="%d" y="94" text-anchor="end" font-family="%s" font-size="30" fill="%s" '
                'opacity="0.9">%s</text>'
                % (W - MARGIN, _esc(FONTS["body"][0]), color, _esc(slogan)))
    out += ('<line x1="%d" y1="124" x2="%d" y2="124" stroke="%s" stroke-width="2" opacity="0.5"/>'
            % (MARGIN, W - MARGIN, rule))
    return out


def _capsules(items: list, y: float, gold: str = "#FFE9AE", fg: str = "#8E1B20", size: int = 34,
              max_width: float = CONTENT_W) -> str:
    items = [str(i) for i in (items or []) if str(i).strip()][:4]
    if not items:
        return ""
    gap = 20
    widths, used = [], 0.0
    for t in items:
        w = min(max(_measure(t, size, "body") + 60, 190), 420)
        if used + w + gap > max_width:
            break
        widths.append(w)
        used += w + gap
    if not widths:
        return ""
    total = sum(widths) + gap * (len(widths) - 1)
    x = (W - total) / 2
    out = []
    for text, w in zip(items, widths):
        out.append('<rect x="%.0f" y="%.0f" width="%.0f" height="74" rx="37" fill="%s" filter="url(#soft)"/>'
                   '<text x="%.0f" y="%.0f" text-anchor="middle" font-family="%s" font-size="%d" fill="%s">%s</text>'
                   % (x, y, w, gold, x + w / 2, y + 50, _esc(FONTS["body"][0]), size, fg,
                      _esc(_fit(text, w - 40, size))))
        x += w + gap
    return "".join(out)


def _agent_dark(agent: dict, qr_path: str | None, y: float, height: float = 252) -> str:
    """深色底上的名片区：左二维码 + 右姓名/电话/微信/公司"""
    agent = agent or {}
    parts = []
    has_qr = bool(qr_path and os.path.exists(qr_path))
    qsize = 210
    if has_qr:
        parts.append('<rect x="%d" y="%.0f" width="246" height="246" rx="18" fill="#FFFFFF" '
                     'filter="url(#soft)"/>' % (MARGIN, y))
        parts.append('<image href="%s" x="%d" y="%.0f" width="%d" height="%d"/>'
                     % (_img_href(qr_path), MARGIN + 18, y + 18, qsize, qsize))
    tx = MARGIN + 292 if has_qr else MARGIN
    out = ["<g>"] + parts
    if has_qr:
        out.append('<text x="%d" y="%.0f" font-family="%s" font-weight="700" font-size="42" '
                   'fill="#FFE9AE">扫码加我微信</text>' % (tx, y + 52, _esc(FONTS["body"][0])))
    name_line = " · ".join(str(x) for x in [agent.get("name"), agent.get("phone")] if x)
    if name_line:
        out.append('<text x="%d" y="%.0f" font-family="%s" font-weight="700" font-size="44" '
                   'fill="#FFFFFF">%s</text>'
                   % (tx, y + (128 if has_qr else 56), _esc(FONTS["body"][0]),
                      _esc(_fit(name_line, W - MARGIN - tx, 44))))
    row_y = y + (196 if has_qr else 124)
    if agent.get("wechat"):
        out.append('<text x="%d" y="%.0f" font-family="%s" font-size="34" fill="#FFFFFF" opacity="0.92">微信 %s</text>'
                   % (tx, row_y, _esc(FONTS["body"][0]), _esc(agent["wechat"])))
        row_y += 44
    if agent.get("company") and len(str(agent["company"])) > 0:
        # 公司名单独一行，允许更长（最多两行）
        avail = W - MARGIN - tx
        text = str(agent["company"])
        first = _fit(text, avail, 32)
        out.append('<text x="%d" y="%.0f" font-family="%s" font-size="32" fill="#FFE9AE" opacity="0.95">%s</text>'
                   % (tx, row_y, _esc(FONTS["body"][0]), _esc(first)))
        if first.endswith("…"):
            rest = text[len(first) - 1:]
            out.append('<text x="%d" y="%.0f" font-family="%s" font-size="32" fill="#FFE9AE" opacity="0.95">%s</text>'
                       % (tx, row_y + 42, _esc(FONTS["body"][0]), _esc(_fit(rest, avail, 32))))
    out.append("</g>")
    return "".join(out)


def _footer(text: str = "房源信息以实际看房为准", color: str = "#FFFFFF", opacity: float = 0.72,
            y: float = 1878) -> str:
    return ('<text x="%d" y="%.0f" text-anchor="middle" font-family="%s" font-size="28" fill="%s" '
            'opacity="%s">%s</text>'
            % (W // 2, y, _esc(FONTS["body"][0]), color, opacity, _esc(text)))


def _area_text(p: dict) -> str:
    """图内面积：整数不带 `.0`（90㎡）、有小数才带（95.5㎡）—— 与房源详情/文案同一口径（F289 同族）"""
    area = p.get("area")
    return f"{_fmt_area(area)}㎡" if area else ""


def _price_text(p: dict) -> str:
    """海报大字价格：复用共用实现（一位小数），near_int 让 29.96万 这类零头直接说 30万"""
    return fmt_price(p, digits=1, empty="价格待定", near_int=True)


def _unit_price_text(p: dict, prefix: str = "单价 ") -> str:
    """海报大字单价**刻意**用"万/㎡"（≥1万时）：画面上"1.88万/㎡"比"18750.00元/㎡"短一半，
    与列表/文案里的"元/㎡"（`fmt_unit_price`）不是同一语境，别硬套共用实现。"""
    up = p.get("unit_price")
    if not up:
        return ""
    try:
        up = float(up)
    except (TypeError, ValueError):
        return ""
    if up >= 10000:
        return prefix + "%.2f万/㎡" % (up / 10000)
    # 出租房算出来的是"每平米月租"，大字也不能漏 /月（F290/F296 同族）
    return prefix + "%.0f元/㎡%s" % (up, "/月" if p.get("property_type") == "rental" else "")


def _layout_text(p: dict) -> str:
    return "".join(x for x in [
        ("%s室" % p.get("rooms")) if p.get("rooms") else "",
        ("%s厅" % p.get("halls")) if p.get("halls") else "",
        ("%s卫" % p.get("bathrooms")) if p.get("bathrooms") else "",
    ] if x)


def _tags_of(d: dict, p: dict | None = None) -> list:
    tags = (p or {}).get("tags") or d.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in tags.replace("，", ",").replace("、", ",").split(",")]
    return [str(t).strip() for t in tags if str(t).strip()]


# ---------------- 模板 A：红金促销（单套，有照片/无照片） ----------------
def template_a(d: dict) -> str:
    p = d["properties"][0]
    agent = d.get("agent") or {}
    title = d.get("title") or "今日主推"
    photo = d.get("photo_path")
    has_photo = bool(photo and os.path.exists(photo))
    sub = d.get("subtitle") or " · ".join(
        str(x) for x in [p.get("community"), p.get("district"), p.get("renovation")] if x)
    code_line = _code_line_for(d, p)
    # 兜底：主标题里若被塞了房号，也按同一档位掩掉（**不许回退原串** —— 那等于没掩）
    title = mask_room_no(title, d.get("room_no_mode"), community=p.get("community")) or title
    tags = _tags_of(d, p)
    cards = [("建面", _area_text(p)),
             ("户型", _layout_text(p)),
             ("装修", p.get("renovation") or "")]
    cards = [c for c in cards if c[1]]

    top = 168
    title_h, sub_h = 196, 84
    # 价格字号按列宽定，卡片高度必须容得下那个大字 —— 否则字会压出卡片、盖到背景上
    divider_x = 660
    left_w = divider_x - MARGIN - 60
    ptext = _price_text(p)
    up_text = _unit_price_text(p)
    psize = _auto_size(ptext, left_w, 150, "title_heavy", 76)
    price_h = 214 + psize * 1.05 + (62 if up_text else 0) + 44
    card_h = max(94 + 60 + max(len(cards), 1) * 122, price_h)
    caps_h = 74 if tags else 0
    agent_h = 252
    foot_h = 40
    gaps = [1.15, 1.0, 0.55, 0.35, 0.3]
    if not tags:
        gaps = [1.15, 1.0, 0.9, 0.35]
    ys = _stack([(title_h, 0), (sub_h, 1), (card_h, 2), (caps_h, 3), (agent_h, 4), (foot_h, 5)],
                top, 1820, gaps)

    tsize = _auto_size(title, CONTENT_W, 138, "title_heavy", 72)
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
           % (W, H, W, H), _defs()]
    out.append('<rect width="%d" height="%d" fill="url(#bg)"/>' % (W, H))
    out.append('<circle cx="950" cy="180" r="280" fill="#FFFFFF" opacity="0.05"/>'
               '<circle cx="110" cy="1500" r="300" fill="#000000" opacity="0.07"/>')
    if has_photo:
        fit = _fit_mode(d, True)
        if fit == "meet":
            pw, ph = _panel_size(W, 900, photo, fit)
            out.append('<rect x="0" y="0" width="%d" height="900" fill="#F3EFE7"/>' % W)
            out.append('<image href="%s" x="%.0f" y="%.0f" width="%.0f" height="%.0f"/>'
                       % (_img_href(photo), (W - pw) / 2, (900 - ph) / 2, pw, ph))
        else:
            out.append('<image href="%s" x="0" y="0" width="%d" height="900" '
                       'preserveAspectRatio="xMidYMid slice"/>' % (_img_href(photo), W))
        out.append('<rect x="0" y="400" width="%d" height="500" fill="url(#photoFade)"/>' % W)
    else:
        out.append('<g opacity="0.16">'
                   '<circle cx="880" cy="352" r="206" fill="none" stroke="#FFE9AE" stroke-width="3"/>'
                   '<circle cx="880" cy="352" r="146" fill="none" stroke="#FFE9AE" stroke-width="2"/>'
                   '<circle cx="204" cy="318" r="112" fill="none" stroke="#FFE9AE" stroke-width="2"/></g>')
        out.append('<path d="M0 470 L%d 330 L%d 424 L0 564 Z" fill="#FFFFFF" opacity="0.06"/>' % (W, W))
        out.append('<rect x="%d" y="206" width="180" height="9" fill="url(#gold)"/>'
                   '<rect x="%d" y="206" width="9" height="180" fill="url(#gold)"/>' % (MARGIN, MARGIN))
    if has_photo:
        out.append('<rect x="0" y="0" width="%d" height="176" fill="url(#topScrim)"/>' % W)
    light_bg = bool(has_photo and _fit_mode(d, True) == "meet")   # 浅底图（户型图等）→ 文字要用深色
    out.append(_brand(agent.get("company", ""), d.get("slogan", ""),
                      color=("#8E1B20" if light_bg else "#FFE9AE"),
                      rule=("#C79A4A" if light_bg else "#FFD98A")))

    # 主标题
    out.append('<text x="%d" y="%.0f" text-anchor="middle" font-family="%s" font-size="%d" fill="url(#gold)" '
               'stroke="#7A0C10" stroke-width="6" filter="url(#soft)" letter-spacing="4">%s</text>'
               % (W // 2, ys[0] + title_h - 44, _esc(FONTS["title_promo"][0]), tsize, _esc(title)))
    out.append('<text x="%d" y="%.0f" text-anchor="middle" font-family="%s" font-size="%d" fill="%s" '
               'letter-spacing="6">%s</text>'
               % (W // 2, ys[1] + 52, _esc(FONTS["body"][0]),
                  _auto_size(sub, CONTENT_W - 40, 44, "body", 32),
                  ("#8E1B20" if light_bg else "#FFE9AE"), _esc(sub)))

    # 房源卡
    cy0 = ys[2]
    out.append('<g filter="url(#soft)"><rect x="%d" y="%.0f" width="%d" height="%.0f" rx="28" '
               'fill="#FFF7EC"/></g>' % (MARGIN, cy0, CONTENT_W, card_h))
    out.append('<rect x="%d" y="%.0f" width="%d" height="94" rx="28" fill="#F2E4CB"/>'
               '<rect x="%d" y="%.0f" width="%d" height="30" fill="#F2E4CB"/>'
               % (MARGIN, cy0, CONTENT_W, MARGIN, cy0 + 64, CONTENT_W))
    out.append('<text x="%d" y="%.0f" text-anchor="middle" font-family="%s" font-weight="700" font-size="%d" '
               'fill="#8E1B20">%s</text>'
               % (W // 2, cy0 + 64, _esc(FONTS["body"][0]),
                  _auto_size(code_line, CONTENT_W - 80, 40, "body", 30),
                  _esc(_fit(code_line, CONTENT_W - 80, 40))))

    # 左：总价（字号与卡片高度在开头一起算好）
    out.append('<text x="%d" y="%.0f" font-family="%s" font-size="40" fill="#8A7A6A">总价</text>'
               % (MARGIN + 50, cy0 + 214, _esc(FONTS["body"][0])))
    out.append('<text x="%d" y="%.0f" font-family="%s" font-weight="900" font-size="%d" fill="#C81E2B" '
               'letter-spacing="-3">%s</text>'
               % (MARGIN + 50, cy0 + 214 + psize * 0.86, _esc(FONTS["number"][0]), psize, _esc(ptext)))
    if up_text:
        out.append('<text x="%d" y="%.0f" font-family="%s" font-size="34" fill="#8A7A6A">%s</text>'
                   % (MARGIN + 50, cy0 + 214 + psize * 0.86 + 62, _esc(FONTS["body"][0]),
                      _esc(_fit(up_text, left_w, 34))))

    # 右：建面/户型/装修（列宽固定，字号自适应）
    right_x = divider_x + 42
    right_w = W - MARGIN - right_x
    out.append('<line x1="%d" y1="%.0f" x2="%d" y2="%.0f" stroke="#E3D3B8" stroke-width="3"/>'
               % (divider_x, cy0 + 130, divider_x, cy0 + card_h - 60))
    ry = cy0 + 200
    for label, val in cards:
        out.append('<text x="%d" y="%.0f" font-family="%s" font-size="32" fill="#8A7A6A">%s</text>'
                   % (right_x, ry, _esc(FONTS["body"][0]), _esc(label)))
        out.append('<text x="%d" y="%.0f" font-family="%s" font-weight="700" font-size="%d" fill="#3C2E26">%s</text>'
                   % (right_x, ry + 62, _esc(FONTS["title_heavy"][0]),
                      _auto_size(val, right_w, 58, "title_heavy", 34), _esc(_fit(val, right_w, 58, "title_heavy"))))
        ry += 122

    if tags:
        out.append(_capsules(tags, ys[3]))
    out.append(_agent_dark(agent, d.get("qr_path"), ys[4]))
    out.append(_footer(d.get("footer", "房源信息以实际看房为准")))
    out.append("</svg>")
    return "\n".join(out)


# ---------------- 模板 B：极简高级（需照片） ----------------
def template_b(d: dict) -> str:
    p = d["properties"][0]
    agent = d.get("agent") or {}
    # 主标题也按房号档位掩码（B 款原先完全没掩，实测 unit 档照印房号）
    title = mask_room_no(d.get("title"), d.get("room_no_mode"),
                         community=p.get("community")) or "今日主推"
    photo = d.get("photo_path")
    cream, ink, gold, mute = "#F5F1EA", "#3C2E26", "#C9A227", "#8C8072"
    sub = d.get("subtitle") or " · ".join(str(x) for x in [p.get("community"), p.get("district")] if x)

    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
           % (W, H, W, H), _defs()]
    out.append('<rect width="%d" height="%d" fill="%s"/>' % (W, H, cream))
    if agent.get("company"):
        out.append('<text x="%d" y="112" font-family="%s" font-size="36" fill="%s" letter-spacing="6">%s</text>'
                   % (80, _esc(FONTS["body"][0]), ink, _esc(_fit(agent["company"], 700, 36))))
    out.append('<line x1="80" y1="148" x2="%d" y2="148" stroke="%s" stroke-width="1.5" opacity="0.32"/>'
               % (W - 80, ink))

    photo_y, box_w, box_h = 196, W - 160, 720
    fit = _fit_mode(d, bool(photo))
    panel_w, photo_h = _panel_size(box_w, box_h, photo, fit) if photo else (box_w, box_h)
    panel_x = (W - panel_w) / 2
    if photo and os.path.exists(photo):
        out.append('<clipPath id="photoClip"><rect x="%.0f" y="%d" width="%.0f" height="%.0f" rx="18"/>'
                   '</clipPath>' % (panel_x, photo_y, panel_w, photo_h))
        if fit == "meet":
            # 户型图/横幅图：白底完整显示，绝不裁切
            out.append('<rect x="%.0f" y="%d" width="%.0f" height="%.0f" rx="18" fill="#FFFFFF"/>'
                       % (panel_x, photo_y, panel_w, photo_h))
        out.append('<image href="%s" x="%.0f" y="%d" width="%.0f" height="%.0f" clip-path="url(#photoClip)" '
                   'preserveAspectRatio="xMidYMid %s"/>'
                   % (_img_href(photo), panel_x, photo_y, panel_w, photo_h, fit))
    else:
        out.append('<rect x="80" y="%d" width="%d" height="%d" rx="18" fill="#E7E0D4"/>'
                   '<text x="%d" y="%d" text-anchor="middle" font-family="%s" font-size="38" fill="%s">'
                   '本模板需要房源照片</text>'
                   % (photo_y, box_w, box_h, W // 2, photo_y + box_h // 2, _esc(FONTS["body"][0]), mute))

    ty = photo_y + photo_h + 132
    tsize = _auto_size(title, W - 160, 104, "body", 64)
    out.append('<text x="80" y="%d" font-family="%s" font-weight="900" font-size="%d" fill="%s" '
               'letter-spacing="4">%s</text>' % (ty, _esc(FONTS["title_serif"][0]), tsize, ink, _esc(title)))
    out.append('<text x="80" y="%d" font-family="%s" font-size="38" fill="%s" letter-spacing="3">%s</text>'
               % (ty + 84, _esc(FONTS["body"][0]), mute, _esc(_fit(sub, W - 160, 38))))
    out.append('<line x1="80" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="3"/>' % (ty + 128, W - 80, ty + 128, gold))

    info = [("建筑面积", _area_text(p) or "—"),
            ("户型", _layout_text(p) or "—"),
            ("楼层", p.get("floor") or "—"),
            ("朝向", p.get("orientation") or "—")]
    for i, (label, val) in enumerate(info):
        cx = 80 + (i % 2) * 470
        cy = ty + 208 + (i // 2) * 116
        out.append('<text x="%d" y="%d" font-family="%s" font-size="30" fill="%s" letter-spacing="2">%s</text>'
                   % (cx, cy, _esc(FONTS["body"][0]), mute, _esc(label)))
        out.append('<text x="%d" y="%d" font-family="%s" font-weight="700" font-size="%d" fill="%s">%s</text>'
                   % (cx, cy + 56, _esc(FONTS["title_heavy"][0]),
                      _auto_size(val, 400, 52, "title_heavy", 32), ink,
                      _esc(_fit(val, 400, 52, "title_heavy"))))

    py = ty + 440
    out.append('<line x1="80" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="2" opacity="0.7"/>'
               % (py, W - 80, py, gold))
    ptext = _price_text(p)
    out.append('<text x="80" y="%d" font-family="%s" font-size="34" fill="%s">总价</text>'
               % (py + 66, _esc(FONTS["body"][0]), mute))
    psize = _auto_size(ptext, 520, 132, "title_heavy", 76)
    out.append('<text x="80" y="%d" font-family="%s" font-weight="900" font-size="%d" fill="%s" '
               'letter-spacing="-2">%s</text>'
               % (py + 66 + psize * 0.84, _esc(FONTS["title_serif"][0]), psize, ink, _esc(ptext)))
    up_text = _unit_price_text(p)
    if up_text:
        out.append('<text x="640" y="%d" font-family="%s" font-size="34" fill="%s">%s</text>'
                   % (py + 176, _esc(FONTS["body"][0]), mute, _esc(_fit(up_text, 340, 34))))

    foot_y = 1770
    out.append('<line x1="80" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="1.2" opacity="0.28"/>'
               % (foot_y - 60, W - 80, foot_y - 60, ink))
    name_line = " · ".join(str(x) for x in [agent.get("name"), agent.get("phone")] if x)
    out.append('<text x="80" y="%d" font-family="%s" font-size="38" fill="%s">%s</text>'
               % (foot_y, _esc(FONTS["body"][0]), ink, _esc(_fit(name_line, 700, 38))))
    if agent.get("wechat"):
        out.append('<text x="80" y="%d" font-family="%s" font-size="32" fill="%s">微信 %s</text>'
                   % (foot_y + 48, _esc(FONTS["body"][0]), mute, _esc(agent["wechat"])))
    if agent.get("company"):
        out.append('<text x="80" y="%d" font-family="%s" font-size="32" fill="%s">%s</text>'
                   % (foot_y + 92, _esc(FONTS["body"][0]), mute,
                      _esc(_fit(str(agent["company"]), 700, 32))))
    if d.get("qr_path"):
        out.append('<rect x="892" y="%d" width="124" height="124" rx="12" fill="#FFFFFF"/>' % (foot_y - 84))
        out.append('<image href="%s" x="900" y="%d" width="108" height="108"/>'
                   % (_img_href(d["qr_path"]), foot_y - 76))
    out.append(_footer(d.get("footer", "房源信息以实际看房为准"), color=mute, opacity=0.95, y=1904))
    out.append("</svg>")
    return "\n".join(out)


# ============================================================================
# 自定义款（参数化）：供「参考图做海报」使用 —— Coco 从参考图提取风格要素后填参数
# ============================================================================
CUSTOM_LAYOUTS = ("hero_top", "minimal", "split")
CUSTOM_DECORS = ("rounded_soft", "sharp", "bordered")
CUSTOM_FIELDS = ("price", "unit_price", "area", "layout", "floor", "orientation", "tags", "community")
_LAYOUT_LABELS = {"hero_top": "大图在上、信息在下", "minimal": "极简留白", "split": "左右分栏"}

PALETTES = {
    "red_gold":   {"bg": "#7E1116", "bg2": "#2E1113", "accent": "#E8C36A", "card": "#FFF8EC", "ink": "#FFF8EC", "mute": "#E3C39C", "on_card": "#3A2A20"},
    "black_gold": {"bg": "#141414", "bg2": "#2B2418", "accent": "#D9B45B", "card": "#FFFFFF", "ink": "#F5F1E6", "mute": "#C9BC9B", "on_card": "#26221C"},
    "cream":      {"bg": "#F5F1EA", "bg2": "#E7DFD0", "accent": "#C9A227", "card": "#FFFFFF", "ink": "#3C2E26", "mute": "#8C8072", "on_card": "#3C2E26"},
    "navy":       {"bg": "#0E2A47", "bg2": "#08192C", "accent": "#8FB8E0", "card": "#FFFFFF", "ink": "#F2F7FC", "mute": "#A9C2DA", "on_card": "#17324D"},
    "green":      {"bg": "#123A2E", "bg2": "#0A2019", "accent": "#C9A227", "card": "#FFFFFF", "ink": "#F1F7F3", "mute": "#A8C6B6", "on_card": "#132E24"},
    "orange":     {"bg": "#C2410C", "bg2": "#7C2D12", "accent": "#FFD98A", "card": "#FFFFFF", "ink": "#FFF6EC", "mute": "#F6C79A", "on_card": "#3B1B08"},
    "pink":       {"bg": "#F3E3E7", "bg2": "#E4C7CF", "accent": "#B04A63", "card": "#FFFFFF", "ink": "#3B2530", "mute": "#8A6A74", "on_card": "#3B2530"},
    "grey":       {"bg": "#2C2F33", "bg2": "#16181B", "accent": "#BFC5CC", "card": "#FFFFFF", "ink": "#F2F4F6", "mute": "#A6ADB4", "on_card": "#24272B"},
}
_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{6})$")
_CN_COLORS = {"红": "#C4161C", "红金": "red_gold", "黑金": "black_gold", "米白": "cream", "藏蓝": "navy",
              "墨绿": "green", "橙红": "orange", "粉紫": "pink", "灰白": "grey", "白": "#FFFFFF", "金": "#D9B45B"}


def normalize_style(style, notes: list | None = None) -> dict:
    """把模型从参考图提取的风格参数**收敛到安全取值**，非法/缺失一律回落默认，并记下提示。

    返回 {"layout","palette","colors","font_style","show_fields","decor","summary"}；
    summary 是给经纪人看的一句话（确认风格用）。
    """
    notes = notes if notes is not None else []
    style = style if isinstance(style, dict) else {}

    layout = str(style.get("layout") or "").strip().lower()
    if layout not in CUSTOM_LAYOUTS:
        if layout:
            notes.append(f"版式「{layout}」不在支持范围，已用默认")
        layout = "hero_top"

    decor = str(style.get("decor") or "").strip().lower()
    if decor not in CUSTOM_DECORS:
        if decor:
            notes.append(f"装饰「{decor}」不支持，已用默认")
        decor = "rounded_soft"

    font_style = str(style.get("font_style") or "").strip().lower()
    if font_style in ("衬线", "宋体", "serif"):
        font_style = "serif"
    elif font_style in ("黑体", "无衬线", "sans", "sans-serif"):
        font_style = "sans"
    elif font_style:
        notes.append(f"字体气质「{style.get('font_style')}」不支持，已用默认")
        font_style = "sans"
    else:
        font_style = "sans"

    palette = style.get("palette")
    colors = dict(PALETTES["red_gold"])
    palette_name = "red_gold"
    if isinstance(palette, str):
        key = palette.strip().lower()
        if key in PALETTES:
            colors, palette_name = dict(PALETTES[key]), key
        elif palette in _CN_COLORS and isinstance(_CN_COLORS[palette], str) and _CN_COLORS[palette] in PALETTES:
            palette_name = _CN_COLORS[palette]
            colors = dict(PALETTES[palette_name])
        elif palette in _CN_COLORS and _HEX_RE.match(_CN_COLORS[palette] or ""):
            colors["accent"] = _CN_COLORS[palette]
        else:
            notes.append(f"配色「{palette}」不认识，已用默认红金")
    elif isinstance(palette, dict):
        for src, dst in (("bg", "bg"), ("background", "bg"), ("accent", "accent"),
                         ("primary", "accent"), ("ink", "ink"), ("text", "ink"), ("mute", "mute")):
            val = palette.get(src)
            if isinstance(val, str) and _HEX_RE.match(val.strip()):
                colors[dst] = val.strip().upper()
            elif isinstance(val, str) and val.strip():
                notes.append(f"色值「{val}」不是 #RRGGBB 格式，已忽略")
        palette_name = "custom"
    elif palette:
        notes.append("配色格式不认识，已用默认红金")

    fields = style.get("show_fields")
    if isinstance(fields, str):
        fields = [x.strip() for x in re.split(r"[,，、/\s]+", fields) if x.strip()]
    if not isinstance(fields, list) or not fields:
        fields = ["price", "area", "layout", "floor", "orientation"]
    good, bad = [], []
    for f in fields:
        key = str(f).strip().lower()
        key = {"总价": "price", "价格": "price", "单价": "unit_price", "建面": "area", "面积": "area",
               "户型": "layout", "楼层": "floor", "朝向": "orientation", "标签": "tags", "小区": "community"}.get(key, key)
        if key in CUSTOM_FIELDS and key not in good:
            good.append(key)
        elif key:
            bad.append(str(f))
    if bad:
        notes.append("这些信息项不支持，已忽略：" + "、".join(bad))
    if not good:
        good = ["price", "area", "layout"]

    summary = (f"版式 {_LAYOUT_LABELS.get(layout, layout)}、配色 {palette_name}、"
               f"字体 {'衬线（稳重高级）' if font_style == 'serif' else '黑体（醒目促销）'}、"
               f"显示 {'/'.join(good)}")
    return {"layout": layout, "palette": palette_name, "colors": colors, "font_style": font_style,
            "show_fields": good, "decor": decor, "summary": summary}


def _rx_of(decor: str) -> int:
    return {"rounded_soft": 22, "sharp": 0, "bordered": 10}.get(decor, 22)


def _t(x, y, text, size, fill, family=None, weight=None, anchor=None, spacing=None) -> str:
    """一行文字（统一入口，避免各处手拼属性）"""
    attrs = [f'x="{x}"', f'y="{y}"', f'font-family="{_esc(family or FONTS["body"][0])}"', f'font-size="{size}"']
    if weight:
        attrs.append(f'font-weight="{weight}"')
    if anchor:
        attrs.append(f'text-anchor="{anchor}"')
    if spacing:
        attrs.append(f'letter-spacing="{spacing}"')
    return '<text %s fill="%s">%s</text>' % (" ".join(attrs), fill, _esc(text))


def _rect(x, y, w, h, rx, fill, stroke=None, sw=2, opacity=None) -> str:
    extra = ""
    if stroke:
        extra += f' stroke="{stroke}" stroke-width="{sw}"'
    if opacity is not None:
        extra += f' opacity="{opacity}"'
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}"{extra}/>'


def _photo_panel(d, c, x, y, w, h, photo, decor) -> list:
    """图片区：有图按 fit（照片裁切铺满 / 户型图完整显示），无图给纯色面板"""
    rx = _rx_of(decor)
    out = []
    if photo and os.path.exists(photo):
        fit = _fit_mode(d, True)
        pw, ph = _panel_size(w, h, photo, fit)
        px, py = x + (w - pw) / 2, y + (h - ph) / 2
        if fit == "meet":
            out.append(_rect(px, py, pw, ph, rx, "#FFFFFF"))
        out.append('<clipPath id="cClip"><rect x="%.0f" y="%.0f" width="%.0f" height="%.0f" rx="%d"/></clipPath>'
                   % (px, py, pw, ph, rx))
        out.append('<image href="%s" x="%.0f" y="%.0f" width="%.0f" height="%.0f" clip-path="url(#cClip)" '
                   'preserveAspectRatio="xMidYMid %s"/>' % (_img_href(photo), px, py, pw, ph, fit))
    else:
        out.append(_defs(c["accent"], c["accent"]))
        out.append(_rect(x, y, w, h, rx, "url(#gradGold)"))
    return out


def _field_rows(style, p) -> list:
    """按 show_fields 生成 [(标签, 值)]"""
    rows = []
    for f in style["show_fields"]:
        if f == "price":
            rows.append(("价格", _price_text(p)))
        elif f == "unit_price":
            rows.append(("单价", _unit_price_text(p, prefix="") or "—"))
        elif f == "area":
            rows.append(("建面", _area_text(p) or "—"))
        elif f == "layout":
            rows.append(("户型", _layout_text(p) or "—"))
        elif f == "floor":
            rows.append(("楼层", p.get("floor") or "—"))
        elif f == "orientation":
            rows.append(("朝向", p.get("orientation") or "—"))
        elif f == "community":
            rows.append(("小区", p.get("community") or "—"))
    return rows


def _hex_luma(hexcolor: str) -> float:
    """#RRGGBB → 相对亮度（0~1），用于判断底色深浅以选择文字颜色。"""
    h = str(hexcolor or "").lstrip("#")
    if len(h) != 6:
        return 0.0
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return 0.0
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _agent_block_custom(agent, qr, y, c, light_bg: bool, rx: int, width: int) -> list:
    """名片区（自定义款专用）：按底色深浅选文字颜色 —— 浅底用深色字，深底用浅色字。

    不直接复用 _agent_dark 的原因：那个是给深色底设计的（白字），压在米白/粉紫这类
    浅色底上会“看不见”（实测踩到）。
    """
    if not agent and not qr:
        return []
    ink = c["on_card"] if light_bg else c["ink"]
    mute = c["mute"] if not light_bg else c["on_card"]
    out = []
    out.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="2" opacity="0.35"/>'
               % (MARGIN, y - 26, MARGIN + width, y - 26, c["accent"]))
    name = " ".join(str(x) for x in [agent.get("name"), agent.get("phone")] if x)
    if name:
        out.append(_t(MARGIN, y + 34, _fit(name, width - 200, 46, "body"), 46, ink, weight="700"))
    wx = agent.get("wechat")
    if wx:
        out.append(_t(MARGIN, y + 84, f"微信 {wx}", 32, mute))
    if agent.get("company"):
        out.append(_t(MARGIN, y + 132, agent["company"], 32, mute))
    if qr and os.path.exists(qr):
        out.append('<image href="%s" x="%d" y="%d" width="160" height="160"/>' % (_img_href(qr), W - MARGIN - 160, y - 6))
        out.append(_t(W - MARGIN - 80, y + 176, "扫码加我微信", 26, mute, anchor="middle"))
    return out


def template_custom(d: dict) -> str:
    """自定义款：按 style 参数渲染（版式/配色/字体气质/信息项/装饰），骨架仍是我们自己的。

    - 参数来自 d["style"]（Coco 从参考图提取）；非法/缺失由 normalize_style 收敛，绝不空图。
    - **无照片时不留大空白**：hero_top 用细幅色带、split 用主色卡片、minimal 直接留白起标题。
    - 名片区按底色深浅选字色（浅底深字/深底浅字），避免白字压在米白底上看不见。
    """
    style = normalize_style(d.get("style"))
    c = style["colors"]
    light_bg = _hex_luma(c["bg"]) > 0.55
    p = d["properties"][0]
    agent = d.get("agent") or {}
    photo = d.get("photo_path")
    has_photo = bool(photo and os.path.exists(photo))
    title = mask_room_no(d.get("title"), d.get("room_no_mode"),
                         community=p.get("community")) or "今日主推"
    rx = _rx_of(style["decor"])
    serif = style["font_style"] == "serif"
    fam_title = FONTS["title_serif"][0] if serif else FONTS["title_heavy"][0]
    layout = style["layout"]
    rows = [r for r in _field_rows(style, p) if r[0] != "价格"][:6]
    tags = _tags_of(d, p) if "tags" in style["show_fields"] else []
    qr = d.get("qr_path")
    code_line = _code_line_for(d, p)
    sub = d.get("subtitle") or " · ".join(str(x) for x in [p.get("community"), p.get("district"), p.get("renovation")] if x)
    price = _price_text(p)
    unit = _unit_price_text(p) if "unit_price" in style["show_fields"] else ""
    ink, mute, accent = c["ink"], c["mute"], c["accent"]
    brand_color = c["on_card"] if light_bg else accent
    CW = W - 2 * MARGIN
    AGENT_Y = 1700

    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (W, H, W, H)]
    out.append(_defs(accent, accent))
    out.append(_rect(0, 0, W, H, 0, c["bg"]))
    if style["decor"] == "bordered":
        out.append(_rect(28, 28, W - 56, H - 56, rx, "none", stroke=accent, sw=3))
    if agent.get("company"):
        out.append(_t(MARGIN, 96, _fit(agent["company"], CW, 40, "body"), 40, brand_color, weight="700", spacing="4"))
    out.append('<line x1="%d" y1="126" x2="%d" y2="126" stroke="%s" stroke-width="2" opacity="0.45"/>'
               % (MARGIN, W - MARGIN, accent))

    def _grid(x, y, cw, items, cols=2, row_h=108):
        """两列信息格（标签小字 + 值大字），最多 6 项"""
        for i, (lab, val) in enumerate(items):
            gx = x + (i % cols) * (cw / cols)
            gy = y + (i // cols) * row_h
            out.append(_t(gx, gy, lab, 28, mute))
            out.append(_t(gx, gy + 50, _fit(val, cw / cols - 24, 44, "body"), 44, ink, weight="700"))

    if layout == "split":
        col_w = 400
        if has_photo:
            out += _photo_panel(d, c, MARGIN, 200, col_w, 1260, photo, style["decor"])
        else:
            out.append(_rect(MARGIN, 200, col_w, 1260, rx, accent, opacity="0.16"))
            if price:
                out.append(_t(MARGIN + col_w / 2, 800, price, _auto_size(price, col_w - 60, 96, "number", 56),
                              accent, family=FONTS["number"][0], weight="900", anchor="middle"))
            if unit:
                out.append(_t(MARGIN + col_w / 2, 860, _fit(unit, col_w - 60, 30, "body"), 30, mute, anchor="middle"))
        cx, cw = MARGIN + col_w + 50, W - MARGIN - (MARGIN + col_w + 50)
        out.append(_t(cx, 268, _fit(title, cw, 82, "body"), 82, ink, family=fam_title, weight="900", spacing="3"))
        out.append(_t(cx, 336, _fit(sub, cw, 32, "body"), 32, mute))
        # 窄栏放不下长标题：只取「楼栋单元」那一段（如「7号楼2单元」），放不下再截断
        _short_code = re.split(r"[,，、\s]+", code_line)[-1] if code_line else code_line
        out.append(_t(cx, 386, _fit(_short_code or code_line, cw, 30, "body"), 30, mute))
        yy = 480
        for lab, val in rows:
            out.append(_t(cx, yy, lab, 28, mute))
            out.append(_t(cx, yy + 50, _fit(val, cw, 46, "body"), 46, ink, weight="700"))
            yy += 118
        if tags:
            out.append("".join(_capsules(tags[:2], min(yy + 10, 1400), accent, c["bg"], 28, cw)))
        if price and has_photo:
            out.append(_t(cx, 1500, price, _auto_size(price, cw, 96, "number", 58), accent,
                          family=FONTS["number"][0], weight="900"))
            if unit:
                out.append(_t(cx, 1556, _fit(unit, cw, 30, "body"), 30, mute))
    elif layout == "minimal":
        y = 300
        if has_photo:
            out += _photo_panel(d, c, MARGIN, 190, CW, 560, photo, style["decor"])
            y = 890
        else:
            y = 620
        out.append(_t(MARGIN, y, _fit(title, CW, 96, "body"), 96, ink, family=fam_title, weight="900", spacing="4"))
        out.append(_t(MARGIN, y + 80, _fit(sub, CW, 36, "body"), 36, mute))
        out.append(_t(MARGIN, y + 132, _fit(code_line, CW, 32, "body"), 32, mute))
        if rows:
            _grid(MARGIN, y + 226, CW, rows[:4], cols=2, row_h=104)
        if price:
            py = y + 226 + ((len(rows[:4]) + 1) // 2) * 104 + 70
            out.append(_t(MARGIN, py, price, _auto_size(price, CW, 120, "number", 70), accent,
                          family=FONTS["number"][0], weight="900"))
            if unit:
                out.append(_t(MARGIN, py + 56, _fit(unit, CW, 32, "body"), 32, mute))
        if tags:
            out.append("".join(_capsules(tags[:3], min(1440, H - 420), accent, c["bg"], 30, CW)))
    else:  # hero_top
        if has_photo:
            out += _photo_panel(d, c, MARGIN, 170, CW, 700, photo, style["decor"])
            ty = 940
        else:
            out.append(_rect(MARGIN, 170, CW, 250, rx, accent, opacity="0.16"))
            ty = 520
        out.append(_t(MARGIN, ty, _fit(title, CW, 92, "body"), 92, ink, family=fam_title, weight="900", spacing="4"))
        out.append(_t(MARGIN, ty + 76, _fit(sub, CW, 36, "body"), 36, mute, spacing="2"))
        out.append(_t(MARGIN, ty + 128, _fit(code_line, CW, 32, "body"), 32, mute))
        if rows:
            _grid(MARGIN, ty + 220, CW, rows, cols=2, row_h=108)
        if tags:
            out.append("".join(_capsules(tags[:3], ty + 220 + ((len(rows) + 1) // 2) * 108 + 40, accent, c["bg"], 30, CW)))
        if price:
            band_y = 1500 if has_photo else 1240
            out.append(_rect(MARGIN, band_y, CW, 120, rx, c["card"], opacity="0.12"))
            out.append(_t(MARGIN + 32, band_y + 78,
                          price, _auto_size(price, CW - 300, 74, "number", 46), accent,
                          family=FONTS["number"][0], weight="900"))
            if unit:
                out.append(_t(W - MARGIN - 32, band_y + 76, _fit(unit, 280, 32, "body"), 32, mute, anchor="end"))

    out += _agent_block_custom(agent, qr, AGENT_Y, c, light_bg, rx, CW)
    out.append(_footer(d.get("footer") or "房源信息以实际看房为准", color=mute, opacity=0.85, y=H - 40))
    out.append("</svg>")
    return "".join(x for x in out if x)


TEMPLATES = {"A": template_a, "B": template_b, "CUSTOM": template_custom}



def render(d: dict, out_path: str | None = None) -> dict:
    """渲染海报：SVG → rsvg-convert → PNG。失败时返回 success=False（调用方回退 Pillow 引擎）。"""
    template = str(d.get("template") or "A").upper()
    if template not in TEMPLATES:
        return {"success": False, "error": "未知模板 %s（可选 A/B）" % template}
    svg = TEMPLATES[template](d)
    if not out_path:
        out_path = os.path.join(tempfile.mkdtemp(prefix="coco_poster_"), "poster_%s.png" % template)
    svg_path = out_path + ".svg"
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    conv = shutil.which("rsvg-convert")
    if not conv:
        return {"success": False, "error": "未安装 rsvg-convert（librsvg2-bin）", "svg_path": svg_path}
    proc = subprocess.run([conv, "-w", str(W), "-h", str(H), "-o", out_path, svg_path],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(out_path):
        return {"success": False, "error": "渲染失败：%s" % proc.stderr.strip()[:200], "svg_path": svg_path}
    return {"success": True, "png_path": out_path, "svg_path": svg_path, "template": template}
