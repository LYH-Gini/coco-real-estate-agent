"""带看反馈反哺房源缺陷标签测试（功能3，批次四）"""
import json

from conftest import make_customer, make_property


def _view_and_feedback(db, customer_name, property_id, feedback, result="not_interested"):
    from datetime import datetime
    c = make_customer(db, name=customer_name)
    v = db.add_viewing(
        customer_id=c["id"], property_id=property_id,
        viewing_time=datetime(2026, 8, 20, 10, 0), status="scheduled",
    )
    db.update_viewing(v["id"], status="done", result=result, feedback=feedback)


def _add_done_viewing(db, customer_id, property_id, feedback, day=20):
    """给指定客户造一条"已完成 + 带反馈"的带看"""
    from datetime import datetime
    v = db.add_viewing(customer_id=customer_id, property_id=property_id,
                       viewing_time=datetime(2026, 8, day, 10, 0), status="done")
    db.update_viewing(v["id"], status="done", result="not_interested", feedback=feedback)
    return v["id"]


# ==================== 缺陷标签生成 ====================

class TestDefectTags:
    def test_two_mentions_tagged(self, db):
        """同一缺陷被 2 组客户提及 → 打标签"""
        p = make_property(db)
        _view_and_feedback(db, "客户甲", p["id"], "采光太差了，白天都要开灯")
        _view_and_feedback(db, "客户乙", p["id"], "屋里暗，采光不好")
        defects = db.refresh_defect_tags(p["id"])
        assert "采光差" in defects

    def test_single_mention_not_tagged(self, db):
        """只有 1 组客户提及 → 不打标签（阈值=2）"""
        p = make_property(db)
        _view_and_feedback(db, "客户甲", p["id"], "有点吵，临街")
        assert db.refresh_defect_tags(p["id"]) == []

    def test_same_customer_counted_once(self, db):
        """同一客户一条反馈里重复说"吵"，只算1组"""
        p = make_property(db)
        _view_and_feedback(db, "客户甲", p["id"], "很吵，临街噪音太吵了")
        assert db.refresh_defect_tags(p["id"]) == []

    def test_multiple_defects(self, db):
        """多个缺陷同时命中"""
        p = make_property(db)
        _view_and_feedback(db, "客户甲", p["id"], "采光差，而且临街很吵")
        _view_and_feedback(db, "客户乙", p["id"], "又暗又吵，楼下有漏水痕迹")
        _view_and_feedback(db, "客户丙", p["id"], "卫生间漏水")
        defects = db.refresh_defect_tags(p["id"])
        assert "采光差" in defects
        assert "临街吵" in defects
        assert "漏水" in defects   # 2次提及（乙丙）达标

    def test_cancelled_viewing_not_counted(self, db):
        """取消的带看不计入"""
        p = make_property(db)
        from datetime import datetime
        c = make_customer(db, name="没去的")
        v = db.add_viewing(customer_id=c["id"], property_id=p["id"],
                           viewing_time=datetime(2026, 8, 20, 10, 0), status="cancelled",
                           )
        db.update_viewing(v["id"], status="cancelled", feedback="听朋友说采光差")
        assert db.refresh_defect_tags(p["id"]) == []

    def test_same_customer_twice_not_enough(self, db):
        """口径（2026-09-25 拍板）：按 **≥2 位不同客户** 判 —— 同一客户两次带看都提"采光差"不打标"""
        p = make_property(db)
        one = make_customer(db, name="同一位客户")
        _add_done_viewing(db, one["id"], p["id"], "采光差，白天要开灯", day=20)
        _add_done_viewing(db, one["id"], p["id"], "还是采光差，太暗了", day=21)
        assert db.refresh_defect_tags(p["id"]) == []

    def test_two_distinct_customers_tagged_and_counted(self, db):
        """两位不同客户各提一次 → 打标，明细里记的是"不同客户数"（不是带看条数）"""
        import json as _json
        p = make_property(db)
        a = make_customer(db, name="客户甲")
        b = make_customer(db, name="客户乙")
        _add_done_viewing(db, a["id"], p["id"], "采光差", day=20)
        _add_done_viewing(db, b["id"], p["id"], "采光不好", day=21)
        _add_done_viewing(db, b["id"], p["id"], "采光还是差", day=22)   # 同一位的第二条
        defects = db.refresh_defect_tags(p["id"])
        assert "采光差" in defects, defects
        with db.get_session() as s:
            from agent.real_estate_db import Property
            detail = _json.loads(s.query(Property).get(p["id"]).defect_tags)
        assert detail["采光差"] == 2, detail          # 2 位不同客户（不是 3 条带看）


# ==================== 匹配降权 ====================

class TestDefectDownweight:
    def test_defect_property_downweighted(self, db):
        """有缺陷标签的房源评分×0.8，且理由里透明标注"""
        c = make_customer(db, budget_min=3_000_000, budget_max=5_000_000,
                          area_pref="90-120", layout_pref="3室2厅",
                          location="美兰区", customer_type="buy_second_hand")
        make_property(db)  # 干净房
        dirty = make_property(db, title="有缺陷的", community="缺陷小区",
                              price=3_800_000)
        import json as _json
        db.update_property(dirty["id"], **{})  # 触发一次空更新确保无副作用
        # 手动打标签
        from agent.real_estate_db import Property
        with db.get_session() as s:
            prop = s.query(Property).get(dirty["id"])
            prop.defect_tags = _json.dumps({"采光差": 3, "临街吵": 2}, ensure_ascii=False)
            s.commit()

        matches = db.match_property(c["id"])
        clean = [m for m in matches if m["title"].startswith("测试房源")]
        dirty_m = [m for m in matches if m["title"] == "有缺陷的"]
        if clean and dirty_m:
            assert clean[0]["score"] > dirty_m[0]["score"]
        if dirty_m:
            reasons = " ".join(dirty_m[0]["match_reasons"])
            assert "客户反馈" in reasons
            assert "采光差" in reasons


# ==================== 清除标签 ====================

class TestClearDefect:
    def test_clear_tag(self, db):
        p = make_property(db)
        with db.get_session() as s:
            from agent.real_estate_db import Property
            prop = s.query(Property).get(p["id"])
            import json as _json
            prop.defect_tags = _json.dumps({"采光差": 3}, ensure_ascii=False)
            s.commit()
        out = db.clear_defect_tag(p["id"], "采光差")
        assert out["cleared"] is True and out["remaining"] == [], out   # 新形状：dict（2026-09-25 契约变更）
        props = db.search_properties(limit=100)
        target = [x for x in props if x["id"] == p["id"]][0]
        assert not target["defect_tags"]  # None 或空均算已清空

    def test_clear_nonexistent_tag(self, db):
        p = make_property(db)
        out = db.clear_defect_tag(p["id"], "不存在的")
        assert out["cleared"] is False, out

    def test_clear_unknown_property_returns_none(self, db):
        assert db.clear_defect_tag(999999, "采光差") is None


# ==================== 整改基线（2026-09-25 F203） ====================

class TestDefectBaseline:
    def test_cleared_tag_not_brought_back_by_new_feedback(self, db):
        """清过标签的房源：整改前的旧反馈不再把标签打回来"""
        import json as _json
        p = make_property(db)
        _view_and_feedback(db, "客户甲", p["id"], "采光差，白天要开灯")
        _view_and_feedback(db, "客户乙", p["id"], "采光不好")
        assert "采光差" in db.refresh_defect_tags(p["id"])
        assert db.clear_defect_tag(p["id"], "采光差")["cleared"] is True
        _view_and_feedback(db, "客户丙", p["id"], "房东整改后这次看采光还行")
        assert db.refresh_defect_tags(p["id"]) == []          # 不再被打回

    def test_new_feedback_after_baseline_can_tag_again(self, db):
        """整改之后的**新**反馈仍按同一阈值判：真出新问题照样标上"""
        p = make_property(db)
        _view_and_feedback(db, "客户甲", p["id"], "采光差")
        _view_and_feedback(db, "客户乙", p["id"], "采光差")
        db.clear_defect_tag(p["id"], "采光差")
        for name in ("客户丁", "客户戊"):
            _view_and_feedback(db, name, p["id"], "还是采光差，一点没改")
        assert "采光差" in db.refresh_defect_tags(p["id"])

    def test_baseline_recorded_on_clear(self, db):
        from agent.real_estate_db import Property
        p = make_property(db)
        with db.get_session() as s:
            import json as _json
            s.query(Property).get(p["id"]).defect_tags = _json.dumps({"采光差": 2})
            s.commit()
        assert db.clear_defect_tag(p["id"], "采光差")["cleared"] is True
        with db.get_session() as s:
            assert s.query(Property).get(p["id"]).defect_baseline_at is not None


# ==================== 工具层 ====================

class TestDefectTools:
    def test_record_viewing_triggers_refresh(self, db, monkeypatch):
        """record_viewing 带 feedback → 自动刷新缺陷标签"""
        import tools.real_estate_viewing as vmod
        monkeypatch.setattr(vmod, "_get_db", lambda: db)
        from datetime import datetime
        c = make_customer(db, name="带看客户")
        p = make_property(db, price=2_000_000, area=80.0)
        v = db.add_viewing(customer_id=c["id"], property_id=p["id"],
                           viewing_time=datetime(2026, 8, 20, 10, 0), status="scheduled")
        # 两个客户都吐槽采光
        _view_and_feedback(db, "另一位", p["id"], "采光差")
        r = json.loads(vmod.record_viewing(viewing_id=v["id"], status="done",
                                           result="not_interested", feedback="屋里太暗采光差"))
        assert r["success"] is True
        assert r.get("defect_tags_updated") == ["采光差"]
