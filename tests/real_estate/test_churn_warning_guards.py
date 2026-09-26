"""churn_warning 回归（2026-09-25 第四组 F176–F179）

背景（实测）：
① `min_risk='abc'`/`null` → `TypeError`（框架兜成"执行失败"）；`0`/`-1` 又把全部有信号的客户倒出来；
② **1.2 万客户：21.8 秒 / 36001 次 SQL**（对每位客户单独查 3~4 次）；
③ 全量名单 12000 位 / 2451KB，无上限无 `truncated`；**空结果时连 `summary` 都没有**；
④ 空库与「有客户但没风险」同一句话；`message` 只列 10 条也不说。

本文件钉住修好之后的行为（含**查询次数**钉子，防 N+1 回来；以及收口批改的密文防御不复退）。
"""
import json
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, text


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_followup as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


def _call(**kwargs):
    import tools.real_estate_followup as m

    return json.loads(m.churn_warning(**kwargs))


def _customer(db, name, tier="B", created_days_ago=60, last_fu_days_ago=None, with_deal=False,
              done_viewing_days_ago=None):
    cid = db.add_customer(name=name, tier=tier, customer_type="rent", phone=f"1380000{abs(hash(name)) % 10000:04d}",
                          created_at=datetime.now() - timedelta(days=created_days_ago))["id"]
    if last_fu_days_ago is not None:
        db.add_followup(customer_id=cid, type="note", content="旧跟进",
                        created_at=datetime.now() - timedelta(days=last_fu_days_ago))
    if done_viewing_days_ago is not None:
        p = db.add_property(title=f"{name} 的房源", price=1_500_000, area=80.0,
                            property_type="second_hand", status="available")
        db.add_viewing(customer_id=cid, property_id=p["id"],
                       viewing_time=datetime.now() - timedelta(days=done_viewing_days_ago),
                       status="done")
    if with_deal:
        p = db.add_property(title=f"{name} 的成交房", price=1_500_000, area=80.0,
                            property_type="second_hand", status="available")
        db.add_deal(customer_id=cid, property_id=p["id"], stage="deposit")
    return cid


# ==================== ① 风险分与名单 ====================

class TestScoring:
    def test_signals_and_weights(self, wired):
        _customer(wired, "久未联系", last_fu_days_ago=40)          # >30 → 50
        _customer(wired, "从未跟进")                                # 无跟进 → 40
        _customer(wired, "带看后沉默", done_viewing_days_ago=10)     # 从未跟进 40 + 20 = 60
        _customer(wired, "S级久未联系", tier="S", last_fu_days_ago=40)  # 50×1.5 = 75
        scores = {c["name"]: c["risk_score"] for c in _call()["customers"]}
        assert scores == {"久未联系": 50, "从未跟进": 40, "带看后沉默": 60, "S级久未联系": 75}, scores

    def test_low_score_filtered_by_default_threshold(self, wired):
        _customer(wired, "中等风险", last_fu_days_ago=20)   # 30 分 < 默认 40
        assert _call()["customers"] == []

    def test_deal_customer_excluded(self, wired):
        _customer(wired, "已成交客户", last_fu_days_ago=40, with_deal=True)
        assert _call()["customers"] == []

    def test_active_only(self, wired):
        cid = _customer(wired, "已关闭客户", last_fu_days_ago=40)
        wired.update_customer(cid, status="closed")
        assert _call()["customers"] == []

    def test_sorted_by_score_desc(self, wired):
        _customer(wired, "低分", last_fu_days_ago=40)
        _customer(wired, "高分", tier="S", last_fu_days_ago=40)
        assert [c["name"] for c in _call()["customers"]] == ["高分", "低分"]

    def test_level_threshold(self, wired):
        _customer(wired, "60分", done_viewing_days_ago=10)         # 60 → 高危
        _customer(wired, "50分", last_fu_days_ago=40)              # 50 → 中危
        levels = {c["name"]: c["risk_level"] for c in _call()["customers"]}
        assert levels == {"60分": "高危", "50分": "中危"}, levels

    def test_winback_script_kept_for_machine(self, wired):
        _customer(wired, "久未联系", last_fu_days_ago=40)
        row = _call()["customers"][0]
        assert row["winback_script"] == "winback_long_absence"
        assert "use_template" not in _call()["message"]


# ==================== ② min_risk 归一（F176）====================

class TestMinRisk:
    @pytest.mark.parametrize("bad", ["abc", None, 0, -1, -100, ""])
    def test_bad_threshold_falls_back_to_default(self, wired, bad):
        _customer(wired, "久未联系", last_fu_days_ago=40)      # 50 分
        _customer(wired, "中等风险", last_fu_days_ago=20)      # 30 分
        out = _call(min_risk=bad)
        assert out["summary"]["total"] == 1, (bad, out["summary"])
        assert [c["name"] for c in out["customers"]] == ["久未联系"], out["customers"]

    def test_high_threshold_returns_empty_with_summary(self, wired):
        _customer(wired, "久未联系", last_fu_days_ago=40)
        out = _call(min_risk=100)
        assert out["customers"] == []
        assert out["summary"] == {"total": 0, "high_risk": 0}
        assert out["count"] == 0 and out["total"] == 0 and out["truncated"] is False

    def test_threshold_boundary_is_inclusive(self, wired):
        _customer(wired, "50分", last_fu_days_ago=40)
        assert _call(min_risk=50)["summary"]["total"] == 1
        assert _call(min_risk=51)["summary"]["total"] == 0


# ==================== ③ 形状与上限（F178）====================

class TestShape:
    def test_capped_with_total_and_truncated(self, wired):
        for i in range(25):
            _customer(wired, f"流失客户{i}", last_fu_days_ago=40)
        out = _call()
        assert len(out["customers"]) == 20
        assert out["count"] == 20 and out["total"] == 25 and out["truncated"] is True
        assert out["summary"]["total"] == 25
        assert "共 25 位客户有流失风险，这里列风险最高的 10 位" in out["message"]

    def test_no_note_when_few(self, wired):
        _customer(wired, "久未联系", last_fu_days_ago=40)
        out = _call()
        assert out["total"] == 1 and out["truncated"] is False
        assert "这里列风险最高的" not in out["message"]

    def test_empty_result_has_summary_and_counts(self, wired):
        _customer(wired, "活跃客户", last_fu_days_ago=1)
        out = _call()
        assert out["summary"] == {"total": 0, "high_risk": 0}
        assert out["count"] == 0 and out["total"] == 0 and out["truncated"] is False
        assert out["message"] == "当前无流失风险客户，保持节奏"


# ==================== ④ 空态（F179）====================

class TestEmpty:
    def test_empty_library_says_so(self, wired):
        out = _call()
        assert out["message"] == "库里还没有客户，先登记客户再看流失预警"

    def test_has_customers_but_no_risk(self, wired):
        _customer(wired, "活跃客户", last_fu_days_ago=1)
        assert _call()["message"] == "当前无流失风险客户，保持节奏"


# ==================== ⑤ 密文防御（收口批改过，别改回去）====================

class TestCipherDefense:
    def test_no_ciphertext_and_warns(self, wired):
        """库里躺着读不出来的密文（密钥不一致的真实形态）→ 不许展示密文，要给 warning

        做法照同族的 test_add_customer_guards：直接写一个 `gAAAA…` 形态的值进加密列
        （比换密钥可靠：夹具库的密钥在实例创建时就定下了）。
        """
        cid = _customer(wired, "密钥测试客户", last_fu_days_ago=40)
        with wired.get_session() as s:
            s.execute(text("UPDATE re_customers SET phone = :p WHERE id = :i"),
                      {"p": "gAAAAA" + "x" * 40, "i": cid})
            s.commit()
        out = _call()
        blob = json.dumps(out, ensure_ascii=False)
        assert "gAAAA" not in blob, blob[:300]
        assert out.get("warning_key_mismatch"), sorted(out.keys())
        assert out.get("cipher_fields"), sorted(out.keys())


# ==================== ⑥ 性能钉子：不许回到 N+1（F177b）====================

class TestQueryCount:
    def test_batched_queries(self, wired):
        """改前每位客户 3~4 次查询（1.2 万客户 = 36001 次）；现在应是常数级"""
        for i in range(300):
            _customer(wired, f"流失客户{i}", last_fu_days_ago=40)
        counter = {"n": 0}

        @event.listens_for(wired.engine, "before_cursor_execute")
        def _count(conn, cursor, statement, params, context, executemany):
            counter["n"] += 1

        try:
            out = _call()
        finally:
            event.remove(wired.engine, "before_cursor_execute", _count)
        assert out["total"] == 300
        assert counter["n"] <= 20, f"查询次数 {counter['n']}（疑似 N+1 回来了）"


# ==================== ⑦ 描述 ====================

class TestDescription:
    def test_description_states_counts_and_cap(self):
        from tools.registry import registry

        schema = registry.get_entry("churn_warning").schema
        desc = schema["description"]
        for word in ("summary.total", "summary.high_risk", "truncated", "只列风险最高的 20 位"):
            assert word in desc, (word, desc)
        assert "按默认 40" in schema["parameters"]["properties"]["min_risk"]["description"]
