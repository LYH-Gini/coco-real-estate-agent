"""list_intent_scores 回归（2026-09-26 第八组 F270–F276）

背景（探针 `t64b` 实测，30 位在跟客户）：
① **"排名"其实只排了最新登记的 N 位**：先取最新 20 位客户再算分排序，于是库里分数最高的
   3 位老客户（各 80 分）在默认调用里**一位都不出现**，名单 20 位全是 5 分的无记录新客户。
   经纪人照这个榜单干活会漏掉手上最热的客户；
② 逐客户算分（N+1）：**20 位 = 81 次 SQL / 0.06s，200 位 = 801 次 / 0.49s**；
③ 只有 `count`，没有 `total`/`truncated`，被截断也不说；
④ `tier` 传乱值 → 静默空列表（上层以为"没有这个等级的客户"）；
⑤ 同分靠数据库返回次序碰巧稳定，没有明确的二次排序；
⑥ 描述 9 字、`tier` 参数说明是空串。

本文件钉住修好之后的行为（含排序可复现与 SQL 次数上限两条钉子）。
"""
import json

import pytest
from sqlalchemy import event

import tools.real_estate_intent as m_intent
from agent.real_estate_db import Customer


@pytest.fixture
def wired(db, monkeypatch):
    monkeypatch.setattr(m_intent, "_get_db", lambda: db)
    return db


def _call(db, **args):
    return db and json.loads(m_intent.list_intent_scores(**args))


@pytest.fixture
def hot_and_cold(wired):
    """3 位高分老客户（先建）+ 27 位无记录新客户（后建）—— 复现"老客户被截断"的现场"""
    hot = [wired.add_customer(name=f"老客户{i}", phone=f"136000000{i:02d}", tier="S")
           for i in range(3)]
    p = wired.add_property(title="排名房源", price=1_500_000, area=90.0,
                           property_type="second_hand", status="available")
    from datetime import datetime
    for c in hot:
        wired.add_viewing(c["id"], p["id"], datetime.now(), status="done")
        wired.add_followup(customer_id=c["id"], content="老客户跟进", type="note")
    cold = [wired.add_customer(name=f"新客户{i:02d}", phone=f"135000000{i:02d}")
            for i in range(27)]
    return {"hot": hot, "cold": cold, "property": p}


# ==================== ① 排名口径（本组的核心回归）====================
class TestRankingScope:
    def test_top_scorers_are_ranked_even_when_oldest(self, hot_and_cold, wired):
        """分数最高的几位必须在"排名"里 —— 不能因为登记早就被截掉"""
        out = _call(wired)
        ids = [row["customer_id"] for row in out["rankings"]]
        hot_ids = [c["id"] for c in hot_and_cold["hot"]]
        assert set(hot_ids).issubset(set(ids)), (ids, hot_ids)
        assert ids[:3] == sorted(hot_ids, reverse=True), ids[:3]

    def test_total_counts_whole_book(self, hot_and_cold, wired):
        """total 是全部参与排名的在跟客户，不是本次条数"""
        out = _call(wired)
        assert out["total"] == 30, out
        assert out["count"] == 20, out
        assert out["truncated"] is True, out

    def test_limit_reaches_deep_ranks(self, hot_and_cold, wired):
        out = _call(wired, limit=200)
        assert out["count"] == out["total"] == 30, out
        assert out["truncated"] is False, out

    def test_closed_customer_not_ranked(self, wired):
        c = wired.add_customer(name="已关闭的", phone="13700000090", tier="S")
        wired.add_customer(name="在跟的", phone="13700000091", tier="C")
        with wired.get_session() as s:
            s.query(Customer).filter(Customer.id == c["id"]).update({Customer.status: "closed"})
            s.commit()
        out = _call(wired)
        assert [row["customer_name"] for row in out["rankings"]] == ["在跟的"], out


# ==================== ② 形状三件齐与空态 ====================
class TestShape:
    def test_three_keys(self, hot_and_cold, wired):
        out = _call(wired)
        for key in ("count", "total", "truncated", "message"):
            assert key in out, out

    def test_truncation_message(self, hot_and_cold, wired):
        out = _call(wired)
        assert "还有 10 位没列出来" in out["message"], out["message"]
        assert "共 30 位在跟客户" in out["message"], out["message"]

    def test_no_truncation_message_when_all_listed(self, hot_and_cold, wired):
        out = _call(wired, limit=200)
        assert "没列出来" not in out["message"], out["message"]

    def test_empty_book(self, wired):
        """没有任何客户 → 说"还没有客户"，不说成"没有符合条件的"""
        out = _call(wired)
        assert out["count"] == out["total"] == 0, out
        assert "还没有在跟客户" in out["message"], out["message"]
        assert out["rankings"] == []

    def test_empty_tier_is_a_different_sentence(self, wired):
        """有客户但没有该等级 → 与"没有客户"分开说"""
        wired.add_customer(name="只有C级", phone="13700000092", tier="C")
        out = _call(wired, tier="S")
        assert out["total"] == 0, out
        assert "还没有S级在跟客户" in out["message"], out["message"]


# ==================== ③ 排序稳定性 ====================
class TestOrdering:
    def test_ties_broken_by_last_followup_then_id(self, wired):
        """同分：最近有跟进（人为）的排前面；都没有则按客户编号降序"""
        from datetime import datetime, timedelta
        a = wired.add_customer(name="同分甲", phone="13700000093", tier="B")
        b = wired.add_customer(name="同分乙", phone="13700000094", tier="B")
        c = wired.add_customer(name="同分丙", phone="13700000095", tier="B")
        with wired.get_session() as s:
            from agent.real_estate_db import Followup
            s.add(Followup(customer_id=b["id"], content="最近联系过", type="call",
                           created_at=datetime.now() - timedelta(days=3)))
            s.commit()
        out = _call(wired)
        ids = [row["customer_id"] for row in out["rankings"]]
        assert ids[0] == b["id"], ids            # 有最近跟进的排最前
        assert ids[1:] == sorted([a["id"], c["id"]], reverse=True), ids  # 剩下按编号降序

    def test_auto_viewing_records_do_not_break_ties(self, wired):
        """同分时，「最近跟进」只看人为跟进：只有自动记录的排在后面

        （带看档 3 自动写的 `type='visit'` 带 `source_viewing_id`，不算经纪人联系过客户 ——
        口径与流失预警族同一个 `_only_human_followups`。）
        """
        from datetime import datetime, timedelta
        auto = wired.add_customer(name="只有自动记录", phone="13700000096", tier="B")
        human = wired.add_customer(name="有人为跟进", phone="13700000097", tier="B")
        with wired.get_session() as s:
            from agent.real_estate_db import Followup
            s.add(Followup(customer_id=auto["id"], content="带看完成自动写的", type="visit",
                           created_at=datetime.now(), source_viewing_id=1))
            s.add(Followup(customer_id=human["id"], content="三天前打过电话", type="call",
                           created_at=datetime.now() - timedelta(days=3)))
            s.commit()
        out = _call(wired)
        rows = {row["customer_id"]: row for row in out["rankings"]}
        assert rows[auto["id"]]["score"] == 15, rows[auto["id"]]      # 无任何人为跟进 → 不加那 15 分
        assert rows[human["id"]]["score"] == 30, rows[human["id"]]
        assert rows[auto["id"]]["last_followup_at"] is None, rows[auto["id"]]
        assert [row["customer_id"] for row in out["rankings"]] == [human["id"], auto["id"]], out

    def test_auto_records_do_not_count_as_followups(self, wired):
        """（2026-09-26 拍板 F288）「近 7 天跟进」这一项**只看人为跟进**：

        带看档 3 自动写的带看跟进 + 回访提醒不算"经纪人联系过客户"。改前实测：一位客户只做过
        2 次带看 + 1 条人工跟进，分项文案写的是「近7天跟进5次 +15」（5 条里 4 条是系统写的）。
        """
        from datetime import datetime
        c = wired.add_customer(name="只有自动记录", phone="13700000098", tier="C")
        with wired.get_session() as s:
            from agent.real_estate_db import Followup
            s.add(Followup(customer_id=c["id"], content="带看完成自动写的带看跟进", type="visit",
                           created_at=datetime.now(), source_viewing_id=1))
            s.add(Followup(customer_id=c["id"], content="带看后回访提醒", type="reminder",
                           created_at=datetime.now(), source_viewing_id=1))
            s.commit()
        out = json.loads(m_intent.intent_score(customer_id=c["id"]))
        intent = out["intent"]
        assert intent["score"] == 5, intent                       # 5 基础，系统记录不给那 15 分
        assert "近7天跟进" not in "".join(intent["breakdown"]), intent["breakdown"]
        assert intent["recent_followups"] == 0, intent
        assert intent["data_sufficient"] is False and intent["score_note"], intent

    def test_human_followup_still_counts(self, wired):
        """人为跟进照旧 +15，且文案里的次数就是人为跟进条数"""
        from datetime import datetime
        c = wired.add_customer(name="人工跟进过的", phone="13700000099", tier="C")
        with wired.get_session() as s:
            from agent.real_estate_db import Followup
            s.add(Followup(customer_id=c["id"], content="打电话聊过", type="call",
                           created_at=datetime.now()))
            s.add(Followup(customer_id=c["id"], content="带看完成自动写的", type="visit",
                           created_at=datetime.now(), source_viewing_id=1))
            s.commit()
        intent = json.loads(m_intent.intent_score(customer_id=c["id"]))["intent"]
        assert intent["score"] == 20, intent                       # 5 基础 + 15 人为跟进
        assert "近7天跟进1次 +15" in intent["breakdown"], intent["breakdown"]

    def test_order_reproducible(self, hot_and_cold, wired):
        first = [row["customer_id"] for row in _call(wired)["rankings"]]
        second = [row["customer_id"] for row in _call(wired)["rankings"]]
        assert first == second, (first, second)


# ==================== ④ 等级筛选 ====================
class TestTierFilter:
    def test_bad_tier_gives_cn_hint(self, wired):
        """乱值不许静默空列表"""
        out = _call(wired, tier="乱写的等级")
        assert out.get("success") is not True, out
        assert "客户等级筛选没能识别" in out["error"], out
        assert "S / A / B / C" in out["error"], out

    def test_tier_recognized_case_insensitively(self, wired):
        wired.add_customer(name="S级客户", phone="13700000098", tier="S")
        wired.add_customer(name="C级客户", phone="13700000099", tier="C")
        out = _call(wired, tier="s")
        assert [row["customer_name"] for row in out["rankings"]] == ["S级客户"], out

    def test_empty_string_tier_means_no_filter(self, wired):
        wired.add_customer(name="谁", phone="13700000100", tier="C")
        out = _call(wired, tier="")
        assert out["total"] == 1, out


# ==================== ⑤ 缺数据标注 ====================
class TestInsufficientFlag:
    def test_rows_carry_flag_and_message_counts(self, hot_and_cold, wired):
        out = _call(wired)
        by_id = {row["customer_id"]: row for row in out["rankings"]}
        hot_ids = [c["id"] for c in hot_and_cold["hot"]]
        for cid in hot_ids:
            assert by_id[cid]["data_sufficient"] is True, by_id[cid]
            assert by_id[cid]["score_note"] is None, by_id[cid]
        cold_listed = [row for row in out["rankings"] if row["customer_id"] not in hot_ids]
        assert len(cold_listed) == 17, out
        assert all(row["data_sufficient"] is False and row["score_note"] for row in cold_listed)
        assert out["insufficient_count"] == 17, out
        assert "17 位还没有带看或跟进记录，分数仅供参考" in out["message"], out["message"]


# ==================== ⑥ 性能：不许逐客户查库 ====================
class TestQueryCount:
    def test_sql_count_is_bounded(self, hot_and_cold, wired):
        """200 位客户也必须是一次聚合（原先 801 次 SQL）——上限 6 次"""
        statements = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(wired.engine, "before_cursor_execute", _rec)
        try:
            out = _call(wired, limit=200)
        finally:
            event.remove(wired.engine, "before_cursor_execute", _rec)
        assert out["count"] == 30, out
        assert len(statements) <= 6, f"{out['count']} 位客户用了 {len(statements)} 次 SQL"


# ==================== ⑦ 描述与参数说明 ====================
class TestSchema:
    def test_description_explains_scope_and_ties(self):
        from tools.registry import registry
        desc = registry.get_entry("list_intent_scores").schema["description"]
        assert len(desc) > 60, desc
        for word in ("全库在跟客户", "前 20 位", "同分", "共几位"):
            assert word in desc, (word, desc)

    def test_tier_param_described(self):
        from tools.registry import registry
        props = (registry.get_entry("list_intent_scores").schema.get("parameters") or {}).get("properties") or {}
        desc = (props.get("tier") or {}).get("description") or ""
        assert "S/A/B/C" in desc, desc
