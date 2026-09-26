"""房源建档去重测试（2026-08-29 加：按 小区名称(标题含房号)+面积 判重，录入前拦截）"""
import json

from conftest import make_property


class TestPropertyDedup:
    def test_duplicate_property_detected(self, db):
        """同名同面积在售房源 → 视为重复"""
        make_property(db, title="海阔天空国瑞城 15号楼1单元1703", area=138.0)
        dup = db.find_duplicate_property(title="海阔天空国瑞城 15号楼1单元1703", area=138.0)
        assert dup is not None
        assert dup["title"] == "海阔天空国瑞城 15号楼1单元1703"

    def test_different_area_not_duplicate(self, db):
        """同名但不同面积 → 不同期数/楼栋，不判重"""
        make_property(db, title="国瑞城 1号楼1单元1703", area=138.0)
        dup = db.find_duplicate_property(title="国瑞城 1号楼1单元1703", area=120.0)
        assert dup is None

    def test_sold_property_not_counted(self, db):
        """已售房源不参与判重（只认在售）"""
        make_property(db, title="国瑞城 15号楼1单元1703", area=138.0, status="sold")
        dup = db.find_duplicate_property(title="国瑞城 15号楼1单元1703", area=138.0)
        assert dup is None

    def test_exclude_id(self, db):
        """更新场景：排除自身 id"""
        p = make_property(db, title="国瑞城 15号楼1单元1703", area=138.0)
        dup = db.find_duplicate_property(title="国瑞城 15号楼1单元1703", area=138.0, exclude_id=p["id"])
        assert dup is None

    def test_tool_duplicate_blocks_before_insert(self, db, monkeypatch):
        """工具层：录入前命中重复 → duplicate=true 不落库；force=true 强制新增"""
        import tools.real_estate_property as t
        monkeypatch.setattr(t, "_get_db", lambda: db)
        # 先造一条在售
        make_property(db, title="国瑞城 15号楼1单元1703", area=138.0, price=2_550_000)
        count_before = db.get_stats().get("available_properties", 0)
        # 重复录入 → 应被拦
        out1 = json.loads(t.add_property(title="国瑞城 15号楼1单元1703", price=2_600_000, area=138.0))
        assert out1["success"] is False
        assert out1["duplicate"] is True
        assert out1["existing_property"]["title"] == "国瑞城 15号楼1单元1703"
        # 未新增（在售数不变）
        assert db.get_stats().get("available_properties", 0) == count_before
        # force=True → 强制新增
        out2 = json.loads(t.add_property(title="国瑞城 15号楼1单元1703", price=2_600_000, area=138.0, force=True))
        assert out2["success"] is True

    def test_duplicate_detected_title_variation(self, db):
        """标题格式不同(区名前缀/空格/逗号)也能判重（归一化后一致）"""
        make_property(db, title="海口美兰区桂林洋海阔天空, 7号楼2单元301", area=88.0)
        dup = db.find_duplicate_property(title="桂林洋海阔天空 7号楼2单元301", area=88.0)
        assert dup is not None
        assert dup["area"] == 88.0

    def test_different_unit_not_duplicate(self, db):
        """不同单元号 → 按归一化后仍不同，不判重"""
        make_property(db, title="海口美兰区桂林洋海阔天空, 7号楼2单元301", area=88.0)
        dup = db.find_duplicate_property(title="桂林洋海阔天空 8号楼2单元302", area=88.0)
        assert dup is None

    def test_normalize_title_unit(self):
        from agent.real_estate_db import _normalize_title
        a = _normalize_title("海口美兰区桂林洋海阔天空, 7号楼2单元301")
        b = _normalize_title("美兰区桂林洋海阔天空 7号楼2单元301")
        c = _normalize_title("海口市龙华区XX花园 1号楼1单元101")
        assert a == b == "海阔天空7号楼2单元301"
        assert c == "XX花园1号楼1单元101"
        assert a != _normalize_title("桂林洋海阔天空 8号楼2单元302")


class TestDuplicateWarning:
    def test_unique_title_no_duplicate_warning(self, db, monkeypatch):
        """修复回归：唯一标题房源 → 插入后不再误报'同名 1 条'（2026-08-29）"""
        import tools.real_estate_property as t
        monkeypatch.setattr(t, "_get_db", lambda: db)
        out = json.loads(t.add_property(title="唯一标题小区XYZ 9号楼901", price=2_000_000, area=90.0))
        assert out["success"] is True
        assert "duplicate_warning" not in out, "唯一标题不应触发同名提示"

    def test_same_title_different_area_warns(self, db, monkeypatch):
        """同名不同面积（不同期数/楼栋）→ 放行 + 提示（排除自身后仍能提示另一条）"""
        import tools.real_estate_property as t
        monkeypatch.setattr(t, "_get_db", lambda: db)
        t.add_property(title="国瑞城 1号楼1单元101", price=2_000_000, area=100.0)
        out = json.loads(t.add_property(title="国瑞城 1号楼1单元101", price=2_100_000, area=120.0))
        assert out["success"] is True
        assert "duplicate_warning" in out
        assert "1 条" in out["duplicate_warning"]


class TestMatchBroadened:
    def test_match_customers_includes_non_sa(self, db):
        """反匹配放宽到所有客户（2026-08-29）：B 级客户也应被匹配到（原来只 S/A）"""
        from conftest import make_customer as mk_customer
        prop = make_property(db, title="海甸岛某小区 3号楼101", price=3_500_000, area=100.0, district="美兰-海甸岛")
        mk_customer(db, name="王五", tier="B", budget_min=3_000_000, budget_max=5_000_000,
                    area_pref="90-120", layout_pref="3室2厅", location="美兰区")
        matched = db.match_customers_for_property(prop["id"])
        assert any(c["customer_name"] == "王五" for c in matched), "B 级客户也应被反匹配到"


class TestMatchTypeHardExclude:
    """2026-08-30: 类型硬排除——买二手不推新房、买新不推二手（原只做租买互斥，新房/二手混推）"""

    def test_buy_second_hand_excludes_new_property(self, db):
        """买二手客户不应匹配到新房"""
        newp = db.add_property(title="某新房 1号楼101", community="新盘", price=3_000_000, area=100.0,
                               rooms=3, halls=2, property_type="new")
        c = db.add_customer(name="买二手客", customer_type="buy_second_hand",
                            budget_min=2_000_000, budget_max=4_000_000,
                            layout_pref="3室2厅", location="美兰区")
        matched = db.match_property(c["id"])
        assert all(m["id"] != newp["id"] for m in matched), "买二手客户不应匹配到新房"

    def test_buy_new_excludes_second_hand(self, db):
        """买新客户不应匹配到二手房"""
        sec = db.add_property(title="某二手 1号楼101", community="老盘", price=3_000_000, area=100.0,
                              rooms=3, halls=2, property_type="second_hand")
        c = db.add_customer(name="买新客", customer_type="buy_new",
                            budget_min=2_000_000, budget_max=4_000_000,
                            layout_pref="3室2厅", location="美兰区")
        matched = db.match_property(c["id"])
        assert all(m["id"] != sec["id"] for m in matched), "买新客户不应匹配到二手房"

    def test_unspecified_buy_matches_both(self, db):
        """买(类型未细分)客户应能匹配到新房和二手"""
        db.add_property(title="某新房 1号楼101", community="新盘", price=3_000_000, area=100.0,
                        rooms=3, halls=2, property_type="new")
        db.add_property(title="某二手 1号楼101", community="老盘", price=3_000_000, area=100.0,
                        rooms=3, halls=2, property_type="second_hand")
        c = db.add_customer(name="不限客", customer_type="buy",
                            budget_min=2_000_000, budget_max=4_000_000,
                            layout_pref="3室2厅", location="美兰区")
        matched = db.match_property(c["id"])
        types = {m.get("property_type") for m in matched}
        assert "new" in types and "second_hand" in types, "买(未细分)客户应能匹配到新房和二手"


class TestOwnerAndTenant:
    def test_add_property_links_owner_dedup(self, db, monkeypatch):
        """录入房源带业主信息 → 自动登记房东并关联；同电话复用不新建（2026-08-29）"""
        import tools.real_estate_property as t
        monkeypatch.setattr(t, "_get_db", lambda: db)
        out1 = json.loads(t.add_property(title="某小区 1号楼101", price=2_000_000, area=90.0,
                                         owner_name="王房东", owner_phone="13900001111"))
        assert out1["success"] is True
        assert out1["owner"]["name"] == "王房东"
        oid1 = out1["owner"]["id"]
        # 房源已关联该房东
        prop1 = db.search_properties(title="某小区 1号楼101")[0]
        assert prop1["owner_id"] == oid1
        # 同电话再录入 → 复用同一房东，不新建
        out2 = json.loads(t.add_property(title="某小区 2号楼102", price=2_100_000, area=100.0,
                                         owner_name="王房东", owner_phone="13900001111"))
        assert out2["owner"]["id"] == oid1
        assert out2["success"] is True

    def test_update_property_links_owner(self, db, monkeypatch):
        """update_property 补业主联系方式 → 自动关联房东"""
        import tools.real_estate_property as t
        monkeypatch.setattr(t, "_get_db", lambda: db)
        p = json.loads(t.add_property(title="某小区 3号楼103", price=2_000_000, area=90.0) )
        out = json.loads(t.update_property(p["property"]["id"], owner_name="李房东", owner_phone="13900002222"))
        assert out["success"] is True
        assert out["owner"]["name"] == "李房东"
        prop = db.search_properties(title="某小区 3号楼103")[0]
        assert prop["owner_id"] == out["owner"]["id"]

    def test_add_property_stores_tenant_requirements(self, db, monkeypatch):
        """出租房源租客要求正式入库（2026-08-29）"""
        import tools.real_estate_property as t
        monkeypatch.setattr(t, "_get_db", lambda: db)
        out = json.loads(t.add_property(title="某公寓 5号楼501", price=3000, area=60.0, property_type="rental",
                                        tenant_requirements="不吸烟，办居住证，学生优先"))
        assert out["success"] is True
        prop = db.search_properties(title="某公寓 5号楼501")[0]
        assert prop["tenant_requirements"] == "不吸烟，办居住证，学生优先"

    def test_tenant_req_conflict_filters_rent_customer(self):
        """租客要求过滤：客户资料明确冲突 → 不匹配；无冲突/否定句 → 匹配（2026-08-29）"""
        import os, tempfile
        from agent.real_estate_db import RealEstateDB
        d = RealEstateDB(f"sqlite:///{tempfile.mkdtemp()}/t.db")
        # 冲突：客户备注"本人吸烟" vs 要求"不吸烟"
        assert d._tenant_req_ok("不吸烟，办居住证", {"notes": "本人吸烟，可办居住证", "tags": ""}) is False
        # 无冲突：客户备注"不吸烟"
        assert d._tenant_req_ok("不吸烟", {"notes": "不吸烟，学生", "tags": ""}) is True
        # 无要求
        assert d._tenant_req_ok(None, {"notes": "吸烟", "tags": ""}) is True
        # 无客户资料
        assert d._tenant_req_ok("不吸烟", {"notes": "", "tags": ""}) is True
