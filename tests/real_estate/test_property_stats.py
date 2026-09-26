"""property_stats 的房源维度统计（2026-09-24）

原先只返回一个 available_properties（在售数），经纪人问"一共多少套房""卖了几套""多少套在出租"
都答不了，而客户侧却有 4 个维度。本用例钉住新增的四个字段与它们的口径。
"""
import json

import pytest
from conftest import make_customer, make_property  # noqa: F401

from tools.real_estate_property import property_stats


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
    return db


def _stats():
    return json.loads(property_stats())["stats"]


def test_property_counts_by_status(tool_db):
    make_property(tool_db, title="在售二手 1号楼101", property_type="second_hand")
    make_property(tool_db, title="在售新房 2号楼201", property_type="new")
    make_property(tool_db, title="在售出租 3号楼301", property_type="rental", price=2500)
    sold = make_property(tool_db, title="已售 4号楼401")
    rented = make_property(tool_db, title="已租 5号楼501", property_type="rental", price=2200)
    tool_db.update_property(sold["id"], status="sold")
    tool_db.update_property(rented["id"], status="rented")

    s = _stats()
    assert s["total_properties"] == 5
    assert s["available_properties"] == 3
    assert s["sold_properties"] == 1
    assert s["rented_properties"] == 1
    assert s["total_properties"] == s["available_properties"] + s["sold_properties"] + s["rented_properties"]


def test_available_by_type_only_counts_available(tool_db):
    make_property(tool_db, title="在售二手 1号楼101", property_type="second_hand")
    make_property(tool_db, title="在售新房 2号楼201", property_type="new")
    sold = make_property(tool_db, title="已售二手 3号楼301", property_type="second_hand")
    tool_db.update_property(sold["id"], status="sold")

    by_type = _stats()["available_by_type"]
    assert by_type == {"new": 1, "second_hand": 1, "rental": 0}, by_type


def test_counts_are_consistent_with_customer_side(tool_db):
    # 客户口径不变：在跟/已关闭分列，等级分布只算在跟
    make_customer(tool_db, name="在跟A", tier="A")
    make_customer(tool_db, name="在跟B", tier="B")
    closed = make_customer(tool_db, name="关掉的", tier="A")
    tool_db.update_customer(closed["id"], status="closed")

    s = _stats()
    assert s["total_customers"] == 2 and s["closed_customers"] == 1
    assert s["tier_counts"]["A"] == 1 and s["tier_counts"]["B"] == 1


def test_empty_db_all_zero(tool_db):
    s = _stats()
    assert s["total_properties"] == 0 and s["available_properties"] == 0
    assert s["available_by_type"] == {"new": 0, "second_hand": 0, "rental": 0}


def test_property_count_note_explains_scope(tool_db):
    # 口径要写在返回里，模型才不会把「在售数」说成「总房源数」
    note = _stats()["property_count_note"]
    assert "在售" in note and "已售" in note and "一手房" in note
