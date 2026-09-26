"""`limit` 兜底统一回归（2026-09-25，同族收口批任务 2）

口径（契约 5）：`limit` 传 0 / 负数 / 非数字 / null 一律按**该工具自己的默认值**，
超过上限按上限；**负数绝不能变成"拉全量"**（列表类工具最容易把上下文撑爆）。

起因：这批之前有**三套实现**（客户侧内联 3 处、房源侧内联 3 处、房东侧共用 1 个），
另外还有 5 个工具**完全没有兜底**：`limit=-1` 直接返回全表、`limit='abc'` 直接崩
（清单在 `results/raw/t35b_limit_baseline.json` 的基线快照里，改动前后逐格对照）。

现在全部走 `agent.real_estate_input.clamp_limit`；本文件锁住"默认值各不同但口径一致"。
"""
import json
from datetime import datetime, timedelta

import pytest

from agent.real_estate_input import clamp_limit
from tools import (real_estate_customer as m_customer, real_estate_deal as m_deal,
                   real_estate_followup as m_followup, real_estate_intent as m_intent,
                   real_estate_owner as m_owner, real_estate_property as m_property,
                   real_estate_viewing as m_viewing)

MODULES = [m_customer, m_deal, m_followup, m_intent, m_owner, m_property, m_viewing]
N = 60          # 造 60 条就够把"默认值 20/50"和"上限 200"区分开


@pytest.fixture
def wired(db, monkeypatch):
    for mod in MODULES:
        monkeypatch.setattr(mod, "_get_db", lambda _db=db: _db)
    return db


@pytest.fixture
def scale(wired):
    """每张表各 60 条（够区分默认值与"全量"）"""
    from agent.real_estate_db import (Customer, Deal, Followup, Owner, PriceHistory, Property,
                                      Referral, Viewing)
    s = wired.get_session()
    now = datetime.now()
    s.bulk_save_objects([Customer(name=f"规模客户{i}", phone=f"139{i:08d}",
                                  customer_type="buy_second_hand", status="active")
                         for i in range(1, N + 1)])
    s.bulk_save_objects([Property(title=f"规模房源{i}", price=1_000_000 + i * 1_000,
                                  area=80.0, property_type="second_hand", status="available")
                         for i in range(1, N + 1)])
    s.bulk_save_objects([Owner(name=f"规模房东{i}", phone=f"138{i:08d}") for i in range(1, N + 1)])
    s.bulk_save_objects([Viewing(customer_id=i, property_id=i, viewing_time=now,
                                 status="scheduled") for i in range(1, N + 1)])
    s.bulk_save_objects([Deal(customer_id=i, property_id=i, stage="deposit")
                         for i in range(1, N + 1)])
    s.bulk_save_objects([Followup(customer_id=i, content=f"规模跟进{i}", type="note")
                         for i in range(1, N + 1)])
    s.bulk_save_objects([PriceHistory(property_id=i, old_price=1_000_000, new_price=1_100_000,
                                      created_at=now - timedelta(days=i % 30))
                         for i in range(1, N + 1)])
    s.bulk_save_objects([Referral(referrer_customer_id=i, referred_name=f"被介绍{i}")
                         for i in range(1, N + 1)])
    s.commit()
    assert wired.list_customers(limit=200) and len(wired.list_customers(limit=200)) >= N - 1
    return wired


# ==================== ① 共用实现本身 ====================
@pytest.mark.parametrize("value", [0, -1, -100, None, "abc", "", "  ", [], {}])
def test_clamp_falls_back_to_default(value):
    assert clamp_limit(value, 20) == 20
    assert clamp_limit(value, 50) == 50


@pytest.mark.parametrize("value,expected", [(1, 1), (7, 7), ("12", 12), (" 12 ", 12), (200, 200)])
def test_clamp_keeps_positive(value, expected):
    assert clamp_limit(value, 20) == expected


@pytest.mark.parametrize("value", [201, 100000, 10 ** 9])
def test_clamp_caps_at_max(value):
    assert clamp_limit(value, 20, 200) == 200
    assert clamp_limit(value, 5, 20) == 20


# ==================== ② 15 个带 limit 的工具：逐工具对照 ====================
# (用例名, 工具函数, 额外参数, 返回体的列表明, 默认值, 上限)
CASES = [
    ("list_customers", m_customer.list_customers, {}, "customers", 20, 200),
    ("list_owners", m_owner.list_owners, {}, "owners", 50, 200),
    ("list_deals", m_deal.list_deals, {}, "deals", 20, 200),
    ("list_viewings", m_viewing.list_viewings, {}, "viewings", 20, 200),
    ("list_intent_scores", m_intent.list_intent_scores, {}, "rankings", 20, 200),
    ("search_property", m_property.search_property, {}, "properties", 20, 200),
    ("referral_stats", m_customer.referral_stats, {}, None, 20, 200),
    ("find_person_by_name", m_owner.find_person_by_name, {"name": "规模"}, None, 20, 200),
    ("customer_change_history", m_customer.customer_change_history, {"customer_id": 1}, "changes",
     20, 200),
    ("price_history", m_property.price_history, {"property_id": 1}, "history", 20, 200),
    ("find_alternatives", m_property.find_alternatives, {"property_id": 1}, "alternatives", 5, 20),
    ("compare_property", m_intent.compare_property, {"property_id": 1}, None, 5, 20),
    ("get_followups", m_followup.get_followups, {"customer_id": 1}, "followups", 20, 200),
    ("owner_portfolio", m_owner.owner_portfolio, {"owner_id": 1}, "properties", 50, 200),
    ("exclusive_expiring", m_owner.exclusive_expiring, {"days": 3650}, "items", 20, 200),
]

CASE_IDS = [c[0] for c in CASES]


def _rows(out, key):
    """取"本次条数"：形状固定的直接取该键，形状不固定的取返回体里最长的列表"""
    if key:
        return out.get(key) or []
    lists = [v for v in out.values() if isinstance(v, list)]
    return max(lists, key=len) if lists else []


def _call(func, **kw):
    return json.loads(func(**kw))


@pytest.mark.parametrize("name,func,extra,key,default,maximum", CASES, ids=CASE_IDS)
def test_limit_fallback_matches_each_tool_default(scale, name, func, extra, key, default, maximum):
    """0/负数/非数字/null 一律与该工具"不传 limit"时一致；负数绝不拉全量"""
    base_n = len(_rows(_call(func, **extra), key))
    assert base_n <= default, (name, base_n)          # 不传时按该工具自己的默认
    for bad in (0, -1, -100, None, "abc"):
        out = _call(func, limit=bad, **extra)
        n = len(_rows(out, key))
        assert n == base_n, (name, bad, n, base_n)
        assert n <= default, (name, bad, n, "负数/非数字不得变成全量")


@pytest.mark.parametrize("name,func,extra,key,default,maximum", CASES, ids=CASE_IDS)
def test_limit_capped_at_max(scale, name, func, extra, key, default, maximum):
    for big in (maximum + 1, 100000):
        out = _call(func, limit=big, **extra)
        assert len(_rows(out, key)) <= maximum, (name, big, len(_rows(out, key)))


@pytest.mark.parametrize("name,func,extra,key,default,maximum", CASES, ids=CASE_IDS)
def test_string_number_still_works(scale, name, func, extra, key, default, maximum):
    """数字字符串（模型常这么传）要照旧当条数用，别被当成"非数字按默认"吞掉"""
    out = _call(func, limit="1", **extra)
    assert "缺少必填参数" not in json.dumps(out, ensure_ascii=False), (name, out)
    rows = _rows(out, key)
    assert len(rows) <= 1, (name, len(rows))
