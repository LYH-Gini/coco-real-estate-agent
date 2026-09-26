"""price_drop_alerts 的扫描范围、输出上限与措辞（2026-09-24）

F26：有降价但没有够得着的客户时，不能说成"无降价房源"（原先两种情形共用一句话）。
F27：① 扫描条数不能写死（实测 300 套房降价只提醒 200 套，后 100 套静默漏）；
     ② 输出要有上限（实测 50 套 × 1000 客户返回 7.65MB、30 秒，会撑爆模型上下文）。
"""
import json

import pytest
from conftest import make_customer, make_property  # noqa: F401

from tools.real_estate_property import price_drop_alerts, update_property


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
    return db


def _alert(**kw):
    return json.loads(price_drop_alerts(**kw))


def _dropped_prop(db, i, old=2_000_000, new=1_900_000):
    p = make_property(db, title=f"降价小区 {i}号楼{i}01", price=float(old), area=100.0)
    update_property(property_id=p["id"], price=new)
    return p


# ---------- F26 措辞 ----------
def test_no_drop_at_all(tool_db):
    r = _alert(days=7)
    assert r["alerts"] == [] and "没有房源降价" in r["message"], r["message"]


def test_drop_but_no_reachable_customer(tool_db):
    # 有降价但没人够得着 → 不能说成「没有降价房源」
    _dropped_prop(tool_db, 1, old=5_000_000, new=4_800_000)
    r = _alert(days=7)
    assert r["alerts"] == []
    assert "1 套房源降价" in r["message"] and "没有预算够得着的客户" in r["message"], r["message"]


# ---------- F27 扫描不漏 + 输出上限 ----------
def test_scan_covers_more_than_200_drops(tool_db):
    for i in range(1, 301):
        make_customer(tool_db, name=f"捞回客户{i}", budget_min=1_000_000, budget_max=1_850_000 + i,
                      customer_type="buy_second_hand")
        _dropped_prop(tool_db, i, old=2_000_000 + i, new=1_900_000 + i)
    r = _alert(days=7)
    assert r["drops_found"] == 300, r.get("drops_found")
    assert r["summary"].startswith("300套降价"), r["summary"]


def test_output_is_capped_every_customer_limited(tool_db):
    for i in range(1, 61):                     # 60 套降价，超过单次展示上限 50
        for j in range(1, 21):
            make_customer(tool_db, name=f"客户{i}_{j}", budget_min=1_000_000,
                          budget_max=1_900_000 + i, customer_type="buy_second_hand")
        _dropped_prop(tool_db, i, old=2_000_000 + i, new=1_900_000 + i)
    r = _alert(days=7)
    assert len(r["alerts"]) == 50, "单次最多列 50 套"
    assert r["truncated"] is True
    assert all(len(a["matched_customers"]) <= 5 for a in r["alerts"]), "每套最多列 5 位客户"


def test_reachable_customer_is_listed(tool_db):
    make_customer(tool_db, name="差一点客户", budget_min=1_000_000, budget_max=1_850_000,
                  customer_type="buy_second_hand")
    p = _dropped_prop(tool_db, 9)
    r = _alert(days=7)
    hit = [a for a in r["alerts"] if a["property_id"] == p["id"]]
    assert hit and hit[0]["drop_amount"] == 100_000
    assert hit[0]["matched_customers"][0]["name"] == "差一点客户"
