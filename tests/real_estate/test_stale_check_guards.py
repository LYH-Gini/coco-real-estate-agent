"""stale_check 回归（2026-09-25 第四组 F170–F172）

背景（实测）：
① 流失名单**全量**塞进返回（1.2 万客户 = **1654KB**），且只有 `still_stale_count`、没有 `total`/`truncated`
   （同族探针里它是 ③形状 唯一还 ⚠️ 的两个工具之一）；
② `message` 只说降了几位，**不提还有几位仍长期无互动**（`still_stale_count` 明明有值）；
③ 空库与「有客户但都不需要降级」**同一句话**（`无客户需要降级`）。

本文件钉住修好之后的行为（含老字段 `still_stale_count` 的兼容口径）。
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_followup as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


def _call():
    import tools.real_estate_followup as m

    return json.loads(m.stale_check())


def _customer(db, name, tier="B", created_days_ago=0):
    return db.add_customer(name=name, tier=tier, customer_type="rent",
                           created_at=datetime.now() - timedelta(days=created_days_ago))["id"]


def _stale_customer(db, name, tier="B", inactive_days=40):
    cid = _customer(db, name, tier=tier, created_days_ago=inactive_days)
    db.add_followup(customer_id=cid, type="note", content="旧跟进",
                    created_at=datetime.now() - timedelta(days=inactive_days))
    return cid


# ==================== ① 形状与上限 ====================

class TestShapeAndLimit:
    def test_list_capped_with_total_and_truncated(self, wired):
        for i in range(25):
            _stale_customer(wired, f"久未联系{i}", tier="C", inactive_days=120)
        out = _call()
        assert len(out["still_stale"]) == 20
        assert out["count"] == 20 and out["total"] == 25 and out["truncated"] is True
        assert out["still_stale_count"] == 25, "老字段保留 = 总数（兼容现有消费者）"

    def test_no_truncation_when_small(self, wired):
        for i in range(3):
            _stale_customer(wired, f"久未联系{i}", tier="C", inactive_days=120)
        out = _call()
        assert out["count"] == 3 and out["total"] == 3 and out["truncated"] is False

    def test_sorted_by_longest_inactive(self, wired):
        _stale_customer(wired, "刚过线", tier="C", inactive_days=61)
        _stale_customer(wired, "很久了", tier="C", inactive_days=200)
        assert _call()["still_stale"][0]["name"] == "很久了"

    def test_items_carry_downgrade_basis(self, wired):   # noqa: D401
        _stale_customer(wired, "久未联系", tier="S", inactive_days=40)
        item = _call()["still_stale"][0]
        assert {"name", "tier", "days_inactive", "threshold", "last_contact"} <= set(item)


# ==================== ② message 三种组合 + 截断 ====================

class TestMessage:
    def test_downgraded_with_remaining(self, wired):
        _stale_customer(wired, "S级久未联系", tier="S", inactive_days=40)
        out = _call()
        assert out["message"] == "本次自动降级 1 位客户；还有 1 位仍长期无互动", out["message"]

    def test_no_downgrade_but_still_stale(self, wired):
        _stale_customer(wired, "S级久未联系", tier="S", inactive_days=40)
        _call()                                   # 第一次降级
        out = _call()                             # 第二次：今天不再降
        assert out["downgrades"] == []
        assert out["message"] == "本次没有需要降级的客户；仍有 1 位长期无互动", out["message"]

    def test_healthy_library(self, wired):
        _customer(wired, "活跃客户", tier="B", created_days_ago=3)
        out = _call()
        assert out["message"] == "没有客户需要降级"

    def test_empty_library_says_so(self, wired):
        out = _call()
        assert out["message"] == "库里还没有客户，先登记客户再看流失情况"

    def test_truncation_note_appended(self, wired):
        for i in range(25):
            _stale_customer(wired, f"久未联系{i}", tier="C", inactive_days=120)
        msg = _call()["message"]
        assert msg.startswith("本次没有需要降级的客户") or msg.startswith("本次自动降级")
        assert "共 25 位客户长期无互动，这里列最久的 20 位（要我列全就说一声）" in msg, msg


# ==================== ③ 降级口径（复测，别被这轮改坏）====================

class TestDowngradeBehaviour:
    def test_reports_who_and_why(self, wired):
        _stale_customer(wired, "S级久未联系", tier="S", inactive_days=40)
        item = _call()["downgrades"][0]
        assert item["name"] == "S级久未联系"
        assert (item["from"], item["to"]) == ("S", "A")
        assert item["threshold"] == 5

    def test_once_per_day(self, wired):
        _stale_customer(wired, "S级久未联系", tier="S", inactive_days=40)
        _call()
        for _ in range(3):
            assert _call()["downgrades"] == []
        with wired.get_session() as s:
            tiers = s.execute(text("SELECT tier FROM re_customers")).fetchall()
        assert [t[0] for t in tiers] == ["A"], tiers

    def test_downgrade_written_to_history(self, wired):
        _stale_customer(wired, "S级久未联系", tier="S", inactive_days=40)
        _call()
        with wired.get_session() as s:
            changes = s.execute(text("SELECT old_value, new_value FROM re_customer_changes")).fetchall()
        assert [tuple(r) for r in changes] == [("S", "A")], changes


# ==================== ④ 描述 ====================

class TestDescription:
    def test_description_states_capabilities(self):
        from tools.registry import registry

        desc = registry.get_entry("stale_check").schema["description"]
        assert len(desc) >= 40, desc
        for word in ("流失", "自动降级", "该等级的阈值", "truncated", "只列最久的 20 位"):
            assert word in desc, (word, desc)

    def test_description_not_written_twice_with_drift(self):
        from tools.registry import registry
        import tools.real_estate_followup as m

        assert registry.get_entry("stale_check").schema["description"] == m.TOOLS[6]["description"]
