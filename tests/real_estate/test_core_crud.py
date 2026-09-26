"""核心工具回归：房源搜索/客户与房源读写/标签/等级/变更历史/去重/图片（2026-09-18 补测）

这些工具此前只有"冒烟"（证明不崩溃），没有结果正确性断言。
"""
import json

from conftest import make_customer, make_property


def _tool(db, monkeypatch, name, args):
    import tools.real_estate_property  # noqa: F401
    import tools.real_estate_customer  # noqa: F401
    import tools.real_estate_images  # noqa: F401
    from tools.registry import registry
    for mod_name, mod in (("real_estate_property", "tools.real_estate_property"),
                          ("real_estate_customer", "tools.real_estate_customer"),
                          ("real_estate_images", "tools.real_estate_images")):
        import importlib
        importlib.import_module(mod)
    import tools.real_estate_property as tp
    import tools.real_estate_customer as tc
    import tools.real_estate_images as ti
    for m in (tp, tc, ti):
        monkeypatch.setattr(m, "_get_db", lambda: db)
    return json.loads(registry.dispatch(name, args))


class TestSearchProperty:
    def test_filters_are_applied(self, db, monkeypatch):
        make_property(db, title="便宜老破小", price=800_000, area=60.0, rooms=2, district="龙华-国贸")
        make_property(db, title="贵新房源", price=2_000_000, area=120.0, rooms=3, district="美兰-海甸岛")
        out = _tool(db, monkeypatch, "search_property", {"max_price": 1_000_000, "district": "龙华"})
        assert out["success"] is True and out["count"] == 1
        assert out["properties"][0]["title"] == "便宜老破小"

    def test_title_substring_search(self, db, monkeypatch):
        make_property(db, title="滨海华庭 3号楼201", price=1_000_000, area=90.0)
        out = _tool(db, monkeypatch, "search_property", {"title": "华庭"})
        assert out["count"] == 1 and "华庭" in out["properties"][0]["title"]

    def test_sold_property_not_returned(self, db, monkeypatch):
        make_property(db, title="已售房", price=1_000_000, area=90.0, status="sold")
        out = _tool(db, monkeypatch, "search_property", {})
        assert out["count"] == 0, "已售房源不该出现在搜索结果里"


class TestCustomerCrud:
    def test_add_get_list_and_stats(self, db, monkeypatch):
        c = make_customer(db, name="读写客户", tier="A")
        got = _tool(db, monkeypatch, "get_customer", {"customer_id": c["id"]})
        assert got["customer"]["name"] == "读写客户"
        lst = _tool(db, monkeypatch, "list_customers", {"tier": "A"})
        assert lst["count"] == 1
        stats = _tool(db, monkeypatch, "customer_stats", {})
        assert stats["success"] is True

    def test_tags_add_remove(self, db, monkeypatch):
        c = make_customer(db, name="标签客户")
        assert _tool(db, monkeypatch, "add_customer_tag", {"customer_id": c["id"], "tag": "刚需"})["success"] is True
        assert "刚需" in _tool(db, monkeypatch, "list_customer_tags", {"customer_id": c["id"]})["tags"]
        assert _tool(db, monkeypatch, "remove_customer_tag", {"customer_id": c["id"], "tag": "刚需"})["success"] is True
        assert _tool(db, monkeypatch, "list_customer_tags", {"customer_id": c["id"]})["tags"] == []

    def test_tier_change_writes_history(self, db, monkeypatch):
        c = make_customer(db, name="升级客户", tier="B")
        _tool(db, monkeypatch, "update_tier", {"customer_id": c["id"], "tier": "S"})
        hist = _tool(db, monkeypatch, "customer_change_history", {"customer_id": c["id"]})
        assert hist["success"] is True and hist["changes"], "等级变更应留痕"


class TestPropertyWrites:
    def test_update_price_and_unit_price(self, db, monkeypatch):
        p = make_property(db, title="调价房源", price=1_000_000, area=100.0)
        out = _tool(db, monkeypatch, "update_property", {"property_id": p["id"], "price": 1_200_000})
        assert out["property"]["price"] == 1_200_000
        assert out["property"]["unit_price"] == 12000.0    # 单价跟着现算

    def test_images_add_and_list(self, db, monkeypatch):
        p = make_property(db, title="图片房源")
        _tool(db, monkeypatch, "add_property_images", {"property_id": p["id"], "images": "a.jpg,b.jpg"})
        out = _tool(db, monkeypatch, "list_property_images", {"property_id": p["id"]})
        assert out["count"] == 2

    def test_dedup_dry_run_keeps_data(self, db, monkeypatch):
        for _ in range(2):
            db.add_property(title="重复房源", price=1_000_000, area=90.0, property_type="second_hand",
                            status="available")
        out = _tool(db, monkeypatch, "deduplicate_properties", {"dry_run": True})
        assert out["success"] is True and out["result"]["duplicate_groups"] >= 1
        assert db.count_available_properties() == 2, "dry_run 不该删数据"


class TestStatsNumbers:
    def test_property_stats_reports_real_numbers(self, db, monkeypatch):
        for i in range(55):        # 超过默认 50 条
            make_property(db, title=f"统计房 {i}", price=1_000_000, area=90.0)
        out = _tool(db, monkeypatch, "property_stats", {})
        stats = out.get("stats") or out
        assert stats.get("available_properties") == 55
