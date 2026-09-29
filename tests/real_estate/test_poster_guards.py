"""海报（generate_property_poster）回归：房号三档掩码 / 图内数字口径 / 选房与人话（2026-09-26）

本文件钉九件事（第 63 项）：
① 房号掩码**任意位置**都掩（原先只去末尾：`…1602 急售`、`1602 南北通透三居`、`1602室 急售` 全照印），
   且 A/B/CUSTOM 三个模板共用同一处；掩完没有定位信息时退回小区名，**绝不回退原串**；
② 图内面积不带 `.0`（`90㎡` 不是 `90.0㎡`），出租房单价带 `/月`（`42元/㎡/月`）；
③ 按 `title` 找房命中多套 → **不猜**，返回候选让经纪人确认；命中一套 → 回执回填编号；
④ 房源不存在 / 已售 / 已租 **分开说**（走共用件 `unavailable_property_note`，动作词是「出海报」）；
⑤ `allow_missing=True` 且没问过房号档位 → 落到**最不曝光**的 unit 档，并在回执里说明；
⑥ 非 CUSTOM 模板传了 `style` → 回执说明"这次没生效"；
⑦ 出图文件名带内容指纹（改标题重出图不再复用同一路径），并**每套房只留最近 5 份**、不碰别的文件；
⑧ 两个渲染引擎都不可用时给中文提示（不裸抛栈）；
⑨ 面积/价格/楼层里的数字**不许**被当房号误抹。
"""
import json
import os
import re

import pytest
from conftest import make_property  # noqa: F401

from tools import real_estate_poster as m_poster
from tools import real_estate_poster_svg as m_svg

CARD = {"company": "安心房产望京店", "name": "张三", "phone": "13800000001"}


def _wire(db, monkeypatch, poster_dir=None):
    monkeypatch.setattr(m_poster, "_get_db", lambda: db)
    monkeypatch.setattr(m_poster, "_agent_card", lambda: dict(CARD))
    if poster_dir is not None:
        monkeypatch.setattr(m_poster, "_poster_dir", lambda: str(poster_dir))
    return db


def _poster(**kw):
    return json.loads(m_poster.generate_property_poster(**kw))


def _sale(db, **kw):
    data = dict(title="海阔天空 7号楼2单元1602", community="海阔天空", district="朝阳区",
                price=1_500_000, area=90.0, rooms=3, halls=2, orientation="南", floor="16层",
                renovation="精装", property_type="second_hand", status="available")
    data.update(kw)
    return make_property(db, **data)


# ---------- ① 房号掩码（任意位置，三模板共用）----------
TITLES = [
    ("末尾房号", "海阔天空 7号楼2单元1602"),
    ("末尾房号无空格", "海阔天空7号楼2单元1602"),
    ("中段+后缀", "海阔天空7号楼2单元1602 急售"),
    ("房号在前", "7号楼2单元1602 南北通透三居"),
    ("整串就是房号", "7号楼2单元1602"),
    ("只有楼层户号", "1602室 急售"),
]


def _svg(template, title, mode, prop, community="海阔天空"):
    d = {"template": template, "room_no_mode": mode, "title": title, "subtitle": "海阔天空 · 朝阳区",
         "properties": [dict(prop)], "agent": CARD, "qr_path": None, "photo_path": None,
         "footer": "房源信息以实际看房为准", "style": m_svg.normalize_style({}, [])}
    return m_svg.TEMPLATES[template](d)


@pytest.mark.parametrize("label,title", TITLES)
@pytest.mark.parametrize("template", ["A", "B", "CUSTOM"])
def test_room_no_masked_everywhere(label, title, template):
    """unit/none 档：主标题里的房号必须全掩（原先只有"末尾房号"这一种掩得住）"""
    prop = {"id": 1, "title": title, "community": "海阔天空", "district": "朝阳区", "area": 90.0,
            "rooms": 3, "halls": 2, "price": 1_500_000, "renovation": "精装", "floor": "16层",
            "orientation": "南", "property_type": "second_hand", "tags": ""}
    for mode in ("unit", "none"):
        svg = _svg(template, title, mode, prop)
        assert "1602" not in svg, f"{template}/{mode}/{label} 房号仍印在图上"
    # full 档照旧保留完整房号
    assert "1602" in _svg(template, title, "full", prop)


def test_masking_never_falls_back_to_original():
    """掩完没剩下定位信息时退回小区名，不许把原串（带房号）放回去"""
    assert m_svg.mask_room_no("1602室 急售", "unit", community="阳光小区") == "阳光小区"
    assert m_svg.mask_room_no("海阔天空7号楼2单元1602 急售", "unit", community="海阔天空") == "海阔天空7号楼2单元 急售"
    assert m_svg.mask_room_no("海阔天空精装三居", "unit", community="海阔天空") == "海阔天空精装三居"


def test_masking_keeps_area_and_price_numbers():
    """⑨ 面积/价格/楼层里的数字不许被当房号误抹"""
    assert m_svg.strip_room_no("89平 1602室 急售") == "89平 急售"
    assert m_svg.strip_room_no("128.5㎡ 3室2厅") == "128.5㎡ 3室2厅"
    assert m_svg.strip_room_no("月租2200 望京小筑") == "月租2200 望京小筑"
    assert m_svg.strip_room_no("2015年建成 海阔天空") == "2015年建成 海阔天空"


# ---------- ② 图内数字口径 ----------
@pytest.mark.parametrize("template", ["A", "B", "CUSTOM"])
def test_area_has_no_trailing_zero(template):
    prop = {"id": 1, "title": "海阔天空 7号楼2单元1602", "community": "海阔天空", "area": 90.0,
            "rooms": 3, "halls": 2, "price": 1_500_000, "property_type": "second_hand", "tags": ""}
    svg = _svg(template, "今日主推", "full", prop)
    assert "90㎡" in svg and "90.0㎡" not in svg, svg[:200]


def test_rental_unit_price_says_per_month():
    """出租房大字单价不能漏 /月（原先印「42元/㎡」）"""
    assert m_svg._unit_price_text({"unit_price": 41.67, "property_type": "rental"}) == "单价 42元/㎡/月"
    assert m_svg._unit_price_text({"unit_price": 16666.67, "property_type": "second_hand"}) == "单价 1.67万/㎡"


# ---------- ③ 按标题找房：不猜 ----------
def test_title_hitting_several_properties_asks_instead_of_guessing(db, monkeypatch):
    _wire(db, monkeypatch)
    _sale(db, title="海阔天空 7号楼2单元1602")
    _sale(db, title="海阔天空 9号楼1单元901", area=95.0)
    out = _poster(title="海阔天空", template="A", poster_title="x", show_room_no="full")
    assert out["success"] is False and out.get("ambiguous") is True, out
    assert len(out.get("candidates") or []) >= 2, out
    assert "把编号给我" in out["error"], out


def test_title_hitting_one_property_returns_the_id(db, monkeypatch):
    _wire(db, monkeypatch)
    p = _sale(db, title="海阔天空 7号楼2单元1602")
    out = _poster(title="海阔天空 7号楼2单元1602", template="A", poster_title="x",
                  show_room_no="full")
    assert out["success"] is True and out["property_id"] == p["id"], out


# ---------- ④ 不存在 / 已售 / 已租 分开说 ----------
def test_missing_property_names_the_id(db, monkeypatch):
    _wire(db, monkeypatch)
    out = _poster(property_id=999999, template="A", poster_title="x", show_room_no="full")
    assert out["success"] is False and "没有编号 999999 的房源" in out["error"], out


@pytest.mark.parametrize("status,phrase", [("sold", "已经售出"), ("rented", "已经出租")])
def test_unavailable_states_use_shared_wording(db, monkeypatch, status, phrase):
    _wire(db, monkeypatch)
    p = _sale(db, title="已成交海报房源", status=status)
    out = _poster(property_id=p["id"], template="A", poster_title="x", show_room_no="full")
    assert out["success"] is False, out
    assert phrase in out["error"] and "不能出海报" in out["error"], out
    assert out["property_status"] in ("已售", "已租"), out


# ---------- ⑤ allow_missing 的房号默认档 ----------
def test_allow_missing_defaults_to_unit_and_says_so(db, monkeypatch):
    _wire(db, monkeypatch)
    p = _sale(db)
    out = _poster(property_id=p["id"], template="A", poster_title="x", allow_missing=True)
    assert out["success"] is True, out
    notes = " ".join(out.get("notes") or [])
    assert "只写楼栋单元" in notes, out
    svg = open(out["poster_path"] + ".svg", encoding="utf-8").read()
    assert "1602" not in svg, "没问过档位时不该把完整房号印上去"


# ---------- ⑥ 非 CUSTOM 传 style ----------
def test_style_on_non_custom_template_is_reported(db, monkeypatch):
    _wire(db, monkeypatch)
    p = _sale(db)
    out = _poster(property_id=p["id"], template="A", poster_title="x", show_room_no="full",
                  style={"palette": "black_gold"})
    assert out["success"] is True, out
    assert any("没生效" in n for n in (out.get("notes") or [])), out


# ---------- ⑦ 文件名指纹 + 清理 ----------
def test_poster_filename_gets_a_fingerprint(db, monkeypatch, tmp_path):
    _wire(db, monkeypatch, poster_dir=tmp_path)
    p = _sale(db)
    a = _poster(property_id=p["id"], template="A", poster_title="今日主推", show_room_no="full")
    b = _poster(property_id=p["id"], template="A", poster_title="业主诚售", show_room_no="full")
    assert a["poster_path"] != b["poster_path"], (a["poster_path"], b["poster_path"])
    assert re.search(r"_[0-9a-f]{8}\.png$", a["poster_path"]), a["poster_path"]
    assert os.path.exists(a["poster_path"]) and os.path.exists(b["poster_path"])
    # 同参重复出图 → 同一个文件（幂等，不再堆文件）
    c = _poster(property_id=p["id"], template="A", poster_title="今日主推", show_room_no="full")
    assert c["poster_path"] == a["poster_path"]


def test_poster_prune_keeps_recent_and_spares_other_files(db, monkeypatch, tmp_path):
    """每套房只留最近 5 份成品：7 份同一套房的旧海报 → 只留最新 5 份（连 .svg），别的文件不碰"""
    _wire(db, monkeypatch, poster_dir=tmp_path)
    for i in range(7):                                   # 同一套房 7 份历史海报
        (tmp_path / f"poster_5_A_{i:08x}.png").write_bytes(b"x")
        (tmp_path / f"poster_5_A_{i:08x}.png.svg").write_text("<svg/>")
        for f in (tmp_path / f"poster_5_A_{i:08x}.png", tmp_path / f"poster_5_A_{i:08x}.png.svg"):
            os.utime(f, (1_600_000_000 + i, 1_600_000_000 + i))
    (tmp_path / "poster_qr.png").write_bytes(b"qr")            # 二维码：不是成品，别删
    (tmp_path / "house_photo.jpg").write_bytes(b"photo")       # 经纪人房源照片：绝不能碰
    fresh = tmp_path / "poster_5_A.png"
    fresh.write_bytes(b"new")
    m_poster._stamp_and_prune(str(fresh))
    left = sorted(f.name for f in tmp_path.glob("poster_*"))
    assert (tmp_path / "poster_qr.png").exists(), "二维码被误删"
    assert (tmp_path / "house_photo.jpg").exists(), "房源照片被误删"
    assert "poster_5_A_00000000.png" not in left, "最旧那份没被清掉"
    assert "poster_5_A_00000000.png.svg" not in left, "旧海报的 .svg 没跟着删"
    assert len([n for n in left if re.search(r"_[0-9a-f]{8}\.png$", n)]) == 5, left


def test_prune_is_per_property_not_global(db, monkeypatch, tmp_path):
    """名额按房源分：1 号房 7 份 → 留 5；2 号房 3 份 → 全留；历史拼图成品单独一个桶，同样留 5 份"""
    _wire(db, monkeypatch, poster_dir=tmp_path)
    for i in range(7):
        (tmp_path / f"poster_1_A_{i:08x}.png").write_bytes(b"x")
        (tmp_path / f"poster_1_A_{i:08x}.png.svg").write_text("<svg/>")
        for f in (tmp_path / f"poster_1_A_{i:08x}.png", tmp_path / f"poster_1_A_{i:08x}.png.svg"):
            os.utime(f, (1_600_000_000 + i, 1_600_000_000 + i))
    for i in range(3):
        (tmp_path / f"poster_2_A_{i:08x}.png").write_bytes(b"x")
        os.utime(tmp_path / f"poster_2_A_{i:08x}.png", (1_600_000_000 + i, 1_600_000_000 + i))
    for i in range(7):
        (tmp_path / f"poster_grid_{i:08x}.png").write_bytes(b"x")
        os.utime(tmp_path / f"poster_grid_{i:08x}.png", (1_600_000_000 + i, 1_600_000_000 + i))
    fresh = tmp_path / "poster_3_A.png"
    fresh.write_bytes(b"new")
    m_poster._stamp_and_prune(str(fresh))

    left = [f.name for f in tmp_path.glob("poster_*.png")]
    assert len([n for n in left if n.startswith("poster_1_A_")]) == 5, left
    assert len([n for n in left if n.startswith("poster_2_A_")]) == 3, left
    assert len([n for n in left if n.startswith("poster_grid_")]) == 5, left
    assert len([n for n in left if n.startswith("poster_3_A_")]) == 1, left


# ---------- ⑧ 两个引擎都不可用 ----------
def test_both_engines_down_gives_chinese_hint(db, monkeypatch, tmp_path):
    _wire(db, monkeypatch, poster_dir=tmp_path)
    p = _sale(db)
    monkeypatch.setattr(m_svg, "render", lambda *a, **kw: {"success": False, "error": "无 rsvg"})

    def boom(*a, **kw):
        raise RuntimeError("Pillow 也不可用")

    monkeypatch.setattr(m_poster, "_render_legacy", boom)
    out = _poster(property_id=p["id"], template="A", poster_title="x", show_room_no="full")
    assert out["success"] is False and "海报没出成" in out["error"], out
    assert "Tool execution failed" not in json.dumps(out, ensure_ascii=False), out
