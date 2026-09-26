"""客户生命周期阶段管理测试（功能5，批次五）"""
import json
from datetime import datetime, timedelta

import pytest

from conftest import make_customer, make_property

from tools.real_estate_customer import update_customer_stage
from tools.real_estate_followup import stage_stagnation


class TestStageUpdate:
    def test_default_stage_lead(self, db):
        c = make_customer(db)
        assert c["stage"] == "lead"

    def test_update_stage(self, db):
        c = make_customer(db)
        db.update_stage(c["id"], "strong")
        assert db.get_customer(c["id"])["stage"] == "strong"

    def test_invalid_stage_rejected(self, db):
        c = make_customer(db)
        with pytest.raises(ValueError):
            db.update_stage(c["id"], "hacking")

    def test_stage_change_history(self, db):
        """阶段流转写入变更历史"""
        c = make_customer(db, stage="lead")
        db.update_stage(c["id"], "interested")
        changes = db.get_customer_changes(c["id"])
        assert any(ch["field"] == "stage" and ch["new_value"] == "interested"
                   for ch in changes)

    def test_tool_output(self, db, monkeypatch):
        monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
        c = make_customer(db)
        r = json.loads(update_customer_stage(customer_id=c["id"], stage="strong"))
        assert r["success"] is True
        assert "强意向" in r["message"]

    def test_tool_invalid_stage(self, db, monkeypatch):
        monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
        c = make_customer(db)
        r = json.loads(update_customer_stage(customer_id=c["id"], stage="bogus"))
        assert r["success"] is False


class TestDealStageLinkage:
    def test_start_deal_auto_advance(self, db):
        """开单自动推进到 dealing（防忘）"""
        p = make_property(db)
        c = make_customer(db)
        db.update_stage(c["id"], "negotiating")
        db.add_deal(customer_id=c["id"], property_id=p["id"], stage="deposit")
        assert db.get_customer(c["id"])["stage"] == "dealing"


class TestStageStagnation:
    def _set_stage_since(self, db, cid, stage, days_ago):
        """把客户阶段设为 stage，并把变更时间伪造为 days_ago 天前"""
        db.update_stage(cid, stage)
        with db.get_session() as s:
            from agent.real_estate_db import CustomerChange
            changes = s.query(CustomerChange).filter(
                CustomerChange.customer_id == cid,
                CustomerChange.field == 'stage').order_by(
                CustomerChange.id.desc()).first()
            changes.created_at = datetime.now() - timedelta(days=days_ago)
            s.commit()

    def test_strong_over_7_days_alerted(self, db):
        c = make_customer(db)
        self._set_stage_since(db, c["id"], "strong", 10)
        alerts = db.stage_stagnation_report()
        assert any(a["customer_id"] == c["id"] and a["stage"] == "strong" for a in alerts)

    def test_strong_within_7_days_ok(self, db):
        c = make_customer(db)
        self._set_stage_since(db, c["id"], "strong", 3)
        assert db.stage_stagnation_report() == []

    def test_viewed_over_14_days_alerted(self, db):
        c = make_customer(db)
        self._set_stage_since(db, c["id"], "viewed", 20)
        alerts = db.stage_stagnation_report()
        assert any(a["stage"] == "viewed" for a in alerts)

    def test_lead_never_alerted(self, db):
        """lead 阶段无超时规则"""
        c = make_customer(db)
        with db.get_session() as s:
            from agent.real_estate_db import Customer
            cust = s.query(Customer).get(c["id"])
            cust.created_at = datetime.now() - timedelta(days=100)
            s.commit()
        assert db.stage_stagnation_report() == []

    def test_tool_output(self, db, monkeypatch):
        monkeypatch.setattr("tools.real_estate_followup._get_db", lambda: db)
        c = make_customer(db)
        self._set_stage_since(db, c["id"], "strong", 15)
        r = json.loads(stage_stagnation())
        assert r["success"] is True
        assert len(r["alerts"]) == 1
