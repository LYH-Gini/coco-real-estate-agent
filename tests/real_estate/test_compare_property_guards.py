"""compare_property 回归（2026-09-26 第八组 F277–F287）

背景（探针 `t64a` 实测）：
① **均价名不符实**：字段叫 `district_avg_price`，实际是**全库在售均价**（含别的区域、含出租房）——
   朝阳区与海淀区两套房源拿到**同一个数** 1200688，同区只算出售应是 1600000；
② **出售房与出租房混在一张对比表**：同小区竞品里 160万、140万 与 2500元/月 并排，
   总价 vs 月租根本不是一个量纲；
③ 出租房源做对比、已售房源做对比，回执**一字未提**；
④ 展示全是裸数字（`price=1500000.0`、`unit_price=16666.67`、`area=90.0`），没有「150万 / 元/月 / ㎡」；
⑤ 竞品没有「共几套、列了几套、被截断」；
⑥ 描述 11 字、`property_id` 说明只有「房源ID」；同小区不足补同区域时两个来源混在一起无标注。

本文件钉住修好之后的行为（断言用真实业务值：非整万售价、2500 元月租）。
"""
import json

import pytest

import tools.real_estate_intent as m_intent


@pytest.fixture
def wired(db, monkeypatch):
    monkeypatch.setattr(m_intent, "_get_db", lambda: db)
    return db


def _add(db, title, price, area, **kw):
    kw.setdefault("property_type", "second_hand")
    kw.setdefault("status", "available")
    return db.add_property(title=title, price=price, area=area, **kw)


@pytest.fixture
def market(wired):
    """阳光小区 3 套在售二手房（目标 150万/90㎡ + 160万/95㎡ + 140万/85㎡）、
    同小区 1 套出租房、同区其它小区 2 套、另区 1 套、同小区 1 套已售"""
    target = _add(wired, "目标房源", 1_500_000, 90.0, community="阳光小区", district="朝阳区")
    tgt_same = _add(wired, "同小区房源", 1_600_000, 95.0, community="阳光小区", district="朝阳区")
    other = _add(wired, "同小区另一套", 1_400_000, 85.0, community="阳光小区", district="朝阳区")
    rent = _add(wired, "同小区出租", 2_500, 60.0, community="阳光小区", district="朝阳区",
                property_type="rental")
    rent2 = _add(wired, "同小区出租二号", 2_800, 65.0, community="阳光小区", district="朝阳区",
                 property_type="rental")
    d1 = _add(wired, "同区甲", 1_800_000, 100.0, community="朝阳新苑", district="朝阳区")
    d2 = _add(wired, "同区乙", 2_000_000, 110.0, community="朝阳新苑", district="朝阳区")
    hd = _add(wired, "别的区", 5_000_000, 120.0, district="海淀区")
    sold = _add(wired, "同小区已售", 1_450_000, 92.0, community="阳光小区", district="朝阳区",
                status="sold")
    return {"target": target, "tgt_same": tgt_same, "other": other, "rent": rent,
            "rent2": rent2, "d1": d1, "d2": d2, "hd": hd, "sold": sold}


def _call(args):
    return json.loads(m_intent.compare_property(**args))


# ==================== ① 均价口径 ====================
class TestAverage:
    def test_avg_is_same_district_and_same_type(self, market):
        """同区域 + 同类型，且**不含这套自己**：朝阳区其它 4 套在售二手房（160/140/180/200 万）"""
        out = _call({"property_id": market["target"]["id"]})
        comp = out["comparison"]
        expected = round((1_600_000 + 1_400_000 + 1_800_000 + 2_000_000) / 4)
        assert comp["district_avg_price"] == expected, comp
        assert comp["avg_scope"].startswith("朝阳区在售二手房均价"), comp["avg_scope"]
        assert "不含这套" in comp["avg_scope"], comp["avg_scope"]
        assert comp["avg_sample"] == 4, comp

    def test_avg_differs_by_district(self, market):
        """不同区域必须给出不同的均价（原先两个区同一个数）"""
        chaoyang = _call({"property_id": market["target"]["id"]})["comparison"]
        haidian = _call({"property_id": market["hd"]["id"]})["comparison"]
        assert chaoyang["district_avg_price"] != haidian["district_avg_price"], (chaoyang, haidian)
        assert haidian["avg_scope"].startswith("海淀区在售二手房均价"), haidian["avg_scope"]

    def test_rentals_do_not_pollute_sale_average(self, market):
        """同小区的 2500 元月租不许进二手房的均价（原先会把均价拉低到 1200688）"""
        comp = _call({"property_id": market["target"]["id"]})["comparison"]
        assert comp["district_avg_price"] > 1_000_000, comp
        assert comp["avg_sample"] == 4, comp

    def test_no_district_says_global_scope(self, wired):
        """房源没填区域 → 如实说这是全库口径，不冒称「同区域均价」"""
        p = _add(wired, "没填区域", 1_500_000, 90.0)
        _add(wired, "另一套", 1_000_000, 80.0)
        comp = _call({"property_id": p["id"]})["comparison"]
        assert comp["avg_scope"].startswith("全库在售二手房均价"), comp["avg_scope"]
        assert "没填区域" in comp["avg_scope"], comp["avg_scope"]

    def test_rental_average_is_monthly_rent(self, wired):
        """出租房源的均价按「元/月」说，不说成「多少万」"""
        p = _add(wired, "出租目标", 2_500, 60.0, property_type="rental", community="租小区",
                 district="朝阳区")
        _add(wired, "出租二号", 3_500, 70.0, property_type="rental", community="租小区",
             district="朝阳区")
        comp = _call({"property_id": p["id"]})["comparison"]
        assert comp["avg_label"] == "3500元/月", comp          # 同区域另一套出租的月租（不含自己）
        assert "在租" not in comp["avg_scope"] or "租房均价" in comp["avg_scope"], comp["avg_scope"]
        assert "租房均价" in comp["avg_scope"], comp["avg_scope"]


# ==================== ② 只比同类型 ====================
class TestSameTypeOnly:
    def test_competitors_are_same_type(self, market):
        comp = _call({"property_id": market["target"]["id"]})["comparison"]
        types = {c["property_type"] for c in comp["competitors"]}
        assert types == {"second_hand"}, comp["competitors"]

    def test_rental_target_compares_rentals(self, market):
        comp = _call({"property_id": market["rent"]["id"]})["comparison"]
        assert {c["property_type"] for c in comp["competitors"]} == {"rental"}, comp["competitors"]
        assert comp["target"]["price_label"] == "2500元/月", comp["target"]

    def test_sold_property_excluded(self, market):
        comp = _call({"property_id": market["target"]["id"]})["comparison"]
        assert market["sold"]["id"] not in {c["id"] for c in comp["competitors"]}, comp

    def test_scope_label_on_each_competitor(self, market):
        """同小区优先、不足补同区域 —— 两个来源必须能分辨"""
        comp = _call({"property_id": market["target"]["id"]})["comparison"]
        labels = {c["scope_label"] for c in comp["competitors"]}
        assert labels <= {"同小区", "同区域"}, comp
        assert labels, comp
        assert all(c["scope"] in ("same_community", "same_district") for c in comp["competitors"])

    def test_district_fill_in_when_community_thin(self, wired):
        """同小区只有 1 套时补同区域，且标注出来源"""
        p = _add(wired, "孤零零目标", 1_500_000, 90.0, community="独栋小区", district="朝阳区")
        _add(wired, "唯一邻居", 1_600_000, 95.0, community="独栋小区", district="朝阳区")
        _add(wired, "同区补位", 1_200_000, 80.0, community="朝阳新苑", district="朝阳区")
        comp = _call({"property_id": p["id"]})["comparison"]
        by_scope = {c["scope"] for c in comp["competitors"]}
        assert by_scope == {"same_community", "same_district"}, comp["competitors"]
        assert comp["truncated"] is False, comp


# ==================== ③ 形状与截断 ====================
class TestShape:
    def test_total_count_truncated(self, market):
        comp = _call({"property_id": market["target"]["id"], "limit": 2})["comparison"]
        assert comp["count"] == 2, comp
        # 同小区 2 套 ∪ 同区域 4 套（同小区本身也在朝阳区，不能重复算）
        assert comp["total"] == 4, comp
        assert comp["truncated"] is True, comp

    def test_message_says_how_many(self, market):
        out = _call({"property_id": market["target"]["id"], "limit": 2})
        assert "共 4 套" in out["message"] and "这里列了 2 套" in out["message"], out["message"]

    def test_message_has_no_internal_terms(self, market):
        for pid in (market["target"]["id"], market["rent"]["id"], market["hd"]["id"]):
            out = _call({"property_id": pid})
            for bad in ("property_id", "compare_property", "scope", "district_avg_price"):
                assert bad not in out["message"], out["message"]

    def test_not_found(self, wired):
        out = _call({"property_id": 999999})
        assert out.get("success") is not True and "不存在" in out["error"], out

    def test_bad_id(self, wired):
        out = _call({"property_id": "abc"})
        assert out.get("success") is not True and "房源编号" in out["error"], out

    def test_empty_market_is_readable(self, wired):
        """全库只有这一套在售 → 不崩、如实说没得比，均价不臆造（给 None，不拿自己当行价）"""
        p = _add(wired, "唯一房源", 1_500_000, 90.0, community="空空小区", district="朝阳区")
        out = _call({"property_id": p["id"]})
        comp = out["comparison"]
        assert comp["competitors"] == [] and comp["total"] == 0, comp
        assert "没有在售的二手房可比" in out["message"], out["message"]
        assert comp["district_avg_price"] is None and comp["avg_label"] is None, comp
        assert "均价" not in out["message"], out["message"]


# ==================== ④ 展示口径 ====================
class TestDisplay:
    def test_price_area_unit_labels(self, market):
        comp = _call({"property_id": market["target"]["id"]})["comparison"]
        target = comp["target"]
        assert target["price_label"] == "150万", target
        assert target["area_label"] == "90", target
        assert target["unit_price_label"] == "16666.67元/㎡", target
        assert target["status_label"] == "在售" and target["property_type_label"] == "二手房", target

    def test_rental_unit_price_suffix(self, market):
        comp = _call({"property_id": market["rent"]["id"]})["comparison"]
        assert comp["target"]["price_label"] == "2500元/月", comp["target"]
        assert comp["target"]["unit_price_label"].endswith("元/㎡/月"), comp["target"]

    def test_non_integer_price_keeps_decimals(self, wired):
        """非整万售价不许被抹成整数（385.50万 不是 386万）"""
        p = _add(wired, "零头价格", 3_855_000, 90.0, community="零头小区", district="朝阳区")
        comp = _call({"property_id": p["id"]})["comparison"]
        assert comp["target"]["price_label"] == "385.50万", comp["target"]

    def test_area_no_trailing_zero(self, market):
        comp = _call({"property_id": market["target"]["id"]})["comparison"]
        for row in comp["competitors"]:
            assert not row["area_label"].endswith(".0"), row


# ==================== ⑤ 已售/已租与缺失区域 ====================
class TestWarnings:
    def test_sold_target_warns(self, market):
        out = _call({"property_id": market["sold"]["id"]})
        assert out["success"] is True, out
        assert any("已售" in w for w in out.get("warnings") or []), out

    def test_missing_community_warns(self, wired):
        p = _add(wired, "没填小区", 1_500_000, 90.0, district="朝阳区")
        out = _call({"property_id": p["id"]})
        assert any("没填小区" in w for w in out.get("warnings") or []), out

    def test_missing_region_and_community_warns(self, wired):
        p = _add(wired, "啥都没填", 1_500_000, 90.0)
        out = _call({"property_id": p["id"]})
        assert any("没填小区也没填区域" in w for w in out.get("warnings") or []), out

    def test_normal_case_has_no_warnings(self, market):
        out = _call({"property_id": market["target"]["id"]})
        assert not out.get("warnings"), out


# ==================== ⑥ 描述与参数说明 ====================
class TestSchema:
    def test_description(self):
        from tools.registry import registry
        desc = registry.get_entry("compare_property").schema["description"]
        assert len(desc) > 80, desc
        for word in ("同小区", "同区域", "卖房比卖房", "均价", "样本", "已售"):
            assert word in desc, (word, desc)

    def test_param_described(self):
        from tools.registry import registry
        props = (registry.get_entry("compare_property").schema.get("parameters") or {}).get("properties") or {}
        assert "编号" in (props.get("property_id") or {}).get("description", ""), props
