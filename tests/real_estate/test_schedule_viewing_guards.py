"""schedule_viewing 回归（2026-09-25 第五组「带看」第 43 项，F180–F185）

背景（实测，见 /root/coco-tool-audit/results/raw/t45.before.final.log）：
① 带看时间只认 `YYYY-MM-DD HH:MM` 一种写法：`明天 10:00`、`周三 10:00`、`3天后 10:00`、
   `2026/10/25 10:00`、`2026年10月25日 10:00`、`2026-10-25 9点30` 全被拒，提示还把
   代码写法 `YYYY-MM-DD HH:MM` 说给经纪人听；
② **房源编号不存在照样建带看**（客户侧有校验、房源侧漏了）→ 造孤儿记录，回执写「房源: None」；
③ 回执没有带看编号（经纪人无法把结果记回这条），只给日期时也不说"按 10:00 记"；
④ 过去的时间静默接受；⑤ 同一客户+房源+时间重复预约 → 落两条一样的记录；
⑥ 房源已售/已租、客户已关闭，预约时没有任何提示。

本文件钉住修好之后的行为。
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_viewing as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


@pytest.fixture
def cid(wired):
    return wired.add_customer(name="带看客户", phone="13800001111", tier="A",
                              customer_type="buy_second_hand")["id"]


@pytest.fixture
def pid(wired):
    return wired.add_property(title="带看房源 1号楼101", price=1500000, area=80,
                              property_type="second_hand", status="available")["id"]


def _call(**kwargs):
    import tools.real_estate_viewing as m

    return json.loads(m.schedule_viewing(**kwargs))


def _viewings(db):
    with db.get_session() as s:
        return s.execute(text("SELECT id, customer_id, property_id, viewing_time, status"
                              " FROM re_viewings ORDER BY id")).fetchall()


SOON = (datetime.now() + timedelta(days=20)).strftime("%Y-%m-%d")


# ==================== ① 时间归一 ====================
class TestWhenNormalization:
    @pytest.mark.parametrize("raw", [
        SOON + " 10:00",
        SOON.replace("-", "/") + " 10:00",
        SOON.replace("-", ".") + " 10:00",
        SOON + "T10:00",
        SOON + " 10:00:00",
    ])
    def test_absolute_forms_land(self, wired, cid, pid, raw):
        out = _call(customer_id=cid, property_id=pid, viewing_time=raw)
        assert out["success"] is True, out
        assert out["viewing"]["viewing_time"].startswith(SOON), out["viewing"]

    def test_chinese_date_lands(self, wired, cid, pid):
        out = _call(customer_id=cid, property_id=pid, viewing_time="2027年3月5日 14:30")
        assert out["success"] is True, out
        assert out["viewing"]["viewing_time"].startswith("2027-03-05T14:30")

    def test_chinese_time_lands(self, wired, cid, pid):
        out = _call(customer_id=cid, property_id=pid, viewing_time=SOON + " 9点30")
        assert out["success"] is True, out
        assert out["viewing"]["viewing_time"].endswith("09:30:00")

    @pytest.mark.parametrize("raw,days", [("明天 10:00", 1), ("后天 09:30", 2), ("3天后 14:00", 3)])
    def test_relative_days_land(self, wired, cid, pid, raw, days):
        out = _call(customer_id=cid, property_id=pid, viewing_time=raw)
        assert out["success"] is True, out
        got = datetime.fromisoformat(out["viewing"]["viewing_time"])
        expect = (datetime.now() + timedelta(days=days)).date()
        assert got.date() == expect, (got, expect)

    def test_weekday_lands(self, wired, cid, pid):
        out = _call(customer_id=cid, property_id=pid, viewing_time="周三 10:00")
        assert out["success"] is True, out
        got = datetime.fromisoformat(out["viewing"]["viewing_time"])
        assert got.weekday() == 2 and got.date() >= datetime.now().date()

    def test_date_only_means_ten_and_says_so(self, wired, cid, pid):
        """只给日期 → 按 10:00 落库，且回执必须说明这是默认值"""
        out = _call(customer_id=cid, property_id=pid, viewing_time=SOON)
        assert out["success"] is True, out
        assert out["viewing"]["viewing_time"].endswith("T10:00:00"), out["viewing"]
        assert "10:00" in out["message"] and "没说具体时间" in out["message"], out["message"]

    def test_given_time_has_no_default_note(self, wired, cid, pid):
        out = _call(customer_id=cid, property_id=pid, viewing_time=SOON + " 15:00")
        assert "没说具体时间" not in out["message"], out["message"]

    @pytest.mark.parametrize("raw", ["随便写的", "", "   ", None, "2026-13-45 10:00",
                                     SOON + " 25:99", "2026年13月45日 10:00"])
    def test_unreadable_time_gives_chinese_hint(self, wired, cid, pid, raw):
        out = _call(customer_id=cid, property_id=pid, viewing_time=raw)
        assert out["success"] is not True, out
        err = out["error"]
        assert "带看时间" in err and "没能识别" in err or "不能为空" in err, err
        # 不许把代码写法说给经纪人听
        assert "YYYY" not in err and "%Y" not in err, err
        assert _viewings(wired) == []

    def test_unreadable_time_quotes_whole_input(self, wired, cid, pid):
        """日期+时刻挤在一个参数里，认不出时引的是经纪人原话，不是拆出来的半截"""
        out = _call(customer_id=cid, property_id=pid, viewing_time="2026年13月45日 10:00")
        assert "2026年13月45日 10:00" in out["error"], out["error"]


# ==================== ② 认人认房（不许造孤儿） ====================
class TestExistenceGuards:
    def test_unknown_customer_is_reported(self, wired, pid):
        out = _call(customer_id=999999, property_id=pid, viewing_time=SOON + " 10:00")
        assert out["success"] is not True and "客户不存在" in out["error"], out
        assert _viewings(wired) == []

    def test_unknown_property_is_reported_and_not_written(self, wired, cid):
        """实测缺陷：房源不存在也照建，回执还写「房源: None」"""
        out = _call(customer_id=cid, property_id=999999, viewing_time=SOON + " 10:00")
        assert out["success"] is not True and "房源不存在" in out["error"], out
        assert _viewings(wired) == []

    @pytest.mark.parametrize("bad", ["abc", True])
    def test_bad_id_forms_give_number_hint(self, wired, cid, pid, bad):
        out = _call(customer_id=bad, property_id=pid, viewing_time=SOON + " 10:00")
        assert out["success"] is not True, out
        assert "客户编号" in out["error"] and "数字" in out["error"], out["error"]
        out = _call(customer_id=cid, property_id=bad, viewing_time=SOON + " 10:00")
        assert out["success"] is not True, out
        assert "房源编号" in out["error"] and "数字" in out["error"], out["error"]
        assert _viewings(wired) == []


# ==================== ③ 回执可对账 ====================
class TestReceipt:
    def test_receipt_carries_viewing_id(self, wired, cid, pid):
        out = _call(customer_id=cid, property_id=pid, viewing_time=SOON + " 10:00")
        assert f"带看编号 {out['viewing']['id']}" in out["message"], out["message"]
        assert "带看客户" in out["message"] and "带看房源" in out["message"], out["message"]

    def test_receipt_has_no_parameter_names(self, wired, cid, pid):
        out = _call(customer_id=cid, property_id=pid, viewing_time=SOON + " 10:00")
        blob = json.dumps(out, ensure_ascii=False)
        for word in ("customer_id", "property_id", "viewing_time", "schedule_viewing", "success"):
            assert word not in out["message"], (word, out["message"])
        assert "record_viewing" not in blob, blob

    def test_read_back_matches(self, wired, cid, pid):
        out = _call(customer_id=cid, property_id=pid, viewing_time=SOON + " 10:00")
        vid = out["viewing"]["id"]
        with wired.get_session() as s:
            row = s.execute(text("SELECT customer_id, property_id, status FROM re_viewings"
                                 " WHERE id = :i"), {"i": vid}).fetchone()
        assert row[0] == cid and row[1] == pid and row[2] == "scheduled", row


# ==================== ④ 不重复登记 ====================
class TestDuplicateSchedule:
    def test_same_slot_is_not_written_twice(self, wired, cid, pid):
        first = _call(customer_id=cid, property_id=pid, viewing_time=SOON + " 10:00")
        second = _call(customer_id=cid, property_id=pid, viewing_time=SOON + " 10:00")
        assert second["success"] is True and second["already_scheduled"] is True, second
        assert second["viewing"]["id"] == first["viewing"]["id"], second
        assert len(_viewings(wired)) == 1, _viewings(wired)
        assert f"带看编号 {first['viewing']['id']}" in second["message"], second["message"]

    def test_different_time_still_lands(self, wired, cid, pid):
        _call(customer_id=cid, property_id=pid, viewing_time=SOON + " 10:00")
        out = _call(customer_id=cid, property_id=pid, viewing_time=SOON + " 16:00")
        assert out["success"] is True, out
        assert len(_viewings(wired)) == 2, _viewings(wired)

    def test_cancelled_slot_can_be_rebooked(self, wired, cid, pid):
        first = _call(customer_id=cid, property_id=pid, viewing_time=SOON + " 10:00")
        wired.update_viewing(first["viewing"]["id"], status="cancelled")
        out = _call(customer_id=cid, property_id=pid, viewing_time=SOON + " 10:00")
        assert out["success"] is True and not out.get("already_scheduled"), out
        assert len(_viewings(wired)) == 2, _viewings(wired)


# ==================== ⑤ 只提醒不拦 ====================
class TestWarnings:
    def test_past_time_warns_but_lands(self, wired, cid, pid):
        past = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d 10:00")
        out = _call(customer_id=cid, property_id=pid, viewing_time=past)
        assert out["success"] is True, out
        assert any("已经过去" in w for w in out["warnings"]), out.get("warnings")
        assert len(_viewings(wired)) == 1

    @pytest.mark.parametrize("status,label", [("sold", "已售"), ("rented", "已租")])
    def test_unavailable_property_warns(self, wired, cid, status, label):
        pid = wired.add_property(title="已成交房源", price=1500000, area=80,
                                 property_type="second_hand", status=status)["id"]
        out = _call(customer_id=cid, property_id=pid, viewing_time=SOON + " 10:00")
        assert out["success"] is True, out
        assert any(label in w and "确认一下" in w for w in out["warnings"]), out.get("warnings")

    def test_closed_customer_warns(self, wired, pid):
        cid = wired.add_customer(name="已关闭客户", phone="13800002222", status="closed")["id"]
        out = _call(customer_id=cid, property_id=pid, viewing_time=SOON + " 10:00")
        assert out["success"] is True, out
        assert any("已关闭" in w for w in out["warnings"]), out.get("warnings")

    def test_available_property_has_no_warning(self, wired, cid, pid):
        out = _call(customer_id=cid, property_id=pid, viewing_time=SOON + " 10:00")
        assert not out.get("warnings"), out

    def test_warnings_wording_has_no_internals(self, wired, cid, pid):
        past = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d 10:00")
        out = _call(customer_id=cid, property_id=pid, viewing_time=past)
        blob = " ".join(out.get("warnings") or [])
        for word in ("status", "sold", "rented", "closed", "customer_id", "property_id"):
            assert word not in blob, (word, blob)


# ==================== ⑥ 描述给模型的能力说明 ====================
class TestSchema:
    def test_description_states_time_forms_and_receipt(self):
        from tools.registry import registry

        schema = registry.get_entry("schedule_viewing").schema
        desc = schema["description"]
        assert "明天" in desc and "带看编号" in desc, desc
        assert "2026-12-31 10:00" in schema["parameters"]["properties"]["viewing_time"]["description"]
