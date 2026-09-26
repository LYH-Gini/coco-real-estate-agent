"""客户统计口径测试（2026-09-23）：客户数按"在跟"（活跃+暂缓）统计，已关闭单列

回到对象：此前不传条件就把已关闭客户一起列出来、客户总数也把它们算进去，
客户越攒越多、关掉的越多，报表数字就越"虚"。
"""
import json

import pytest

from conftest import make_customer, make_property

import agent.real_estate_db as dbmod


@pytest.fixture
def tools_db(db, monkeypatch):
    monkeypatch.setattr(dbmod, "_db_instance", db)
    import tools.real_estate_customer  # noqa: F401 —— import 时注册工具
    import tools.real_estate_analytics  # noqa: F401
    return db


def _call(name, args):
    from tools.registry import registry
    entry = registry.get_entry(name)
    assert entry is not None, f"{name} 没注册"
    return json.loads(entry.handler(args, session_id="test:session", task_id="t"))


class TestListCustomersScope:
    def test_default_excludes_closed(self, db):
        make_customer(db, name="活跃客户", status="active")
        make_customer(db, name="暂缓客户", status="paused")
        make_customer(db, name="已关闭客户", status="closed")
        # 只断言"范围"（是否含已关闭）；默认排序是"最新录入优先"，顺序断言见
        # tests/real_estate/test_list_customers_guards.py（2026-09-24 改口径）
        names = {c["name"] for c in db.list_customers()}
        assert names == {"活跃客户", "暂缓客户"}

    def test_explicit_status_still_supported(self, db):
        make_customer(db, name="活跃客户", status="active")
        make_customer(db, name="已关闭客户", status="closed")
        names = [c["name"] for c in db.list_customers(status="closed")]
        assert names == ["已关闭客户"]

    def test_include_closed_lists_everything(self, db):
        make_customer(db, name="活跃客户", status="active")
        make_customer(db, name="已关闭客户", status="closed")
        assert len(db.list_customers(include_closed=True)) == 2

    def test_limit_applies_to_tracking_customers(self, db):
        """默认口径下 limit 数的是在跟客户，已关闭不占用名额"""
        for i in range(3):
            make_customer(db, name=f"活跃{i}", status="active")
        make_customer(db, name="已关闭客户", status="closed")
        assert len(db.list_customers(limit=2)) == 2


class TestGetStatsScope:
    def test_customer_count_is_tracking_only(self, db):
        make_customer(db, name="活跃客户", status="active")
        make_customer(db, name="暂缓客户", status="paused")
        make_customer(db, name="已关闭客户", status="closed")
        stats = db.get_stats()
        assert stats["total_customers"] == 2
        assert stats["closed_customers"] == 1
        assert "在跟" in stats["customer_count_note"]

    def test_tier_counts_exclude_closed(self, db):
        make_customer(db, name="S级在跟", tier="S", status="active")
        make_customer(db, name="S级已关闭", tier="S", status="closed")
        stats = db.get_stats()
        assert stats["tier_counts"]["S"] == 1
        assert sum(stats["tier_counts"].values()) == stats["total_customers"]

    def test_available_properties_still_excludes_sold(self, db):
        make_property(db, title="在售房", status="available")
        make_property(db, title="已售房", status="sold")
        assert db.get_stats()["available_properties"] == 1


class TestChannelStatsScope:
    def test_closed_counted_separately(self, db):
        make_customer(db, name="抖音在跟", source="抖音", status="active")
        make_customer(db, name="抖音已关闭", source="抖音", status="closed")
        channels = {c["source"]: c for c in db.get_channel_stats()}
        assert channels["抖音"]["customers"] == 1
        assert channels["抖音"]["closed"] == 1
        assert channels["抖音"]["tiers"]["A"] == 1  # 只统计在跟的那位（已关闭的 A 级不算）

    def test_conversion_rate_uses_tracking_customers(self, db):
        """成交率分母只算在跟客户：同一渠道 1 成交 / 2 在跟 = 50%"""
        buyer = make_customer(db, name="成交客户", source="贝壳", status="active")
        make_customer(db, name="另一个在跟客户", source="贝壳", status="active")
        make_customer(db, name="已关闭且成交过的客户", source="贝壳", status="closed")
        p = make_property(db, title="成交房源")
        db.add_deal(customer_id=buyer["id"], property_id=p["id"], stage="deposit")
        channels = {c["source"]: c for c in db.get_channel_stats()}
        assert channels["贝壳"]["customers"] == 2
        assert channels["贝壳"]["deals"] == 1
        assert channels["贝壳"]["conversion_rate"] == 50.0


class TestToolLayerScope:
    def test_list_customers_tool_reports_scope(self, tools_db):
        make_customer(tools_db, name="活跃客户", status="active")
        make_customer(tools_db, name="已关闭客户", status="closed")
        default = _call("list_customers", {})
        assert default["count"] == 1
        assert "在跟" in default["count_scope"]
        everything = _call("list_customers", {"include_closed": True})
        assert everything["count"] == 2
        assert everything["count_scope"] == "含已关闭"

    def test_customer_stats_tool_exposes_closed_count(self, tools_db):
        make_customer(tools_db, name="活跃客户", status="active")
        make_customer(tools_db, name="已关闭客户", status="closed")
        result = _call("customer_stats", {})
        assert result["stats"]["total_customers"] == 1
        assert result["stats"]["closed_customers"] == 1

    def test_channel_stats_tool_exposes_closed_field(self, tools_db):
        make_customer(tools_db, name="抖音已关闭", source="抖音", status="closed")
        result = _call("channel_stats", {})
        assert result["channels"][0]["closed"] == 1
        assert result["channels"][0]["customers"] == 0
