"""intent_score 回归（2026-09-26 第八组 F265–F269）

背景（探针 `t64a`/`t64b` 实测）：
① **分项看不到分数**：`breakdown=["等级S基础分","带看2次 +30","近7天跟进5次 +15","预算明确 +10"]`
   加起来 55 ≠ 总分 95 —— 经纪人想核账核不出来（「等级S基础分」没写几分）；
② **把"没数据"当"低意向"**：全新客户（无跟进/无带看/无预算）给 5 分，与真冷客户长得一模一样、
   一个字说明都没有 —— 要求「不许把缺数据当低意向」；
③ 描述 14 字、`customer_id` 说明只有「客户ID」；
④ 预算只有裸元值 `[2000000, 3000000]`，没有「200万-300万」的可读口径（契约 25）；
⑤ 计分逻辑埋在 `db.customer_intent_score` 里，与排名各写一套 —— 现在收成
   `_score_intent()` 一处（排名侧同源，见 `test_intent_ranking_guards.py`）。

本文件钉住修好之后的行为。**断言用真实业务值**（非整万售价、5000 元租房预算）。
"""
import json

import pytest

import tools.real_estate_intent as m_intent
from agent.real_estate_db import Deal, Followup, Viewing


@pytest.fixture
def wired(db, monkeypatch):
    monkeypatch.setattr(m_intent, "_get_db", lambda: db)
    return db


@pytest.fixture
def fixtures(wired):
    """一位高分客户（带看 2 次 + 近期跟进 + 预算 + S 级）+ 一位全新客户 + 一位已成交客户"""
    hot = wired.add_customer(name="高分客户", phone="13700000001", tier="S",
                             budget_min=2_000_000, budget_max=3_000_000,
                             customer_type="buy_second_hand")
    fresh = wired.add_customer(name="全新客户", phone="13700000002")
    dealt = wired.add_customer(name="已成交客户", phone="13700000003", tier="C")
    p1 = wired.add_property(title="意向房源1", price=1_500_000, area=90.0,
                            property_type="second_hand", status="available")
    p2 = wired.add_property(title="意向房源2", price=1_600_000, area=95.0,
                            property_type="second_hand", status="available")
    wired.add_viewing(hot["id"], p1["id"], __import__("datetime").datetime.now(), status="done")
    wired.add_viewing(hot["id"], p2["id"], __import__("datetime").datetime.now(), status="done")
    wired.add_followup(customer_id=hot["id"], content="客户想看第二套", type="note")
    # 已成交客户：直接建一条成交单
    with wired.get_session() as s:
        s.add(Deal(customer_id=dealt["id"], property_id=p1["id"], stage="deposit",
                   price=1_500_000))
        s.commit()
    return {"hot": hot, "fresh": fresh, "dealt": dealt, "p1": p1, "p2": p2}


def _score(args):
    return json.loads(m_intent.intent_score(**args))


def _sum_breakdown(items):
    """把分项里写明的 +N 加起来（用于断言"分项能加回总分"）"""
    total = 0
    for item in items or []:
        for token in str(item).replace("+", " +").split():
            if token.startswith("+"):
                total += int(token[1:])
    return total


# ==================== ① 分项可核对 ====================
class TestBreakdown:
    def test_breakdown_sums_to_score(self, fixtures):
        """分项相加必须等于总分（每一项都要写清加了几分）"""
        out = _score({"customer_id": fixtures["hot"]["id"]})
        intent = out["intent"]
        assert intent["breakdown"], intent
        assert _sum_breakdown(intent["breakdown"]) == intent["score"], intent

    def test_tier_item_carries_number(self, fixtures):
        """等级基础分那一项原先只有「等级S基础分」，现在必须带分数"""
        intent = _score({"customer_id": fixtures["hot"]["id"]})["intent"]
        assert "等级S基础分 +40" in intent["breakdown"], intent["breakdown"]

    def test_every_item_cn(self, fixtures):
        intent = _score({"customer_id": fixtures["hot"]["id"]})["intent"]
        for item in intent["breakdown"]:
            assert any("\u4e00" <= ch <= "\u9fff" for ch in item), item

    def test_score_reproducible(self, fixtures):
        """同一客户重复调用必须同分（不许有随机数/时间漂移）"""
        cid = fixtures["hot"]["id"]
        assert _score({"customer_id": cid})["intent"]["score"] == \
               _score({"customer_id": cid})["intent"]["score"]

    def test_deal_scores_100_with_reason(self, fixtures):
        """已成交直接 100 分，且分项如实说明不再累加"""
        intent = _score({"customer_id": fixtures["dealt"]["id"]})["intent"]
        assert intent["score"] == 100, intent
        assert intent["breakdown"] == ["已成交直接 100 分（不再累加）"], intent["breakdown"]

    def test_viewing_cap_30(self, wired):
        """带看加分封顶 30（4 次带看也是 +30，不是 +60）"""
        c = wired.add_customer(name="带看狂", phone="13700000010", tier="C")
        p = wired.add_property(title="封顶房源", price=1_000_000, area=80.0,
                               property_type="second_hand", status="available")
        now = __import__("datetime").datetime.now()
        for _ in range(4):
            wired.add_viewing(c["id"], p["id"], now, status="done")
        intent = _score({"customer_id": c["id"]})["intent"]
        assert "带看4次 +30" in intent["breakdown"], intent["breakdown"]
        assert _sum_breakdown(intent["breakdown"]) == intent["score"] == 35, intent

    def test_only_done_viewings_count(self, wired):
        """没完成的带看（待带看/已取消）不算意向 —— 只有 status='done' 才加分"""
        c = wired.add_customer(name="只约没看", phone="13700000011", tier="C")
        p = wired.add_property(title="没看成的房源", price=1_000_000, area=80.0,
                               property_type="second_hand", status="available")
        wired.add_viewing(c["id"], p["id"], __import__("datetime").datetime.now(),
                          status="scheduled")
        intent = _score({"customer_id": c["id"]})["intent"]
        assert intent["score"] == 5, intent
        assert intent["viewing_count"] == 0, intent

    def test_tier_missing_uses_lowest_with_cn_note(self, wired):
        """等级没填：按最低档给基础分，分项文案如实说明（不猜等级）"""
        c = wired.add_customer(name="没填等级", phone="13700000012")
        with wired.get_session() as s:
            from agent.real_estate_db import Customer
            s.query(Customer).filter(Customer.id == c["id"]).update({Customer.tier: None})
            s.commit()
        intent = _score({"customer_id": c["id"]})["intent"]
        assert intent["score"] == 5, intent
        assert "等级没填" in intent["breakdown"][0], intent["breakdown"]


# ==================== ② 缺数据 ≠ 低意向 ====================
class TestInsufficientData:
    def test_fresh_customer_is_flagged(self, fixtures):
        """全新客户必须带 data_sufficient=False 与中文说明"""
        intent = _score({"customer_id": fixtures["fresh"]["id"]})["intent"]
        assert intent["data_sufficient"] is False, intent
        assert intent["score_note"], intent
        assert "不能当成「不感兴趣」" in intent["score_note"], intent["score_note"]

    def test_fresh_customer_message_explains(self, fixtures):
        """给经纪人看的那句话也要说清（不能只留一个 5 分让他猜）"""
        out = _score({"customer_id": fixtures["fresh"]["id"]})
        assert "只反映登记等级" in out["message"], out["message"]
        assert "意向度 5 分" in out["message"], out["message"]

    def test_hot_customer_no_note(self, fixtures):
        intent = _score({"customer_id": fixtures["hot"]["id"]})["intent"]
        assert intent["data_sufficient"] is True, intent
        assert intent["score_note"] is None, intent

    def test_budget_alone_is_not_behavior_data(self, wired):
        """只填了预算（没有带看/跟进/成交）仍算"数据不足" —— 预算不是行为"""
        c = wired.add_customer(name="只有预算", phone="13700000013", tier="B",
                               budget_min=2_000_000, budget_max=3_000_000)
        intent = _score({"customer_id": c["id"]})["intent"]
        assert intent["score"] == 25, intent          # 15 基础 + 10 预算
        assert intent["data_sufficient"] is False, intent
        assert intent["score_note"], intent

    def test_followup_alone_is_enough(self, wired):
        c = wired.add_customer(name="只有跟进", phone="13700000014", tier="C")
        wired.add_followup(customer_id=c["id"], content="打过电话", type="call")
        intent = _score({"customer_id": c["id"]})["intent"]
        assert intent["data_sufficient"] is True, intent
        assert intent["score_note"] is None, intent

    def test_stale_followup_is_not_activity(self, wired):
        """8 天前的跟进不算「近 7 天活跃」，但也不等于没数据（不能当冷客户）"""
        from datetime import datetime, timedelta
        c = wired.add_customer(name="老跟进", phone="13700000015", tier="C")
        with wired.get_session() as s:
            s.add(Followup(customer_id=c["id"], content="很久以前联系过", type="call",
                           created_at=datetime.now() - timedelta(days=8)))
            s.commit()
        intent = _score({"customer_id": c["id"]})["intent"]
        assert intent["recent_followups"] == 0, intent
        assert intent["data_sufficient"] is False, intent
        assert intent["last_followup_at"], intent      # 最近跟进时间仍要能看到

    def test_system_written_records_are_not_activity(self, wired):
        """（F288，拍板）带看档 3 自动写的两条跟进不算「经纪人联系过」→ 不给那 15 分

        改前实测：一位客户只做过 2 次带看 + 1 条人工跟进，分项文案写「近7天跟进5次 +15」。
        """
        from datetime import datetime
        c = wired.add_customer(name="只有自动记录", phone="13700000016", tier="C")
        with wired.get_session() as s:
            s.add(Followup(customer_id=c["id"], content="带看完成自动写的带看跟进", type="visit",
                           created_at=datetime.now(), source_viewing_id=1))
            s.add(Followup(customer_id=c["id"], content="带看后回访提醒", type="reminder",
                           created_at=datetime.now(), source_viewing_id=1))
            s.commit()
        intent = _score({"customer_id": c["id"]})["intent"]
        assert intent["score"] == 5 and intent["recent_followups"] == 0, intent
        assert "近7天跟进" not in "".join(intent["breakdown"]), intent["breakdown"]
        assert intent["data_sufficient"] is False and intent["score_note"], intent


# ==================== ③ 编号形态与不存在 ====================
class TestIdGuards:
    def test_not_found(self, wired):
        out = _score({"customer_id": 999999})
        assert out.get("success") is not True and "不存在" in out["error"], out

    def test_bad_id_text(self, fixtures):
        out = _score({"customer_id": "abc"})
        assert out.get("success") is not True, out
        assert "客户编号" in out["error"] and "没能识别" in out["error"], out

    def test_numeric_string_id(self, fixtures):
        out = _score({"customer_id": str(fixtures["hot"]["id"])})
        assert out.get("success") is True, out

    def test_bool_id_does_not_hit_id_1(self, fixtures):
        out = _score({"customer_id": True})
        assert out.get("success") is not True, out
        assert fixtures["hot"]["id"] != 1 or "hot" not in json.dumps(out, ensure_ascii=False)

    def test_closed_customer_still_answerable(self, wired):
        """已关闭客户直接问也要能答（只是不进在跟排名）"""
        c = wired.add_customer(name="已关闭客户", phone="13700000016", tier="S")
        with wired.get_session() as s:
            from agent.real_estate_db import Customer
            s.query(Customer).filter(Customer.id == c["id"]).update({Customer.status: "closed"})
            s.commit()
        intent = _score({"customer_id": c["id"]})["intent"]
        assert intent["score"] == 40 and intent["status"] == "closed", intent


# ==================== ④ 展示口径 ====================
class TestDisplay:
    def test_budget_label_wans(self, fixtures):
        intent = _score({"customer_id": fixtures["hot"]["id"]})["intent"]
        assert intent["budget_label"] == "200万-300万", intent

    def test_budget_label_small_amount_in_yuan(self, wired):
        """租房预算 5000 元按元说，不说成「0.5万」"""
        c = wired.add_customer(name="租房客户", phone="13700000017", tier="C",
                               customer_type="rent", budget_min=5000, budget_max=8000)
        intent = _score({"customer_id": c["id"]})["intent"]
        assert intent["budget_label"] == "5000元-8000元", intent

    def test_budget_label_none_when_unfilled(self, fixtures):
        intent = _score({"customer_id": fixtures["fresh"]["id"]})["intent"]
        assert intent["budget_label"] is None, intent

    def test_message_has_no_internal_terms(self, fixtures):
        """给经纪人看的话里不许出现参数名/工具名/代码写法（契约 29）"""
        for cid in (fixtures["hot"]["id"], fixtures["fresh"]["id"], fixtures["dealt"]["id"]):
            msg = _score({"customer_id": cid})["message"]
            for bad in ("customer_id", "intent_score", "score_note", "data_sufficient", "limit"):
                assert bad not in msg, msg

    def test_message_lists_each_bonus(self, fixtures):
        msg = _score({"customer_id": fixtures["hot"]["id"]})["message"]
        assert msg.startswith("意向度 95 分："), msg
        assert "等级S基础分 +40" in msg and "带看2次 +30" in msg, msg


# ==================== ⑤ 描述与参数说明 ====================
class TestSchema:
    def _props(self, name):
        from tools.registry import registry
        return (registry.get_entry(name).schema.get("parameters") or {}).get("properties") or {}

    def test_description_explains_scoring(self):
        from tools.registry import registry
        desc = registry.get_entry("intent_score").schema["description"]
        for word in ("等级", "带看", "跟进", "预算", "已成交", "数据不足"):
            assert word in desc, desc
        assert len(desc) > 80, desc

    def test_description_points_to_ranking_tool(self):
        from tools.registry import registry
        desc = registry.get_entry("intent_score").schema["description"]
        assert "list_intent_scores" in desc, desc

    def test_param_described(self):
        desc = (self._props("intent_score").get("customer_id") or {}).get("description") or ""
        assert len(desc) > 6 and "编号" in desc, desc
