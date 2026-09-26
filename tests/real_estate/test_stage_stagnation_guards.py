"""stage_stagnation 回归（2026-09-25 第四组 F173–F175）

背景（实测）：
① **阶段英文名直出**：`在「viewed」已停留 20 天`、条目里也是 `stage: "viewed"`（经纪人看到内部代号）；
② **1.2 万滞留客户 → 11999 位 / 2170KB**，没有 `count`/`total`/`truncated`/上限；
   而且 **12003 次 SQL / 7.3 秒**（对每位客户单独查一条阶段变更 = N+1）；
③ 空库与「有客户但没人滞留」同一句话（`无阶段滞留客户，节奏健康`）。

本文件钉住修好之后的行为 —— 包含一条**查询次数**的钉子，防止 N+1 回来。
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, text


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_followup as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


def _call():
    import tools.real_estate_followup as m

    return json.loads(m.stage_stagnation())


def _customer(db, name, stage="lead", tier="A", created_days_ago=0,
              stage_changed_days_ago=None, with_change_record=True):
    cid = db.add_customer(name=name, tier=tier, customer_type="rent",
                          created_at=datetime.now() - timedelta(days=created_days_ago))["id"]
    if stage != "lead":
        if with_change_record:
            db.update_customer(cid, stage=stage)
            with db.get_session() as s:
                s.execute(text("UPDATE re_customer_changes SET created_at = :t "
                               "WHERE customer_id = :i AND field = 'stage'"),
                          {"t": datetime.now() - timedelta(days=(stage_changed_days_ago or 0)), "i": cid})
                s.execute(text("UPDATE re_customers SET created_at = :t WHERE id = :i"),
                          {"t": datetime.now() - timedelta(days=created_days_ago), "i": cid})
                s.commit()
        else:
            # 历史台账：直接改库 → 没有阶段变更记录（走"创建时间兜底"这条口径）
            with db.get_session() as s:
                s.execute(text("UPDATE re_customers SET stage = :st WHERE id = :i"),
                          {"st": stage, "i": cid})
                s.commit()
    return cid


# ==================== ① 阶段给中文 ====================

class TestStageLabel:
    def test_items_carry_chinese_label(self, wired):
        _customer(wired, "已看房滞留", stage="viewed", created_days_ago=40, stage_changed_days_ago=20)
        item = _call()["alerts"][0]
        assert item["stage"] == "viewed"          # 机器读的原值保留
        assert item["stage_label"] == "已看房"

    def test_message_uses_chinese_not_enum(self, wired):
        _customer(wired, "强意向滞留", stage="strong", created_days_ago=30, stage_changed_days_ago=10)
        msg = _call()["message"]
        assert "「强意向」" in msg, msg
        for enum in ("strong", "viewed", "negotiating"):
            assert enum not in msg, (enum, msg)

    def test_title_has_no_emoji(self, wired):
        _customer(wired, "强意向滞留", stage="strong", created_days_ago=30, stage_changed_days_ago=10)
        assert "⏰" not in _call()["message"]


# ==================== ② 形状与上限 ====================

class TestShapeAndLimit:
    def test_capped_with_total_and_truncated(self, wired):
        for i in range(25):
            _customer(wired, f"滞留客户{i}", stage="strong", created_days_ago=60,
                      stage_changed_days_ago=30)
        out = _call()
        assert len(out["alerts"]) == 20
        assert out["count"] == 20 and out["total"] == 25 and out["truncated"] is True
        assert "共 25 位客户阶段停留超时，这里列最久的 20 位（要我列全就说一声）" in out["message"]

    def test_small_list_not_truncated(self, wired):
        _customer(wired, "滞留客户", stage="strong", created_days_ago=60, stage_changed_days_ago=30)
        out = _call()
        assert out["count"] == 1 and out["total"] == 1 and out["truncated"] is False

    def test_sorted_by_days_desc(self, wired):
        _customer(wired, "刚超一点", stage="strong", created_days_ago=60, stage_changed_days_ago=8)
        _customer(wired, "卡很久了", stage="strong", created_days_ago=60, stage_changed_days_ago=40)
        assert [a["name"] for a in _call()["alerts"]] == ["卡很久了", "刚超一点"]


# ==================== ③ 判定口径（改动后仍与老口径一致）====================

class TestRules:
    def test_change_time_wins_over_created_at(self, wired):
        """有阶段变更记录 → 按变更时间算（客户创建更早也不算滞留）"""
        _customer(wired, "变更记录优先", stage="strong", created_days_ago=100,
                  stage_changed_days_ago=3)
        assert _call()["alerts"] == []

    def test_created_at_fallback_without_change_record(self, wired):
        """历史台账没有变更记录 → 按客户创建时间兜底"""
        _customer(wired, "老台账", stage="strong", created_days_ago=15, with_change_record=False)
        alerts = _call()["alerts"]
        assert [a["name"] for a in alerts] == ["老台账"]
        assert alerts[0]["days_in_stage"] == 15

    @pytest.mark.parametrize("stage,days,should_alert", [
        ("strong", 7, False), ("strong", 8, True),      # 阈值 7：大于才算
        ("viewed", 14, False), ("viewed", 15, True),    # 阈值 14
        ("negotiating", 7, False), ("negotiating", 8, True),
        ("lead", 100, False), ("interested", 100, False),   # 不在规则里的阶段永远不报
    ])
    def test_thresholds(self, wired, stage, days, should_alert):
        _customer(wired, f"{stage}-{days}", stage=stage, created_days_ago=200,
                  stage_changed_days_ago=days,
                  with_change_record=stage != "lead")
        names = [a["name"] for a in _call()["alerts"]]
        assert (f"{stage}-{days}" in names) is should_alert, (stage, days, names)

    def test_only_active_customers(self, wired):
        cid = _customer(wired, "已关闭客户", stage="strong", created_days_ago=60,
                        stage_changed_days_ago=30)
        wired.update_customer(cid, status="closed")
        assert _call()["alerts"] == []


# ==================== ④ 空态 ====================

class TestEmpty:
    def test_empty_library_says_so(self, wired):
        assert _call()["message"] == "库里还没有客户，先登记客户再看阶段滞留"

    def test_no_stagnation_keeps_original_wording(self, wired):
        _customer(wired, "活跃客户", created_days_ago=3)
        assert _call()["message"] == "无阶段滞留客户，节奏健康"


# ==================== ⑤ 性能钉子：不许回到 N+1 ====================

class TestQueryCount:
    def test_uses_a_few_queries_not_one_per_customer(self, wired):
        """改前每位客户一次查询（1.2 万客户 = 12003 次）；现在应是常数级"""
        for i in range(300):
            _customer(wired, f"滞留客户{i}", stage="strong", created_days_ago=60,
                      stage_changed_days_ago=30)
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


# ==================== ⑥ 描述 ====================

class TestDescription:
    def test_description_states_capabilities(self):
        from tools.registry import registry

        desc = registry.get_entry("stage_stagnation").schema["description"]
        assert len(desc) >= 30, desc
        for word in ("强意向", "已看房", "谈判", "truncated", "只列最久的 20 位"):
            assert word in desc, (word, desc)

    def test_tool_has_single_description_site(self):
        """它不在模块 TOOLS 列表里（单独注册），描述只有一处写点 —— 防"双写漂移"的老毛病"""
        import tools.real_estate_followup as m

        assert all(tool.get("name") != "stage_stagnation" for tool in m.TOOLS)
