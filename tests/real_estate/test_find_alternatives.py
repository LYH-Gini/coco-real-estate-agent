"""find_alternatives 的用途隔离、房源存在性与 limit 边界（2026-09-24）

F28：出租房的平替里混进买卖房源（实测 2500元/月 的出租房，平替换算里出现 150 万二手房，价差 600 倍）。
F29：原房源不存在时说"暂无贴近度足够的替代房源"，与其它工具口径不一致。
F30：limit 传 0 给 0 套、传负数给 12 套、传超大给全部，且没有上限。
"""
import json

import pytest
from conftest import make_property  # noqa: F401

from tools.real_estate_property import find_alternatives, update_property


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
    return db


def _alts(pid, **kw):
    return json.loads(find_alternatives(property_id=pid, **kw))


def _mk(db, title, **kw):
    data = dict(price=2_000_000.0, area=100.0, rooms=3, halls=2, district="海口美兰区",
                community="平替小区", property_type="second_hand")
    data.update(kw)
    return make_property(db, title=title, **data)


def test_rental_alternatives_stay_rental(tool_db):
    origin = _mk(tool_db, "出租小区 1号楼101", price=2_500.0, area=60.0, rooms=1, halls=1,
                 property_type="rental", community="出租小区")
    _mk(tool_db, "出租小区 2号楼201", price=2_600.0, area=62.0, rooms=1, halls=1,
        property_type="rental", community="出租小区")
    _mk(tool_db, "出售小区 3号楼301", price=1_500_000.0, area=60.0, rooms=1, halls=1,
        community="出售小区")                     # 同面积同区域，但用途不同 → 不该出现

    alts = _alts(origin["id"])["alternatives"]
    assert alts, "应找到同用途替代"
    assert all(a["property_type"] == "rental" for a in alts), alts


def test_sell_alternatives_may_cross_new_and_second_hand(tool_db):
    """出售内部可以互推：二手房被抢 → 同小区新盘可以当平替"""
    origin = _mk(tool_db, "出售小区 4号楼401", property_type="second_hand", community="出售小区")
    _mk(tool_db, "出售小区 5号楼501", property_type="new", community="出售小区", price=2_050_000.0)
    alts = _alts(origin["id"])["alternatives"]
    assert any(a["property_type"] == "new" for a in alts), alts


def test_missing_property_is_not_found(tool_db):
    r = _alts(999999)
    assert r["success"] is False and r.get("not_found") is True
    assert "没有编号为" in r["error"]


@pytest.mark.parametrize("bad_limit", [0, -3, "abc", None])
def test_bad_limit_falls_back_to_default(tool_db, bad_limit):
    origin = _mk(tool_db, "平替小区 6号楼601")
    for i in range(7, 20):
        _mk(tool_db, f"平替小区 {i}号楼{i}01", price=2_000_000.0 + i * 1000)
    alts = _alts(origin["id"], limit=bad_limit)["alternatives"]
    assert 0 < len(alts) <= 5, alts          # 按默认 5，不会因为传 0/负数变成 0 条或全量


def test_limit_is_capped(tool_db):
    origin = _mk(tool_db, "平替小区 30号楼3001")
    for i in range(31, 80):
        _mk(tool_db, f"平替小区 {i}号楼{i}01", price=2_000_000.0 + i * 1000)
    alts = _alts(origin["id"], limit=999)["alternatives"]
    assert len(alts) <= 20, len(alts)


def test_origin_and_off_market_excluded(tool_db):
    origin = _mk(tool_db, "平替小区 90号楼9001")
    near = _mk(tool_db, "平替小区 91号楼9101", price=2_050_000.0)
    update_property(property_id=near["id"], status="sold")
    alts = _alts(origin["id"])["alternatives"]
    assert all(a["id"] not in (origin["id"], near["id"]) for a in alts), alts
