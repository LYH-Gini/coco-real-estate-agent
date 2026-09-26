"""add_followup 写入前校验回归（2026-09-25 第四组 F125–F133）

背景（真实实测，不是设想）：
① 客户/房源编号查不到照样写库 → 孤儿跟进，还会被 `get_overdue`/`midday_check`
   当成真客户报出来（经纪人只看到一个查不到名字的编号）；
② 下次跟进日期只认 ISO，`2026/12/31`、`2026年12月31日`、`12月31日` 一律拒；
③ 下次跟进时间不校验，`25:99`、`下午三点` 原样入库（早报会照原样展示）；
④ 给了时间不并进 `next_date` → 逾期判定比经纪人约定的时间早（与设置提醒、
   带看后自动提醒的口径不一致：那两个写的是带时刻的 datetime）；
⑤ 跟进类型不归一：`随便写的`、80 个字母都入库，而列宽只有 50 字符
   （生产 PostgreSQL 会整单失败）；
⑥ 内容空串/纯空格入库；
⑦ 成功返回没有给经纪人看的回显句（同族的 add_owner 有）。

本文件钉住修好之后的行为；日期/时间归一后的 **next_date 必须带时刻**，
这是与 `schedule_reminder`／带看自动提醒共用的一套口径。
"""
import json
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def wired(db, monkeypatch):
    """让跟进工具指向临时库"""
    import tools.real_estate_followup as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


@pytest.fixture
def fixtures(wired):
    c = wired.add_customer(name="跟进客户", tier="A", customer_type="buy_second_hand")
    other = wired.add_customer(name="另一位客户", tier="C", customer_type="rent")
    p = wired.add_property(title="跟进房源", price=1_500_000, area=80.0,
                           property_type="second_hand", status="available")
    return {"customer": c, "other": other, "property": p}


def _call(args):
    import tools.real_estate_followup as m

    return json.loads(m.add_followup(**args))


def _followup_rows(db):
    from agent.real_estate_db import Followup

    with db.get_session() as s:
        return s.query(Followup).all()


# ==================== ① 认人认房：不写孤儿 ====================

class TestTargetMustExist:
    def test_missing_customer_is_rejected_and_not_written(self, wired, fixtures):
        out = _call({"customer_id": 999999, "content": "孤儿跟进"})
        assert out["success"] is False
        assert out["error"] == "客户不存在，请先在客户列表里核对编号"
        assert _followup_rows(wired) == []

    def test_missing_property_is_rejected_and_not_written(self, wired, fixtures):
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "孤儿房源跟进",
                     "property_id": 999999})
        assert out["success"] is False
        assert out["error"] == "房源不存在，请核对房源编号"
        assert _followup_rows(wired) == []

    def test_orphan_followup_never_reaches_overdue(self, wired, fixtures):
        """端到端：写不进孤儿 → 逾期列表里不会冒出查不到名字的编号"""
        import tools.real_estate_followup as m

        my = (datetime.now() - timedelta(days=2)).isoformat()
        out = _call({"customer_id": 987654, "content": "孤儿逾期", "next_date": my})
        assert out["success"] is False
        overdue = json.loads(m.get_overdue())["overdue"]
        assert all(o["customer_id"] != 987654 for o in overdue), overdue

    def test_real_customer_still_writes(self, wired, fixtures):
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "正常跟进",
                     "property_id": fixtures["property"]["id"]})
        assert out["success"] is True
        rows = _followup_rows(wired)
        assert len(rows) == 1
        assert rows[0].customer_id == fixtures["customer"]["id"]
        assert rows[0].property_id == fixtures["property"]["id"]


# ==================== ② 跟进内容 ====================

class TestContent:
    @pytest.mark.parametrize("content", ["", "   ", "\u3000"])
    def test_blank_content_is_rejected(self, wired, fixtures, content):
        out = _call({"customer_id": fixtures["customer"]["id"], "content": content})
        assert out["success"] is False
        assert out["error"] == "跟进内容不能为空，请写一句这次沟通的情况"
        assert _followup_rows(wired) == []

    def test_content_is_stripped(self, wired, fixtures):
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "  聊了学区房  "})
        assert out["success"] is True
        assert _followup_rows(wired)[0].content == "聊了学区房"


# ==================== ③ 跟进类型 ====================

class TestType:
    @pytest.mark.parametrize("raw,expected", [
        ("call", "call"), ("CALL", "call"), ("电话", "call"), ("回访", "call"),
        ("phone", "call"), ("visit", "visit"), ("带看", "visit"), ("看房", "visit"),
        ("deal", "deal"), ("成交", "deal"), ("note", "note"), ("备注", "note"),
        ("reminder", "reminder"), ("提醒", "reminder"), (None, "note"),
    ])
    def test_type_normalized(self, wired, fixtures, raw, expected):
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "类型归一", "type": raw})
        assert out["success"] is True, out
        assert _followup_rows(wired)[0].type == expected

    @pytest.mark.parametrize("raw", ["随便写的", "T" * 80, "微信语音"])
    def test_unknown_type_rejected_with_options(self, wired, fixtures, raw):
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "类型乱值", "type": raw})
        assert out["success"] is False
        assert "没能识别" in out["error"]
        assert "call（电话）" in out["error"]
        assert _followup_rows(wired) == []

    def test_type_never_exceeds_column(self, wired, fixtures):
        """列宽 50：只有 5 个枚举值能进库，历史那种 80 字类型现在会被拦下"""
        _call({"customer_id": fixtures["customer"]["id"], "content": "x", "type": "T" * 80})
        assert all(len(r.type) <= 50 for r in _followup_rows(wired))


# ==================== ④ 日期 ====================

class TestNextDate:
    @pytest.mark.parametrize("raw,expect", [
        ("2027-03-05", "2027-03-05"),
        ("2027/03/05", "2027-03-05"),
        ("2027.03.05", "2027-03-05"),
        ("2027年3月5日", "2027-03-05"),
    ])
    def test_date_formats_accepted(self, wired, fixtures, raw, expect):
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "日期写法", "next_date": raw})
        assert out["success"] is True, out
        assert _followup_rows(wired)[0].next_date.strftime("%Y-%m-%d") == expect

    def test_month_day_only_means_future(self, wired, fixtures):
        """只有月日按当年，已过则次年（到期日总是指未来）"""
        today = datetime.now()
        raw = f"{(today + timedelta(days=2)).month}月{(today + timedelta(days=2)).day}日"
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "只有月日", "next_date": raw})
        assert out["success"] is True, out
        stored = _followup_rows(wired)[0].next_date
        assert stored.date() >= today.date()

    def test_unknown_date_rejected(self, wired, fixtures):
        # 2026-09-25 起「明天/下周三」这类相对说法已支持（见 test_relative_dates.py），
        # 这里改用仍认不出的说法来钉「不猜」
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "x", "next_date": "月底"})
        assert out["success"] is False
        assert out["error"] == "下次跟进日期没能识别：收到的是「月底」。请用 2026-12-31 这类写法"

    @pytest.mark.parametrize("raw,expect", [
        ("2027-03-05T14:30:00", datetime(2027, 3, 5, 14, 30)),
        ("2027-03-05 14:30", datetime(2027, 3, 5, 14, 30)),
        ("2027-03-05T14:30:00.123456", datetime(2027, 3, 5, 14, 30)),
    ])
    def test_datetime_strings_still_accepted(self, wired, fixtures, raw, expect):
        """回归：模型常把整个 datetime 塞进 next_date（原先 fromisoformat 能认，归一后必须照样认）"""
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "带时刻的日期",
                     "next_date": raw})
        assert out["success"] is True, out
        assert _followup_rows(wired)[0].next_date == expect

    def test_explicit_time_wins_over_embedded(self, wired, fixtures):
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "显式时间优先",
                     "next_date": "2027-03-05T14:30:00", "next_time": "09:00"})
        assert out["success"] is True, out
        assert _followup_rows(wired)[0].next_date == datetime(2027, 3, 5, 9, 0)
        assert _followup_rows(wired)[0].next_time == "09:00"

    def test_no_date_stays_empty(self, wired, fixtures):
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "x", "next_date": ""})
        assert out["success"] is True
        assert _followup_rows(wired)[0].next_date is None


# ==================== ⑤ 时间（含与 next_date 的口径）====================

class TestNextTime:
    @pytest.mark.parametrize("raw,expect", [
        ("09:30", "09:30"), ("9:30", "09:30"), ("9点30", "09:30"),
        ("0930", "09:30"), ("9点", "09:00"), ("9", "09:00"), ("00:00", "00:00"),
    ])
    def test_time_normalized(self, wired, fixtures, raw, expect):
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "时间归一",
                     "next_time": raw})
        assert out["success"] is True, out
        assert _followup_rows(wired)[0].next_time == expect

    @pytest.mark.parametrize("raw", ["25:99", "下午三点", "9点70", "-1:00"])
    def test_unknown_time_rejected(self, wired, fixtures, raw):
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "x", "next_time": raw})
        assert out["success"] is False
        assert out["error"] == f"下次跟进时间没能识别：收到的是「{raw}」。请用 09:30 这类写法"
        assert _followup_rows(wired) == []

    def test_time_is_folded_into_next_date(self, wired, fixtures):
        """与 schedule_reminder 同口径：给了时间，next_date 就要带到那一刻"""
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "并时刻",
                     "next_date": "2027-03-05", "next_time": "14:30"})
        assert out["success"] is True, out
        stored = _followup_rows(wired)[0].next_date
        assert stored == datetime(2027, 3, 5, 14, 30)

    def test_schedule_reminder_uses_same_shape(self, wired, fixtures):
        """设置提醒走的也是同一套口径（回归两者不再分叉）"""
        import tools.real_estate_followup as m

        out = json.loads(m.schedule_reminder(customer_id=fixtures["customer"]["id"],
                                             date="2027-03-05", time="14:30"))
        assert out["success"] is True, out
        stored = _followup_rows(wired)[0].next_date
        assert stored == datetime(2027, 3, 5, 14, 30)

    def test_overdue_uses_the_time_of_day(self, wired, fixtures):
        """并进时刻后，当天稍晚的约定不再一过零点就报逾期"""
        import tools.real_estate_followup as m

        now = datetime.now()
        later, earlier = now + timedelta(minutes=30), now - timedelta(minutes=30)
        if later.date() != now.date() or earlier.date() != now.date():
            pytest.skip("跑在跨天时刻，时间窗断言不成立")
        cid = fixtures["customer"]["id"]
        _call({"customer_id": cid, "content": "稍晚的约定", "next_date": now.strftime("%Y-%m-%d"),
               "next_time": later.strftime("%H:%M")})
        overdue = json.loads(m.get_overdue())["overdue"]
        assert all(o["customer_id"] != cid for o in overdue), "还没到点就不该算逾期"
        # 再写一条已过点的（逾期判定只看该客户最新一条跟进）
        _call({"customer_id": cid, "content": "已过点的约定", "next_date": now.strftime("%Y-%m-%d"),
               "next_time": earlier.strftime("%H:%M")})
        overdue = json.loads(m.get_overdue())["overdue"]
        assert any(o["customer_id"] == cid for o in overdue), "过了点就该算逾期"


# ==================== ⑥ 列宽与回显 ====================

class TestOutputs:
    def test_agent_id_clipped_with_warning(self, wired, fixtures):
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "x",
                     "agent_id": "A" * 150})
        assert out["success"] is True
        assert _followup_rows(wired)[0].agent_id == "A" * 100
        assert out["warnings"] == ["经纪人编号超过 100 字，只保留了前 100 字。"]

    def test_message_with_date_and_time(self, wired, fixtures):
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "回显",
                     "type": "call", "next_date": "2027-03-05", "next_time": "9点30"})
        assert out["message"] == "已记录「跟进客户」的电话跟进，下次跟进 2027-03-05 09:30"

    def test_message_with_date_only(self, wired, fixtures):
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "回显",
                     "next_date": "2027-03-05"})
        assert out["message"] == "已记录「跟进客户」的备注跟进，下次跟进 2027-03-05"

    def test_message_without_schedule(self, wired, fixtures):
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "回显"})
        assert out["message"] == "已记录「跟进客户」的备注跟进"

    def test_message_has_no_internal_terms(self, wired, fixtures):
        out = _call({"customer_id": fixtures["customer"]["id"], "content": "回显"})
        for word in ("use_template", "winback", "task_id", "session_id", "re_customers"):
            assert word not in out["message"]

    def test_read_back_matches_write(self, wired, fixtures):
        """反向读回：写入的字段与 get_followups 读出来的一致"""
        import tools.real_estate_followup as m

        _call({"customer_id": fixtures["customer"]["id"], "content": "读回一致",
               "type": "visit", "next_date": "2027-03-05", "next_time": "9点30"})
        got = json.loads(m.get_followups(customer_id=fixtures["customer"]["id"]))["followups"]
        assert len(got) == 1
        assert got[0]["type"] == "visit" and got[0]["next_time"] == "09:30"
        assert got[0]["next_date"] == "2027-03-05T09:30:00"

    def test_other_customer_followups_untouched(self, wired, fixtures):
        _call({"customer_id": fixtures["customer"]["id"], "content": "甲"})
        _call({"customer_id": fixtures["other"]["id"], "content": "乙"})
        rows = {r.customer_id: r.content for r in _followup_rows(wired)}
        assert rows[fixtures["customer"]["id"]] == "甲"
        assert rows[fixtures["other"]["id"]] == "乙"


# ==================== ⑦ 描述（模型靠它判断这工具能给什么）====================

class TestDescription:
    def test_description_states_what_it_does(self):
        from tools.registry import registry

        desc = registry.get_entry("add_followup").schema["description"]
        assert len(desc) >= 30, desc
        for word in ("跟进", "电话", "带看", "日期", "房源"):
            assert word in desc, (word, desc)

    def test_description_not_written_twice_with_drift(self):
        """模块级 TOOLS 与 registry.register 各写一份描述，两份必须一致"""
        from tools.registry import registry
        import tools.real_estate_followup as m

        assert registry.get_entry("add_followup").schema["description"] == m.TOOLS[0]["description"]

    def test_param_descriptions_state_accepted_forms(self):
        """参数说明要写清能认的写法，否则模型只按 ISO 传"""
        from tools.registry import registry

        props = registry.get_entry("add_followup").schema["parameters"]["properties"]
        assert "2026/12/31" in props["next_date"]["description"]
        assert "09:30" in props["next_time"]["description"]
        assert "中文" in props["type"]["description"]
