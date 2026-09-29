"""海报 v2（SVG 引擎 + 信息齐全校验 + 经纪人名片 + 标题候选）测试。

隔离原则：使用 sqlite 临时库；海报输出写到 tmp_path；渲染类测试在缺少 rsvg-convert
的环境（如精简 CI）自动跳过。
"""
from __future__ import annotations

import json
import shutil

import pytest

import tools.real_estate_poster as poster
import tools.real_estate_settings as settings

HAS_RSVG = shutil.which("rsvg-convert") is not None
needs_rsvg = pytest.mark.skipif(not HAS_RSVG, reason="未安装 rsvg-convert（librsvg2-bin）")


@pytest.fixture
def wired(db, tmp_path, monkeypatch):
    """把海报/设置工具接到临时库，并把输出目录指向 tmp_path"""
    monkeypatch.setattr(poster, "_get_db", lambda: db)
    monkeypatch.setattr(settings, "_get_db", lambda: db)
    monkeypatch.setattr(poster, "_poster_dir", lambda: str(tmp_path))
    return db


def _prop(db, **over):
    data = dict(title="262栋1009", community="海岸雅居", district="美兰-海甸岛",
                price=1_280_000, area=89.5, rooms=2, halls=1, renovation="精装",
                property_type="second_hand", status="available", tags="满五唯一,采光通透")
    data.update(over)
    return db.add_property(**data)


def _save_card(**over):
    data = dict(name="李经理", phone="138-0000-0000", wechat="hk-6688", company="海口中房联 · 海甸岛门店")
    data.update(over)
    return json.loads(settings.save_agent_card(**data))


# ---------------- 经纪人名片 ----------------
def test_save_and_get_agent_card(wired):
    assert settings.get_agent_card_or_empty() == {"name": "", "phone": "", "wechat": "", "company": ""}
    res = json.loads(settings.save_agent_card(company="海口中房联"))
    assert res["success"] and res["card"]["company"] == "海口中房联"
    assert set(res["still_missing"]) == {"姓名", "电话", "微信"}
    card = json.loads(settings.get_agent_card())
    assert card["card"]["company"] == "海口中房联"
    assert "姓名" in card["missing"]


def test_save_agent_card_partial_keeps_old_values(wired):
    settings.save_agent_card(name="王经理", company="宇恒房产")
    settings.save_agent_card(phone="139-0000-0000")
    card = settings.get_agent_card_or_empty()
    # 2026-09-27（第十二组 F414）起手机号会写法归一（去掉横线/空格/国码），
    # 免得海报与名片上照印「139-0000-0000」这种写法
    assert card == {"name": "王经理", "phone": "13900000000", "wechat": "", "company": "宇恒房产"}


def test_save_agent_card_empty_input_rejected(wired):
    res = json.loads(settings.save_agent_card())
    assert res["success"] is False


def test_company_saved_as_brand_for_legacy(wired):
    """公司名同时写入旧字段 brand_name，旧海报引擎也能读到"""
    settings.save_agent_card(company="海口中房联")
    assert settings.get_brand_or_none() == "海口中房联"


# ---------------- 标题候选 ----------------
def test_suggest_poster_titles(wired):
    pid = _prop(wired)["id"]
    res = json.loads(poster.suggest_poster_titles(property_id=pid))
    assert res["success"] is True
    assert 2 <= len(res["candidates"]) <= 3
    assert len(set(res["candidates"])) == len(res["candidates"])


def test_title_candidates_by_type():
    rental = poster._title_candidates({"property_type": "rental"})
    new = poster._title_candidates({"property_type": "new"})
    second = poster._title_candidates({"property_type": "second_hand"})
    assert rental and new and second
    assert set(rental) != set(second)
    for cands in (rental, new, second):
        assert len(cands) <= 3


# ---------------- 信息齐全校验 ----------------
def test_missing_info_blocks_render(wired):
    """名片没有 → 不出图，返回 missing 清单（规矩：信息不齐不许乱出）"""
    pid = _prop(wired)["id"]
    res = json.loads(poster.generate_property_poster(property_id=pid, poster_title="仅此一套",
                                                     template="A", show_room_no="full"))
    assert res["success"] is False
    assert res["need_info"] is True
    assert any("公司" in m for m in res["missing"])
    assert any("联系方式" in m for m in res["missing"])
    assert "一次问清" in res["ask"]


def test_missing_title_returns_candidates(wired):
    _save_card()
    pid = _prop(wired)["id"]
    res = json.loads(poster.generate_property_poster(property_id=pid, template="A", show_room_no="full"))
    assert res["success"] is False
    assert res["need_title"] is True
    assert 2 <= len(res["candidates"]) <= 3


def test_missing_property_fields_listed(wired, monkeypatch):
    """房源缺面积/价格 → 计入 missing 清单（用桩库模拟脏数据）"""
    _save_card()

    class _StubDB:
        def get_available_property(self, pid):
            return {"id": pid, "title": "262栋1009", "community": "海岸雅居",
                    "price": None, "area": None, "property_type": "second_hand"}

    monkeypatch.setattr(poster, "_get_db", lambda: _StubDB())
    res = json.loads(poster.generate_property_poster(property_id=1, poster_title="今日主推",
                                                     template="A", show_room_no="full"))
    assert res["need_info"] is True
    assert any("建筑面积" in m for m in res["missing"])
    assert any("价格" in m for m in res["missing"])


def test_template_b_without_photo_asks_photo(wired):
    _save_card()
    pid = _prop(wired)["id"]
    res = json.loads(poster.generate_property_poster(property_id=pid, poster_title="今日主推", template="B"))
    assert res["success"] is False
    assert res["need_photo"] is True
    assert set(res["templates_without_photo"]) == {"A"}


@needs_rsvg
def test_template_b_with_photo_renders(wired, tmp_path):
    photo = tmp_path / "room.png"
    from PIL import Image

    Image.new("RGB", (400, 300), (120, 90, 60)).save(photo)
    _save_card()
    # B 款需要 照片+楼层+朝向（2026-09-21 起出图前会校验）；这里直接写库，所以楼层要显式给
    pid = _prop(wired, images=str(photo), floor="10层", orientation="北")["id"]
    res = json.loads(poster.generate_property_poster(property_id=pid, poster_title="今日主推", template="B"))
    assert res["success"] is True
    assert res["template"] == "B"


def test_allow_missing_renders_anyway(wired):
    """经纪人明确说"先出图"时允许缺项出图（allow_missing）"""
    pid = _prop(wired)["id"]
    res = json.loads(poster.generate_property_poster(
        property_id=pid, poster_title="仅此一套", allow_missing=True))
    assert res["success"] is True
    assert any("公司名称" in n for n in res["notes"])  # 未提供公司名 → 明确提示未显示品牌


# ---------------- 渲染与合规 ----------------
@needs_rsvg
def test_render_no_platform_branding_and_has_footer(wired, tmp_path):
    _save_card()
    pid = _prop(wired)["id"]
    res = json.loads(poster.generate_property_poster(property_id=pid, poster_title="仅此一套",
                                                     template="A", show_room_no="full"))
    assert res["success"] is True
    png = res["poster_path"]
    svg = open(png + ".svg", encoding="utf-8").read()
    for bad in ("Coco", "COCO", "可可"):
        assert bad not in svg, f"海报不应出现平台名 {bad}"
    assert "海口中房联" in svg            # 只显示经纪人公司名
    assert "房源信息以实际看房为准" in svg


@needs_rsvg
def test_no_qr_generated_from_wechat_id(wired, tmp_path):
    """有微信号也不自动生成二维码：拿微信号生成的码扫出来只是一串文字，微信里加不上好友"""
    _save_card()
    pid = _prop(wired)["id"]
    res = json.loads(poster.generate_property_poster(property_id=pid, poster_title="仅此一套",
                                                     template="A", show_room_no="full"))
    assert res["success"] is True
    svg = open(res["poster_path"] + ".svg", encoding="utf-8").read()
    assert "扫码加我微信" not in svg
    assert "微信 hk-6688" in svg


@needs_rsvg
def test_company_absent_hides_brand_bar(tmp_path, db, monkeypatch):
    monkeypatch.setattr(poster, "_get_db", lambda: db)
    monkeypatch.setattr(settings, "_get_db", lambda: db)
    monkeypatch.setattr(poster, "_poster_dir", lambda: str(tmp_path))
    settings.save_agent_card(name="李经理", phone="138-0000-0000")
    pid = _prop(db)["id"]
    # 经纪人没给公司名 → 缺项拦截；他明确说"先出图"时才出，且不写品牌（不臆造）
    blocked = json.loads(poster.generate_property_poster(property_id=pid, poster_title="今日主推",
                                                         template="A", show_room_no="full"))
    assert blocked["need_info"] is True and any("公司" in m for m in blocked["missing"])
    res = json.loads(poster.generate_property_poster(
        property_id=pid, poster_title="今日主推", allow_missing=True))
    assert res["success"] is True
    svg = open(res["poster_path"] + ".svg", encoding="utf-8").read()
    assert "李经理" in svg
    assert "Coco" not in svg and "可可" not in svg
    assert "海口中房联" not in svg          # 未提供公司名 → 品牌栏不出现，也不编造


@needs_rsvg
def test_template_auto_pick_prefers_b_for_high_end_with_photo(wired, tmp_path):
    photo = tmp_path / "big.png"
    from PIL import Image

    Image.new("RGB", (300, 300), (30, 30, 30)).save(photo)
    _save_card()
    pid = _prop(wired, area=140.0, renovation="豪装", images=str(photo), floor="10层", orientation="朝南")["id"]
    # 现在模板默认要问经纪人；"你看着办"= 传 auto 时才按房源特征自动挑（本用例测的正是这个）
    res = json.loads(poster.generate_property_poster(property_id=pid, poster_title="今日主推", template="auto"))
    assert res["template"] == "B"
    assert res["success"] is True


class TestAskBeforeRendering:
    """出图前必须先问：模板选哪款、房号要不要写（2026-09-21 要求）"""

    def test_asks_template_when_not_specified(self, wired):
        _save_card()
        pid = _prop(wired)["id"]
        res = json.loads(poster.generate_property_poster(property_id=pid, poster_title="今日主推"))
        assert res["success"] is False and res["need_template"] is True
        codes = [t["code"] for t in res["templates"]]
        assert codes == ["A", "B", "CUSTOM"]      # 模板库：A/B + 参考图风格（自定义款）
        assert any("模板" in m for m in res["missing"])

    def test_asks_room_no_for_template_that_prints_it(self, wired):
        """A 款会印房源标识行（含房号）→ 必须问经纪人要不要写"""
        _save_card()
        pid = _prop(wired)["id"]
        res = json.loads(poster.generate_property_poster(property_id=pid, poster_title="今日主推",
                                                         template="A"))
        assert res["success"] is False
        assert any("房号" in m for m in res["missing"]), res["missing"]

    def test_b_does_not_ask_room_no(self, wired, tmp_path):
        """B 款不印房号 → 不该为它多问一轮"""
        from PIL import Image

        photo = tmp_path / "p.png"
        Image.new("RGB", (300, 200), (10, 10, 10)).save(photo)
        _save_card()
        pid = _prop(wired, images=str(photo), floor="10层", orientation="北")["id"]
        res = json.loads(poster.generate_property_poster(property_id=pid, poster_title="今日主推",
                                                         template="B"))
        assert res.get("success") is True, res    # 信息齐 → 直接出图，不再问房号
        assert not any("房号" in m for m in (res.get("missing") or []))


class TestRoomNoMasking:
    PROP = {"title": "海口美兰区桂林洋海阔天空, 7号楼2单元1602", "community": "海阔天空",
            "district": "美兰-桂林洋", "area": 110.0, "rooms": 3, "halls": 2, "price": 2_000_000,
            "property_type": "second_hand", "floor": "16层", "orientation": "北"}

    def _svg(self, mode):
        from tools.real_estate_poster_svg import template_a

        return template_a({"properties": [dict(self.PROP)], "agent": {"company": "某某地产", "name": "李"},
                           "title": "今日主推", "room_no_mode": mode})

    def test_full_keeps_room_no(self):
        assert "1602" in self._svg("full")

    def test_unit_hides_room_no_but_keeps_building(self):
        svg = self._svg("unit")
        assert "1602" not in svg
        assert "7号楼2单元" in svg

    def test_none_shows_community_only(self):
        svg = self._svg("none")
        assert "1602" not in svg and "海阔天空" in svg

    def test_display_title_helper_masks(self):
        """标识行 = 小区 + 楼栋单元 + 房号（不用整条标题：标题里的面积/价格会跟着上墙）"""
        assert poster._poster_display_title(self.PROP, "full") == "海阔天空 7号楼2单元1602"
        assert poster._poster_display_title(self.PROP, "unit") == "海阔天空 7号楼2单元"
        assert poster._poster_display_title(self.PROP, "none") == "海阔天空"

    def test_normalize_room_no_mode_aliases(self):
        for raw, want in (("完整", "full"), ("只写楼栋", "unit"), ("不写", "none"), (None, None)):
            assert poster._norm_room_no_mode(raw) == want


def test_pick_template_reason_and_alias():
    code, reason = poster._pick_template({"area": 80}, "premium", "")
    assert code == "A" and "指定" in reason
    # 已删除的清单款：不再静默回落，而是返回 None 让 Coco 从模板库里重新问
    code2, why2 = poster._pick_template({"area": 80}, "list", "")
    assert code2 is None and "list" in why2


# ---------------- 价格与文本工具 ----------------
def test_price_text_formats():
    from tools import real_estate_poster_svg as svg

    assert svg._price_text({"price": 1_280_000, "property_type": "second_hand"}) == "128万"
    assert svg._price_text({"price": 3_200, "property_type": "rental"}) == "3200元/月"
    assert svg._price_text({"price": None}) == "价格待定"


def test_unit_price_text_formats():
    from tools import real_estate_poster_svg as svg

    assert "万/㎡" in svg._unit_price_text({"unit_price": 14301.68})
    assert "元/㎡" in svg._unit_price_text({"unit_price": 8500})
    assert svg._unit_price_text({}) == ""


def test_vector_engine_helpers():
    from tools import real_estate_poster_svg as svg

    assert svg._fit("很长的标题文字", 50, 40).endswith("…")
    assert svg._auto_size("短", 900, 100) == 100
    assert svg._auto_size("很长很长很长很长的标题文字", 200, 100, "body", 40) <= 100
    assert svg._layout_text({"rooms": 2, "halls": 1}) == "2室1厅"


def test_vector_engine_unknown_template():
    from tools import real_estate_poster_svg as svg

    res = svg.render({"template": "Z", "properties": [{}]})
    assert res["success"] is False
    assert "未知模板" in res["error"]


# ---------------- 工具注册 ----------------
def test_new_tools_registered_and_in_toolset():
    from tools.registry import registry

    names = set(registry.get_all_tool_names())
    for name in ("generate_property_poster", "suggest_poster_titles",
                 "save_agent_card", "get_agent_card"):
        assert name in names, f"{name} 未注册到 registry"
        assert name in registry.get_tool_names_for_toolset("real_estate"), f"{name} 不在 real_estate 工具集"
