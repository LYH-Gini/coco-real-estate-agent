"""search_property 的状态筛选、匹配总数、limit 上限、默认最新优先（2026-09-24）

覆盖这一项查出的 4 条问题：
- F11 加 status：能查已售/已租/全部（原来只能看在售）
- F12 返回 total（匹配总数）与 count（本次条数）+ truncated，避免模型把 20 当总数
- F13 limit ≤ 0 一律按默认 20，且不超过上限 200（原来 0→50、负数→全库）
- F14 默认按最新录入优先（原来只看得到最早的 20 套）
同时钉住 db 层默认行为不变（不传 status/sort 时仍是"只看在售、不排序"），避免影响其它调用方。
"""
import json

import pytest
from conftest import make_property  # noqa: F401

from tools.real_estate_property import search_property


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
    return db


def _search(**kw):
    return json.loads(search_property(**kw))


def _mk(db, i, **kw):
    data = dict(title=f"搜索小区 {i}号楼101", price=1_000_000 + i * 10000, area=90.0,
                rooms=2, halls=1, district="海口美兰区", property_type="second_hand", status="available")
    data.update(kw)
    return make_property(db, **data)


# ---------- F11 状态筛选 ----------
def test_default_only_available(tool_db):
    _mk(tool_db, 1)
    _mk(tool_db, 2, status="sold")
    r = _search(district="美兰")
    assert r["count"] == 1 and r["properties"][0]["status"] == "available"


def test_status_sold_and_rented_and_all(tool_db):
    _mk(tool_db, 1)
    _mk(tool_db, 2, status="sold")
    _mk(tool_db, 3, status="rented")
    assert _search(district="美兰", status="sold")["count"] == 1
    assert _search(district="美兰", status="rented")["count"] == 1
    assert _search(district="美兰", status="all")["count"] == 3


def test_invalid_status_hint(tool_db):
    r = _search(status="unknown")
    assert r["success"] is False and "状态" in r["error"]


def test_invalid_sort_hint(tool_db):
    r = _search(sort="cheap")
    assert r["success"] is False and "排序" in r["error"]


# ---------- F12 total / count ----------
def test_total_vs_count_and_truncated(tool_db):
    for i in range(1, 26):
        _mk(tool_db, i)
    r = _search(district="美兰", limit=20)
    assert r["count"] == 20 and r["total"] == 25 and r["truncated"] is True
    assert "共匹配 25 套" in r["message"]


# ---------- F13 limit 边界 ----------
@pytest.mark.parametrize("bad_limit", [0, -3, "abc", None])
def test_bad_limit_falls_back_to_default(tool_db, bad_limit):
    for i in range(1, 26):
        _mk(tool_db, i)
    r = _search(district="美兰", limit=bad_limit)
    assert r["count"] == 20, f"limit={bad_limit} 应按默认 20"


def test_limit_capped_at_200(tool_db):
    for i in range(1, 226):
        _mk(tool_db, i)
    r = _search(district="美兰", limit=9999)
    assert r["count"] == 200 and r["total"] == 225


# ---------- F14 默认最新录入优先 ----------
def test_default_sort_latest_first(tool_db):
    ids = [_mk(tool_db, i)["id"] for i in range(1, 26)]
    r = _search(district="美兰", limit=3)
    got = [p["id"] for p in r["properties"]]
    assert got == sorted(ids, reverse=True)[:3], "默认应给最新录入的房源"


def test_sort_price(tool_db):
    for i in range(1, 6):
        _mk(tool_db, i)
    asc = [p["price"] for p in _search(district="美兰", sort="price_asc", limit=10)["properties"]]
    desc = [p["price"] for p in _search(district="美兰", sort="price_desc", limit=10)["properties"]]
    assert asc == sorted(asc) and desc == sorted(desc, reverse=True)


# ---------- db 层默认行为不变（其它调用方依赖）----------
def test_db_layer_defaults_unchanged(tool_db):
    _mk(tool_db, 1)
    _mk(tool_db, 2, status="sold")
    rows = tool_db.search_properties(district="美兰")          # 不传 status/sort：只看在售、不排序
    assert len(rows) == 1 and isinstance(rows, list)
    rows2, total = tool_db.search_properties(district="美兰", status="all", with_total=True)
    assert len(rows2) == 2 and total == 2
