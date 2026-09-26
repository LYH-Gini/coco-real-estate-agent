"""list_viewings 回归（2026-09-25 第五组「带看」第 46 项，F196–F199）

背景（实测，见 /root/coco-tool-audit/results/raw/t49.before.log）：
① 列表每条只有内部值（`status="scheduled"` / `result="interested"` / ISO 时间），比详情差一档 ——
   同一实体两条出口口径不一致；
② 只有 `count`，缺 `total`/`truncated`：`limit=1` 时不说一共有几条（Coco 会说"这位客户有 1 条带看"）；
③ 状态筛选：`status='已完成'`（中文）筛不到任何东西，`status='乱写的状态'` 静默返回空列表；
④ "客户不存在"、"房源不存在"、"客户存在但没带看"**全都回同一个空列表**，连一句话都没有；
⑤ 列表不显示回访提醒（详情已有）；
⑥ 描述 19 字。

本文件钉住修好之后的行为（含"列表显示提醒只查一次"的查询次数钉子）。
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


def _customer(db, name="列表客户", phone="13800001111"):
    return db.add_customer(name=name, phone=phone, tier="A",
                           customer_type="buy_second_hand")["id"]


def _property(db, title="列表房源 1号楼101"):
    return db.add_property(title=title, price=1500000, area=80,
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


def _call(**kwargs):
    import tools.real_estate_viewing as m

    return json.loads(m.list_viewings(**kwargs))


@pytest.fixture
def fixture_ids(wired):
    cid = _customer(wired)
    other = _customer(wired, name="没带看客户", phone="13800002222")
    pid = _property(wired)
    v1 = _schedule(cid, pid, days=1)
    v2 = _schedule(cid, pid, days=3)
    _record(viewing_id=v1, status="已完成", result="感兴趣")
    return {"cid": cid, "other": other, "pid": pid, "v1": v1, "v2": v2}


# ==================== ① 展示口径（复用详情那一套） ====================
class TestDisplay:
    def test_labels_and_readable_time(self, wired, fixture_ids):
        out = _call(customer_id=fixture_ids["cid"])
        done = [x for x in out["viewings"] if x["id"] == fixture_ids["v1"]][0]
        assert done["status_label"] == "已完成" and done["result_label"] == "感兴趣", done
        assert done["viewing_time_label"] and "T" not in done["viewing_time_label"], done
        plan = [x for x in out["viewings"] if x["id"] == fixture_ids["v2"]][0]
        assert plan["status_label"] == "待带看" and not plan["result_label"], plan

    def test_same_viewing_same_labels_as_detail(self, wired, fixture_ids):
        import tools.real_estate_viewing as m

        listed = [x for x in _call(customer_id=fixture_ids["cid"])["viewings"]
                  if x["id"] == fixture_ids["v1"]][0]
        detail = json.loads(m.get_viewing(viewing_id=fixture_ids["v1"]))["viewing"]
        for key in ("status", "status_label", "result", "result_label", "viewing_time_label"):
            assert listed[key] == detail[key], (key, listed.get(key), detail.get(key))

    def test_orphan_labels_in_list(self, wired, fixture_ids):
        """客户被外部删掉（防御）时列表也给可读标注，不回 null"""
        with wired.get_session() as s:
            s.execute(text("DELETE FROM re_customers WHERE id = :i"), {"i": fixture_ids["cid"]})
            s.commit()
        out = _call(customer_id=fixture_ids["cid"], limit=200)
        # 客户已删 → 列表本身会先报"客户不存在"，这里直接查该客户之外的入口
        out = _call(limit=200)
        names = {x["id"]: x["customer_name"] for x in out["viewings"]}
        assert names.get(fixture_ids["v1"]) == f"已删除客户（id={fixture_ids['cid']}）", names


# ==================== ② 形状三件齐 ====================
class TestShape:
    def test_count_total_truncated(self, wired, fixture_ids):
        out = _call(customer_id=fixture_ids["cid"])
        assert (out["count"], out["total"], out["truncated"]) == (2, 2, False), out

    def test_total_is_full_count_when_truncated(self, wired, fixture_ids):
        out = _call(customer_id=fixture_ids["cid"], limit=1)
        assert out["count"] == 1 and out["total"] == 2 and out["truncated"] is True, out
        assert "共 2 条带看" in out["message"] and "要我多列就说一声" in out["message"], out["message"]

    def test_total_matches_sql(self, wired, fixture_ids):
        with wired.get_session() as s:
            n = s.execute(text("SELECT count(*) FROM re_viewings WHERE customer_id = :i"),
                          {"i": fixture_ids["cid"]}).scalar()
        assert _call(customer_id=fixture_ids["cid"])["total"] == n

    def test_message_has_no_internals(self, wired, fixture_ids):
        out = _call(customer_id=fixture_ids["cid"], limit=1)
        for word in ("limit", "truncated", "status", "viewings", "list_viewings"):
            assert word not in out["message"], (word, out["message"])


# ==================== ③ 状态筛选（认中文、乱值给提示） ====================
class TestStatusFilter:
    @pytest.mark.parametrize("value", ["done", "已完成", "看完了"])
    def test_done_filter_accepts_chinese(self, wired, fixture_ids, value):
        out = _call(customer_id=fixture_ids["cid"], status=value)
        assert out["success"] is True, out
        assert [x["id"] for x in out["viewings"]] == [fixture_ids["v1"]], out

    @pytest.mark.parametrize("value", ["scheduled", "待带看", "已取消"])
    def test_other_filters(self, wired, fixture_ids, value):
        out = _call(status=value)
        assert out["success"] is True, out
        if value == "已取消":
            assert out["viewings"] == [] and out["total"] == 0, out
        else:
            assert fixture_ids["v2"] in [x["id"] for x in out["viewings"]], out

    def test_bad_status_gives_chinese_hint(self, wired, fixture_ids):
        out = _call(customer_id=fixture_ids["cid"], status="乱写的状态")
        assert out["success"] is not True, out
        assert "带看状态没能识别" in out["error"] and "已完成" in out["error"], out["error"]

    def test_done_count_matches_rows(self, wired, fixture_ids):
        out = _call(customer_id=fixture_ids["cid"], status="已完成")
        assert out["count"] == out["total"] == 1 and out["truncated"] is False, out


# ==================== ④ 空态分开说 ====================
class TestEmptyStates:
    def test_unknown_customer(self, wired):
        out = _call(customer_id=999999)
        assert out["success"] is not True and "客户不存在" in out["error"], out

    def test_unknown_property(self, wired):
        out = _call(property_id=999999)
        assert out["success"] is not True and "房源不存在" in out["error"], out

    def test_customer_without_viewings(self, wired, fixture_ids):
        out = _call(customer_id=fixture_ids["other"])
        assert out["success"] is True and out["total"] == 0, out
        assert out["message"] == "这位客户还没有带看记录", out["message"]

    def test_property_without_viewings(self, wired, fixture_ids):
        pid = _property(wired, title="没人看过的新房源")
        out = _call(property_id=pid)
        assert out["total"] == 0 and out["message"] == "这套房源还没有带看记录", out

    def test_filter_without_match(self, wired, fixture_ids):
        out = _call(customer_id=fixture_ids["cid"], status="已取消")
        assert out["total"] == 0 and out["message"] == "没有符合这个条件的带看记录", out

    def test_empty_db(self, wired):
        out = _call()
        assert out["total"] == 0 and out["message"] == "还没有任何带看记录", out


# ==================== ⑤ 列表显示回访提醒 ====================
class TestReminderInList:
    def test_done_viewing_has_reminder_label(self, wired, fixture_ids):
        done = [x for x in _call(customer_id=fixture_ids["cid"])["viewings"]
                if x["id"] == fixture_ids["v1"]][0]
        assert done["reminder_label"].startswith("已安排回访提醒（"), done
        assert "None" not in done["reminder_label"], done
        assert done["reminder"]["type"] == "reminder", done["reminder"]

    def test_scheduled_viewing_has_no_reminder(self, wired, fixture_ids):
        plan = [x for x in _call(customer_id=fixture_ids["cid"])["viewings"]
                if x["id"] == fixture_ids["v2"]][0]
        assert "reminder" not in plan and "reminder_label" not in plan, plan

    def test_label_matches_detail(self, wired, fixture_ids):
        import tools.real_estate_viewing as m

        listed = [x for x in _call(customer_id=fixture_ids["cid"])["viewings"]
                  if x["id"] == fixture_ids["v1"]][0]
        detail = json.loads(m.get_viewing(viewing_id=fixture_ids["v1"]))["viewing"]
        assert listed["reminder_label"] == detail["reminder_label"], (listed, detail)


# ==================== ⑥ 防 N+1：提醒是批量取，不是一条一查 ====================
class TestQueryCount:
    def test_reminder_lookup_is_batched(self, wired):
        """20 条带看（各带一条提醒）→ 查询次数应是常数级，不是 20+ 次"""
        for i in range(20):
            cid = _customer(wired, name=f"批量客户{i}", phone=f"138000{i:05d}")
            pid = _property(wired, title=f"批量房源{i}")
            vid = _schedule(cid, pid, days=1 + i)
            _record(viewing_id=vid, status="已完成", result="感兴趣")
        counter = {"n": 0}

        @event.listens_for(wired.engine, "before_cursor_execute")
        def _count(conn, cursor, statement, params, context, executemany):
            counter["n"] += 1

        try:
            out = _call(limit=200)
        finally:
            event.remove(wired.engine, "before_cursor_execute", _count)
        assert out["total"] == 20 and out["count"] == 20, out
        assert all(x.get("reminder_label") for x in out["viewings"]), out["viewings"][:2]
        assert counter["n"] <= 12, f"查询次数 {counter['n']}（疑似逐条查提醒的 N+1 回来了）"


# ==================== ⑦ 入口形态 ====================
class TestArgs:
    def test_bad_id_forms(self, wired):
        for args in ({"customer_id": "abc"}, {"property_id": "abc"}):
            out = _call(**args)
            assert out["success"] is not True, out
            assert "编号" in out["error"] and "数字" in out["error"], out["error"]

    def test_limit_forms(self, wired, fixture_ids):
        for value in (0, -1, "abc", None, 100000):
            out = _call(customer_id=fixture_ids["cid"], limit=value)
            assert out["success"] is True, out
            assert len(out["viewings"]) <= 200, out

    def test_numeric_string_id_works(self, wired, fixture_ids):
        out = _call(customer_id=str(fixture_ids["cid"]))
        assert out["success"] is True and out["total"] == 2, out

    def test_default_order_latest_first(self, wired, fixture_ids):
        times = [x["viewing_time"] for x in _call(customer_id=fixture_ids["cid"])["viewings"]]
        assert times == sorted(times, reverse=True), times

    def test_schema_description_states_ability(self):
        from tools.registry import registry

        schema = registry.get_entry("list_viewings").schema
        desc = schema["description"]
        assert len(desc) >= 60, desc
        for word in ("total", "truncated", "待带看", "回访提醒"):
            assert word in desc, (word, desc)


# ==================== ⑧ 记结果回执也走同一套展示 ====================
class TestRecordViewingReceipt:
    def test_receipt_viewing_has_labels(self, wired, fixture_ids):
        vid = _schedule(fixture_ids["cid"], fixture_ids["pid"], days=9)
        out = _record(viewing_id=vid, status="已完成", result="不感兴趣")
        v = out["viewing"]
        assert v["status_label"] == "已完成" and v["result_label"] == "不感兴趣", v
        assert v["viewing_time_label"] and "T" not in v["viewing_time_label"], v
        assert v["status"] == "done" and v["result"] == "not_interested", v      # 原值照旧
