"""参考图风格（自定义款）海报的回归测试（2026-09-21 加）。

要求：经纪人发一张参考海报图 → Coco 参考它的风格出图。
实现：Coco 用看图能力提取「可参数化」的风格要素（版式/配色/字体气质/显示哪些信息/装饰），
本工具按这些参数渲染 —— **同风格，不是像素级复刻**；参数非法一律收敛到安全取值，绝不空图。

本文件钉住：三种版式可渲染、配色/字体确实生效、参数收敛与提示、以及"该问的先问"。
"""
import json

import pytest


def _prop_dict(**over):
    p = {"title": "海口美兰区桂林洋海阔天空, 7号楼2单元1602", "community": "海阔天空",
         "district": "美兰-桂林洋", "area": 110.0, "rooms": 3, "halls": 2, "price": 2_600_000,
         "property_type": "second_hand", "floor": "16层", "orientation": "北", "renovation": "精装",
         "tags": "满五唯一,地铁房", "unit_price": 23636}
    p.update(over)
    return p


def _render(style, mode="unit", photo=None):
    from tools.real_estate_poster_svg import render

    d = {"template": "CUSTOM", "properties": [_prop_dict()], "agent": {"company": "某某地产", "name": "李经理",
                                                                       "phone": "13800000000"},
         "title": "今日主推", "footer": "房源信息以实际看房为准", "room_no_mode": mode,
         "photo_path": photo, "style": style}
    import tempfile
    import os

    out = os.path.join(tempfile.mkdtemp(), "p.png")
    return render(d, out)


class TestStyleNormalization:
    def test_valid_style_passes_through(self):
        from tools.real_estate_poster_svg import normalize_style

        s = normalize_style({"layout": "split", "palette": "navy", "font_style": "serif",
                             "show_fields": ["price", "floor"], "decor": "sharp"})
        assert s["layout"] == "split" and s["palette"] == "navy" and s["font_style"] == "serif"
        assert s["show_fields"] == ["price", "floor"] and s["decor"] == "sharp"
        assert "左右分栏" in s["summary"]

    def test_unknown_values_fall_back_with_notes(self):
        from tools.real_estate_poster_svg import normalize_style

        notes: list = []
        s = normalize_style({"layout": "花里胡哨", "palette": "土豪金", "font_style": "隶书",
                             "show_fields": ["价格", "不存在"]}, notes)
        assert s["layout"] == "hero_top" and s["font_style"] == "sans"
        assert s["show_fields"] == ["price"]
        assert any("版式" in n for n in notes) and any("配色" in n for n in notes)
        assert any("字体" in n for n in notes) and any("不存在" in n for n in notes)

    def test_custom_hex_colors_accepted_and_bad_ones_rejected(self):
        from tools.real_estate_poster_svg import normalize_style

        notes: list = []
        s = normalize_style({"palette": {"bg": "#123456", "accent": "红的不对"}}, notes)
        assert s["colors"]["bg"] == "#123456"
        assert any("不是 #RRGGBB" in n for n in notes)

    def test_chinese_field_names_normalized(self):
        from tools.real_estate_poster_svg import normalize_style

        s = normalize_style({"show_fields": ["价格", "楼层", "朝向", "标签"]})
        assert s["show_fields"] == ["price", "floor", "orientation", "tags"]


class TestCustomRender:
    @pytest.mark.parametrize("layout", ["hero_top", "minimal", "split"])
    @pytest.mark.parametrize("palette", ["red_gold", "cream", "navy"])
    def test_renders_all_layouts_and_palettes(self, layout, palette):
        if not __import__("shutil").which("rsvg-convert"):
            pytest.skip("未安装 rsvg-convert")
        r = _render({"layout": layout, "palette": palette, "font_style": "sans",
                     "show_fields": ["price", "area", "layout", "floor", "orientation", "tags"]})
        assert r.get("success") is True, r
        assert r.get("template") == "CUSTOM"

    def test_palette_and_font_actually_applied(self):
        if not __import__("shutil").which("rsvg-convert"):
            pytest.skip("未安装 rsvg-convert")
        r = _render({"layout": "split", "palette": "navy", "font_style": "serif"})
        svg = open(r["png_path"] + ".svg", encoding="utf-8").read()
        assert "#0E2A47" in svg, "藏蓝底色没生效"
        assert "Noto Serif CJK SC" in svg, "衬线字体没生效"

    def test_room_no_mask_applies(self):
        if not __import__("shutil").which("rsvg-convert"):
            pytest.skip("未安装 rsvg-convert")
        unit_svg = open(_render({"layout": "hero_top", "palette": "red_gold"}, mode="unit")["png_path"] + ".svg",
                        encoding="utf-8").read()
        none_svg = open(_render({"layout": "hero_top", "palette": "red_gold"}, mode="none")["png_path"] + ".svg",
                        encoding="utf-8").read()
        assert "1602" not in unit_svg and "1602" not in none_svg
        assert "海阔天空" in none_svg


class TestCustomTemplateAsksFirst:
    def _registry(self, tmp_path, monkeypatch):
        url = f"sqlite:///{tmp_path}/custom.db"
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("COCO_ENC_KEY", "")
        from agent.real_estate_db import init_real_estate_db

        init_real_estate_db(url)
        import model_tools  # noqa: F401
        from tools import real_estate_settings as settings

        settings.save_agent_card(name="李经理", phone="13800000000", wechat="hk-1", company="某某地产")
        from tools.registry import registry

        return registry

    def test_style_shows_fields_that_are_missing_are_asked(self, tmp_path, monkeypatch):
        """风格里要显示楼层/朝向，但库里没有 → 先问清（不先出图）"""
        registry = self._registry(tmp_path, monkeypatch)
        added = json.loads(registry.get_entry("add_property").handler(
            {"title": "雅居乐金沙湾 3室2厅", "community": "雅居乐金沙湾", "price": 2_600_000, "area": 115.0},
            session_id="agent:main:feishu:dm:oc_x"))
        pid = added["property"]["id"]
        out = json.loads(registry.get_entry("generate_property_poster").handler(
            {"property_id": pid, "template": "CUSTOM", "poster_title": "今日主推",
             "style": {"layout": "hero_top", "palette": "red_gold", "show_fields": ["price", "floor", "orientation"]}},
            session_id="agent:main:feishu:dm:oc_x"))
        assert out["success"] is False and out.get("need_info") is True
        joined = "；".join(out["missing"])
        assert "楼层" in joined or "朝向" in joined

    def test_room_no_is_asked_for_custom(self, tmp_path, monkeypatch):
        registry = self._registry(tmp_path, monkeypatch)
        added = json.loads(registry.get_entry("add_property").handler(
            {"title": "海口某小区 2号楼2单元1602", "community": "某小区", "price": 2_600_000, "area": 115.0,
             "orientation": "北"},
            session_id="agent:main:feishu:dm:oc_x"))
        out = json.loads(registry.get_entry("generate_property_poster").handler(
            {"property_id": added["property"]["id"], "template": "CUSTOM", "poster_title": "今日主推",
             "style": {"layout": "minimal", "palette": "cream"}},
            session_id="agent:main:feishu:dm:oc_x"))
        assert out["success"] is False
        assert any("房号" in m for m in out["missing"]), out["missing"]

    def test_style_summary_returned_for_confirmation(self, tmp_path, monkeypatch):
        """出图后要能拿到风格摘要，让 Coco 复述给经纪人确认"""
        registry = self._registry(tmp_path, monkeypatch)
        added = json.loads(registry.get_entry("add_property").handler(
            {"title": "海口某小区 2号楼2单元1602", "community": "某小区", "price": 2_600_000, "area": 115.0,
             "orientation": "北", "floor": "16层"},
            session_id="agent:main:feishu:dm:oc_x"))
        out = json.loads(registry.get_entry("generate_property_poster").handler(
            {"property_id": added["property"]["id"], "template": "custom", "poster_title": "今日主推",
             "show_room_no": "unit", "style": {"layout": "minimal", "palette": "navy", "font_style": "serif"}},
            session_id="agent:main:feishu:dm:oc_x"))
        assert out.get("success") is True, out
        assert "版式" in out["style_summary"] and "配色" in out["style_summary"]
