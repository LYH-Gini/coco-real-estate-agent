"""match_property 必须把 perfect_match 与 unit_price 给到模型（2026-09-24）

背景：系统提示词与操作手册都写着"match_property 单候选带 perfect_match，只有 perfect_match=true 才能标
完全匹配"，但工具层的字段白名单把它过滤掉了 —— 等于要求模型引用一个不存在的字段，它只能自己猜口径
（2026-08-13 真实事故：把 150 万标成 160-200 万预算客户的"完全匹配"）。
"""
import json

import pytest
from conftest import make_customer, make_property  # noqa: F401

from tools.real_estate_property import match_property


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
    return db


def _match(cid):
    return json.loads(match_property(customer_id=cid))


def test_each_match_carries_perfect_match_and_unit_price(tool_db):
    c = make_customer(tool_db, budget_min=2_000_000, budget_max=2_500_000, location="海口美兰区",
                      layout_pref="3室2厅", customer_type="buy_second_hand")
    make_property(tool_db, title="完美匹配 1号楼101", price=2_100_000, area=120, rooms=3, halls=2,
                  district="海口美兰区", community="完美匹配", property_type="second_hand")
    m = _match(c["id"])["matches"][0]
    assert m["perfect_match"] is True, m
    assert m["unit_price"] == round(2_100_000 / 120, 2), m


def test_over_budget_is_not_perfect_match(tool_db):
    """超预算房源（会作为参考返回）必须是 perfect_match=false，不能让模型标成完全匹配"""
    c = make_customer(tool_db, budget_min=1_500_000, budget_max=2_000_000, location="海口美兰区",
                      layout_pref="3室2厅", customer_type="buy_second_hand")
    make_property(tool_db, title="超预算 2号楼201", price=2_800_000, area=120, rooms=3, halls=2,
                  district="海口美兰区", community="超预算", property_type="second_hand")
    r = _match(c["id"])
    assert r["matches"], "超预算房源仍应作为参考返回"
    assert r["matches"][0]["perfect_match"] is False, r["matches"][0]


def test_region_mismatch_is_not_perfect_match(tool_db):
    """区域不符 → 不能算完全匹配（手册明确要求）"""
    c = make_customer(tool_db, budget_min=2_000_000, budget_max=2_500_000, location="海口秀英区",
                      layout_pref="3室2厅", customer_type="buy_second_hand")
    make_property(tool_db, title="别的区 3号楼301", price=2_100_000, area=120, rooms=3, halls=2,
                  district="海口美兰区", community="别的区", property_type="second_hand")
    m = _match(c["id"])["matches"][0]
    assert m["perfect_match"] is False, m
