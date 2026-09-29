"""客户返回体补中文阶段名 / 状态名（2026-09-29 实测）

真实回复：客户详情里写成「阶段 lead、状态 active」，另一轮又把同一个阶段翻成「线索期」——
一个字段两种说法都不对。根因是返回体里只有英文键，模型只能自己翻。
本文件钉住：客户相关的读路径与写入回执都带中文名，英文键照旧保留（消费方在用）。
"""
import json

import pytest

import tools.real_estate_customer  # noqa: F401  导入即注册工具
from tools.registry import registry

SID = "20260929_000000_abcdef12"


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
    return db


def call(name, args):
    raw = registry.dispatch(name, args, session_id=SID, task_id="cn-label")
    return json.loads(raw) if isinstance(raw, str) else raw


def add_customer(db, **overrides):
    data = dict(name="客户甲", phone="13900000001", tier="B",
                budget_min=1_500_000, budget_max=2_000_000,
                customer_type="buy_second_hand", source="贝壳")
    data.update(overrides)
    return db.add_customer(**data)


def test_details_carry_cn_labels(tool_db):
    cid = add_customer(tool_db)["id"]
    out = call("get_customer", {"customer_id": cid})
    assert out["success"] is True, out
    cust = out["customer"]
    assert cust["stage_label"] == "潜在", cust
    assert cust["status_label"] == "在跟", cust
    # 英文键保留，别为了补中文把老字段删掉
    assert cust["stage"] == "lead" and cust["status"] == "active"


def test_list_items_carry_cn_labels(tool_db):
    add_customer(tool_db)
    out = call("list_customers", {})
    assert out["success"] is True, out
    for row in out["customers"]:
        assert row["stage_label"] and row["status_label"], row


def test_closed_status_is_said_in_chinese(tool_db):
    cid = add_customer(tool_db)["id"]
    tool_db.update_customer(cid, status="closed")
    out = call("get_customer", {"customer_id": cid})
    assert out["customer"]["status_label"] == "已关闭", out


def test_add_customer_receipt_carries_labels(tool_db):
    out = call("add_customer", {"name": "客户乙", "phone": "13900000002"})
    assert out["success"] is True, out
    assert out["customer"]["stage_label"] == "潜在", out
    assert out["customer"]["status_label"] == "在跟", out


def test_update_customer_receipt_carries_labels(tool_db):
    cid = add_customer(tool_db)["id"]
    out = call("update_customer", {"customer_id": cid, "budget_max": "180万"})
    assert out["success"] is True, out
    assert out["customer"]["stage_label"] == "潜在", out


def test_update_tier_and_stage_receipts_carry_labels(tool_db):
    cid = add_customer(tool_db)["id"]
    tier_out = call("update_tier", {"customer_id": cid, "tier": "A"})
    assert tier_out["customer"]["stage_label"] == "潜在", tier_out
    stage_out = call("update_customer_stage", {"customer_id": cid, "stage": "已看房"})
    assert stage_out["customer"]["stage_label"] == "已看房", stage_out
    assert stage_out["customer"]["status_label"] == "在跟", stage_out


def test_unknown_value_falls_back_to_raw(tool_db):
    """库里存着没见过的阶段值时，照原样给，不臆造中文名"""
    from tools.real_estate_customer import _with_cn_labels
    out = _with_cn_labels({"stage": "weird", "status": "active"})
    assert out["stage_label"] == "weird"
    assert out["status_label"] == "在跟"
