"""带看完成后的连带动作回归（2026-09-25，选定的「档 3」，提交 `XXXX`）

带看真的完成后，Coco 连带做四件事（都按"同一条带看只做一次"）：
① 把这次带看记到那位客户的跟进里（type='visit'，含结果与客户反馈）
② 客户阶段在 潜在/意向/强意向/流失 时挪到「已看房」（已看房及更靠后的档位、已关闭的客户一律不动）
③ 安排 1 小时后回访提醒（同一条带看只留一条，见 test_record_viewing_guards.py）
④ 有反馈或"不感兴趣"时重扫该房源缺陷标签（同上）

另有两条口径（2026-09-25 拍板）：
- 带看跟进会成为该客户"最新一条跟进" → 原来的逾期提醒从此不再报（**如实说明**，不静默）；
- 「流失」的客户也推进到「已看房」（他又带看了，说明没流失）。
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_viewing as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


@pytest.fixture
def followup_wired(db, monkeypatch):
    """get_overdue / add_followup 这些跟进侧工具也要指向同一个临时库"""
    import tools.real_estate_followup as f

    monkeypatch.setattr(f, "_get_db", lambda: db)
    return db


@pytest.fixture
def cid(wired):
    return wired.add_customer(name="档三客户", phone="13800001111", tier="A",
                              customer_type="buy_second_hand")["id"]


@pytest.fixture
def pid(wired):
    return wired.add_property(title="档三房源 1号楼101", price=1500000, area=80,
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


def _followups(db, cid, followup_type=None):
    sql = "SELECT id, type, content, next_date, property_id, source_viewing_id FROM re_followups"
    params, where = {"c": cid}, ["customer_id = :c"]
    if followup_type:
        where.append("type = :t")
        params["t"] = followup_type
    with db.get_session() as s:
        return s.execute(text(sql + " WHERE " + " AND ".join(where) + " ORDER BY id"),
                         params).fetchall()


def _customer(db, cid):
    with db.get_session() as s:
        return s.execute(text("SELECT stage, status FROM re_customers WHERE id = :i"),
                         {"i": cid}).fetchone()


def _changes(db, cid):
    with db.get_session() as s:
        return s.execute(text("SELECT field, old_value, new_value FROM re_customer_changes"
                              " WHERE customer_id = :i ORDER BY id"), {"i": cid}).fetchall()


# ==================== ① 记到客户跟进里 ====================
class TestVisitFollowup:
    def test_done_writes_visit_followup(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已完成", result="感兴趣", feedback="户型还行")
        rows = _followups(wired, cid, "visit")
        assert len(rows) == 1, rows
        row = rows[0]
        assert "带看" in row[2] and "档三房源" in row[2], row
        assert "感兴趣" in row[2] and "户型还行" in row[2], row
        assert row[3] is None, row                     # 不带下次跟进时间（拍板）
        assert row[4] == pid and row[5] == vid, row    # 关联房源 + 来源带看
        assert out["followup"]["id"] == row[0], out

    def test_visit_followup_is_idempotent(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        first = _record(viewing_id=vid, status="已完成", result="感兴趣")
        again = _record(viewing_id=vid, status="已完成", result="不感兴趣")
        rows = _followups(wired, cid, "visit")
        assert len(rows) == 1, rows                                  # 不重复建
        assert again["followup"]["id"] == first["followup"]["id"], again
        assert rows[0][2] == "带看 档三房源 1号楼101，客户不感兴趣", rows   # 内容跟着更新（整句断言，"不感兴趣"里含"感兴趣"）

    def test_only_feedback_updates_existing_visit(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status="已完成")
        out = _record(viewing_id=vid, feedback="客户说再看看")
        rows = _followups(wired, cid, "visit")
        assert len(rows) == 1 and "客户说再看看" in rows[0][2], rows
        assert out["followup"]["id"] == rows[0][0], out

    def test_cancelled_writes_no_visit(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status="已取消")
        assert _followups(wired, cid, "visit") == []

    def test_receipt_mentions_followup(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已完成", result="感兴趣")
        assert out["message"].startswith("带看记录："), out["message"]
        assert "已记到这位客户的跟进里" in out["message"], out["message"]
        assert f"（带看编号 {vid}）" in out["message"], out["message"]


# ==================== ② 客户阶段推进 ====================
class TestStageAdvance:
    @pytest.mark.parametrize("start", ["lead", "interested", "strong", "lost"])
    def test_advances_from_earlier_stages(self, wired, cid, pid, start):
        wired.update_stage(cid, start)
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已完成", result="感兴趣")
        assert _customer(wired, cid)[0] == "viewed", _customer(wired, cid)
        assert out["stage_change"]["to"] == "viewed", out
        assert out["stage_change"]["from"] == start, out
        assert "客户阶段也从" in out["message"], out["message"]

    def test_lost_is_advanced_and_explained(self, wired, cid, pid):
        """拍板：标着「流失」的也推进到已看房（他又带看了，说明没流失）"""
        wired.update_stage(cid, "lost")
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已完成")
        assert _customer(wired, cid)[0] == "viewed", out
        assert out["stage_change"]["from_label"] == "流失", out

    @pytest.mark.parametrize("start", ["viewed", "negotiating", "dealing", "maintain"])
    def test_later_stages_untouched(self, wired, cid, pid, start):
        wired.update_stage(cid, start)
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已完成", result="感兴趣")
        assert _customer(wired, cid)[0] == start, (start, _customer(wired, cid))
        assert "stage_change" not in out, out
        assert "客户阶段也从" not in out["message"], out["message"]

    def test_closed_customer_untouched(self, wired, pid):
        cid = wired.add_customer(name="已关闭客户", phone="13800002222", status="closed")["id"]
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已完成", result="感兴趣")
        assert _customer(wired, cid)[0] == "lead", _customer(wired, cid)   # 阶段没动
        assert "stage_change" not in out, out

    def test_stage_change_is_traced(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status="已完成")
        assert ("stage", "lead", "viewed") in _changes(wired, cid), _changes(wired, cid)

    def test_no_advance_when_not_done(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已取消")
        assert _customer(wired, cid)[0] == "lead", out
        assert "stage_change" not in out, out


# ==================== ③ 逾期提醒不受带看影响（F215 起如实说明） ====================
class TestOverdueNotSuperseded:
    """2026-09-26（F215，拍板全库统一）：逾期按「最新一条**人为**跟进」判定，
    带看自动写的两条跟进不算 —— 于是记完带看，原来那条逾期提醒**依然有效**（不再被取代）。
    这组用例钉的就是修后的行为（修前钉的是"被取代"，见 FINDINGS F215）。"""

    def _make_overdue(self, wired, cid, pid):
        """造一条已逾期的提醒（该客户"最新一条人为跟进"是逾期状态）—— 走 db 层，避免碰到真库"""
        row = wired.add_followup(
            customer_id=cid, property_id=pid, type="call", content="约他回电（已过期）",
            next_date=datetime.now() - timedelta(days=2), next_time="09:00")
        assert row["id"], row
        latest = wired.get_latest_followup(cid)
        assert latest and str(latest["next_date"])[:10] == \
            (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"), latest

    def test_overdue_still_valid_after_viewing(self, wired, cid, pid):
        """原来那条逾期提醒仍然有效（带看记录不算人工跟进）—— 如实说明，不静默"""
        self._make_overdue(wired, cid, pid)
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已完成", result="感兴趣")
        assert any("原来那条提醒仍然有效" in w for w in out["warnings"]), out.get("warnings")

    def test_no_overdue_no_warning(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已完成")
        assert not any("仍然有效" in w for w in (out.get("warnings") or [])), out.get("warnings")

    def test_overdue_still_reported_by_get_overdue(self, wired, followup_wired, cid, pid):
        """带看自动写的跟进不算人工跟进 → 记完带看，这条旧逾期**照旧**在逾期清单里（F215 修后的口径）"""
        import tools.real_estate_followup as f

        self._make_overdue(wired, cid, pid)
        before = json.loads(f.get_overdue())
        assert any(x.get("customer_id") == cid for x in (before.get("overdue") or [])), before
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status="已完成")
        after = json.loads(f.get_overdue())
        assert any(x.get("customer_id") == cid for x in (after.get("overdue") or [])), after


# ==================== ④ 回执里的下一步建议 ====================
class TestNextStepAdvice:
    @pytest.mark.parametrize("result,expect", [
        ("interested", "找几套同小区"),
        ("not_interested", "按他的要求换几套"),
        ("pending", "过两天提醒你跟一下"),
    ])
    def test_advice_by_result(self, wired, cid, pid, result, expect):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已完成", result=result)
        assert expect in out["message"], out["message"]

    def test_advice_on_cancel(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已取消")
        assert "重新约个时间" in out["message"], out["message"]

    def test_no_advice_for_feedback_only(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, feedback="客户说再看看")
        assert "要不要" not in out["message"], out["message"]

    def test_message_has_no_internals(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        out = _record(viewing_id=vid, status="已完成", result="感兴趣", feedback="还行")
        for word in ("status", "visit", "stage", "viewed", "record_viewing", "followup"):
            assert word not in out["message"], (word, out["message"])

    def test_schema_description_states_aftercare(self):
        from tools.registry import registry

        desc = registry.get_entry("record_viewing").schema["description"]
        assert "已看房" in desc and "跟进里" in desc and "回访提醒" in desc, desc
