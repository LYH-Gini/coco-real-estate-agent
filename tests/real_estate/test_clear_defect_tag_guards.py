"""clear_defect_tag 回归（2026-09-25 第五组「带看」第 48 项，F203–F205）

背景（实测，见 /root/coco-tool-audit/results/raw/t51.before2.log、t45b.log）：
① **手清后被打回**：手动清除标签成功、房东整改后，只要再录一条带看反馈，重扫就把整改前的
   历史差评重新算进去、标签又回来了 —— "手动清除"等于白做；
② 房源不存在时也回「清除失败：该房源没有标签 X」（经纪人以为房源在、只是没这个标签）；
③ 标签名不归一：`' 临街吵 '`（多两个空格）清不掉；空标签回的是「没有标签 」（残缺话）。

本文件钉住修好之后的行为（含整改基线、SQL 聚合重扫、查询次数钉子）。
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, text


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_viewing as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


def _customer(db, name="标签客户", phone="13800001111"):
    return db.add_customer(name=name, phone=phone, tier="A",
                           customer_type="buy_second_hand")["id"]


def _property(db, title="标签房源 1号楼101"):
    return db.add_property(title=title, price=1500000, area=80,
                           property_type="second_hand", status="available")["id"]


def _feedback(wired, cid, pid, feedback, day=1, result="not_interested"):
    """走真实路径：约带看 → 记结果（会给该房源重扫缺陷标签）"""
    import tools.real_estate_viewing as m

    when = (datetime.now() + timedelta(days=day)).strftime("%Y-%m-%d 10:00")
    out = json.loads(m.schedule_viewing(customer_id=cid, property_id=pid, viewing_time=when))
    vid = out["viewing"]["id"]
    return json.loads(m.record_viewing(viewing_id=vid, status="已完成", result=result,
                                       feedback=feedback))


def _call(**kwargs):
    import tools.real_estate_viewing as m

    return json.loads(m.clear_defect_tag(**kwargs))


def _tags(db, pid):
    with db.get_session() as s:
        return s.execute(text("SELECT defect_tags FROM re_properties WHERE id = :i"),
                         {"i": pid}).scalar()


def _baseline(db, pid):
    with db.get_session() as s:
        return s.execute(text("SELECT defect_baseline_at FROM re_properties WHERE id = :i"),
                         {"i": pid}).scalar()


@pytest.fixture
def tagged(wired):
    """两位客户提"采光差" → 自动打标；第三位提"临街吵"（不够阈值）"""
    cid1, cid2, cid3 = (_customer(wired, "标签甲", "13800001111"),
                        _customer(wired, "标签乙", "13800002222"),
                        _customer(wired, "标签丙", "13800003333"))
    pid = _property(wired)
    _feedback(wired, cid1, pid, "采光差，白天要开灯", day=1)
    _feedback(wired, cid2, pid, "采光不好", day=2)
    _feedback(wired, cid3, pid, "临街吵", day=3)
    assert "采光差" in (_tags(wired, pid) or ""), _tags(wired, pid)
    return {"pid": pid, "cid1": cid1, "cid2": cid2, "cid3": cid3}


# ==================== ① 正常路径 ====================
class TestClear:
    def test_clear_success_receipt(self, wired, tagged):
        out = _call(property_id=tagged["pid"], tag="采光差")
        assert out["success"] is True, out
        assert out["message"] == ("已清除缺陷标签：采光差"
                                  "（已记下整改时间，整改前的旧差评不会再把这个标签打回来）"), out["message"]
        assert out["defect_tags"] == [], out

    def test_clear_lands_in_db(self, wired, tagged):
        _call(property_id=tagged["pid"], tag="采光差")
        assert _tags(wired, tagged["pid"]) is None, _tags(wired, tagged["pid"])

    def test_clear_records_baseline(self, wired, tagged):
        assert _baseline(wired, tagged["pid"]) is None
        _call(property_id=tagged["pid"], tag="采光差")
        assert _baseline(wired, tagged["pid"]) is not None

    def test_receipt_has_no_internals(self, wired, tagged):
        out = _call(property_id=tagged["pid"], tag="采光差")
        for word in ("defect_tags", "baseline", "clear_defect_tag", "None"):
            assert word not in out["message"], (word, out["message"])

    def test_other_tags_kept(self, wired, tagged):
        """只清指定标签，别的标签不动"""
        import tools.real_estate_viewing as m

        with wired.get_session() as s:
            s.execute(text("UPDATE re_properties SET defect_tags = :t WHERE id = :i"),
                      {"t": json.dumps({"采光差": 2, "临街吵": 3}, ensure_ascii=False),
                       "i": tagged["pid"]})
            s.commit()
        out = _call(property_id=tagged["pid"], tag="采光差")
        assert out["defect_tags"] == ["临街吵"], out
        assert json.loads(_tags(wired, tagged["pid"])) == {"临街吵": 3}


# ==================== ② 整改基线：清完不被打回 ====================
class TestBaseline:
    def test_not_brought_back_by_new_feedback(self, wired, tagged):
        """2026-09-25 点名要治的：房东整改 + 手动清除后，下一客户再看房不该把标签打回来"""
        _call(property_id=tagged["pid"], tag="采光差")
        _feedback(wired, tagged["cid3"], tagged["pid"], "房东整改后这次看采光还行", day=5,
                  result="interested")
        assert _tags(wired, tagged["pid"]) is None, _tags(wired, tagged["pid"])

    def test_new_problem_after_baseline_can_tag_again(self, wired, tagged):
        """整改之后**新**的反馈仍按 ≥2 位不同客户判：真出新问题照样标"""
        _call(property_id=tagged["pid"], tag="采光差")
        _feedback(wired, tagged["cid1"], tagged["pid"], "墙角有水印，漏水", day=6)
        _feedback(wired, tagged["cid2"], tagged["pid"], "卫生间漏水", day=7)
        assert "漏水" in (_tags(wired, tagged["pid"]) or ""), _tags(wired, tagged["pid"])

    def test_old_feedback_single_customer_after_baseline_not_enough(self, wired, tagged):
        """整改后只有一位客户再提同一个问题 → 仍不够阈值"""
        _call(property_id=tagged["pid"], tag="采光差")
        _feedback(wired, tagged["cid3"], tagged["pid"], "还是采光差", day=8)
        assert _tags(wired, tagged["pid"]) is None, _tags(wired, tagged["pid"])


# ==================== ③ 边界与异常 ====================
class TestEdges:
    def test_unknown_property(self, wired):
        out = _call(property_id=999999, tag="采光差")
        assert out["success"] is not True and "房源不存在" in out["error"], out

    def test_tag_not_present(self, wired, tagged):
        out = _call(property_id=tagged["pid"], tag="漏水")
        assert out["success"] is not True, out
        assert out["error"] == "这套房源没有「漏水」这个标签", out["error"]
        assert out["defect_tags"] == ["采光差"], out          # 顺手把现有标签给出来核对

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_empty_tag(self, wired, tagged, bad):
        out = _call(property_id=tagged["pid"], tag=bad)
        assert out["success"] is not True, out
        assert "标签不能为空" in out["error"] and "采光差" in out["error"], out["error"]

    def test_tag_is_stripped(self, wired, tagged):
        """标签名多打空格也要能清掉"""
        out = _call(property_id=tagged["pid"], tag="  采光差 ")
        assert out["success"] is True, out
        assert _tags(wired, tagged["pid"]) is None

    def test_bad_id_forms(self, wired, tagged):
        for bad in ("abc", True):
            out = _call(property_id=bad, tag="采光差")
            assert out["success"] is not True, out
            assert "房源编号" in out["error"] and "数字" in out["error"], out["error"]

    def test_dirty_tags_json_is_reported(self, wired, tagged):
        with wired.get_session() as s:
            s.execute(text("UPDATE re_properties SET defect_tags = :t WHERE id = :i"),
                      {"t": '{"坏了', "i": tagged["pid"]})
            s.commit()
        out = _call(property_id=tagged["pid"], tag="采光差")
        assert out["success"] is not True and "没有" in out["error"], out

    def test_repeat_clear_is_readable(self, wired, tagged):
        assert _call(property_id=tagged["pid"], tag="采光差")["success"] is True
        again = _call(property_id=tagged["pid"], tag="采光差")
        assert again["success"] is not True and "没有「采光差」" in again["error"], again


# ==================== ④ 重扫：SQL 聚合后的口径与代价 ====================
class TestRescan:
    def test_same_customer_twice_not_enough(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        _feedback(wired, cid, pid, "采光差", day=1)
        _feedback(wired, cid, pid, "还是采光差", day=2)
        assert _tags(wired, pid) is None, _tags(wired, pid)

    def test_two_customers_same_problem_tagged(self, wired):
        cid1, cid2 = _customer(wired, "甲", "13800001111"), _customer(wired, "乙", "13800002222")
        pid = _property(wired)
        _feedback(wired, cid1, pid, "采光差", day=1)
        _feedback(wired, cid2, pid, "采光不好", day=2)
        assert json.loads(_tags(wired, pid)) == {"采光差": 2}, _tags(wired, pid)

    def test_multiple_tags_counted_separately(self, wired):
        cid1, cid2, cid3 = (_customer(wired, "甲", "13800001111"),
                            _customer(wired, "乙", "13800002222"),
                            _customer(wired, "丙", "13800003333"))
        pid = _property(wired)
        _feedback(wired, cid1, pid, "采光差，临街很吵", day=1)
        _feedback(wired, cid2, pid, "又暗又吵，楼下漏水", day=2)
        _feedback(wired, cid3, pid, "卫生间漏水", day=3)
        tags = json.loads(_tags(wired, pid))
        assert tags["采光差"] == 2 and tags["临街吵"] == 2 and tags["漏水"] == 2, tags

    def test_query_count_is_constant(self, wired, tagged):
        """重扫改成 SQL 聚合：1 次聚合 + 取房源，不再是 1（取全表）+ N 行懒加载"""
        cid = _customer(wired, "批量", "13800009999")
        for i in range(20):
            _feedback(wired, cid, tagged["pid"], f"采光差第{i}次", day=10 + i)
        counter = {"n": 0}

        @event.listens_for(wired.engine, "before_cursor_execute")
        def _count(conn, cursor, statement, params, context, executemany):
            counter["n"] += 1

        try:
            wired.refresh_defect_tags(tagged["pid"])
        finally:
            event.remove(wired.engine, "before_cursor_execute", _count)
        assert counter["n"] <= 4, f"查询次数 {counter['n']}（疑似逐行扫反馈的 N+1 回来了）"

    def test_description_states_baseline(self):
        from tools.registry import registry

        desc = registry.get_entry("clear_defect_tag").schema["description"]
        assert len(desc) >= 60, desc
        for word in ("整改时间", "不会再把这个标签打回来", "2 位不同客户"):
            assert word in desc, (word, desc)
