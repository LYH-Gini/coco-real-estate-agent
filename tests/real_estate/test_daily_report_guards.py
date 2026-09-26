"""daily_report 回归（2026-09-25 第四组 F159–F164）

背景（实测）：
① **有「今天要跟进」的客户时早报直接崩溃**：`db.daily_report()` 在 `with session` 块外访问
   `f.customer.name` → `DetachedInstanceError`（系统提示词主动让 Coco 调它，正是早报最有用那天必挂）；
② `auto_downgrade_stale_customers` 逐客户 `query(Customer).get(id)`：1.2 万流失客户 = **11.19s**；
③ 早报把**全量**流失名单塞进返回：12000 位 / **1654KB**，无上限无说明；
④ 空库只回一串 0；
⑤ `today_followups`（总数）与 `today_tasks`（只列前 10）对不上也不说明；
⑥ 描述 6 个字、没说会顺手降级。

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


def _call():
    import tools.real_estate_followup as m

    return json.loads(m.daily_report())


def _customer(db, name, tier="B", created_days_ago=0, **extra):
    return db.add_customer(name=name, tier=tier, customer_type="rent",
                           created_at=datetime.now() - timedelta(days=created_days_ago),
                           **extra)


def _due_today(db, cid, content="今天要跟进", hour=9):
    today = datetime.now().date()
    return db.add_followup(customer_id=cid, type="note", content=content,
                           next_date=datetime.combine(today, datetime.min.time()).replace(hour=hour),
                           next_time=f"{hour:02d}:00")


# ==================== ① 崩溃回归 ====================

class TestNoCrash:
    def test_task_due_today_does_not_crash(self, wired):
        """改前一有客户今天要跟进就 DetachedInstanceError"""
        cid = _customer(wired, "今天到期客户")["id"]
        _due_today(wired, cid, "带看后回访")
        out = _call()
        assert out.get("success") is True, out
        assert "Detached" not in json.dumps(out, ensure_ascii=False)

    def test_today_tasks_carry_customer_name(self, wired):
        cid = _customer(wired, "张三")["id"]
        _due_today(wired, cid, "谈续约", hour=9)
        out = _call()
        tasks = out["report"]["today_tasks"]
        assert len(tasks) == 1
        assert tasks[0]["customer"] == "张三", tasks
        assert tasks[0]["content"] == "谈续约"
        assert tasks[0]["time"] == "09:00"
        assert out["report"]["today_followups"] == 1

    def test_tasks_sorted_by_time(self, wired):
        late = _customer(wired, "下午的客户")["id"]
        early = _customer(wired, "上午的客户")["id"]
        _due_today(wired, late, "下午谈", hour=15)
        _due_today(wired, early, "上午谈", hour=8)
        tasks = _call()["report"]["today_tasks"]
        assert [t["customer"] for t in tasks] == ["上午的客户", "下午的客户"]

    def test_no_tasks_is_fine(self, wired):
        _customer(wired, "没有跟进安排")
        out = _call()
        assert out["report"]["today_tasks"] == [] and out["report"]["today_followups"] == 0


# ==================== ② 条数上限与说明 ====================

class TestLimits:
    def test_today_tasks_capped_with_note(self, wired):
        for i in range(15):
            cid = _customer(wired, f"今天要跟进{i}")["id"]
            _due_today(wired, cid)
        out = _call()
        assert out["report"]["today_followups"] == 15
        assert len(out["report"]["today_tasks"]) == 10
        assert out["message"] == "今天要跟进 15 位，这里列前 10 位"

    def test_stale_list_capped_with_total(self, wired):
        for i in range(25):
            _customer(wired, f"久未联系{i}", tier="C", created_days_ago=120)
        out = _call()
        report = out["report"]
        assert report["stale_total"] == 25
        assert len(report["stale_customers"]) == 20
        assert "共 25 位客户长期无互动，这里列最久的 20 位" in out["message"]
        assert "要我列全就说一声" in out["message"]

    def test_stale_list_not_truncated_when_small(self, wired):
        for i in range(3):
            _customer(wired, f"久未联系{i}", tier="C", created_days_ago=120)
        out = _call()
        assert len(out["report"]["stale_customers"]) == 3
        assert out["report"]["stale_total"] == 3
        assert "message" not in out or "长期无互动" not in out.get("message", "")

    def test_stale_sorted_by_longest_inactive_first(self, wired):
        _customer(wired, "刚过线", tier="C", created_days_ago=61)
        _customer(wired, "很久了", tier="C", created_days_ago=200)
        stale = _call()["report"]["stale_customers"]
        assert stale[0]["name"] == "很久了", stale

    def test_normal_case_adds_no_noise(self, wired):
        _customer(wired, "普通客户")
        assert "message" not in _call()

    def test_empty_library_says_so(self, wired):
        out = _call()
        assert out["message"] == "库里还没有客户，先登记客户再看早报"
        assert out["report"]["total_customers"] == 0


# ==================== ③ 自动降级：口径、依据、分块 ====================

class TestDowngrade:
    def test_reports_who_and_why(self, wired):
        _customer(wired, "久未联系客户", tier="S", created_days_ago=40)
        out = _call()
        item = out["report"]["downgrades"][0]
        assert item["name"] == "久未联系客户"
        assert (item["from"], item["to"]) == ("S", "A")
        assert item["days_inactive"] >= 40
        assert item["threshold"] == 5, item      # 降级依据（该等级的阈值）也要给

    def test_once_per_day_still_holds(self, wired):
        _customer(wired, "久未联系客户", tier="S", created_days_ago=40)
        _call()
        tiers = []
        for _ in range(3):
            _call()
            with wired.get_session() as s:
                tiers.append(s.execute(text("SELECT tier FROM re_customers")).fetchone()[0])
        assert set(tiers) == {"A"}, tiers        # 反复调用不再连降

    def test_batching_across_chunks(self, wired):
        """分批取客户（500/批）后，跨块边界的行为与逐条一致"""
        for i in range(1200):
            _customer(wired, f"批量流失{i}", tier="S", created_days_ago=40)
        out = _call()
        assert len(out["report"]["downgrades"]) == 1200
        with wired.get_session() as s:
            t = s.execute(text("SELECT DISTINCT tier FROM re_customers")).fetchall()
        assert [row[0] for row in t] == ["A"]
        changes = 0
        with wired.get_session() as s:
            changes = s.execute(text("SELECT COUNT(*) FROM re_customer_changes")).fetchone()[0]
        assert changes == 1200


# ==================== ④ 描述 ====================

class TestDescription:
    def test_description_states_capabilities(self):
        from tools.registry import registry

        desc = registry.get_entry("daily_report").schema["description"]
        assert len(desc) >= 40, desc
        for word in ("早报", "自动降一级", "downgrades", "stale_total", "只列最久的 20 位"):
            assert word in desc, (word, desc)

    def test_description_not_written_twice_with_drift(self):
        from tools.registry import registry
        import tools.real_estate_followup as m

        assert registry.get_entry("daily_report").schema["description"] == m.TOOLS[4]["description"]
