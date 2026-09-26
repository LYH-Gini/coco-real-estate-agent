"""房源详情工具 get_property_detail 测试（2026-09-18 加）

目标：问"某套房的详细信息"时一次拿到全部字段 + 单价 + 业主；标题查不到就如实报 not_found
并给候选（绝不拿别的房源充当答案）；命中多套时让经纪人确认编号。
"""
import json

from conftest import make_property


def _detail(db, monkeypatch, **kw):
    import tools.real_estate_property as t
    monkeypatch.setattr(t, "_get_db", lambda: db)
    return json.loads(t.get_property_detail(**kw))


class TestPropertyDetail:
    def test_by_id_returns_full_detail(self, db, monkeypatch):
        """按编号查：房源字段 + 现算单价 + 业主段 + 图片数一次给全"""
        o = db.add_owner(name="陈利权", phone="13800138000", wechat="clq_wx")
        p = make_property(db, title="262栋1009", price=283_700, area=36.72,
                          images="a.jpg,b.jpg")
        db.update_property(p["id"], owner_id=o["id"], viewing_note="钥匙在门店")
        data = _detail(db, monkeypatch, property_id=p["id"])
        assert data["success"] is True
        assert data["property"]["unit_price"] == 7726.03  # 283700/36.72 两位小数
        assert data["owner"]["name"] == "陈利权"
        assert data["owner"]["phone"] == "13800138000"
        assert data["images"]["count"] == 2
        assert "陈利权" in data["message"]
        assert "13800138000" in data["message"]
        assert "7726.03" in data["message"]
        assert "钥匙在门店" in data["message"]

    def test_by_title_exact_match(self, db, monkeypatch):
        """按标题精确命中 → 返回详情（标题写法与库里一致）"""
        make_property(db, title="263栋1006", price=383_500, area=34.68)
        data = _detail(db, monkeypatch, title="263栋1006")
        assert data["success"] is True
        assert data["property"]["title"] == "263栋1006"
        assert data["property"]["unit_price"] == 11058.25

    def test_not_found_returns_candidates_not_other_property(self, db, monkeypatch):
        """标题查不到：报 not_found + 候选，且**不返回任何单套详情**（防止拿别的房源顶替）"""
        make_property(db, title="263栋1006", price=383_500, area=34.68)
        data = _detail(db, monkeypatch, title="263栋1009")
        assert data["success"] is False
        assert data["not_found"] is True
        assert "property" not in data          # 关键：没有单套数据可顶替
        assert data["candidates"], "应给出最接近的候选"
        assert "263栋1006" in [c["title"] for c in data["candidates"]]
        assert "263栋1009" in data["error"]

    def test_not_found_without_candidates(self, db, monkeypatch):
        """完全对不上的标题：not_found 且候选为空（不编造）"""
        make_property(db, title="263栋1006", price=383_500, area=34.68)
        data = _detail(db, monkeypatch, title="完全无关的楼栋ZZZ")
        assert data["success"] is False
        assert data["not_found"] is True
        assert data["candidates"] == []

    def test_ambiguous_title_lists_candidates(self, db, monkeypatch):
        """同名多套（不同期数/楼栋）→ ambiguous + 候选列表，不猜是哪一套"""
        make_property(db, title="海阔天空 1号楼101", area=88.0, price=1_000_000)
        make_property(db, title="海阔天空 1号楼101", area=120.0, price=1_500_000)
        data = _detail(db, monkeypatch, title="海阔天空 1号楼101")
        assert data["success"] is False
        assert data["ambiguous"] is True
        assert len(data["candidates"]) == 2
        assert "property" not in data

    def test_owner_missing_is_reported_explicitly(self, db, monkeypatch):
        """未关联业主：明确提示"未录入业主信息"，不静默省略"""
        p = make_property(db, title="无业主房源", price=300_000, area=50.0)
        data = _detail(db, monkeypatch, property_id=p["id"])
        assert data["success"] is True
        assert data["owner"] is None
        assert "还没录业主信息" in data["message"]

    def test_price_history_included(self, db, monkeypatch):
        """调过价的房源：详情里带最近一次调价记录"""
        p = make_property(db, title="调价房源", price=400_000, area=100.0)
        db.update_property(p["id"], price=380_000)
        data = _detail(db, monkeypatch, title="调价房源")
        assert data["success"] is True
        assert data["price_history"], "应带调价记录"
        assert data["property"]["unit_price"] == 3800.0  # 改价后单价同步
        assert "调价" in data["message"]

    def test_rental_price_label(self, db, monkeypatch):
        """出租房源：价格按月租展示、单价按 元/㎡ 现算"""
        make_property(db, title="单间出租", price=1000, area=25.0, property_type="rental")
        data = _detail(db, monkeypatch, title="单间出租")
        assert data["success"] is True
        assert "1000元/月" in data["message"]
        assert data["property"]["unit_price"] == 40.0

    def test_requires_id_or_title(self, db, monkeypatch):
        """两个参数都没给 → 明确报错（不返回随机房源）"""
        data = _detail(db, monkeypatch)
        assert data["success"] is False
        assert "房源编号" in data["error"] or "标题" in data["error"]

    def test_unknown_id(self, db, monkeypatch):
        """编号不存在 → not_found"""
        data = _detail(db, monkeypatch, property_id=999999)
        assert data["success"] is False
        assert data["not_found"] is True
