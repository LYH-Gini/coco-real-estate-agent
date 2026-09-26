"""schedule_reminder 回归（2026-09-25 第四组 F151–F157）

背景（实测）：
① 日期/时间都不归一：`2026/12/31`、`2026年12月31日`、`12月31日`、`明天` 全回「日期格式错误」，
   **时间写错也报"日期格式错误"**（指错对象）；模型塞整个 `2026-09-28T14:30:00` 也报错；
② `date=''` 照落库 → 造出 `next_date` 为空、**永远不会触发**的提醒，回显里还谎报 09:00；
③ `time=''` → 库里 `next_time=''`（空串形态）；
④ 返回体只有 `{success, message}`，没有可核对的提醒对象；
⑤ 同一客户同一时间设 2 次 → 落 3 条；且**设一条提醒会静默消解该客户原来的逾期提醒**；
⑥ 描述 8 个字、四个参数一个说明都没有。

本文件钉住修好之后的行为。
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_followup as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


@pytest.fixture
def cid(wired):
    return wired.add_customer(name="提醒客户", tier="A", customer_type="buy_second_hand")["id"]


def _call(**kwargs):
    import tools.real_estate_followup as m

    return json.loads(m.schedule_reminder(**kwargs))


def _rows(db, cid=None):
    sql = "SELECT id, type, content, next_date, next_time FROM re_followups"
    params = {}
    if cid is not None:
        sql += " WHERE customer_id = :i"
        params["i"] = cid
    with db.get_session() as s:
        return s.execute(text(sql), params).fetchall()


SOON = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")


# ==================== ① 日期/时间归一 ====================

class TestWhenNormalization:
    @pytest.mark.parametrize("raw,expect", [
        (SOON, None),                              # ISO 原样
        (SOON.replace("-", "/"), None),            # 2026/09/28
        (SOON.replace("-", "."), None),            # 2026.09.28
    ])
    def test_date_formats_land(self, wired, cid, raw, expect):
        out = _call(customer_id=cid, date=raw, time="14:30")
        assert out["success"] is True, out
        assert out["reminder"]["next_date"].endswith("14:30:00")
        assert out["reminder"]["next_time"] == "14:30"

    def test_chinese_date_lands(self, wired, cid):
        out = _call(customer_id=cid, date="2027年3月5日", time="14:30")
        assert out["success"] is True, out
        assert out["reminder"]["next_date"].startswith("2027-03-05")

    def test_month_day_only_means_future(self, wired, cid):
        today = datetime.now()
        target = today + timedelta(days=2)
        out = _call(customer_id=cid, date=f"{target.month}月{target.day}日", time="09:00")
        assert out["success"] is True, out
        assert datetime.fromisoformat(out["reminder"]["next_date"]).date() >= today.date()

    def test_iso_datetime_with_time_lands(self, wired, cid):
        out = _call(customer_id=cid, date=f"{SOON}T14:30:00", time="14:30")
        assert out["success"] is True, out
        assert out["reminder"]["next_date"] == f"{SOON}T14:30:00"

    @pytest.mark.parametrize("raw,label", [
        ("9点30", "09:30"), ("0930", "09:30"), ("9:30", "09:30"), ("9点", "09:00"),
    ])
    def test_time_formats_land(self, wired, cid, raw, label):
        out = _call(customer_id=cid, date=SOON, time=raw)
        assert out["success"] is True, out
        assert out["reminder"]["next_time"] == label

    def test_time_missing_defaults_to_0900(self, wired, cid):
        out = _call(customer_id=cid, date=SOON)
        assert out["success"] is True, out
        assert out["reminder"]["next_time"] == "09:00"
        assert out["reminder"]["next_date"].endswith("T09:00:00")

    def test_time_blank_defaults_not_empty_string(self, wired, cid):
        """空串时间按 09:00 落，不留空串形态（契约：空串按未填，别两种形态并存）"""
        out = _call(customer_id=cid, date=SOON, time="")
        assert out["success"] is True, out
        assert out["reminder"]["next_time"] == "09:00"
        assert _rows(wired, cid)[0][4] == "09:00"


# ==================== ② 认不出/为空：提示指向正确对象、不落库 ====================

class TestRejections:
    def test_unknown_date_points_at_date(self, wired, cid):
        # 「明天/下周三」这类相对说法已支持（见 test_relative_dates.py），这里用仍认不出的说法
        out = _call(customer_id=cid, date="月底", time="09:00")
        assert out["success"] is False
        assert out["error"] == "提醒日期没能识别：收到的是「月底」。请用 2026-12-31 或 12月31日 这类写法"
        assert _rows(wired, cid) == []

    def test_unknown_time_points_at_time_not_date(self, wired, cid):
        """改前这里也报「日期格式错误」—— 指错对象"""
        out = _call(customer_id=cid, date=SOON, time="下午三点")
        assert out["success"] is False
        assert out["error"] == "提醒时间没能识别：收到的是「下午三点」。请用 09:30 这类写法"
        assert _rows(wired, cid) == []

    @pytest.mark.parametrize("bad_time", ["25:99", "9点70", "-1:00"])
    def test_out_of_range_time_rejected(self, wired, cid, bad_time):
        out = _call(customer_id=cid, date=SOON, time=bad_time)
        assert out["success"] is False and "提醒时间没能识别" in out["error"], out
        assert _rows(wired, cid) == []

    @pytest.mark.parametrize("bad_date", ["", "   "])
    def test_blank_date_rejected_and_not_written(self, wired, cid, bad_date):
        """改前 date='' 会落一条 next_date 为空、永远不触发的提醒"""
        out = _call(customer_id=cid, date=bad_date, time="09:00")
        assert out["success"] is False
        assert out["error"] == "请说个提醒日期（如 2026-12-31 或 12月31日）"
        assert _rows(wired, cid) == []

    def test_missing_customer_rejected(self, wired, cid):
        out = _call(customer_id=999999, date=SOON, time="09:00")
        assert out["success"] is False and "客户不存在" in out["error"]


# ==================== ③ 返回体可核对 ====================

class TestPayload:
    def test_returns_structured_reminder(self, wired, cid):
        out = _call(customer_id=cid, date=SOON, time="14:30", content="带看后回访")
        assert out["success"] is True
        reminder = out["reminder"]
        for key in ("id", "customer_id", "type", "content", "next_date", "next_time"):
            assert key in reminder, key
        assert reminder["type"] == "reminder" and reminder["content"] == "带看后回访"
        assert out["message"] == f"已设置「提醒客户」{SOON} 14:30 的跟进提醒"

    def test_default_content(self, wired, cid):
        out = _call(customer_id=cid, date=SOON, time="09:00")
        assert out["reminder"]["content"] == "跟进客户 提醒客户"

    def test_blank_content_falls_back_to_default(self, wired, cid):
        out = _call(customer_id=cid, date=SOON, time="09:00", content="   ")
        assert out["reminder"]["content"] == "跟进客户 提醒客户"


# ==================== ④ 重复设置 ====================

class TestDuplicate:
    def test_same_time_same_content_not_added_twice(self, wired, cid):
        first = _call(customer_id=cid, date=SOON, time="14:30")
        second = _call(customer_id=cid, date=SOON, time="14:30")
        assert len(_rows(wired, cid)) == 1
        assert second["duplicate"] is True
        assert second["reminder"]["id"] == first["reminder"]["id"]
        assert second["message"] == (f"这位客户 {SOON} 14:30 的提醒已经在"
                                     f"（第 {first['reminder']['id']} 条），没有重复加")

    def test_same_time_new_content_updates_existing(self, wired, cid):
        first = _call(customer_id=cid, date=SOON, time="14:30", content="旧内容")
        second = _call(customer_id=cid, date=SOON, time="14:30", content="新内容")
        assert len(_rows(wired, cid)) == 1
        assert second["duplicate"] is True
        assert second["reminder"]["id"] == first["reminder"]["id"]
        assert second["reminder"]["content"] == "新内容"
        assert second["message"] == "已把这条提醒的内容改成「新内容」"

    def test_repeated_calls_stay_one_row(self, wired, cid):
        for _ in range(5):
            _call(customer_id=cid, date=SOON, time="14:30")
        assert len(_rows(wired, cid)) == 1

    def test_different_time_creates_another(self, wired, cid):
        _call(customer_id=cid, date=SOON, time="14:30")
        _call(customer_id=cid, date=SOON, time="16:30")
        assert len(_rows(wired, cid)) == 2


# ==================== ⑤ 如实说明的两种 warnings ====================

class TestWarnings:
    def test_past_time_warns_it_is_already_overdue(self, wired, cid):
        past = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        out = _call(customer_id=cid, date=past, time="09:00")
        assert out["success"] is True
        assert "提醒时间已经过了，会立刻出现在逾期提醒里" in (out.get("warnings") or [])

    def test_superseding_previous_overdue_is_disclosed(self, wired, cid):
        """改前：设提醒把该客户的逾期静默消解（逾期清单里凭空消失）。现在必须说明。"""
        old = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        _call(customer_id=cid, date=old, time="09:00")
        import tools.real_estate_followup as m

        assert cid in [o["customer_id"] for o in json.loads(m.get_overdue())["overdue"]]
        out = _call(customer_id=cid, date=SOON, time="09:00")
        # 钉整句（F215 起口径是「人工跟进」）
        assert ("这位客户之前那条逾期提醒不再出现了（系统按最新一条人工跟进算逾期）"
                in (out.get("warnings") or [])), out
        assert cid not in [o["customer_id"] for o in json.loads(m.get_overdue())["overdue"]]

    def test_no_warning_when_nothing_superseded(self, wired, cid):
        out = _call(customer_id=cid, date=SOON, time="09:00")
        assert not out.get("warnings")


# ==================== ⑥ 描述与参数 ====================

class TestDescription:
    def test_description_states_capabilities(self):
        from tools.registry import registry

        desc = registry.get_entry("schedule_reminder").schema["description"]
        assert len(desc) >= 40, desc
        for word in ("提醒", "2026年12月31日", "09:30", "不重复建", "reminder"):
            assert word in desc, (word, desc)

    def test_description_not_written_twice_with_drift(self):
        from tools.registry import registry
        import tools.real_estate_followup as m

        assert registry.get_entry("schedule_reminder").schema["description"] == m.TOOLS[3]["description"]

    def test_params_have_descriptions_and_time_is_optional(self):
        from tools.registry import registry

        schema = registry.get_entry("schedule_reminder").schema
        props = schema["parameters"]["properties"]
        assert all(props[k].get("description") for k in ("customer_id", "date", "time", "content"))
        assert "time" not in schema["parameters"]["required"]      # 不传也能设（按 09:00）
        assert "09:30" in props["time"]["description"]
