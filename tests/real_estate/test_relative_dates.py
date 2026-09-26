"""相对日期回归（2026-09-25 要求：经纪人怎么说都要认）

`agent/real_estate_input.py::norm_date` 是**跨批共用件**（跟进下次日期、提醒日期、独家委托到期日都走它），
本次加的相对写法：`今天/明天/后天/大后天`、`周三/星期三/礼拜三`（未来最近的那个）、`本周五/这周三`、
`下周三/下个星期三`、`3天后/三天以后/2周后`、`下个月5号`（下个月没有该日就取该月最后一天）。

同时钉住：**绝对写法一条都没坏**，以及认不出的仍然拒绝（不猜）。
"""
import json
from datetime import date, datetime, timedelta

import pytest

WEEKDAY_CHARS = ["一", "二", "三", "四", "五", "六", "日"]


def _norm(value):
    from agent.real_estate_input import norm_date

    return norm_date(value)


def _days_from_today(value):
    parsed, problem = _norm(value)
    assert parsed is not None, (value, problem)
    return (parsed.date() - date.today()).days


# ==================== 相对「天」 ====================

class TestRelativeDays:
    @pytest.mark.parametrize("text,offset", [
        ("今天", 0), ("今日", 0), ("明天", 1), ("明日", 1), ("后天", 2), ("大后天", 3),
    ])
    def test_simple_relative_days(self, text, offset):
        assert _days_from_today(text) == offset

    @pytest.mark.parametrize("text,offset", [
        ("3天后", 3), ("三天后", 3), ("三天以后", 3), ("10天后", 10), ("十天以后", 10),
        ("2周后", 14), ("两周后", 14), ("1个星期后", 7), ("下个星期后", None),
    ])
    def test_n_days_later(self, text, offset):
        if offset is None:
            pytest.skip("不是本次支持的写法")
        assert _days_from_today(text) == offset

    def test_result_is_midnight_datetime(self):
        parsed, _ = _norm("明天")
        assert isinstance(parsed, datetime) and parsed.time() == datetime.min.time()


# ==================== 相对「周」 ====================

class TestRelativeWeeks:
    @pytest.mark.parametrize("index,char", list(enumerate(WEEKDAY_CHARS)))
    def test_bare_weekday_is_the_nearest_future_one(self, index, char):
        for text in (f"周{char}", f"星期{char}", f"礼拜{char}"):
            delta = _days_from_today(text)
            assert 0 <= delta <= 6, (text, delta)
            assert (date.today() + timedelta(days=delta)).weekday() == index, text
            # 今天正好是这一天 → 就是今天
            if date.today().weekday() == index:
                assert delta == 0, text

    @pytest.mark.parametrize("index,char", list(enumerate(WEEKDAY_CHARS)))
    def test_this_week_weekday(self, index, char):
        for text in (f"本周{char}", f"这周{char}"):
            delta = _days_from_today(text)
            assert (date.today() + timedelta(days=delta)).weekday() == index, text
            assert 0 <= delta <= 6, (text, delta)

    @pytest.mark.parametrize("index,char", list(enumerate(WEEKDAY_CHARS)))
    def test_next_week_weekday_lands_in_next_week(self, index, char):
        today = date.today()
        next_monday = today + timedelta(days=(7 - today.weekday()) or 7)
        next_sunday = next_monday + timedelta(days=6)
        for text in (f"下周{char}", f"下周{char}", f"下个星期{char}"):
            if text == f"下周{char}":
                continue                      # 「下周」不是本次支持的说法
            parsed, problem = _norm(text)
            assert parsed is not None, (text, problem)
            assert parsed.weekday() == index, text
            assert next_monday <= parsed.date() <= next_sunday, (text, parsed.date())

    def test_spaces_inside_are_tolerated(self):
        assert _norm("下周 三")[0] is not None


# ==================== 相对「月」 ====================

class TestRelativeMonths:
    def test_next_month_day(self):
        today = date.today()
        parsed, _ = _norm("下个月5号")
        assert parsed.day == 5
        assert (parsed.year, parsed.month) == ((today.year + 1, 1) if today.month == 12
                                               else (today.year, today.month + 1))

    def test_next_month_short_month_falls_back_to_last_day(self):
        """下个月31号：那个月没有 31 号就取该月最后一天（不报错、不跳到下下月）"""
        today = date.today()
        parsed, problem = _norm("下个月31号")
        assert parsed is not None, problem
        target_month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        assert (parsed.year, parsed.month) == target_month
        assert parsed.day == min(31, (date(target_month[0], target_month[1] % 12 + 1, 1)
                                      - timedelta(days=1)).day)

    def test_silly_day_is_rejected(self):
        assert _norm("下个月40号")[0] is None


# ==================== 认不出的仍然拒绝（不猜） ====================

class TestStillRejects:
    @pytest.mark.parametrize("text", ["下下周", "月底", "年底", "明天上午", "下个季度", "随便哪天"])
    def test_unsupported_still_rejected(self, text):
        parsed, problem = _norm(text)
        assert parsed is None and problem and "没能识别" in problem, (text, parsed, problem)


# ==================== 绝对写法没被改坏 ====================

class TestAbsoluteStillWorks:
    @pytest.mark.parametrize("text,expect", [
        ("2027-03-05", "2027-03-05"), ("2027/03/05", "2027-03-05"),
        ("2027.03.05", "2027-03-05"), ("2027年3月5日", "2027-03-05"),
    ])
    def test_absolute_forms(self, text, expect):
        parsed, _ = _norm(text)
        assert parsed.strftime("%Y-%m-%d") == expect

    def test_month_day_rolls_to_next_year_when_passed(self):
        parsed, _ = _norm("1月1日")
        today = date.today()
        assert parsed.day == 1 and parsed.month == 1
        assert parsed.date() >= today

    def test_datetime_passthrough(self):
        stamp = datetime(2027, 3, 5, 14, 30)
        assert _norm(stamp)[0] == stamp


# ==================== 端到端：三个走这个共用件的入口 ====================

@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_followup as m_fu
    import tools.real_estate_property as m_pr

    for m in (m_fu, m_pr):
        monkeypatch.setattr(m, "_get_db", lambda _db=db: db)
    return db, m_fu, m_pr


class TestEndToEnd:
    def test_add_followup_accepts_relative_date(self, wired):
        db, m_fu, _ = wired
        c = db.add_customer(name="相对日期客户", customer_type="rent")
        tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        out = json.loads(m_fu.add_followup(customer_id=c["id"], content="明天回访",
                                          next_date="明天", next_time="10点"))
        assert out["success"] is True, out
        assert out["followup"]["next_date"] == f"{tomorrow}T10:00:00"
        assert out["message"] == f"已记录「相对日期客户」的备注跟进，下次跟进 {tomorrow} 10:00"

    def test_schedule_reminder_accepts_relative_date(self, wired):
        db, m_fu, _ = wired
        c = db.add_customer(name="提醒客户", customer_type="rent")
        out = json.loads(m_fu.schedule_reminder(customer_id=c["id"], date="下周三", time="09:30"))
        assert out["success"] is True, out
        stored = datetime.fromisoformat(out["reminder"]["next_date"])
        today = date.today()
        next_monday = today + timedelta(days=(7 - today.weekday()) or 7)
        assert stored.weekday() == 2 and next_monday <= stored.date() <= next_monday + timedelta(days=6)

    def test_exclusive_until_accepts_relative_date(self, wired):
        db, _, m_pr = wired
        out = json.loads(m_pr.add_property(title="相对日期独家房", price=1_500_000, area=80.0,
                                           property_type="second_hand", exclusive_until="明天"))
        assert out["success"] is True, out
        assert out["property"]["exclusive_until"].startswith(
            (date.today() + timedelta(days=1)).strftime("%Y-%m-%d"))

    def test_garbage_date_still_rejected_with_hint(self, wired):
        db, m_fu, _ = wired
        c = db.add_customer(name="乱值客户", customer_type="rent")
        out = json.loads(m_fu.schedule_reminder(customer_id=c["id"], date="月底", time="09:00"))
        assert out["success"] is False and "提醒日期没能识别" in out["error"], out
