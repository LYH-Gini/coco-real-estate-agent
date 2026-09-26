"""流失预警 + 一键平替测试（功能2+4，批次三）"""
import json
from datetime import datetime, timedelta

from conftest import make_customer, make_property

from tools.real_estate_followup import churn_warning
from tools.real_estate_property import find_alternatives


# ==================== 流失预警 ====================

class TestChurnRisk:
    def test_never_contacted_medium_risk(self, db):
        """从未跟进 +40 分（中危）"""
        make_customer(db, name="新客未跟进", tier="A")
        rows = db.churn_risk_customers(min_risk=40)
        assert len(rows) == 1
        assert rows[0]["risk_score"] == 40
        assert "从未跟进" in rows[0]["signals"]

    def test_over_30_days_high_signal(self, db):
        """30天未联系 +50"""
        c = make_customer(db, name="久未联系")
        db.add_followup(customer_id=c["id"], type="电话", content="聊需求",
                        created_at=datetime.now() - timedelta(days=35))
        rows = db.churn_risk_customers()
        assert rows[0]["risk_score"] == 50
        assert rows[0]["risk_level"] == "中危"

    def test_s_tier_multiplier(self, db):
        """S级客户风险 ×1.5：40*1.5=60 → 高危"""
        make_customer(db, name="S级快凉", tier="S")
        rows = db.churn_risk_customers()
        assert rows[0]["risk_score"] == 60
        assert rows[0]["risk_level"] == "高危"

    def test_recent_followup_no_risk(self, db):
        """刚跟进过的客户不进名单"""
        c = make_customer(db, name="活跃客户")
        db.add_followup(customer_id=c["id"], type="电话", content="正常跟进")
        assert db.churn_risk_customers() == []

    def test_deal_customer_excluded(self, db):
        """已成交客户不进流失名单"""
        p = make_property(db)
        c = make_customer(db, name="成交客户")
        db.add_deal(customer_id=c["id"], property_id=p["id"], stage="finalized")
        assert db.churn_risk_customers() == []

    def test_min_risk_filter(self, db):
        """min_risk=60 只留高危"""
        make_customer(db, name="中危", tier="A")   # 40分
        make_customer(db, name="高危", tier="S")   # 60分（40*1.5）
        rows = db.churn_risk_customers(min_risk=60)
        assert len(rows) == 1
        assert rows[0]["name"] == "高危"

    def test_tool_output(self, db, monkeypatch):
        monkeypatch.setattr("tools.real_estate_followup._get_db", lambda: db)
        make_customer(db, name="要流失的", tier="S")
        r = json.loads(churn_warning())
        assert r["success"] is True
        assert r["summary"]["high_risk"] == 1
        assert "挽回" in r["message"]


# ==================== 一键平替 ====================

class TestFindAlternatives:
    def test_same_community_top(self, db):
        """同小区同户型排最前"""
        origin = make_property(db, title="原房", community="海甸岛A小区",
                               district="美兰-海甸岛", price=3_000_000,
                               area=100.0, rooms=3, halls=2)
        make_property(db, title="同小区同户型", community="海甸岛A小区",
                      district="美兰-海甸岛", price=3_100_000,
                      area=105.0, rooms=3, halls=2)
        alts = db.find_alternatives(origin["id"])
        assert alts[0]["title"] == "同小区同户型"

    def test_sold_excluded(self, db):
        """已售房源不算平替"""
        origin = make_property(db, title="原房", community="A小区", price=3_000_000, area=100.0)
        make_property(db, title="已售的", community="A小区", price=3_000_000,
                      area=100.0, rooms=3, halls=2, status="sold")
        alts = db.find_alternatives(origin["id"])
        assert all(a["title"] != "已售的" for a in alts)

    def test_origin_excluded(self, db):
        make_property(db, title="原房", community="A小区", price=3_000_000, area=100.0)
        alts = db.find_alternatives(1)
        assert all(a["id"] != 1 for a in alts)

    def test_low_relevance_filtered(self, db):
        """完全不相关的房（不同区不同价位）被过滤"""
        origin = make_property(db, title="原房", community="A小区",
                               district="美兰区", price=3_000_000, area=100.0)
        make_property(db, title="无关房", community="B小区",
                      district="龙华区", price=10_000_000, area=200.0)
        alts = db.find_alternatives(origin["id"])
        assert all(a["title"] != "无关房" for a in alts)

    def test_sorted_by_closeness(self, db):
        origin = make_property(db, title="原房", community="A小区",
                               price=3_000_000, area=100.0, rooms=3, halls=2)
        make_property(db, title="贴近的", community="A小区", price=3_050_000,
                      area=102.0, rooms=3, halls=2)
        make_property(db, title="较远的", community="C小区", price=3_600_000,
                      area=130.0, rooms=4, halls=2)
        alts = db.find_alternatives(origin["id"])
        assert alts[0]["title"] == "贴近的"

    def test_tool_output(self, db, monkeypatch):
        monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
        origin = make_property(db, title="被抢的房", community="A小区", price=3_000_000, area=100.0)
        make_property(db, title="平替", community="A小区", price=3_050_000,
                      area=100.0, rooms=3, halls=2)
        r = json.loads(find_alternatives(property_id=origin["id"]))
        assert r["success"] is True
        assert any(a["title"] == "平替" for a in r["alternatives"])
