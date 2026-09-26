"""批量匹配的候选池复用（2026-09-24 修 F18）

背景：原先 match_all_customers 为**每个客户**各拉一次全量候选池，实测 12000 套 × 100 客户要 41 秒。
改成按客户类型分桶、整批只拉一次，判定逻辑不变。
本用例钉住两条契约：
① 批量结果必须与"逐客户单独调 match_property"完全一致（改了性能不能改结果）；
② 池按客户类型分桶后，各类型客户的候选仍然正确（不会串池）。
"""
import json

import pytest
from conftest import make_customer, make_property  # noqa: F401

from tools.real_estate_property import batch_match_report, match_property


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
    return db


def _seed(db):
    c_second = make_customer(db, name="买二手客户", budget_min=1_500_000, budget_max=2_500_000,
                             location="海口美兰区", layout_pref="3室2厅", customer_type="buy_second_hand")
    c_new = make_customer(db, name="买一手客户", budget_min=1_500_000, budget_max=2_500_000,
                          location="海口美兰区", layout_pref="3室2厅", customer_type="buy_new")
    c_rent = make_customer(db, name="租房客户", budget_min=1000, budget_max=4000,
                           location=None, layout_pref=None, customer_type="rent")
    make_property(db, title="二手房 1号楼101", price=2_000_000, area=120, rooms=3, halls=2,
                  district="海口美兰区", community="二手小区", property_type="second_hand")
    make_property(db, title="一手房 2号楼201", price=2_000_000, area=120, rooms=3, halls=2,
                  district="海口美兰区", community="新盘小区", property_type="new")
    make_property(db, title="出租房 3号楼301", price=2500, area=60, rooms=1, halls=1,
                  district="海口美兰区", community="出租小区", property_type="rental")
    return c_second, c_new, c_rent


def test_batch_equals_per_customer_single_match(tool_db):
    """批量结果 == 逐客户单独匹配（池复用不能改变任何一条结果）"""
    c_second, c_new, c_rent = _seed(tool_db)
    batch = json.loads(batch_match_report(top_n=3))
    assert len(batch["customers"]) == 3
    for row in batch["customers"]:
        single = json.loads(match_property(customer_id=row["customer_id"], top_n=3))
        assert [m["id"] for m in row["matches"]] == [m["id"] for m in single["matches"]], row["customer_name"]
        assert [m["perfect_match"] for m in row["matches"]] == [m["perfect_match"] for m in single["matches"]]
        expected = ("完全匹配" if any(m["perfect_match"] for m in single["matches"])
                    else ("接近匹配" if single["matches"] else "无匹配"))
        assert row["match_status"] == expected, row["customer_name"]


def test_pool_buckets_do_not_leak_across_types(tool_db):
    """池按客户类型分桶：买二手只拿二手、买一手只拿一手、租房只拿出租"""
    _seed(tool_db)
    batch = json.loads(batch_match_report(top_n=5))
    by_name = {c["customer_name"]: c for c in batch["customers"]}
    assert [m["title"] for m in by_name["买二手客户"]["matches"]] == ["二手房 1号楼101"]
    assert [m["title"] for m in by_name["买一手客户"]["matches"]] == ["一手房 2号楼201"]
    assert [m["title"] for m in by_name["租房客户"]["matches"]] == ["出租房 3号楼301"]


def test_summary_counts_match_details_with_pool_reuse(tool_db):
    _seed(tool_db)
    r = json.loads(batch_match_report(top_n=1))
    cs = r["customers"]
    assert r["summary"] == {
        "total": len(cs),
        "perfect_match": sum(1 for c in cs if c["match_status"] == "完全匹配"),
        "close_match": sum(1 for c in cs if c["match_status"] == "接近匹配"),
        "no_match": sum(1 for c in cs if c["match_status"] == "无匹配"),
    }
