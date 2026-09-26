"""update_customer_stage 回归（2026-09-24）：阶段归一与中文提示、阶段/状态/成交的提示（只提示不拦）

覆盖两处修复（2026-09-24 定口径）：
① F60 非法阶段不再甩英文 Python 列表：中文提示 + 可用阶段中英对照；并接受中文说法与大小写变体。
② F61 阶段与状态/成交是两套维度 —— **只加提示、不拦**：关闭客户改阶段、有成交记录回退、
   改成"流失"但状态仍在跟，这三种情况给 warnings，判定一律不动。
"""
import json

import pytest

from tools import real_estate_customer as mod
from tools.real_estate_customer import update_customer_stage, update_customer
from tools.registry import registry


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
    return db


def make(db, **overrides):
    data = dict(name="客户甲", tier="C", customer_type="buy_second_hand", status="active")
    data.update(overrides)
    return db.add_customer(**data)


def call(cid, stage):
    return json.loads(update_customer_stage(customer_id=cid, stage=stage))


# ---------- ① 归一与提示 ----------
@pytest.mark.parametrize("raw,expected", [
    ("lead", "lead"), ("Lead", "lead"), ("  lead ", "lead"),
    ("潜在", "lead"), ("意向", "interested"), ("强意向", "strong"), ("已看房", "viewed"),
    ("谈判", "negotiating"), ("成交中", "dealing"), ("售后维护", "maintain"), ("流失", "lost"),
    ("LEAD", "lead"), ("Interested", "interested"),
])
def test_stage_normalized(tool_db, raw, expected):
    cid = make(tool_db)["id"]
    r = call(cid, raw)
    assert r["success"] is True and r["customer"]["stage"] == expected, (raw, r)


@pytest.mark.parametrize("raw", ["X", "abc", "", "潜在中", "1"])
def test_invalid_stage_gets_chinese_hint(tool_db, raw):
    cid = make(tool_db)["id"]
    r = call(cid, raw)
    assert r["success"] is False, (raw, r)
    err = r["error"]
    assert "客户阶段没能识别" in err and "可用阶段" in err, err
    assert "潜在(lead)" in err and "流失(lost)" in err, err
    assert "['lead'" not in err and "可选: [" not in err, err
    assert tool_db.get_customer(cid)["stage"] == "lead"      # 没落库


def test_message_uses_chinese_stage_label(tool_db):
    cid = make(tool_db)["id"]
    r = call(cid, "成交中")
    assert "成交中" in r["message"] and r["customer"]["stage"] == "dealing", r


def test_stage_traced_and_not_duplicated(tool_db):
    cid = make(tool_db)["id"]
    call(cid, "strong")
    call(cid, "strong")
    rows = [c for c in tool_db.get_customer_changes(cid, limit=50) if c["field"] == "stage"]
    assert len(rows) == 1 and (rows[0]["old_value"], rows[0]["new_value"]) == ("lead", "strong"), rows


def test_stage_names_match_db_enum():
    from agent.real_estate_db import RealEstateDB
    assert set(mod.STAGES) == set(RealEstateDB.VALID_STAGES)


# ---------- ② 只提示不拦 ----------
def test_closed_customer_stage_change_warns_but_still_works(tool_db):
    cid = make(tool_db, status="closed")["id"]
    r = call(cid, "strong")
    assert r["success"] is True and r["customer"]["stage"] == "strong", r
    assert any("已关闭" in w for w in r["warnings"]), r


def test_stage_rollback_with_deal_warns_but_still_works(tool_db):
    cid = make(tool_db, name="成交过的客")["id"]
    call(cid, "dealing")
    tool_db.add_deal(customer_id=cid, property_id=1, price=3_000_000) \
        if hasattr(tool_db, "add_deal") else None
    # 用与生产相同的路径建成交记录（start_deal 内部会写 re_deals）
    from tools.real_estate_deal import start_deal
    if not tool_db.customer_has_deal(cid):
        db_prop = tool_db.add_property(title="回归房源 1号楼101", price=3_000_000, area=100,
                                      rooms=3, halls=2, property_type="second_hand")
        start_deal(customer_id=cid, property_id=db_prop["id"], price=3_000_000)
    r = call(cid, "lead")
    assert r["success"] is True and r["customer"]["stage"] == "lead", r
    assert any("成交记录" in w for w in r["warnings"]), r


def test_lost_stage_warns_status_still_active(tool_db):
    cid = make(tool_db)["id"]
    r = call(cid, "lost")
    assert r["success"] is True and r["customer"]["status"] == "active", r
    assert any("在跟" in w for w in r["warnings"]), r


def test_no_warnings_for_normal_forward_move(tool_db):
    cid = make(tool_db)["id"]
    r = call(cid, "interested")
    assert r["success"] is True and not r.get("warnings"), r


def test_lost_on_already_closed_customer_has_no_lost_warning(tool_db):
    """已经关闭的客户标流失时，不该再提醒仍在跟"""
    cid = make(tool_db, status="closed")["id"]
    r = call(cid, "lost")
    assert r["success"] is True
    assert not any("状态仍是" in w for w in r.get("warnings") or []), r


# ---------- 既有契约 ----------
def test_unknown_customer_says_not_exists(tool_db):
    r = call(999999, "lead")
    assert r["success"] is False and "不存在" in r["error"]


def test_description_still_lists_stages():
    desc = registry.get_entry("update_customer_stage").schema.get("description", "")
    for word in ("潜在", "流失", "变更历史"):
        assert word in desc, (word, desc)
