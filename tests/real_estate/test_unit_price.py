"""房源单价自动计算测试（2026-09-18 加）

单价改为读取时按 总价÷面积 现算、保留两位小数（历史 unit_price 列已由迁移 009 删除），
避免"录入那一刻算一次、之后改价不改单价"导致展示层拿到空值或过期值。
"""
import json

from conftest import make_property
from agent.real_estate_db import Property


class TestUnitPrice:
    def test_computed_with_two_decimals(self, db):
        """总价 38.35万 / 面积 34.68㎡ → 11058.25 元/㎡（两位小数，不取整）"""
        p = make_property(db, price=383_500, area=34.68)
        assert p["unit_price"] == 11058.25

    def test_follows_price_change(self, db):
        """改总价后单价跟着变（旧实现只在录入时算一次，改价不重算）"""
        p = make_property(db, price=383_500, area=34.68)
        updated = db.update_property(p["id"], price=400_000)
        assert updated["unit_price"] == 11534.03
        # 重新读一遍也要是新单价（不是那个已经改动的返回值在自证）
        assert db.search_properties(title="测试房源")[0]["unit_price"] == 11534.03

    def test_follows_area_change(self, db):
        """改面积后单价跟着变"""
        p = make_property(db, price=400_000, area=100.0)
        updated = db.update_property(p["id"], area=80.0)
        assert updated["unit_price"] == 5000.0

    def test_none_when_area_missing(self, db):
        """面积缺失/为 0 → 算不出单价，返回 None（不编造数字）"""
        p = make_property(db, price=300_000, area=0)
        assert p["unit_price"] is None

    def test_no_unit_price_column(self):
        """单价不再占用数据库列（迁移 009 删列；防止有人再加回存储列）"""
        assert "unit_price" not in {c.name for c in Property.__table__.columns}

    def test_tool_layer_add_property(self, db, monkeypatch):
        """工具层录入房源后直接返回算好的单价"""
        import tools.real_estate_property as t
        monkeypatch.setattr(t, "_get_db", lambda: db)
        out = json.loads(t.add_property(title="263栋1006 1室2厅", price=383_500, area=34.68))
        assert out["success"] is True
        assert out["property"]["unit_price"] == 11058.25

    def test_tool_layer_update_property(self, db, monkeypatch):
        """工具层改总价后返回的单价同步更新"""
        import tools.real_estate_property as t
        monkeypatch.setattr(t, "_get_db", lambda: db)
        p = make_property(db, price=383_500, area=34.68)
        out = json.loads(t.update_property(property_id=p["id"], price=400_000))
        assert out["success"] is True
        assert out["property"]["unit_price"] == 11534.03
