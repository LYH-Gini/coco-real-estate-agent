"""record_viewing 回归（2026-09-25 第五组「带看」第 44 项，F186–F191）

背景（实测，见 /root/coco-tool-audit/results/raw/t46.before3.log）：
① 同一条带看重复记「完成」会重复生成回访提醒（done 两次 → 2 条一模一样的「带看后回访」）；
② 自动生成的回访提醒 `property_id` 是空的（打开提醒看不到是哪套房之后的回访）；
③ `status`/`result` 乱值直出英文枚举（`status 必须是 scheduled/done/cancelled`），
   而经纪人说的 `已完成`/`已取消`/`感兴趣` 一律被拒 —— 与「记跟进」认中文类型的口径不一致；
④ 什么都不给（或只给空串）也回 `success: true`、没有回执，库里一行没动；只补一句反馈时也没有回执；
⑤ `feedback=''` 落库成空字符串（"未填"有两种形态并存）；
⑥ 把已完成的带看改回「待带看」静默覆盖，一声不吭。

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
    return wired.add_customer(name="带看结果客户", phone="13800001111", tier="A",
                              customer_type="buy_second_hand")["id"]


@pytest.fixture
def pid(wired):
    return wired.add_property(title="带看结果房源 1号楼101", price=1500000, area=80,
                              property_type="second_hand", status="available")["id"]


def _schedule(cid, pid, days=3):
    import tools.real_estate_viewing as m

    when = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d 10:00")
    out = json.loads(m.schedule_viewing(customer_id=cid, property_id=pid, viewing_time=when))
    assert out["success"] is True, out
    return out["viewing"]["id"]


def _record(**kwargs):
    import tools.real_estate_viewing as m

    return json.loads(m.record_viewing(**kwargs))


def _reminders(db, viewing_id=None, customer_id=None, followup_type="reminder"):
    sql = ("SELECT id, customer_id, property_id, type, content, next_date, next_time,"
           " source_viewing_id FROM re_followups")
    params, where = {}, []
    if viewing_id is not None:
        where.append("source_viewing_id = :v")
        params["v"] = viewing_id
    if customer_id is not None:
        where.append("customer_id = :c")
        params["c"] = customer_id
    if followup_type:
        where.append("type = :t")
        params["t"] = followup_type
    if where:
        sql += " WHERE " + " AND ".join(where)
    with db.get_session() as s:
        return s.execute(text(sql + " ORDER BY id"), params).fetchall()


def _row(db, viewing_id):
    with db.get_session() as s:
        return s.execute(text("SELECT status, result, feedback FROM re_viewings WHERE id = :i"),
                         {"i": viewing_id}).fetchone()


# ==================== ① 状态/意向归一 ====================
class TestEnumNormalization:
    @pytest.mark.parametrize("raw,expect", [
        ("已完成", "done"), ("已看", "done"), ("看完了", "done"), ("看过了", "done"),
        ("done", "done"), ("DONE", "done"),
        ("已取消", "cancelled"), ("取消", "cancelled"), ("没去", "cancelled"),
        ("待带看", "scheduled"), ("待看", "scheduled"), ("还没看", "scheduled"),
    ])
    def test_status_chinese_and_english_land(self, wired, cid, pid, raw, expect):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status=raw)
        assert out["success"] is True, out
        assert _row(wired, vid)[0] == expect, (_row(wired, vid), out)

    @pytest.mark.parametrize("raw,expect", [
        ("感兴趣", "interested"), ("有意向", "interested"), ("满意", "interested"),
        ("不感兴趣", "not_interested"), ("没兴趣", "not_interested"),
        ("再考虑", "pending"), ("再看看", "pending"), ("待定", "pending"),
        ("interested", "interested"), ("PENDING", "pending"),
    ])
    def test_result_chinese_and_english_land(self, wired, cid, pid, raw, expect):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, result=raw)
        assert out["success"] is True, out
        assert _row(wired, vid)[1] == expect, (_row(wired, vid), out)

    def test_status_hint_is_chinese_first(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="随便写的")
        err = out["error"]
        assert out["success"] is not True, out
        assert "带看状态没能识别" in err and "已完成" in err, err
        assert err.index("已完成") < err.index("done"), err      # 中文在前、英文值只在括号里对照

    def test_result_hint_is_chinese_first(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, result="随便写的")
        err = out["error"]
        assert out["success"] is not True, out
        assert "客户意向没能识别" in err and "感兴趣" in err, err
        assert err.index("感兴趣") < err.index("interested"), err

    def test_bad_enum_does_not_write(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status="随便写的")
        assert _row(wired, vid)[0] == "scheduled", _row(wired, vid)


# ==================== ② 空调用要有提示 ====================
class TestEmptyCall:
    @pytest.mark.parametrize("args", [{}, {"status": ""}, {"feedback": ""}, {"status": "", "result": ""}])
    def test_nothing_to_record_is_reported(self, wired, cid, pid, args):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, **args)
        assert out["success"] is not True, out
        assert "没说记什么" in out["error"], out

    def test_empty_feedback_is_not_stored(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, feedback="")
        assert _row(wired, vid)[2] is None, _row(wired, vid)
        with wired.get_session() as s:
            n = s.execute(text("SELECT count(*) FROM re_viewings WHERE feedback = ''")).scalar()
        assert n == 0, n


# ==================== ③ 回执 ====================
class TestReceipt:
    def test_status_and_result_receipt(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已完成", result="感兴趣")
        assert out["message"].startswith(f"带看记录：已完成、客户感兴趣（带看编号 {vid}）"), out["message"]

    def test_feedback_only_receipt(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, feedback="客户觉得楼层太高")
        assert f"已记下客户反馈（带看编号 {vid}）" in out["message"], out["message"]

    def test_result_only_receipt(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, result="不感兴趣")
        assert "带看记录：客户不感兴趣" in out["message"], out["message"]

    def test_receipt_has_no_parameter_names(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已完成", result="感兴趣", feedback="还行")
        for word in ("status", "result", "feedback", "viewing_id", "record_viewing"):
            assert word not in out["message"], (word, out["message"])


# ==================== ④ 回访提醒：一条带看只留一条 ====================
class TestReminderIdempotent:
    def test_done_twice_creates_one_reminder(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        first = _record(viewing_id=vid, status="已完成", result="感兴趣")
        assert first["success"] is True and first.get("reminder"), first
        again = _record(viewing_id=vid, status="已完成", result="感兴趣")
        rows = _reminders(wired, viewing_id=vid)
        assert len(rows) == 1, rows
        assert "回访提醒已经在" in again["message"], again["message"]
        assert again["reminder"]["id"] == first["reminder"]["id"], again

    def test_reminder_links_property_and_customer(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status="已完成")
        row = _reminders(wired, viewing_id=vid)[0]
        assert row[1] == cid and row[2] == pid, row            # customer_id / property_id
        assert row[3] == "reminder", row
        assert "带看后回访" in row[4], row

    def test_reminder_time_is_about_one_hour_later(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status="已完成")
        row = _reminders(wired, viewing_id=vid)[0]
        when = datetime.fromisoformat(str(row[5]).replace(" ", "T")[:19])
        assert abs((when - (datetime.now() + timedelta(hours=1))).total_seconds()) < 120, row

    def test_two_viewings_each_get_own_reminder(self, wired, cid, pid):
        v1 = _schedule(cid, pid, days=3)
        v2 = _schedule(cid, pid, days=5)
        _record(viewing_id=v1, status="已完成")
        _record(viewing_id=v2, status="已完成")
        assert len(_reminders(wired, viewing_id=v1)) == 1
        assert len(_reminders(wired, viewing_id=v2)) == 1
        assert _reminders(wired, viewing_id=v1)[0][0] != _reminders(wired, viewing_id=v2)[0][0]

    def test_no_reminder_when_not_done(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已取消")
        assert _reminders(wired, viewing_id=vid) == [], out


# ==================== ⑤ 状态回退只提醒不拦 ====================
class TestStatusRollback:
    def test_done_back_to_scheduled_warns(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status="已完成")
        out = _record(viewing_id=vid, status="待带看")
        assert out["success"] is True, out
        assert any("原来记的是已完成" in w for w in out["warnings"]), out.get("warnings")
        assert _row(wired, vid)[0] == "scheduled", _row(wired, vid)

    def test_forward_change_has_no_warning(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已完成")
        assert not out.get("warnings"), out

    def test_warning_wording_has_no_internals(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status="已完成")
        out = _record(viewing_id=vid, status="已取消")
        blob = " ".join(out.get("warnings") or [])
        for word in ("status", "scheduled", "done", "cancelled", "viewing_id"):
            assert word not in blob, (word, blob)


# ==================== ⑥ 编号形态与不存在 ====================
class TestIdForms:
    @pytest.mark.parametrize("bad", ["abc", True])
    def test_bad_viewing_id(self, wired, bad):
        out = _record(viewing_id=bad, status="已完成")
        assert out["success"] is not True, out
        assert "带看编号" in out["error"] and "数字" in out["error"], out["error"]

    def test_unknown_viewing(self, wired):
        out = _record(viewing_id=999999, status="已完成")
        assert out["success"] is not True and "带看记录不存在" in out["error"], out

    def test_unknown_viewing_does_not_write_reminder(self, wired, cid):
        _record(viewing_id=999999, status="已完成")
        assert _reminders(wired, customer_id=cid) == []


# ==================== ⑧ 兜底失败要如实说（不许静默吞异常） ====================
class TestFailureIsReported:
    def _boom(self, *a, **kw):
        raise RuntimeError("boom")

    def test_reminder_failure_is_reported(self, wired, cid, pid, monkeypatch):
        import tools.real_estate_viewing as m

        monkeypatch.setattr(wired, "add_followup", self._boom)
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已完成", result="感兴趣")
        assert out["success"] is True, out                      # 带看结果本身照旧记下
        assert _row(wired, vid)[0] == "done", _row(wired, vid)
        assert any("回访提醒这次没建起来" in w for w in out["warnings"]), out.get("warnings")
        assert out.get("reminder_error") == "RuntimeError", out
        assert not out.get("reminder"), out

    def test_defect_rescan_failure_is_reported(self, wired, cid, pid, monkeypatch):
        import tools.real_estate_viewing as m

        monkeypatch.setattr(wired, "refresh_defect_tags", self._boom)
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已完成", feedback="采光差")
        assert out["success"] is True, out
        assert out["viewing"]["feedback"] == "采光差", out
        assert any("缺陷标签这次没能重新统计" in w for w in out["warnings"]), out.get("warnings")
        assert out.get("defect_rescan_error") == "RuntimeError", out

    def test_warning_wording_has_no_internals(self, wired, cid, pid, monkeypatch):
        monkeypatch.setattr(wired, "add_followup", self._boom)
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已完成")
        blob = " ".join(out.get("warnings") or [])
        for word in ("RuntimeError", "boom", "Traceback", "add_followup", "except"):
            assert word not in blob, (word, blob)


# ==================== ⑦ 读回口径 ====================
class TestReadBack:
    def test_get_viewing_matches(self, wired, cid, pid):
        import tools.real_estate_viewing as m

        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status="已完成", result="感兴趣", feedback="户型还行")
        gv = json.loads(m.get_viewing(viewing_id=vid))["viewing"]
        assert gv["status"] == "done" and gv["result"] == "interested", gv
        assert gv["feedback"] == "户型还行", gv

    def test_schema_description_states_ability(self):
        from tools.registry import registry

        schema = registry.get_entry("record_viewing").schema
        desc = schema["description"]
        assert "已完成" in desc and "回访提醒" in desc, desc
        props = schema["parameters"]["properties"]
        assert "已完成" in props["status"]["description"], props["status"]
        assert "感兴趣" in props["result"]["description"], props["result"]
