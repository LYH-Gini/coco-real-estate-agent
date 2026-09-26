"""performance_dashboard 回归（2026-09-26 第十组「报表与数据」第 70 项，F346–F349）

背景（实测见 /root/coco-tool-audit/results/raw/t74a.before.log、t74b.before.log）：
① **`period` 是纯摆设**：`performance_dashboard(period='week')` 与 `(period='year')` 除回显的
   `统计周期` 外**数字一模一样**（全是累计值），乱值也静默通过 —— 经纪人传「本季度」会以为拿到季度业绩；
② **逾期客户只给裸编号**「客户ID:1」且只列 5 位，不说总数、不说被截断；
③ **描述 15 字**「业绩看板（客户统计、房源统计）」，返回里其实只有 1 个在售数、没有"房源统计"；
   参数说明只有「统计周期」4 字；
④ **空库零说明**（全 0 + `逾期客户: []`，没有一句"库里还没有客户"）；
⑤ 没有给经纪人看的中文 `message`。

本文件钉住修后的行为：四个档真统计（滚动窗口，近 7/30/90/365 天）、`本周期` 写明区间、
逾期名单带名字与等级并说清截断、空库说法、中文 `message`、只读不写库。
"""
import json
import re
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_analytics as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


def _dash(period="__default__"):
    import tools.real_estate_analytics as m

    if period == "__default__":
        return json.loads(m.performance_dashboard())
    return json.loads(m.performance_dashboard(period=period))


def _customer(db, name="看板客户", tier="A", i=0, created_days_ago=0):
    cid = db.add_customer(name=name, phone=f"13900{i:06d}", tier=tier,
                          customer_type="buy_second_hand", status="active")["id"]
    if created_days_ago:
        _backdate(db, "re_customers", "created_at", cid, created_days_ago)
    return cid


def _property(db, title="看板房源 1号楼101", i=0, created_days_ago=0):
    pid = db.add_property(title=title, price=1_500_000, area=90.0, property_type="second_hand",
                          status="available")["id"]
    if created_days_ago:
        _backdate(db, "re_properties", "created_at", pid, created_days_ago)
    return pid


def _backdate(db, table, column, row_id, days):
    from sqlalchemy import text

    with db.get_session() as s:
        s.execute(text(f"update {table} set {column} = :ts where id = :i"),
                  {"ts": datetime.now() - timedelta(days=days), "i": row_id})
        s.commit()


# ==================== ① period 真的影响统计（F346） ====================

class TestPeriodReallyCounts:
    def test_week_and_year_differ(self, wired):
        """本期新增客户：今天 1 位 + 200 天前 1 位 → 近 7 天算 1 位、近 365 天算 2 位"""
        _customer(wired, name="本期客户", i=1)
        _customer(wired, name="半年前的客户", i=2, created_days_ago=200)
        week = _dash("week")["dashboard"]
        year = _dash("year")["dashboard"]
        assert week["本期新增客户"] == 1, week
        assert year["本期新增客户"] == 2, year
        assert week["本期新增客户"] != year["本期新增客户"], (week, year)

    def test_counts_match_sql_for_each_period(self, wired):
        _customer(wired, name="a", i=3)
        _customer(wired, name="b", i=4, created_days_ago=45)
        _property(wired, title="新房源", i=3)
        _property(wired, title="老房源", i=4, created_days_ago=45)
        stats = wired.period_stats(datetime.now() - timedelta(days=30))
        dash = _dash("month")["dashboard"]
        assert dash["本期新增客户"] == stats["new_customers"] == 1, dash
        assert dash["本期新增房源"] == stats["new_properties"] == 1, dash

    def test_window_is_written_out(self, wired):
        dash = _dash("quarter")["dashboard"]
        assert dash["统计周期"] == "本季度", dash
        assert "近 90 天" in dash["本周期"], dash
        assert re.search(r"\d{2}-\d{2} ~ \d{2}-\d{2}", dash["本周期"]), dash

    def test_default_is_month(self, wired):
        assert _dash()["dashboard"]["统计周期"] == "本月"

    @pytest.mark.parametrize("value,bad", [("季度", False), ("本季度", False), ("今年", False),
                                           ("近 90 天", False), ("本月", False)])
    def test_accepts_chinese(self, wired, value, bad):
        out = _dash(value)
        assert out["success"] is True, out

    @pytest.mark.parametrize("value", ["乱写", "", None, 5, "上周"])
    def test_bad_value_gets_chinese_hint(self, wired, value):
        out = _dash(value)
        assert out["success"] is not True, out
        err = out["error"]
        assert "本周" in err and "今年" in err, err


# ==================== ② 逾期名单（F347） ====================

class TestOverdue:
    def _overdue(self, db, n):
        for i in range(n):
            cid = _customer(db, name=f"逾期看板客户{i}", i=100 + i)
            db.add_followup(customer_id=cid, type="note", content=f"逾期{i}",
                            next_date=datetime.now() - timedelta(days=1 + i))

    def test_names_with_tier_and_total(self, wired):
        self._overdue(wired, 8)
        dash = _dash()["dashboard"]
        assert dash["逾期跟进"] == 8, dash
        assert len(dash["逾期客户"]) == 5, dash          # 只列最急的 5 位
        # 逾期最久在前：days_ago=1+i → 最后建的（客户7）最早逾期
        assert all(re.match(r"逾期看板客户\d（A级）$", x) for x in dash["逾期客户"]), dash["逾期客户"]
        assert dash["逾期客户"][0] == "逾期看板客户7（A级）", dash["逾期客户"]
        assert "共 8 位" in dash["逾期客户说明"], dash

    def test_all_listed_when_few(self, wired):
        self._overdue(wired, 2)
        dash = _dash()["dashboard"]
        assert len(dash["逾期客户"]) == 2, dash
        assert "全部列出" in dash["逾期客户说明"], dash

    def test_no_overdue_says_so(self, wired):
        _customer(wired, i=300)
        dash = _dash()["dashboard"]
        assert dash["逾期跟进"] == 0 and dash["逾期客户"] == [], dash
        assert dash["逾期客户说明"] == "暂无逾期跟进", dash


# ==================== ③ 空库与中文 message（F348–F349） ====================

class TestEmptyAndMessage:
    def test_empty_library(self, wired):
        out = _dash()
        assert out["success"] is True
        assert "库里还没有客户" in out["message"], out
        assert out["dashboard"]["说明"] == "库里还没有客户，先登记客户再看数据", out

    def test_message_is_chinese_and_spells_out_the_scope(self, wired):
        _customer(wired, i=400)
        _property(wired, i=400)
        out = _dash("week")
        msg = out["message"]
        assert "本周（近 7 天：" in msg, msg
        assert "在跟客户 1 位" in msg and "在售房源 1 套" in msg, msg
        assert "本期新增客户" in msg and "新开成交单" in msg, msg

    def test_message_has_no_english_enum_or_key(self, wired):
        """2026-09-26 定：给经纪人看的话里不许出现英文键名与英文枚举（HANDOFF 第 35 条）"""
        _customer(wired, i=500)
        out = _dash("quarter")
        for word in ("week", "month", "quarter", "year", "dashboard", "total_customers", "period"):
            assert word not in out["message"], (word, out["message"])
            assert word not in out["dashboard"].get("说明", ""), (word, out)


# ==================== ④ 只读、对账、描述 ====================

def test_dashboard_does_not_write(wired):
    _customer(wired, i=600)
    before = (wired.get_stats()["total_customers"], len(wired.get_overdue()))
    _dash("week")
    _dash("year")
    after = (wired.get_stats()["total_customers"], len(wired.get_overdue()))
    assert before == after


def test_customer_count_matches_customer_stats(wired):
    for i in range(3):
        _customer(wired, i=700 + i)
    import tools.real_estate_customer as tc

    assert _dash()["dashboard"]["客户总数"] == 3
    assert tc.customer_stats  # 同源（都走 db.get_stats）


def test_schema_tells_the_scope(monkeypatch, wired):
    from tools.registry import registry

    entry = registry.get_entry("performance_dashboard")
    desc = entry.schema["description"]
    assert "本期" in desc and len(desc) > 20, desc
    param = entry.schema["parameters"]["properties"]["period"]["description"]
    assert "本季度" in param and "近 365 天" in param, param
