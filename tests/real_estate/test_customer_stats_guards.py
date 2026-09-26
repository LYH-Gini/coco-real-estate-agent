"""customer_stats 回归（2026-09-24）：补客户类型/活跃暂缓分布、逾期数改 SQL 聚合、描述统一

覆盖三处修复（2026-09-24 拍板）：
① F62 客户侧维度不全 → 补 customer_type_counts（在跟口径，未细分单列）与 active/paused 分布；
   来源分布不重复实现，口径说明里指向 channel_stats。
② F63 描述两处不一致且没写清维度 → 统一成一段并点名能给什么。
③ F64 overdue_followups 由"拉全量再数"改成 SQL 聚合（口径与 get_overdue 完全一致）。
"""
import json
from datetime import datetime

import pytest

from tools import real_estate_customer as mod
from tools.real_estate_customer import customer_stats
from tools.registry import registry


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
    return db


def make(db, name, tier="C", ctype="buy_second_hand", status="active"):
    return db.add_customer(name=name, tier=tier, customer_type=ctype, status=status)["id"]


def stats_of(db):
    return json.loads(customer_stats())["stats"]


# ---------- ① 客户侧维度 ----------
def test_customer_type_counts_cover_every_customer(tool_db):
    make(tool_db, "一手客", ctype="buy_new")
    make(tool_db, "二手客", ctype="buy_second_hand")
    make(tool_db, "租房客1", ctype="rent")
    make(tool_db, "租房客2", ctype="rent")
    make(tool_db, "未细分客", ctype="unspecified")
    make(tool_db, "关掉的", ctype="rent", status="closed")
    st = stats_of(tool_db)
    assert st["customer_type_counts"]["buy_new"] == 1
    assert st["customer_type_counts"]["buy_second_hand"] == 1
    assert st["customer_type_counts"]["rent"] == 2          # 已关闭的不计入
    assert st["customer_type_counts"]["unspecified"] == 1
    assert sum(st["customer_type_counts"].values()) == st["total_customers"], st


def test_legacy_buy_type_counted_as_unspecified(tool_db):
    """历史遗留的 buy 值（老默认值）要归到未细分那一档，不能漏成其它"""
    tool_db.add_customer(name="历史客户", tier="C", customer_type="buy", status="active")
    st = stats_of(tool_db)
    assert st["customer_type_counts"]["unspecified"] == 1, st["customer_type_counts"]
    assert "other" not in st["customer_type_counts"]


def test_customer_type_counts_match_list_customers_totals(tool_db):
    make(tool_db, "a", ctype="rent")
    make(tool_db, "b", ctype="rent")
    make(tool_db, "c", ctype="buy_new")
    st = stats_of(tool_db)
    from tools.real_estate_customer import list_customers
    for ctype in ("rent", "buy_new", "buy_second_hand", "unspecified"):
        listed = json.loads(list_customers(customer_type=ctype, limit=1))
        assert listed["total"] == st["customer_type_counts"][ctype], ctype


def test_active_and_paused_split_sums_to_total(tool_db):
    make(tool_db, "在跟1")
    make(tool_db, "在跟2")
    make(tool_db, "暂缓", status="paused")
    make(tool_db, "关掉", status="closed")
    st = stats_of(tool_db)
    assert st["active_customers"] == 2 and st["paused_customers"] == 1
    assert st["active_customers"] + st["paused_customers"] == st["total_customers"]


def test_counts_agree_with_list_customers_total(tool_db):
    make(tool_db, "x")
    make(tool_db, "y", status="closed")
    st = stats_of(tool_db)
    from tools.real_estate_customer import list_customers
    assert st["total_customers"] == json.loads(list_customers())["total"]
    assert st["closed_customers"] == 1
    assert sum(st["tier_counts"].values()) == st["total_customers"]


def test_type_note_points_to_channel_stats(tool_db):
    st = stats_of(tool_db)
    assert "channel_stats" in st["customer_type_note"]
    assert "在跟" in st["customer_count_note"]


# ---------- ③ 逾期数：SQL 聚合与 get_overdue 完全同口径 ----------
def test_overdue_count_matches_get_overdue_length(tool_db):
    cid = make(tool_db, "有老跟进的")
    tool_db.add_followup(customer_id=cid, type="note", content="早该回访",
                         next_date=datetime(2020, 1, 1))
    assert stats_of(tool_db)["overdue_followups"] == len(tool_db.get_overdue()) == 1


def test_overdue_count_ignores_handled_customers(tool_db):
    """客户后续又记了新跟进（无 next_date）→ 旧提醒不再算逾期（与 get_overdue 同一口径）"""
    cid = make(tool_db, "已处理")
    tool_db.add_followup(customer_id=cid, type="note", content="旧提醒",
                         next_date=datetime(2020, 1, 1))
    tool_db.add_followup(customer_id=cid, type="note", content="新跟进", next_date=None)
    assert stats_of(tool_db)["overdue_followups"] == len(tool_db.get_overdue()) == 0


def test_overdue_count_zero_on_empty_db(db):
    assert db.count_overdue_followups() == 0


# ---------- 空库与结构 ----------
def test_empty_db_all_zero_same_shape(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
    st = stats_of(db)
    assert st["total_customers"] == 0 and st["closed_customers"] == 0
    assert st["active_customers"] == 0 and st["paused_customers"] == 0
    assert all(v == 0 for v in st["customer_type_counts"].values())
    assert st["total_properties"] == 0 and st["overdue_followups"] == 0


# ---------- ② 描述 ----------
def test_description_unified_and_states_dimensions():
    desc = registry.get_entry("customer_stats").schema.get("description", "")
    assert desc == mod.TOOLS[5]["description"]
    for word in ("客户数", "等级", "类型", "房源", "逾期", "channel_stats"):
        assert word in desc, (word, desc)
