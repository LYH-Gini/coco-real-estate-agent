"""核心工具回归：跟进/逾期、带看、成交五阶段、话术库、生日（2026-09-18 补测）"""
import json
from datetime import datetime, timedelta, timezone
import zoneinfo

from conftest import make_customer, make_property

_CN = zoneinfo.ZoneInfo("Asia/Shanghai")


def _dispatch(db, monkeypatch, name, args):
    import tools.real_estate_followup  # noqa: F401
    import tools.real_estate_viewing  # noqa: F401
    import tools.real_estate_deal  # noqa: F401
    import tools.real_estate_scripts  # noqa: F401
    import tools.real_estate_communication  # noqa: F401
    import tools.real_estate_birthday  # noqa: F401
    import tools.real_estate_property  # noqa: F401
    import tools.real_estate_customer  # noqa: F401
    from tools.registry import registry
    import tools.real_estate_followup as tf
    import tools.real_estate_viewing as tv
    import tools.real_estate_deal as td
    import tools.real_estate_scripts as ts
    import tools.real_estate_birthday as tb
    import tools.real_estate_property as tp
    import tools.real_estate_customer as tc
    for m in (tf, tv, td, ts, tb, tp, tc):
        monkeypatch.setattr(m, "_get_db", lambda: db)
    return json.loads(registry.dispatch(name, args))


class TestFollowups:
    def test_add_and_list(self, db, monkeypatch):
        c = make_customer(db, name="跟进客户")
        out = _dispatch(db, monkeypatch, "add_followup",
                        {"customer_id": c["id"], "content": "电话沟通", "type": "call"})
        assert out["success"] is True
        got = _dispatch(db, monkeypatch, "get_followups", {"customer_id": c["id"]})
        assert got["success"] is True and len(got["followups"]) == 1

    def test_overdue_disappears_after_new_followup(self, db, monkeypatch):
        """逾期判定按"该客户最新一条跟进"：新跟进应消解旧提醒（2026-08-12 修复的行为）"""
        c = make_customer(db, name="逾期客户")
        old = (datetime.now(_CN) - timedelta(days=3)).strftime("%Y-%m-%d")
        _dispatch(db, monkeypatch, "add_followup",
                  {"customer_id": c["id"], "content": "旧跟进", "next_date": old})
        before = _dispatch(db, monkeypatch, "get_overdue", {})
        assert any(o["customer_id"] == c["id"] for o in before["overdue"]), before
        future = (datetime.now(_CN) + timedelta(days=5)).strftime("%Y-%m-%d")
        _dispatch(db, monkeypatch, "add_followup",
                  {"customer_id": c["id"], "content": "新跟进", "next_date": future})
        after = _dispatch(db, monkeypatch, "get_overdue", {})
        assert all(o["customer_id"] != c["id"] for o in after["overdue"]), "新跟进后不该再报逾期"

    def test_midday_and_daily_report_run(self, db, monkeypatch):
        make_customer(db, name="报表客户", tier="S")
        assert _dispatch(db, monkeypatch, "midday_check", {})["success"] is True
        assert _dispatch(db, monkeypatch, "daily_report", {})["success"] is True
        assert _dispatch(db, monkeypatch, "stale_check", {})["success"] is True


class TestViewing:
    def test_schedule_record_and_stats(self, db, monkeypatch):
        c = make_customer(db, name="带看客户")
        p = make_property(db, title="带看房源")
        sched = _dispatch(db, monkeypatch, "schedule_viewing",
                          {"customer_id": c["id"], "property_id": p["id"], "viewing_time": "2026-09-20 14:00"})
        assert sched["success"] is True
        vid = sched["viewing"]["id"]
        rec = _dispatch(db, monkeypatch, "record_viewing",
                        {"viewing_id": vid, "status": "done", "result": "interested", "feedback": "客户觉得采光好"})
        assert rec["success"] is True
        assert _dispatch(db, monkeypatch, "get_viewing", {"viewing_id": vid})["success"] is True
        assert _dispatch(db, monkeypatch, "list_viewings", {})["success"] is True
        assert _dispatch(db, monkeypatch, "viewing_stats", {})["success"] is True

    def test_negative_feedback_marks_defect_after_two_customers(self, db, monkeypatch):
        """带看负反馈反哺缺陷标签：同一缺陷被 2 位客户提及才打标（功能设计阈值）"""
        p = make_property(db, title="缺陷房源")
        for i in (1, 2):
            c = make_customer(db, name=f"差评客户{i}")
            sched = _dispatch(db, monkeypatch, "schedule_viewing",
                              {"customer_id": c["id"], "property_id": p["id"],
                               "viewing_time": "2026-09-20 14:00"})
            out = _dispatch(db, monkeypatch, "record_viewing",
                            {"viewing_id": sched["viewing"]["id"], "status": "done",
                             "result": "not_interested", "feedback": "采光太差了"})
            assert out.get("success") is True, out
        got = db.get_available_property(p["id"])
        assert got["defect_tags"], "两位客户都提到采光差，应写入缺陷标签"


class TestDealFlow:
    def test_stages_and_property_taken_off_market(self, db, monkeypatch):
        c = make_customer(db, name="成交客户")
        p = make_property(db, title="成交房源", price=1_000_000, area=90.0)
        start = _dispatch(db, monkeypatch, "start_deal",
                          {"customer_id": c["id"], "property_id": p["id"], "price": 990_000})
        assert start["success"] is True
        did = start["deal"]["id"]
        for stage in ("signing", "loan", "transfer", "finalized"):
            out = _dispatch(db, monkeypatch, "advance_deal", {"deal_id": did, "stage": stage})
            assert out["success"] is True, out
        assert _dispatch(db, monkeypatch, "get_deal", {"deal_id": did})["deal"]["stage"] == "finalized"
        assert _dispatch(db, monkeypatch, "list_deals", {})["success"] is True
        assert _dispatch(db, monkeypatch, "deal_stats", {})["success"] is True
        assert db.get_available_property(p["id"]) is None, "成交后房源应下架（不在售）"


class TestScriptsAndTemplates:
    def test_save_get_list_delete(self, db, monkeypatch):
        s = _dispatch(db, monkeypatch, "save_script",
                      {"name": "议价话术", "content": "理解您的预算考虑", "scenario": "objection_handling"})
        assert s["success"] is True
        assert _dispatch(db, monkeypatch, "get_script_by_name", {"name": "议价话术"})["success"] is True
        assert _dispatch(db, monkeypatch, "list_scripts", {})["success"] is True
        assert _dispatch(db, monkeypatch, "delete_script", {"script_id": s["script"]["id"]})["success"] is True

    def test_use_template_renders_variables(self, db, monkeypatch):
        out = _dispatch(db, monkeypatch, "use_template",
                        {"template_name": "property_recommend",
                         "variables": {"community": "测试小区", "price": "128", "rooms": 3,
                                       "halls": 2, "area": "89.5", "highlights": "南北通透"}})
        assert out.get("success") is True, out
        body = json.dumps(out, ensure_ascii=False)
        assert "测试小区" in body and "89.5" in body, out


class TestBirthday:
    def test_update_and_check(self, db, monkeypatch):
        c = make_customer(db, name="生日客户")
        today = datetime.now(_CN).strftime("%m-%d")
        assert _dispatch(db, monkeypatch, "update_birthday",
                         {"customer_id": c["id"], "birthday": f"1990-{today}"})["success"] is True
        out = _dispatch(db, monkeypatch, "birthday_check", {})
        assert out["success"] is True
        # 用与工具同一时区的"今天"；跨午夜时可能被算成明天 → 两边都接受
        hits = (out.get("today_birthdays") or []) + (out.get("tomorrow_birthdays") or [])
        assert any(item.get("name") == "生日客户" for item in hits), out
