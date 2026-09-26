"""全库「给经纪人看的话」文案守卫（2026-09-25 实测指出后加）

问题形态（原话：「经纪人看了不知道是什么意思，也不知道要怎样操作」）：
给经纪人看的那句话里混进了**对模型说的话**或**内部口径**——参数名（`limit`/`force`/`document`）、
内部调用写法（`update_customer(customer_id=12, ...)`）、内部工具名（`intent_score`/`churn_warning`）、
以及"请把 limit 调大"这类把操作甩给经纪人的句子。

做法（按仓库规矩**不能用读源码文本的测试**）：把每个能产出这些话术的路径**真的跑一遍**，
断言 `message` / `error` / `warnings` / `notes` 这几处"给人看"的文本里不出现上述内部口径。

范围说明：`ask`、`note_*`、description、参数说明是**给模型看的**（按契约 16 只约束"给经纪人看的话"），
本守卫不覆盖它们。
"""
import json
import re
from datetime import datetime, timedelta

from sqlalchemy import text
from types import SimpleNamespace

import pytest

FORBIDDEN_PARAM_WORDS = ["limit", "force=", "document", "dry_run", "merge=", "fill_missing_only",
                         "top_n", "min_risk", "include_closed", "poster_title", "allow_missing",
                         "keep_id", "session_id", "task_id", "count_scope", "truncated",
                         "total=", "count=", "参数", "本工具"]
FORBIDDEN_TOOL_NAMES = ["update_customer", "add_customer", "update_property", "add_property",
                        "update_owner", "use_template", "get_overdue", "add_followup",
                        "list_customers", "match_property", "deduplicate_properties",
                        "generate_property_poster", "suggest_poster_titles", "churn_warning",
                        "intent_score", "exclusive_expiring", "schedule_reminder",
                        "record_viewing", "start_deal", "get_property_detail", "search_property",
                        "get_customer_form", "get_property_form", "stale_check", "daily_report"]
FORBIDDEN_PERSONAL = ["Ava", "艾娃"]  # 助手名字不该出现在给经纪人看的文本里
CODE_CALL = re.compile(r"[a-z_]{4,}\s*\(")


def assert_agent_facing(label, out):
    """out 是工具返回的 dict：检查所有"给人看"的文本"""
    texts = []
    for key in ("message", "error"):
        if isinstance(out.get(key), str):
            texts.append((key, out[key]))
    for key in ("warnings", "notes"):
        for i, item in enumerate(out.get(key) or []):
            if isinstance(item, str):
                texts.append((f"{key}[{i}]", item))
    assert texts, f"{label}：这条用例没产出任何给人看的文本，守卫无效"
    for key, text in texts:
        blob = text
        for word in FORBIDDEN_PARAM_WORDS:
            assert word not in blob, f"{label}[{key}] 出现内部口径「{word}」：{blob}"
        for name in FORBIDDEN_TOOL_NAMES:
            assert name not in blob, f"{label}[{key}] 出现内部工具名「{name}」：{blob}"
        for name in FORBIDDEN_PERSONAL:
            assert name not in blob, f"{label}[{key}] 出现助手名字「{name}」：{blob}"
        assert not CODE_CALL.search(blob), f"{label}[{key}] 出现代码写法：{blob}"


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_analytics as m_an
    import tools.real_estate_customer as m_cu
    import tools.real_estate_followup as m_fu
    import tools.real_estate_owner as m_ow
    import tools.real_estate_poster as m_po
    import tools.real_estate_property as m_pr

    for m in (m_an, m_cu, m_fu, m_ow, m_po, m_pr):
        monkeypatch.setattr(m, "_get_db", lambda _db=db: _db)
    return SimpleNamespace(db=db, an=m_an, cu=m_cu, fu=m_fu, ow=m_ow, po=m_po, pr=m_pr)


def _load(raw):
    return json.loads(raw)


def _customers(db, n, name="客户", **extra):
    return [db.add_customer(name=f"{name}{i}", customer_type="rent", tier="B", **extra)
            for i in range(n)]


def _properties(db, n, **extra):
    return [db.add_property(title=f"房源{i}", price=1_500_000 + i, area=80.0 + i,
                            property_type="second_hand", status="available", **extra)
            for i in range(n)]


# ==================== 截断类文案（6 条列表工具 + 4 条中文明细）====================

def case_list_customers(w):
    _customers(w.db, 25)
    return _load(w.cu.list_customers(limit=20))


def case_search_property(w):
    _properties(w.db, 25)
    return _load(w.pr.search_property(limit=20))


def case_list_owners(w):
    for i in range(60):
        w.db.add_owner(name=f"房东{i}", phone=f"1380000{i:04d}")
    return _load(w.ow.list_owners(limit=50))


def case_find_person_by_name(w):
    _customers(w.db, 25, name="欧阳")
    return _load(w.ow.find_person_by_name(name="欧阳", limit=20))


def case_owner_portfolio(w):
    o = w.db.add_owner(name="房东甲", phone="13800001111")
    for i in range(55):
        p = w.db.add_property(title=f"名下房源{i}", price=1_500_000, area=80.0,
                              property_type="second_hand", status="available")
        w.db.update_property(p["id"], owner_id=o["id"])
    return _load(w.ow.owner_portfolio(owner_id=o["id"], limit=50))


def case_exclusive_expiring(w):
    soon = datetime.now() + timedelta(days=10)
    for i in range(25):
        p = w.db.add_property(title=f"独家房源{i}", price=1_500_000, area=80.0,
                              property_type="second_hand", status="available")
        w.db.update_property(p["id"], exclusive_until=soon)
    return _load(w.ow.exclusive_expiring(days=30, limit=20))


def case_customer_change_history(w):
    c = w.db.add_customer(name="变更客户", customer_type="rent")
    for i in range(25):
        w.db.update_customer(c["id"], notes=f"第{i}次改动")
    return _load(w.cu.customer_change_history(customer_id=c["id"], limit=20))


def case_referral_stats(w):
    for i in range(25):
        c = w.db.add_customer(name=f"介绍人{i}", customer_type="rent")
        w.db.add_referral(referrer_customer_id=c["id"], referred_name=f"被介绍人{i}")
    return _load(w.cu.referral_stats(limit=20))


def case_get_followups(w):
    c = w.db.add_customer(name="跟进客户", customer_type="rent")
    now = datetime.now()
    for i in range(25):
        w.db.add_followup(customer_id=c["id"], type="note", content=f"第{i}条",
                          created_at=now + timedelta(minutes=i))
    return _load(w.fu.get_followups(customer_id=c["id"], limit=20))


def case_get_overdue(w):
    now = datetime.now()
    for i in range(55):
        c = w.db.add_customer(name=f"逾期客户{i}", customer_type="rent")
        w.db.add_followup(customer_id=c["id"], type="note", content="逾期",
                          created_at=now - timedelta(days=5),
                          next_date=now - timedelta(days=3))
    return _load(w.fu.get_overdue(limit=50))


# ==================== 判重 / 撞号 / 只补空缺 ====================

def case_add_customer_duplicate(w):
    w.db.add_customer(name="张三", phone="13800001111", customer_type="rent")
    return _load(w.cu.add_customer(name="张三", phone="13800001111", customer_type="rent"))


def case_update_customer_phone_conflict(w):
    a = w.db.add_customer(name="张三", phone="13800001111", customer_type="rent")
    b = w.db.add_customer(name="李四", phone="13800002222", customer_type="rent")
    return _load(w.cu.update_customer(customer_id=b["id"], phone="13800001111"))


def case_add_owner_duplicate(w):
    w.db.add_owner(name="房东甲", phone="13800003333")
    return _load(w.ow.add_owner(name="房东甲", phone="13800003333"))


def case_add_property_duplicate(w):
    args = dict(title="滨海华庭 1-1802", community="滨海华庭",
                price=1_500_000, area=80.0, property_type="second_hand")
    w.db.add_property(**args)
    return _load(w.pr.add_property(**args))


def case_deduplicate_properties(w):
    for _ in range(2):
        w.db.add_property(title="滨海华庭 1-1802", community="滨海华庭",
                          price=1_500_000, area=80.0, property_type="second_hand",
                          status="available")
    return _load(w.pr.deduplicate_properties(dry_run=True))


def case_update_customer_stage_warning(w):
    c = w.db.add_customer(name="流失客户", customer_type="rent", stage="strong")
    return _load(w.cu.update_customer_stage(customer_id=c["id"], stage="lost"))


# ==================== 详情 / 简报 ====================

def case_get_property_detail_without_owner(w):
    p = w.db.add_property(title="无业主房源", price=1_500_000, area=80.0,
                          property_type="second_hand", status="available")
    return _load(w.pr.get_property_detail(property_id=p["id"]))


def case_schedule_reminder(w):
    """设提醒的三条话术：正常设置 / 重复设置 / 时间已过（warnings）"""
    c = w.db.add_customer(name="提醒客户", customer_type="rent")
    soon = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    past = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    first = _load(w.fu.schedule_reminder(customer_id=c["id"], date=soon, time="09:30"))
    dup = _load(w.fu.schedule_reminder(customer_id=c["id"], date=soon, time="09:30"))
    old_one = _load(w.fu.schedule_reminder(customer_id=c["id"], date=past, time="09:00"))
    return {
        "message": "｜".join(filter(None, [first.get("message"), dup.get("message"), old_one.get("message")])),
        "warnings": (first.get("warnings") or []) + (dup.get("warnings") or []) + (old_one.get("warnings") or []),
    }


def case_daily_report(w):
    """早报的三条说明句：今天要跟进被截断 / 流失名单被截断（正常情况不加说明）"""
    today = datetime.now().date()
    for i in range(12):
        c = w.db.add_customer(name=f"今天要跟进{i}", customer_type="rent")
        w.db.add_followup(customer_id=c["id"], type="note", content=f"第{i}条",
                          next_date=datetime.combine(today, datetime.min.time()).replace(hour=9),
                          next_time="09:00")
    for i in range(25):
        w.db.add_customer(name=f"久未联系{i}", tier="C", customer_type="rent",
                          created_at=datetime.now() - timedelta(days=120))
    out = _load(w.fu.daily_report())
    empty = _load(w.fu.daily_report())          # 再调一次，确认不重复降级/不飘
    return {"message": "｜".join(filter(None, [out.get("message"), empty.get("message")])),
            "warnings": (out.get("warnings") or []) + (empty.get("warnings") or [])}


def case_midday_check(w):
    """午间检查的说明句：逾期被截断 / 流失名单被截断"""
    for i in range(7):
        c = w.db.add_customer(name=f"逾期客户{i}", customer_type="rent")
        w.db.add_followup(customer_id=c["id"], type="note", content=f"第{i}条",
                          created_at=datetime.now() - timedelta(days=3),
                          next_date=datetime.now() - timedelta(days=3), next_time="09:00")
    for i in range(25):
        w.db.add_customer(name=f"久未联系{i}", tier="C", customer_type="rent",
                          created_at=datetime.now() - timedelta(days=120))
    out = _load(w.fu.midday_check())
    return {"message": out.get("message") or "",
            "warnings": (out.get("warnings") or [])}


def case_stale_check(w):
    """流失检查的说明句：降级 + 仍超期 + 名单被截断"""
    for i in range(25):
        c = w.db.add_customer(name=f"久未联系{i}", tier="C", customer_type="rent",
                              created_at=datetime.now() - timedelta(days=120))
    out = _load(w.fu.stale_check())
    return {"message": out.get("message") or "",
            "warnings": (out.get("warnings") or [])}


def case_stage_stagnation(w):
    """阶段滞留的提醒句：25 位 → 名单被截断 + 阶段用中文"""
    for i in range(25):
        c = w.db.add_customer(name=f"滞留客户{i}", tier="A", customer_type="rent")
        w.db.update_customer(c["id"], stage="strong")
        with w.db.get_session() as s:
            s.execute(text("UPDATE re_customer_changes SET created_at = :t "
                           "WHERE customer_id = :i AND field = 'stage'"),
                      {"t": datetime.now() - timedelta(days=30), "i": c["id"]})
            s.commit()
    out = _load(w.fu.stage_stagnation())
    return {"message": out.get("message") or "", "warnings": (out.get("warnings") or [])}


def case_churn_warning(w):
    """流失预警的三条话术：正常（含挽回建议）/ 名单被截断 / 空库"""
    for i in range(25):
        c = w.db.add_customer(name=f"流失客户{i}", tier="B", customer_type="rent")
        w.db.add_followup(customer_id=c["id"], type="note", content="旧跟进",
                          created_at=datetime.now() - timedelta(days=40))
    out = _load(w.fu.churn_warning())
    empty_lib = {"message": out.get("message") or ""}
    return {"message": empty_lib["message"], "warnings": (out.get("warnings") or [])}


def case_market_brief(w):
    _customers(w.db, 3)
    return _load(w.an.market_brief())


SCENARIOS = [
    ("list_customers 截断", case_list_customers),
    ("search_property 截断", case_search_property),
    ("list_owners 截断", case_list_owners),
    ("find_person_by_name 截断", case_find_person_by_name),
    ("owner_portfolio 截断", case_owner_portfolio),
    ("exclusive_expiring 截断", case_exclusive_expiring),
    ("customer_change_history 截断", case_customer_change_history),
    ("referral_stats 截断", case_referral_stats),
    ("get_followups 截断", case_get_followups),
    ("get_overdue 截断", case_get_overdue),
    ("add_customer 查重", case_add_customer_duplicate),
    ("update_customer 撞号", case_update_customer_phone_conflict),
    ("add_owner 查重", case_add_owner_duplicate),
    ("add_property 判重", case_add_property_duplicate),
    ("deduplicate_properties", case_deduplicate_properties),
    ("update_customer_stage 警告", case_update_customer_stage_warning),
    ("get_property_detail 无业主", case_get_property_detail_without_owner),
    ("market_brief 简报", case_market_brief),
    ("schedule_reminder 话术", case_schedule_reminder),
    ("daily_report 说明句", case_daily_report),
    ("midday_check 说明句", case_midday_check),
    ("stale_check 说明句", case_stale_check),
    ("stage_stagnation 说明句", case_stage_stagnation),
    ("churn_warning 话术", case_churn_warning),
]


@pytest.mark.parametrize("label,builder", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_agent_facing_text_has_no_internal_terms(wired, label, builder):
    out = builder(wired)
    assert_agent_facing(label, out)
