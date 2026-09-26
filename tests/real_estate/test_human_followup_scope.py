"""「最后一次联系 = 最后一次**人为**跟进」回归（2026-09-26，拍板按 B 修）

背景（实测 `scripts/t52e_cold_lead_scope_probe.py`）：
带看「档 3」在带看完成后自动写两条跟进（`type='visit'` 带看跟进 + `type='reminder'` 回访提醒，
两条都带 `source_viewing_id`）。此前「最后一次联系」把这两条也算进去，于是：

- **「客户是否被冷落」这一族被掩盖**：带看后没人跟的客户永远不进流失名单、流失预警的
  「带看后无跟进」信号在生产上永不触发（实测：带看完成的客户风险分 `None`、不在流失名单；
  删掉两条自动记录 → 立刻 60 分 + 该信号）。
- **逾期一族不能跟着改**：`get_overdue` 算的是「哪件待办到点了」，带看自动建的回访提醒
  正是它要送达的待办（档 3 的回访机制靠它）—— 排除它这条回访提醒会整条消失（实测 1 条 → 0 条）。

所以本文件钉两组行为：
① 冷落判定只看人为跟进（带看自动记录不算、人工记的带看跟进仍算）；
② **逾期口径保持原样**（自动回访提醒照旧进逾期清单）—— 这条是防止以后有人"顺手统一"掉它。
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_followup as m_fu
    import tools.real_estate_viewing as m_v

    monkeypatch.setattr(m_fu, "_get_db", lambda: db)
    monkeypatch.setattr(m_v, "_get_db", lambda: db)
    return db


def _customer(db, name="冷落客户", phone="13800001111", tier="B", created_days_ago=60):
    cid = db.add_customer(name=name, phone=phone, tier=tier,
                          customer_type="buy_second_hand")["id"]
    with db.get_session() as s:
        s.execute(text("UPDATE re_customers SET created_at = :t WHERE id = :i"),
                  {"t": datetime.now() - timedelta(days=created_days_ago), "i": cid})
        s.commit()
    return cid


def _property(db, title="冷落房源 1号楼101"):
    return db.add_property(title=title, price=1_500_000, area=80.0,
                           property_type="second_hand", status="available")["id"]


def _done_viewing(db, cid, pid, days_ago=10):
    """走真实工具路径记一次「已完成」带看（于是档 3 会写那两条自动跟进）"""
    import tools.real_estate_viewing as m_v

    when = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d 10:00")
    vid = db.add_viewing(customer_id=cid, property_id=pid,
                         viewing_time=datetime.strptime(when, "%Y-%m-%d %H:%M"))["id"]
    out = json.loads(m_v.record_viewing(viewing_id=vid, status="done", result="interested"))
    assert out["success"] is True, out
    return vid


def _auto_followups(db, cid):
    """档 3 自动写的跟进（`source_viewing_id` 不为空）—— 该列只做内部记账，不出现在工具返回里"""
    with db.get_session() as s:
        rows = s.execute(text("SELECT id, type, source_viewing_id FROM re_followups"
                              " WHERE customer_id = :i ORDER BY id"), {"i": cid}).fetchall()
    return [(r[0], r[1], r[2]) for r in rows if r[2] is not None]


def _churn(db, cid):
    return {x["customer_id"]: x for x in db.churn_risk_customers(min_risk=0)}.get(cid)


def _stale(db, cid):
    return {x["customer_id"]: x for x in db.get_stale_customers()}.get(cid)


# ==================== ① 冷落判定：带看自动记录不算联系 ====================

class TestAutoFollowupsAreNotContact:
    def test_viewing_only_customer_shows_up_as_churning(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        _done_viewing(wired, cid, pid)
        rows = _auto_followups(wired, cid)
        assert rows, "档 3 应当写了自动跟进（夹具前提）"

        row = _churn(wired, cid)
        assert row is not None, "带看后没人跟的客户必须进流失预警"
        assert "带看后无跟进" in row["signals"], row
        assert row["risk_score"] == 60, row

    def test_viewing_only_customer_is_stale(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        _done_viewing(wired, cid, pid)
        item = _stale(wired, cid)
        assert item is not None, "带看后长期没人跟的客户必须进流失名单"
        assert item["days_inactive"] >= 30, item

    def test_human_followup_clears_both(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        _done_viewing(wired, cid, pid)
        wired.add_followup(customer_id=cid, content="电话回访：客户还在考虑", type="call",
                           next_date=datetime.now() + timedelta(days=3))
        assert _churn(wired, cid) is None, "记了人为跟进就不该再报冷落"
        assert _stale(wired, cid) is None, "记了人为跟进就不该再算超期"

    def test_manual_visit_followup_counts_as_contact(self, wired):
        """人工记的「带看」跟进（不带来源带看 id）算真实联系 —— 别按 type 一刀切排除"""
        cid = _customer(wired)
        pid = _property(wired)
        wired.add_followup(customer_id=cid, content="带客户看了这套房", type="visit")
        assert _auto_followups(wired, cid) == [], "人工记的带看跟进不该带来源带看 id（夹具前提）"
        assert _churn(wired, cid) is None
        assert _stale(wired, cid) is None


# ==================== ② 逾期口径也只看人为跟进（全库一刀切，2026-09-26 拍板） ====================

class TestOverdueCountsHumanOnly:
    def test_auto_callback_reminder_is_not_overdue(self, wired):
        """带看自动建的回访提醒不再进逾期清单（修前它是那 1 条）"""
        cid = _customer(wired)
        pid = _property(wired)
        _done_viewing(wired, cid, pid)
        # 把那条自动回访提醒的到期时间挪到昨天（模拟"回访时间过了"）
        with wired.get_session() as s:
            s.execute(text("UPDATE re_followups SET next_date = :t"
                           " WHERE customer_id = :i AND type = 'reminder'"),
                      {"t": datetime.now() - timedelta(days=1), "i": cid})
            s.commit()
        overdue = [x for x in wired.get_overdue() if x.get("customer_id") == cid]
        assert overdue == [], overdue

    def test_auto_reminder_does_not_hide_an_older_human_overdue(self, wired):
        """人工跟进的逾期照旧报：自动记录不占"最新一条人为跟进"的位置"""
        cid = _customer(wired)
        pid = _property(wired)
        wired.add_followup(customer_id=cid, content="约他回电（已过期）", type="call",
                           next_date=datetime.now() - timedelta(days=2))
        _done_viewing(wired, cid, pid)
        overdue = [x for x in wired.get_overdue() if x.get("customer_id") == cid]
        assert len(overdue) == 1 and overdue[0].get("type") == "call", overdue

    def test_overdue_count_matches_the_list(self, wired):
        """逾期计数与清单必须同一口径（自动记录两边都不算）"""
        cid = _customer(wired)
        pid = _property(wired)
        _done_viewing(wired, cid, pid)
        with wired.get_session() as s:
            s.execute(text("UPDATE re_followups SET next_date = :t"
                           " WHERE customer_id = :i AND type = 'reminder'"),
                      {"t": datetime.now() - timedelta(days=1), "i": cid})
            s.commit()
        assert wired.count_overdue_followups() == len(wired.get_overdue()) == 0
        wired.add_followup(customer_id=cid, content="逾期的人工提醒", type="call",
                           next_date=datetime.now() - timedelta(days=1))
        assert wired.count_overdue_followups() == len(wired.get_overdue()) == 1
