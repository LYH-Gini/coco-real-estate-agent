"""九宫格（generate_poster_grid）回归：图上文字 / 截断与去重 / 档位 / 形状（2026-09-26 第 64 项）

九宫格的输出是一张 Pillow 拼图，所以本文件用**拦截 `ImageDraw.text`** 的办法把"图上到底写了什么"抓出来断言
（图片类工具唯一能验到图上文字的可靠手段）。版式按拍板**不动**，只钉口径与形状：

① 图上面积不带 `.0`（`71㎡` 不是 `71.0㎡`）；
② 不存在 / 已售 / 已租 **分开说**（走共用件），并带 `unavailable` 结构化；
③ 超过 9 套 → 说明只画了前 9 套、没画上哪些，`count`/`total`/`truncated` 三件齐；
④ 同一编号传两次 → 每套只画一格 + 说明；
⑤ `show_room_no` 乱值给中文可选值清单（不许静默按默认出图）；没给/空值才走默认 unit；
⑥ unit/none 档图上不出现具体房号，full 档**看得见**房号（缩字号优先，别从尾部截掉）；
⑦ 成品名带内容指纹（改内容重出图不再复用同一路径），同参重复出图内容一致；
⑧ `qr_content` 参数已按拍板移除（schema 里没有，传了会被告知可用参数）。
"""
import json
import os
import re

import pytest
from conftest import make_property  # noqa: F401
from PIL import Image, ImageDraw

from tools import real_estate_poster as m_poster

_ORIG_TEXT = ImageDraw.ImageDraw.text
RECORDED = []


@pytest.fixture
def drawn(monkeypatch):
    """拦截绘制文字：调用工具后拿到"图上写了什么" """
    def _spy(self, xy, text, *a, **kw):
        RECORDED.append(str(text))
        return _ORIG_TEXT(self, xy, text, *a, **kw)

    RECORDED.clear()
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", _spy)
    return RECORDED


@pytest.fixture
def wired(db, monkeypatch, tmp_path):
    monkeypatch.setattr(m_poster, "_get_db", lambda: db)
    monkeypatch.setattr(m_poster, "_poster_dir", lambda: str(tmp_path))
    return db


def _grid(**kw):
    return json.loads(m_poster.generate_poster_grid(**kw))


def _mk(db, i, **kw):
    data = dict(title=f"格子小区 {i}号楼{i}0{i}", price=1_000_000 + i * 10_000, area=70.0 + i,
                community="格子小区", district="朝阳区", renovation="精装", status="available",
                property_type="second_hand")
    data.update(kw)
    return make_property(db, **data)["id"]


# ---------- ① 图上面积口径 ----------
def test_area_on_image_has_no_trailing_zero(wired, drawn):
    pids = [_mk(wired, i) for i in (1, 2)]
    out = _grid(property_ids=pids)
    assert out["success"] is True, out
    assert all(x.endswith("㎡") and ".0㎡" not in x for x in drawn if x.endswith("㎡")), drawn
    assert "71㎡" in drawn and "72㎡" in drawn, drawn


# ---------- ② 不存在 / 已售 / 已租 ----------
def test_missing_id_says_so(wired, drawn):
    out = _grid(property_ids=[999999])
    assert out["success"] is False, out
    assert "没有编号 999999 的房源" in out["error"], out
    assert out["unavailable"][0]["status"] == "不存在", out


@pytest.mark.parametrize("status,phrase,label", [("sold", "已经售出", "已售"),
                                                 ("rented", "已经出租", "已租")])
def test_unavailable_states_are_named(wired, drawn, status, phrase, label):
    pid = _mk(wired, 1, status=status)
    out = _grid(property_ids=[pid])
    assert out["success"] is False and "grid_path" not in out, out
    assert phrase in out["error"] and "不能做九宫格" in out["error"], out
    assert out["unavailable"][0]["status"] == label, out


# ---------- ③ 超过 9 套 ----------
def test_more_than_nine_explains_truncation(wired, drawn):
    pids = [_mk(wired, i) for i in range(1, 13)]
    out = _grid(property_ids=pids)
    assert out["success"] is True, out
    assert out["count"] == 9 and out["total"] == 12 and out["truncated"] is True, out
    assert len(out["property_ids"]) == 9, out
    warn = " ".join(out.get("warnings") or [])
    assert "只画了前 9 套" in warn and "11" in warn and "12" in warn, out
    assert "只画了前 9 套" in out["message"], out


def test_nine_or_fewer_has_no_truncation_warning(wired, drawn):
    pids = [_mk(wired, i) for i in range(1, 4)]
    out = _grid(property_ids=pids)
    assert out["count"] == 3 and out["total"] == 3 and out["truncated"] is False, out
    assert not out.get("warnings"), out


# ---------- ④ 重复编号 ----------
def test_duplicate_ids_drawn_once(wired, drawn):
    pid = _mk(wired, 1)
    out = _grid(property_ids=[pid, pid])
    assert out["property_ids"] == [pid], out
    assert "给了两次" in " ".join(out.get("warnings") or []), out
    titles = [x for x in drawn if "格子小区" in x]
    assert len(titles) == 1, drawn


# ---------- ⑤⑥ 房号档位 ----------
@pytest.mark.parametrize("bad", ["乱写的档位", "show", "unitx"])
def test_bad_room_no_mode_gives_options_and_no_image(wired, drawn, bad):
    pid = _mk(wired, 1)
    out = _grid(property_ids=[pid], show_room_no=bad)
    assert out["success"] is False and "grid_path" not in out, out
    for word in ("完整写", "只写楼栋单元", "小区名"):
        assert word in out["error"], out
    # 问经纪人的选项只给中文：别把内部值念出来（2026-09-29 实测就是这么漏出去的）
    assert "full 写" not in out["error"] and "none 不写" not in out["error"], out


@pytest.mark.parametrize("value", [None, ""])
def test_default_mode_is_unit_when_not_given(wired, drawn, value):
    """没给/空值 = 用默认档 unit（文档承诺的默认行为）"""
    pid = _mk(wired, 1, title="格子小区 3号楼1602")
    out = _grid(property_ids=[pid], show_room_no=value)
    assert out["success"] is True, out
    assert not any("1602" in x for x in drawn), drawn


def test_unit_and_none_hide_room_no(wired, drawn):
    pid = _mk(wired, 1, title="格子小区 3号楼1602 急售")
    for mode in ("unit", "none"):
        RECORDED.clear()
        out = _grid(property_ids=[pid], show_room_no=mode)
        assert out["success"] is True, out
        assert not any("1602" in x for x in drawn), (mode, drawn)


def test_full_mode_keeps_room_no_visible(wired, drawn):
    """full 档要从图上看得见房号（原先从尾部截断成了「格子小区 3号楼…」）"""
    pid = _mk(wired, 1, title="格子小区 3号楼1602 急售")
    out = _grid(property_ids=[pid], show_room_no="full")
    assert out["success"] is True, out
    assert any("1602" in x for x in drawn), drawn


# ---------- ⑦ 成品文件名 ----------
def test_grid_filename_gets_fingerprint_and_is_stable(wired, drawn, tmp_path):
    pids = [_mk(wired, i) for i in (1, 2)]
    a = _grid(property_ids=pids)
    b = _grid(property_ids=pids)
    assert re.search(r"poster_grid_[0-9a-f]{8}\.png$", a["grid_path"]), a["grid_path"]
    assert a["grid_path"] == b["grid_path"], (a["grid_path"], b["grid_path"])
    assert os.path.exists(a["grid_path"])
    assert Image.open(a["grid_path"]).size == (1080, 1080)


def test_grid_is_covered_by_the_prune_pattern(wired):
    """九宫格成品要落在「每套房各留 5 份」的清理范围内（九宫格自己算一个桶）"""
    assert m_poster._POSTER_ARTIFACT_RE.match("poster_grid.png"), "grid 基础名不在清理范围"
    assert m_poster._POSTER_ARTIFACT_RE.match("poster_grid_deadbeef.png"), "grid 指纹名不在清理范围"
    assert m_poster._POSTER_ARTIFACT_RE.match("poster_1_A_deadbeef.png"), "海报指纹名不在清理范围"
    assert not m_poster._POSTER_ARTIFACT_RE.match("poster_qr.png"), "二维码不该被清理"
    assert not m_poster._POSTER_ARTIFACT_RE.match("house_photo.jpg"), "房源照片不该被清理"
    matched = m_poster._POSTER_ARTIFACT_RE.match("poster_1_A_deadbeef.png")
    assert matched.group("pid") == "1", "要能认出是哪套房（名额按房源分）"
    assert m_poster._POSTER_ARTIFACT_RE.match("poster_grid_deadbeef.png").group("grid") == "grid"


@pytest.mark.parametrize("name", ["poster_1_A.png.svg", "poster_1_A_deadbeef.png.svg",
                                  "poster_grid.png.svg", "poster_grid_deadbeef.png.svg",
                                  "poster_1_A.svg"])
def test_svg_companions_are_covered(name):
    """SVG 源文件的名字是「成品名 + .svg」，也要被清理认到（否则会一直堆积）"""
    assert m_poster._POSTER_ARTIFACT_RE.match(name), name


def test_prune_groups_png_with_its_svg(wired, tmp_path):
    """同一张图的 .png 与它的 .svg 算一组：一起留、一起删（老实现把 .svg 漏在外面）"""
    for i in range(7):                                   # 同一套房 7 份 → 留最近 5 份
        (tmp_path / f"poster_1_A_{i:08x}.png").write_bytes(b"x")
        (tmp_path / f"poster_1_A_{i:08x}.png.svg").write_text("<svg/>")
        os.utime(tmp_path / f"poster_1_A_{i:08x}.png", (1_600_000_000 + i, 1_600_000_000 + i))
        os.utime(tmp_path / f"poster_1_A_{i:08x}.png.svg", (1_600_000_000 + i, 1_600_000_000 + i))
    fresh = tmp_path / "poster_1_A.png"
    fresh.write_bytes(b"new")
    m_poster._stamp_and_prune(str(fresh))
    left = sorted(f.name for f in tmp_path.glob("poster_*"))
    assert not [n for n in left if n.startswith("poster_1_A_00000000")], f"最旧那组没被清掉：{left[:4]}"
    assert len([n for n in left if n.endswith(".png")]) == 5, left
    # 留下的每张 png 都该带着自己的 svg（不被拆散）
    left_pngs = {n for n in left if n.endswith(".png")}
    left_svgs = {n[: -len(".svg")] for n in left if n.endswith(".png.svg")}
    assert left_svgs <= left_pngs, (sorted(left_pngs)[:3], sorted(left_svgs)[:3])
    assert not [n for n in left if n.startswith("poster_1_A_00000000") and n.endswith(".png.svg")]


# ---------- ⑧ 参数已收敛 ----------
def test_qr_content_removed_from_schema():
    from tools.registry import registry

    props = registry.get_entry("generate_poster_grid").schema["parameters"]["properties"]
    assert "qr_content" not in props, props          # 声明了却没用上的参数已按拍板移除
    assert set(props) == {"property_ids", "show_room_no"}, props
    desc = registry.get_entry("generate_poster_grid").schema["description"]
    assert "最多 9 套" in desc and "超过 9 套" in desc, desc
