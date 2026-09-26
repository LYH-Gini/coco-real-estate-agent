"""匹配引擎测试：评分算法、区域归一化、租买互斥、户型硬性要求、perfect_match

回归背景：2026-08-13 实测（100 客户 70 房源）暴露的匹配 bug 群，
本文件是那批修复的回归防线。
"""
import pytest

from conftest import make_customer, make_property


# ==================== 区域归一化 _norm_district ====================

class TestNormDistrict:
    def test_standard_xiqu(self, db):
        assert db._norm_district("美兰区") == "美兰"

    def test_haikou_prefix(self, db):
        """带城市前缀的地址归一化出正确区名（2026-08-28 修：原正则截出'口美兰'）"""
        assert db._norm_district("海口美兰区海甸岛") == "美兰"
        assert db._norm_district("海口市龙华区国贸") == "龙华"
        assert db._norm_district("三亚吉阳区某小区") == "吉阳"

    def test_dash_format(self, db):
        assert db._norm_district("美兰-海甸岛") == "美兰"

    def test_empty(self, db):
        assert db._norm_district("") == ""
        assert db._norm_district(None) == ""

    def test_no_qu_returns_first_segment(self, db):
        assert db._norm_district("秀英-西海岸") == "秀英"


# ==================== 区域匹配 _match_region ====================

class TestMatchRegion:
    def test_normalized_match_across_formats(self, db):
        """客户'美兰区' 应命中房源 '美兰-海甸岛'（8-13 回归案例）"""
        assert db._match_region("美兰区", "美兰-海甸岛", "某小区") is True

    def test_reverse_small_area_in_customer_loc(self, db):
        """客户写小片区'秀英大道'，房源存'秀英-秀英小街' → 区域级匹配"""
        assert db._match_region("秀英大道", "秀英-秀英小街", "某小区") is True

    def test_customer_loc_in_community(self, db):
        assert db._match_region("海甸岛", "美兰区", "海甸岛二东路小区") is True

    def test_no_location_means_ok(self, db):
        """客户无区域需求视为满足"""
        assert db._match_region("", "龙华区", "某小区") is True

    def test_different_district_fails(self, db):
        assert db._match_region("美兰区", "龙华区", "某小区") is False


# ==================== 户型匹配 _match_layout ====================

class TestMatchLayout:
    def test_exact_match(self, db):
        assert db._match_layout("3室2厅", 3, 2) is True

    def test_room_mismatch_rejected(self, db):
        """客户要 3 室，2 室房源必须排除（8-11 案例）"""
        assert db._match_layout("3室2厅", 2, 2) is False

    def test_range_layout(self, db):
        """'1-2室' 范围式偏好：1 室应通过（8-13 修的单值正则 bug）"""
        assert db._match_layout("1-2室1厅", 1, 1) is True
        assert db._match_layout("1-2室1厅", 3, 1) is False

    def test_no_pref_always_true(self, db):
        assert db._match_layout(None, 5, 3) is True

    def test_prop_without_rooms(self, db):
        assert db._match_layout("3室", None, None) is True


# ==================== 租买互斥 _match_type ====================

class TestMatchType:
    def test_rent_customer_only_rental(self, db):
        assert db._match_type("rent", "rental") is True
        assert db._match_type("rent", "second_hand") is False
        assert db._match_type("rent", "new") is False

    def test_buy_new_only_new(self, db):
        assert db._match_type("buy_new", "new") is True
        assert db._match_type("buy_new", "second_hand") is False

    def test_buy_second_hand_only_second_hand(self, db):
        assert db._match_type("buy_second_hand", "second_hand") is True
        assert db._match_type("buy_second_hand", "new") is False

    def test_unspecified_buy_accepts_all(self, db):
        """旧数据 customer_type='buy' 不限类型"""
        assert db._match_type("buy", "new") is True
        assert db._match_type("buy", "second_hand") is True


# ==================== 面积解析 _parse_area ====================

class TestParseArea:
    def test_range(self, db):
        assert db._parse_area("90-120") == (90.0, 120.0)

    def test_single_value_tolerance(self, db):
        lo, hi = db._parse_area("100平")
        assert lo == 90.0
        assert 109.9 < hi < 110.1  # 浮点容差

    def test_empty(self, db):
        assert db._parse_area("") == (0, 999999)


# ==================== 端到端匹配 match_property ====================

class TestMatchProperty:
    def test_perfect_match_full_score(self, db):
        """预算内+户型+区域+类型+装修全部命中 → perfect_match=True，满分"""
        c = make_customer(db, renovation="精装")
        p = make_property(db)  # 400万 3室2厅 美兰-海甸岛 100平 精装
        matches = db.match_property(c["id"])
        assert len(matches) == 1
        m = matches[0]
        assert m["id"] == p["id"]
        assert m["perfect_match"] is True
        assert m["score"] == 30 + 20 + 25 + 15 + 10  # 价格+面积+户型+区域+装修

    def test_rent_buy_mutual_exclusion(self, db):
        """租房客户绝不被推买卖房源；买房客户绝不被推租房（8-13 案例）"""
        renter = make_customer(db, name="租客", customer_type="rent",
                               budget_min=1500, budget_max=2500)
        make_property(db)  # 400万二手房在库
        assert db.match_property(renter["id"]) == []

        buyer = make_customer(db, name="买主")
        make_property(db, title="出租房", price=2000, area=60.0,
                      rooms=2, halls=1, property_type="rental")
        buyer_matches = db.match_property(buyer["id"])
        assert all(m["property_type"] != "rental" for m in buyer_matches)

    def test_over_budget_not_perfect(self, db):
        """超预算房源不得标 perfect_match（8-13 案例：280万推给230万预算）"""
        c = make_customer(db, budget_min=2_000_000, budget_max=2_300_000)
        make_property(db, price=2_800_000)
        matches = db.match_property(c["id"])
        if matches:
            assert matches[0]["perfect_match"] is False
            assert "超预算" in matches[0]["match_reasons"]

    def test_layout_hard_filter(self, db):
        """客户要 3 室，2 室房源直接排除（8-11 案例）"""
        c = make_customer(db, layout_pref="3室2厅")
        make_property(db, rooms=2, halls=2, title="两居室")
        assert db.match_property(c["id"]) == []

    def test_customer_with_deal_excluded(self, db):
        """已有交易记录的客户不再推房源（8-11 案例：过户完成仍被推荐）"""
        c = make_customer(db)
        p = make_property(db)
        db.add_deal(customer_id=c["id"], property_id=p["id"], stage="finalized")
        assert db.match_property(c["id"]) == []

    def test_scores_sorted_desc(self, db):
        """多房源结果按分数降序"""
        c = make_customer(db)
        make_property(db, title="完全命中")                       # 全命中
        make_property(db, title="区域不符", district="龙华区")     # 少区域分
        matches = db.match_property(c["id"])
        assert [m["score"] for m in matches] == sorted(
            [m["score"] for m in matches], reverse=True)
        assert matches[0]["title"] == "完全命中"

    def test_type_mismatch_marked_not_perfect(self, db):
        """买新房客户推二手房：不加分且不算完全匹配（8-13 案例）"""
        c = make_customer(db, customer_type="buy_new")
        make_property(db, property_type="second_hand")
        matches = db.match_property(c["id"])
        if matches:
            m = matches[0]
            assert m["perfect_match"] is False
            assert "类型不符" in m["match_reasons"]

    def test_nonexistent_customer(self, db):
        assert db.match_property(99999) == []


# ==================== 区域硬优先级（2026-08-30 加） ====================

class TestRegionPriority:
    def test_region_mismatch_labeled(self, db):
        """客户指定秀英区，美兰区房源必须带'区域不符'标注（2026-08-30 周女士案例）"""
        c = make_customer(db, customer_type="buy_second_hand", location="秀英区")
        make_property(db, title="无本区", district="美兰区")
        matches = db.match_property(c["id"])
        assert matches
        assert "区域不符" in matches[0]["match_reasons"]

    def test_region_preferred_sort(self, db):
        """客户指定秀英区，秀英区超预算(138万)房源应排在其他区在预算内(95万)房源【前面】。

        2026-08-30 周女士案例：客户要秀英区，引擎却把美兰区 95 万(75分)排在真秀英区
        138 万超预算(60分)前面。区域硬优先级 tier 应把秀英区顶到最前。
        """
        c = make_customer(db, customer_type="buy_second_hand", location="秀英区",
                          budget_min=900000, budget_max=1300000, area_pref=None,
                          layout_pref="2室")
        make_property(db, title="错区在预算", district="美兰区", price=950000,
                      area=75.0, rooms=2, halls=1)
        make_property(db, title="本区超预算", district="秀英区", price=1380000,
                      area=89.0, rooms=2, halls=2)
        matches = db.match_property(c["id"])
        assert [m["title"] for m in matches] == ["本区超预算", "错区在预算"]
        assert "超预算" in matches[0]["match_reasons"]
        assert "区域不符" in matches[1]["match_reasons"]

    def test_region_tier_no_location_unchanged(self, db):
        """客户没填区域时，排序仍纯按分数降序（回归，确认改动不误伤存量行为）"""
        c = make_customer(db, location="")  # 无区域需求
        make_property(db, title="高分房")                        # 全命中 400万 3室2厅 美兰-海甸岛
        make_property(db, title="低分房", district="龙华区")       # 少区域分
        matches = db.match_property(c["id"])
        scores = [m["score"] for m in matches]
        assert scores == sorted(scores, reverse=True)
        assert matches[0]["title"] == "高分房"
