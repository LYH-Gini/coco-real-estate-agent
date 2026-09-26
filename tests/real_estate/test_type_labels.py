"""房源/客户类型标签口径测试（2026-09-18：new 显示名改为「一手房」）"""
import json

from conftest import make_property


class TestTypeLabels:
    def test_new_display_label_is_yishou(self):
        """new 的展示标签是「一手房」，不是含糊的「新房」"""
        import tools.real_estate_property as t
        assert t._TYPE_LABELS["new"] == "一手房"
        assert t._TYPE_LABELS["second_hand"] == "二手房"
        assert t._TYPE_LABELS["rental"] == "租房"

    def test_detail_message_uses_yishou(self, db, monkeypatch):
        """房源详情里显示「一手房」（避免经纪人误读成新录入的房源）"""
        import tools.real_estate_property as t
        monkeypatch.setattr(t, "_get_db", lambda: db)
        p = make_property(db, title="某新盘 1号楼101", price=2_000_000, area=100.0, property_type="new")
        data = json.loads(t.get_property_detail(property_id=p["id"]))
        assert data["success"] is True
        assert "一手房" in data["message"]
        assert "类型 一手房" in data["message"]

    def test_customer_form_uses_yishou(self):
        """客户登记模板里也写「买一手房」"""
        import tools.real_estate_customer as c
        data = json.loads(c.get_customer_form())
        assert "买一手房" in data["form"]

    def test_prompt_keeps_synonyms(self):
        """提示词必须同时认「一手房/新房/新盘」，否则经纪人按老说法说会抽不到类型"""
        import agent.real_estate_prompt as p
        text = p.get_real_estate_prompt()
        assert "一手房/新房/新盘" in text          # 房源类型归一化
        assert "买一手房/买新房" in text            # 客户类型归一化
