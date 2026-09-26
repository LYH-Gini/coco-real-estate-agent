"""调价记录与反匹配提醒测试（功能1，批次二）"""
import json

from conftest import make_customer, make_property

from tools.real_estate_property import price_history, price_drop_alerts


# ==================== 调价自动记录 ====================

class TestPriceHistoryAuto:
    def test_price_change_recorded(self, db):
        """update_property 改价 → 自动写历史"""
        p = make_property(db, price=2_800_000)
        db.update_property(p["id"], price=2_650_000)
        h = db.get_price_history(p["id"])
        assert len(h) == 1
        assert h[0]["old_price"] == 2_800_000
        assert h[0]["new_price"] == 2_650_000
        assert h[0]["change"] == -150_000

    def test_price_increase_also_recorded(self, db):
        p = make_property(db, price=3_000_000)
        db.update_property(p["id"], price=3_200_000)
        h = db.get_price_history(p["id"])
        assert h[0]["change"] == 200_000

    def test_no_price_change_no_record(self, db):
        """改其他字段不产生调价记录"""
        p = make_property(db, price=3_000_000)
        db.update_property(p["id"], title="改名了")
        assert db.get_price_history(p["id"]) == []


# ==================== 降价反匹配 ====================

class TestPriceDropAlerts:
    def _setup_drop(self, db, old=2_800_000, new=2_650_000):
        p = make_property(db, price=old)
        db.update_property(p["id"], price=new, price_change_reason="房东急售")
        return p

    def test_budget_gap_customer_matched(self, db):
        """预算上限 270万 < 旧价280万 且 >= 新价*0.95(251.75万) → 命中"""
        self._setup_drop(db)
        make_customer(db, name="张三", budget_min=2_000_000, budget_max=2_700_000)
        p2 = make_property(db, title="占位房", price=1_000_000, area=50.0)  # 避免空库干扰
        matches = db.find_customers_for_price_drop(1)
        assert len(matches) == 1
        assert matches[0]["name"] == "张三"

    def test_gap_over_5pct_not_matched(self, db):
        """预算上限 < 新价*95% → 不命中（差太多不捞）"""
        self._setup_drop(db, old=2_800_000, new=2_500_000)
        # 新价*0.95 = 237.5万；预算 230万 < 237.5万 → 不命中
        make_customer(db, budget_min=1_800_000, budget_max=2_300_000)
        assert db.find_customers_for_price_drop(1) == []

    def test_already_affordable_before_not_matched(self, db):
        """预算本来就 >= 旧价的客户不属于"差一点"→ 不命中"""
        self._setup_drop(db, old=2_800_000, new=2_650_000)
        make_customer(db, budget_min=2_500_000, budget_max=3_000_000)  # 本来就买得起
        assert db.find_customers_for_price_drop(1) == []

    def test_deal_customer_excluded(self, db):
        """已成交客户不出现在捞回名单"""
        p = self._setup_drop(db)
        c = make_customer(db, budget_min=2_000_000, budget_max=2_700_000)
        db.add_deal(customer_id=c["id"], property_id=p["id"], stage="deposit")
        assert db.find_customers_for_price_drop(p["id"]) == []

    def test_price_rise_not_alerted(self, db):
        """涨价不触发反匹配"""
        p = make_property(db, price=2_500_000)
        db.update_property(p["id"], price=2_800_000)  # 涨价
        make_customer(db, budget_min=2_000_000, budget_max=2_700_000)
        assert db.find_customers_for_price_drop(p["id"]) == []


# ==================== 工具层 ====================

class TestPriceTools:
    def test_price_history_tool(self, db, monkeypatch):
        monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
        p = make_property(db, price=3_000_000)
        db.update_property(p["id"], price=2_800_000)
        r = json.loads(price_history(property_id=p["id"]))
        assert r["success"] is True
        assert r["history"][0]["change"] == -200_000

    def test_price_drop_alerts_tool(self, db, monkeypatch):
        monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
        p = make_property(db, price=2_800_000, title="急售两房")
        db.update_property(p["id"], price=2_600_000)
        make_customer(db, name="李四", budget_min=2_100_000, budget_max=2_700_000)
        r = json.loads(price_drop_alerts(days=7))
        assert r["success"] is True
        assert len(r["alerts"]) == 1
        assert r["alerts"][0]["matched_customers"][0]["name"] == "李四"

    def test_price_drop_alerts_empty(self, db, monkeypatch):
        monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
        make_property(db)
        r = json.loads(price_drop_alerts(days=7))
        assert r["success"] is True
        assert r["alerts"] == []
