"""generate_report 回归（2026-09-26 第十组「报表与数据」第 69 项，F337–F345）

背景（实测见 /root/coco-tool-audit/results/raw/t74a.before.log、t75.before.log、t74b.before.log）：
① **全篇是累计值，标题却写"周报"**：夹具里一半数据在 60 天前，报告仍写「客户总数：3 / 成交总数：2 /
   完成 2 次带看」，而按 09-19~09-26 过滤本周只有成交 1 单、带看完成 1 次 —— 经纪人会把累计当本期业绩；
② **`本week共管理…`**：英文枚举直接混进给经纪人看的中文句子；
③ **月报标题 `月报（2026-08）` 像自然月**，实现却是"近 30 天滚动窗口"（跨两个月）；
④ **逾期清单 15 位只列 10 位不说被截断**，明细还是「客户#18」这种裸编号（`db.get_overdue()` 不带客户名）；
⑤ **空库照样说「无逾期客户，跟进情况良好」**，且没有已看的带看也报「带看兴趣率偏低」；
⑥ **period 不认中文**（周报/月报被拒），乱值/空值提示是英文枚举 `period 必须是 week/month`。

本文件钉住修后的口径：**本期与累计分开写**、标题写明"近 N 天 + 区间"、逾期清单说清截断并带客户名、
没有已看的带看时不报"兴趣率偏低"、空库给空态说法。
"""
import json
import re
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_report as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


def _report(**kwargs):
    import tools.real_estate_report as m

    return json.loads(m.generate_report(**kwargs))


def _customer(db, name="报告客户", tier="A", i=0, created_days_ago=0):
    cid = db.add_customer(name=name, phone=f"13800{i:06d}", tier=tier,
                          customer_type="buy_second_hand", status="active")["id"]
    if created_days_ago:
        _backdate(db, "re_customers", "created_at", cid, created_days_ago)
    return cid


def _property(db, title="报告房源 1号楼101", price=1_500_000, i=0, created_days_ago=0):
    pid = db.add_property(title=title, price=price, area=90.0, property_type="second_hand",
                          status="available")["id"]
    if created_days_ago:
        _backdate(db, "re_properties", "created_at", pid, created_days_ago)
    return pid


def _backdate(db, table, column, row_id, days):
    """把一行的时间挪到 N 天前（造"往期数据"，用来把本期与累计分开）"""
    from sqlalchemy import text

    with db.get_session() as s:
        s.execute(text(f"update {table} set {column} = :ts where id = :i"),
                  {"ts": datetime.now() - timedelta(days=days), "i": row_id})
        s.commit()


def _old_viewing(db, cid, pid, result="interested", days_ago=40):
    vid = db.add_viewing(customer_id=cid, property_id=pid,
                         viewing_time=datetime.now() - timedelta(days=days_ago),
                         status="done", result=result)["id"]
    return vid


# ==================== ① 本期与累计分开写（F337） ====================

class TestPeriodVersusCumulative:
    def test_title_states_the_window(self, wired):
        out = _report(period="week")
        assert "近 7 天" in out["title"], out["title"]
        assert re.search(r"\d{2}-\d{2} ~ \d{2}-\d{2}", out["title"]), out["title"]

    def test_month_title_states_the_window_not_a_calendar_month(self, wired):
        out = _report(period="month")
        assert "近 30 天" in out["title"], out["title"]
        # 旧写法是"月报（2026-08）"——自然月的样子，会被读成"8 月的月报"
        assert not re.search(r"月报（\d{4}-\d{2}）", out["title"]), out["title"]
        assert re.search(r"\d{2}-\d{2} ~ \d{2}-\d{2}", out["title"]), out["title"]

    def test_period_section_counts_only_this_window(self, wired):
        old_c = _customer(wired, name="往期客户", i=1, created_days_ago=40)
        new_c = _customer(wired, name="本期客户", i=2)
        old_p = _property(wired, title="往期房源 1号楼101", i=1, created_days_ago=40)
        new_p = _property(wired, title="本期房源 2号楼202", i=2)
        _old_viewing(wired, old_c, old_p, result="interested", days_ago=40)
        db_deal = wired.add_deal(customer_id=old_c, property_id=old_p, price=1_400_000)
        _backdate(wired, "re_deals", "created_at", db_deal["id"], 40)
        new_deal = wired.add_deal(customer_id=new_c, property_id=new_p, price=1_600_000)

        report = _report(period="week")["report"]
        period_seg = report.split("## 本期（")[1].split("## 累计（")[0]
        cumulative_seg = report.split("## 累计（")[1].split("## 逾期跟进")[0]
        # 本期：只有今天新开的那一单、今天建档的那一位
        assert "- 新增客户：1 位" in period_seg, period_seg
        assert "- 新开成交单：1 单" in period_seg, period_seg
        # 累计：两单都在里面
        assert f"在册 {len([db_deal, new_deal])} 单" in cumulative_seg, cumulative_seg

    def test_summary_spells_out_both_scopes(self, wired):
        _customer(wired, i=3)
        _property(wired, i=3)
        report = _report(period="week")["report"]
        summary = report.split("## 总结")[-1]
        assert "累计：" in summary, summary
        assert "近 7 天（" in summary, summary

    def test_no_english_enum_in_sentences(self, wired):
        for period in ("week", "month"):
            report = _report(period=period)["report"]
            assert not re.search(r"本\s*(week|month)\b", report), report
            assert "本week" not in report and "本month" not in report, report


# ==================== ② 逾期清单（F341–F342） ====================

class TestOverdueList:
    def _overdue(self, db, n, days_ago=1):
        ids = []
        for i in range(n):
            cid = _customer(db, name=f"逾期客户{i}", i=100 + i)
            db.add_followup(customer_id=cid, type="note", content=f"逾期{i}",
                            next_date=datetime.now() - timedelta(days=days_ago + i))
            ids.append(cid)
        return ids

    def test_shows_total_and_says_it_is_truncated(self, wired):
        self._overdue(wired, 15)
        report = _report()["report"]
        head = [ln for ln in report.splitlines() if ln.startswith("## 逾期跟进")]
        assert head and "15 位" in head[0] and "10 位" in head[0], head
        assert "还有 5 位没列出来" in report, report

    def test_items_carry_customer_name_and_tier(self, wired):
        self._overdue(wired, 2)
        report = _report()["report"]
        seg = report.split("## 逾期跟进")[1].split("## 总结")[0]
        assert "逾期客户0（A级，原定" in seg, seg
        assert not re.search(r"- 客户#\d+", seg), seg

    def test_deleted_customer_gets_readable_label(self, wired):
        _customer(wired, name="正常客户", i=201)      # 库里还有人，才走"有逾期"这条分支
        cid = _customer(wired, name="将被删除", i=200)
        wired.add_followup(customer_id=cid, type="note", content="孤儿",
                           next_date=datetime.now() - timedelta(days=3))
        from sqlalchemy import text

        with wired.get_session() as s:      # 模拟存量孤儿（绕过生产删除路径，只测展示防御）
            s.execute(text("delete from re_customers where id = :i"), {"i": cid})
            s.commit()
        report = _report()["report"]
        assert f"已删除客户（id={cid}）" in report, report

    def test_no_overdue_says_so_without_cheering(self, wired):
        _customer(wired, i=4)
        _property(wired, i=4)
        report = _report()["report"]
        assert "- 暂无逾期跟进" in report, report
        assert "跟进情况良好" not in report, report


# ==================== ③ 空库与"没有已看"（F343） ====================

class TestEmptyAndNoViewings:
    def test_empty_library_says_register_first(self, wired):
        out = _report()
        assert out["success"] is True
        report = out["report"]
        assert "库里还没有客户" in report, report
        assert "跟进情况良好" not in report, report
        assert "## 逾期跟进" in report and "- 库里还没有客户" in report, report

    def test_no_done_viewing_does_not_report_low_interest(self, wired):
        cid = _customer(wired, i=5)
        pid = _property(wired, i=5)
        # 只有"待带看"，一次都没看
        wired.add_viewing(customer_id=cid, property_id=pid,
                          viewing_time=datetime.now() + timedelta(days=1),
                          status="scheduled")
        report = _report()["report"]
        assert "兴趣率暂无" in report or "还没有已看的带看" in report, report
        assert "兴趣率偏低" not in report, report

    def test_low_interest_warning_still_fires_with_data(self, wired):
        cid = _customer(wired, i=6)
        pid = _property(wired, i=6)
        for k in range(3):
            wired.add_viewing(customer_id=cid, property_id=pid,
                              viewing_time=datetime.now() - timedelta(days=k + 1),
                              status="done", result="not_interested")
        report = _report()["report"]
        assert "兴趣率偏低" in report, report


# ==================== ④ period 写法（F344–F345） ====================

class TestPeriodArgument:
    @pytest.mark.parametrize("value", ["周报", "本周", "这周", "月报", "本月", "week", "month",
                                       "WEEK", "Month", "近7天", "近 30 天"])
    def test_accepts_chinese_and_english(self, wired, value):
        out = _report(period=value)
        assert out["success"] is True, out
        assert out["period"] in ("week", "month"), out

    @pytest.mark.parametrize("value", ["乱写", "", 5, None, "季度"])
    def test_bad_value_gets_chinese_hint(self, wired, value):
        out = _report(period=value)
        assert out["success"] is not True
        err = out["error"]
        assert "周报" in err and "月报" in err, err

    def test_default_is_week(self, wired):
        assert _report()["period"] == "week"


# ==================== ⑤ 只读与对账 ====================

def test_report_does_not_write(wired):
    _customer(wired, i=7)
    _property(wired, i=7)
    before = (wired.get_stats()["total_customers"], wired.get_stats()["overdue_followups"])
    _report(period="week")
    _report(period="month")
    after = (wired.get_stats()["total_customers"], wired.get_stats()["overdue_followups"])
    assert before == after


def test_numbers_match_customer_stats(wired):
    for i in range(3):
        _customer(wired, i=10 + i)
    report = _report()["report"]
    shown = int(re.search(r"- 在跟客户：(\d+) 位", report).group(1))
    assert shown == wired.get_stats()["total_customers"] == 3, report


def test_period_stats_matches_sql(wired):
    _customer(wired, name="本期新增", i=20)
    _customer(wired, name="往期客户", i=21, created_days_ago=30)
    stats = wired.period_stats(datetime.now() - timedelta(days=7))
    assert stats["new_customers"] == 1, stats
