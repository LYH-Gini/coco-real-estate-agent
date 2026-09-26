"""定时任务脚本行为测试（2026-09-23 定时任务重设计）

覆盖四件事：
1. 逾期哨兵：按天去重、只在"有新的"时说话、S 级催过未动才升级、没逾期就静默；
2. 机会提醒：只有真有匹配才输出、同一客户×房源 7 天内不重复、没机会不叫模型；
3. 早报数据：四个板块 + 生日只列已录入生日的客户（没录入就明确写"无"，不许编造）；
4. 收工小结/周报数据：板块齐全、数字来自真实统计（不复用有模拟数据的旧工具）。
"""
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from conftest import make_customer, make_property

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
_MODULE_ORDER = (
    "coco_cron_overdue", "coco_cron_opportunity", "coco_cron_daily",
    "coco_cron_dayend", "coco_cron_weekly",
)


@pytest.fixture(scope="module")
def mods():
    """按依赖顺序载入仓库里的 cron 脚本（源文件即交付物）"""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    loaded = {}
    for name in _MODULE_ORDER:
        if name in sys.modules:
            loaded[name] = sys.modules[name]
            continue
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded


def _item(cid, name="客户", tier="A", days=1, content="", followed=False):
    return {"customer_id": cid, "name": name, "tier": tier, "days": days,
            "content": content, "followed_today": followed}


class TestOverdueSentinel:
    """逾期哨兵：一天最多两次、同一客户当天只说一次、没事就闭嘴"""

    def test_first_run_lists_all_sorted_by_tier(self, mods):
        m = mods["coco_cron_overdue"]
        now = datetime(2026, 9, 23, 10, 0)
        items = [_item(1, "小李", "B", 3), _item(2, "小王", "S", 1)]
        msg, state = m.plan_message(items, {}, now)
        assert "2 位" in msg
        assert msg.index("小王") < msg.index("小李")  # S 级排前面
        assert "逾期 3 天" in msg and "逾期 1 天" in msg
        assert state["reminded"] == {"1": 3, "2": 1}

    def test_second_run_same_day_is_silent(self, mods):
        m = mods["coco_cron_overdue"]
        now = datetime(2026, 9, 23, 17, 0)
        items = [_item(1, "小李", "B", 3)]
        _, state = m.plan_message(items, {}, datetime(2026, 9, 23, 10, 0))
        msg, _ = m.plan_message(items, state, now)
        assert msg is None  # 上午已经说过了，下午不再念同一批人

    def test_later_run_reports_only_new_arrivals(self, mods):
        m = mods["coco_cron_overdue"]
        _, state = m.plan_message([_item(1, "小李", "B", 3)], {}, datetime(2026, 9, 23, 10, 0))
        msg, _ = m.plan_message(
            [_item(1, "小李", "B", 3), _item(2, "小张", "A", 0)],
            state, datetime(2026, 9, 23, 17, 0))
        assert "新增 1 位" in msg
        assert "小张" in msg and "小李" not in msg

    def test_s_tier_reminded_without_followup_escalates_once(self, mods):
        m = mods["coco_cron_overdue"]
        items = [_item(1, "小王", "S", 2)]
        _, state = m.plan_message(items, {}, datetime(2026, 9, 23, 10, 0))
        msg, state = m.plan_message(items, state, datetime(2026, 9, 23, 17, 0))
        assert "⚠️" in msg and "小王" in msg
        msg2, _ = m.plan_message(items, state, datetime(2026, 9, 23, 18, 0))
        assert msg2 is None  # 升级只说一次

    def test_s_tier_followed_up_today_is_not_escalated(self, mods):
        m = mods["coco_cron_overdue"]
        items = [_item(1, "小王", "S", 2)]
        _, state = m.plan_message(items, {}, datetime(2026, 9, 23, 10, 0))
        msg, _ = m.plan_message(
            [_item(1, "小王", "S", 2, followed=True)], state, datetime(2026, 9, 23, 17, 0))
        assert msg is None

    def test_state_resets_next_day(self, mods):
        m = mods["coco_cron_overdue"]
        items = [_item(1, "小李", "B", 3)]
        _, state = m.plan_message(items, {}, datetime(2026, 9, 23, 10, 0))
        msg, _ = m.plan_message(items, state, datetime(2026, 9, 24, 10, 0))
        assert msg is not None and "小李" in msg

    def test_no_overdue_is_silent(self, mods):
        m = mods["coco_cron_overdue"]
        msg, state = m.plan_message([], {}, datetime(2026, 9, 23, 10, 0))
        assert msg is None and state["reminded"] == {}

    def test_collect_reads_real_db(self, mods, db):
        c = make_customer(db, name="小王", tier="S")
        db.add_followup(customer_id=c["id"], type="phone", content="问学区房",
                        next_date=datetime.now() - timedelta(days=2))
        items = mods["coco_cron_overdue"].collect_overdue(db, datetime.now())
        assert len(items) == 1
        item = items[0]
        assert (item["name"], item["tier"]) == ("小王", "S")
        assert item["days"] >= 2 and "学区房" in item["content"]

    def test_main_is_silent_on_clean_db(self, mods, db, monkeypatch, capsys):
        m = mods["coco_cron_overdue"]
        saved = {}
        monkeypatch.setattr(m, "get_db", lambda: db)
        monkeypatch.setattr(m, "load_state", lambda name: {})
        monkeypatch.setattr(m, "save_state", lambda name, state: saved.update(state))
        assert m.main() == 0
        assert capsys.readouterr().out == ""  # 空输出 = 官方调度器不投递、不叫模型
        assert saved.get("reminded") == {}

    def test_main_prints_and_remembers(self, mods, db, monkeypatch, capsys):
        m = mods["coco_cron_overdue"]
        c = make_customer(db, name="小王", tier="S")
        db.add_followup(customer_id=c["id"], type="phone", content="问学区房",
                        next_date=datetime.now() - timedelta(days=1))
        saved = {}
        monkeypatch.setattr(m, "get_db", lambda: db)
        monkeypatch.setattr(m, "load_state", lambda name: {})
        monkeypatch.setattr(m, "save_state", lambda name, state: saved.update(state))
        assert m.main() == 0
        out = capsys.readouterr().out
        assert "小王" in out and "逾期 1 天" in out
        assert saved["reminded"] == {str(c["id"]): 1}


class TestOpportunity:
    """机会提醒：脚本筛数据，有匹配才有输出（没匹配就不叫模型）"""

    def _seed_drop(self, db, price_old=2_600_000, price_new=1_980_000):
        prop = make_property(db, title="江南小区 88㎡", price=price_old, area=88.0)
        db.update_property(prop["id"], price=price_new)  # 触发调价记录
        return prop

    def test_no_opportunity_means_no_output(self, mods, db):
        m = mods["coco_cron_opportunity"]
        fresh, _ = m.collect_opportunities(db, {}, datetime.now())
        assert fresh == []
        assert m.format_data(fresh) == ""

    def test_price_drop_with_now_affordable_customer(self, mods, db):
        m = mods["coco_cron_opportunity"]
        prop = self._seed_drop(db)
        make_customer(db, name="李伟", tier="A", budget_min=1_500_000, budget_max=2_000_000)
        fresh, _ = m.collect_opportunities(db, {}, datetime.now())
        drop = [i for i in fresh if i["kind"] == "drop"]
        assert drop and drop[0]["property_id"] == prop["id"]
        assert drop[0]["matches"][0]["name"] == "李伟"
        text = m.format_data(fresh)
        assert "降价捞回" in text and "李伟" in text and f"编号{prop['id']}" in text

    def test_drop_is_not_pushed_twice_within_window(self, mods, db):
        m = mods["coco_cron_opportunity"]
        self._seed_drop(db)
        make_customer(db, name="李伟", tier="A", budget_min=1_500_000, budget_max=2_000_000)
        _, state = m.collect_opportunities(db, {}, datetime.now())
        fresh, _ = m.collect_opportunities(db, state, datetime.now() + timedelta(hours=2))
        assert fresh == []

    def test_new_property_matching_s_tier_customer(self, mods, db):
        m = mods["coco_cron_opportunity"]
        make_customer(db, name="张敏", tier="S", budget_min=3_000_000, budget_max=5_000_000)
        make_property(db, title="学府名苑 76㎡", price=4_000_000, area=100.0, rooms=3, halls=2)
        fresh, _ = m.collect_opportunities(db, {}, datetime.now())
        new = [i for i in fresh if i["kind"] == "new"]
        assert new and new[0]["matches"][0]["customer_name"] == "张敏"
        assert "新上房源" in m.format_data(fresh)

    def test_fresh_dropped_property_is_not_reported_twice(self, mods, db):
        """刚降价的房源已经在"降价捞回"里报过，不该在同一条消息里再当"新上房源"报一遍"""
        m = mods["coco_cron_opportunity"]
        prop = self._seed_drop(db)
        make_customer(db, name="李伟", tier="A", budget_min=1_500_000, budget_max=2_000_000)
        fresh, _ = m.collect_opportunities(db, {}, datetime.now())
        assert [i["kind"] for i in fresh] == ["drop"]
        text = m.format_data(fresh)
        assert text.count(f"编号{prop['id']}") == 1


class TestDailyData:
    """早报数据：四块齐全，生日只认已录入的客户"""

    def test_blocks_cover_followup_viewing_birthday(self, mods, db):
        m = mods["coco_cron_daily"]
        now = datetime.now()
        c = make_customer(db, name="小王", tier="S", birthday=f"1990-{now.month:02d}-{now.day:02d}")
        db.add_followup(customer_id=c["id"], type="phone", content="问学区房",
                        next_date=now - timedelta(days=1))
        prop = make_property(db, title="江南小区 88㎡")
        db.add_viewing(customer_id=c["id"], property_id=prop["id"],
                       viewing_time=now.replace(hour=14, minute=0, second=0, microsecond=0),
                       status="scheduled")
        out = m.build_data(db, now)
        assert "【今天要跟进】" in out and "小王（S级）逾期 1 天" in out
        assert "【今天的带看】" in out and "江南小区 88㎡" in out
        assert "【生日（仅已录入生日的客户）】" in out and "小王 生日" in out
        assert "【成交节点】" in out

    def test_birthday_block_says_none_when_not_recorded(self, mods, db):
        m = mods["coco_cron_daily"]
        make_customer(db, name="未录生日客户")
        out = m.build_data(db, datetime.now())
        assert "无（库里没有已录入生日的客户）" in out
        assert "未录生日客户 生日" not in out  # 没录入就不许出现，更不许编造


class TestDayEndData:
    """收工小结数据：今天做了什么 / 该做没做 / 明天第一件事"""

    def test_reports_activity_and_pending(self, mods, db):
        m = mods["coco_cron_dayend"]
        now = datetime.now()
        # 小王：跟进记录是昨天写的、下次跟进日已过 → 属于"今天该做没做"
        late = make_customer(db, name="小王", tier="S")
        db.add_followup(customer_id=late["id"], type="phone", content="问学区房",
                        next_date=now - timedelta(days=1), created_at=now - timedelta(days=1))
        # 小张：今天记过跟进 → 属于"今天做了什么"
        done = make_customer(db, name="小张", tier="A")
        db.add_followup(customer_id=done["id"], type="phone", content="回访")
        out = m.build_data(db, now)
        assert "【今天做了什么】" in out and "记录跟进 1 条" in out
        assert "【今天该做没做的】" in out and "小王（S级）逾期 1 天仍无跟进记录" in out
        assert "【明天第一件事】" in out and "小王（S级）要跟进" in out

    def test_never_silent_even_on_empty_day(self, mods, db):
        m = mods["coco_cron_dayend"]
        out = m.build_data(db, datetime.now())
        assert "今天库里没有任何跟进/带看/成交记录" in out
        assert "无（逾期客户今天都跟过了）" in out


class TestWeeklyData:
    """周报数据：真实统计（不复用带模拟数据的旧工具）"""

    def test_blocks_and_watch_list(self, mods, db):
        m = mods["coco_cron_weekly"]
        now = datetime.now()
        c = make_customer(db, name="小王", tier="S")
        db.add_followup(customer_id=c["id"], type="phone", content="问学区房",
                        next_date=now - timedelta(days=2))
        make_property(db, title="新上房源", price=4_000_000)
        out = m.build_data(db, now)
        assert "【统计区间】" in out
        assert "【活动量】" in out and "新增房源 1 套" in out
        assert "【客户分布（在跟）】" in out
        assert "【渠道】" in out
        assert "【该盯的人】" in out and "小王（S级）逾期 2 天未跟进" in out

    def test_channel_line_separates_customers_from_orders(self, mods, db):
        """渠道行要分开说"多少位客户成交 / 一共多少张单"（F359）

        原先写「成交 N 单」而 N 是"有成交的客户数"—— 一位客户开两张单时这行会少报，
        且与同一条周报【活动量】段的「新增成交单 N 张」（真单数）含义冲突。
        """
        m = mods["coco_cron_weekly"]
        c = make_customer(db, name="渠道客户", source="贝壳", status="active")
        for i in range(2):
            p = make_property(db, title=f"渠道房源{i}", price=1_500_000 + i)
            db.add_deal(customer_id=c["id"], property_id=p["id"], price=1_500_000 + i)
        lines = m._channel_lines(db)
        assert lines, "渠道行没出来"
        assert "贝壳：1 位客户（其中 1 位已成交、共 2 张单）" in lines[0], lines

    def test_no_simulated_funnel_numbers(self, mods, db):
        """周报只用库内真实计数；模拟口径（如"假设30%带看"）不许出现"""
        m = mods["coco_cron_weekly"]
        out = m.build_data(db, datetime.now())
        assert "假设" not in out and "模拟" not in out
