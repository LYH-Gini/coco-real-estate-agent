"""海报出图前的信息齐全校验（2026-09-21 要求）。

要求原话：**「在做海报之前只要涉及需要朝向的就要提前询问经纪人，而不是图做完了发现问题才问。
只要海报需要的信息，在没拿到完整信息之前就要询问经纪人，拿到完整信息再执行作图」**。

B 极简高级款会显示「楼层 / 朝向」两栏 —— 所以缺这两项时必须**先问清再出图**，
而不是先出一张两栏是「—」的成品。A 红金促销款不显示这两栏，不要求它们。
"""
import json


class TestMissingCheck:
    PROP = {"title": "7号楼2单元1602", "area": 110.0, "price": 2_000_000}
    CARD = {"company": "某某地产", "name": "李经理"}

    def test_b_template_requires_floor_and_orientation(self):
        from tools.real_estate_poster import _missing_poster_info

        miss = _missing_poster_info(self.PROP, self.CARD, need_photo=False,
                                    need_floor=True, need_orientation=True)
        assert any("楼层" in m for m in miss), miss
        assert any("朝向" in m for m in miss), miss

    def test_a_template_does_not_require_them(self):
        from tools.real_estate_poster import _missing_poster_info

        miss = _missing_poster_info(self.PROP, self.CARD, need_photo=False)
        assert not any("楼层" in m or "朝向" in m for m in miss), miss

    def test_no_demand_when_values_present(self):
        from tools.real_estate_poster import _missing_poster_info

        prop = dict(self.PROP, floor="16层", orientation="北")
        miss = _missing_poster_info(prop, self.CARD, need_photo=False,
                                    need_floor=True, need_orientation=True)
        assert miss == [], miss

    def test_floor_hint_mentions_auto_inference(self):
        """缺楼层时，提示语要告诉 Coco「带房号会自动推断、不必问」"""
        from tools.real_estate_poster import _missing_poster_info

        miss = _missing_poster_info(self.PROP, self.CARD, need_photo=False, need_floor=True)
        assert any("自动" in m and "房号" in m for m in miss), miss


class TestPosterToolAsksFirst:
    def _prep(self, tmp_path, monkeypatch):
        url = f"sqlite:///{tmp_path}/poster.db"
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("COCO_ENC_KEY", "")
        from agent.real_estate_db import init_real_estate_db

        init_real_estate_db(url)
        import model_tools  # noqa: F401
        import tools.real_estate_poster as poster

        # 让他看起来“有照片、有名片”，从而走进 B 款分支（不真渲染）
        monkeypatch.setattr(poster, "_property_photo", lambda p: __file__)
        monkeypatch.setattr(poster, "_agent_card", lambda: {"company": "某某地产", "name": "李经理",
                                                           "phone": "13800000000"})
        from tools.registry import registry

        return registry, poster

    def test_refuses_and_lists_floor_orientation_before_rendering(self, tmp_path, monkeypatch):
        registry, _ = self._prep(tmp_path, monkeypatch)
        added = json.loads(registry.get_entry("add_property").handler(
            {"title": "雅居乐金沙湾 3室2厅", "community": "雅居乐金沙湾", "district": "秀英区",
             "price": 2_600_000, "area": 115.0, "rooms": 3, "halls": 2, "renovation": "毛坯"},
            session_id="agent:main:feishu:dm:oc_x",
        ))
        pid = added["property"]["id"]
        assert "inferred" not in added  # 无房号 → 推不出楼层

        out = json.loads(registry.get_entry("generate_property_poster").handler(
            {"property_id": pid, "template": "B", "poster_title": "新盘在售"},
            session_id="agent:main:feishu:dm:oc_x",
        ))
        assert out["success"] is False and out.get("need_info") is True, out
        joined = "；".join(out["missing"])
        assert "楼层" in joined and "朝向" in joined, out["missing"]
        assert "一次问清" in out["ask"], out["ask"]

    def test_b_with_photo_missing_only_photo_still_lists_all_three(self, tmp_path, monkeypatch):
        """B 款缺照片时，也要把楼层/朝向一并问清（一次问齐，不是先出图再补问）"""
        registry, poster = self._prep(tmp_path, monkeypatch)
        monkeypatch.setattr(poster, "_property_photo", lambda p: None)
        added = json.loads(registry.get_entry("add_property").handler(
            {"title": "某小区 2号楼2单元1902", "community": "某小区", "price": 1_900_000, "area": 120.0},
            session_id="agent:main:feishu:dm:oc_x",
        ))
        out = json.loads(registry.get_entry("generate_property_poster").handler(
            {"property_id": added["property"]["id"], "template": "B", "poster_title": "x"},
            session_id="agent:main:feishu:dm:oc_x",
        ))
        assert out.get("need_photo") is True, out
        joined = "；".join(out["missing"])
        assert "房源照片" in joined and "朝向" in joined, out["missing"]
