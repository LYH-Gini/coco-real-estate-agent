"""update_tier 回归（2026-09-24）：与 update_customer 同口径、写入类路径的密文防御、描述写清能力

覆盖三处修复：
① F57 等级非法值提示语与 update_customer 统一成一句，小写/带"级"的写法按同一套归一（原先 update_tier 自己写死 list 比较，'s' 直接失败）。
② F58 写入类工具（update_tier / update_customer / update_customer_stage）返回体里的联系方式也要做防御，不再把 gAAAA… 交给上层。
③ F59 description 从 6 个字补成"何时用 + 会留痕"。
"""
import json

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from agent.real_estate_db import KEY_MISMATCH_HINT
from tools import real_estate_customer as mod
from tools.real_estate_customer import update_customer, update_customer_stage, update_tier
from tools.registry import registry


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
    return db


@pytest.fixture
def enc_tool_db(enc_db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: enc_db)
    return enc_db


def make(db, **overrides):
    data = dict(name="客户甲", tier="C", customer_type="buy_second_hand", status="active")
    data.update(overrides)
    return db.add_customer(**data)


def call_tier(cid, tier):
    return json.loads(update_tier(customer_id=cid, tier=tier))


# ---------- ① 与 update_customer 同口径 ----------
def test_tier_error_message_matches_update_customer(tool_db):
    cid = make(tool_db)["id"]
    from_tier = call_tier(cid, "X")["error"]
    from_update = json.loads(update_customer(customer_id=cid, tier="X"))["error"]
    assert from_tier == from_update, (from_tier, from_update)
    assert "收到的是「X」" in from_tier and "S / A / B / C" in from_tier


@pytest.mark.parametrize("raw,expected", [("s", "S"), ("a", "A"), ("b", "B"), ("c", "C"), ("S", "S")])
def test_tier_normalized_like_update_customer(tool_db, raw, expected):
    cid = make(tool_db)["id"]
    r = call_tier(cid, raw)
    assert r["success"] is True and r["customer"]["tier"] == expected, (raw, r)


@pytest.mark.parametrize("raw", ["X", "SS", "", "三级", "A级", "1"])
def test_invalid_tier_rejected_and_not_stored(tool_db, raw):
    cid = make(tool_db)["id"]
    r = call_tier(cid, raw)
    assert r["success"] is False and "等级" in r["error"], (raw, r)
    assert tool_db.get_customer(cid)["tier"] == "C"


def test_tier_change_is_traced_once(tool_db):
    cid = make(tool_db)["id"]
    call_tier(cid, "A")
    call_tier(cid, "A")            # 重复设同值不留痕
    rows = [c for c in tool_db.get_customer_changes(cid, limit=50) if c["field"] == "tier"]
    assert len(rows) == 1 and (rows[0]["old_value"], rows[0]["new_value"]) == ("C", "A"), rows


# ---------- ② 写入类路径的密文防御 ----------
def _put_wrong_key_ciphertext(db, cid):
    cipher = Fernet(Fernet.generate_key()).encrypt(b"13900001111").decode()
    with db.get_session() as s:
        s.execute(text("UPDATE re_customers SET phone = :c, wechat = :c WHERE id = :i"),
                  {"c": cipher, "i": cid})
        s.commit()


@pytest.mark.parametrize("caller", ["update_tier", "update_customer", "update_customer_stage"])
def test_write_paths_never_return_ciphertext(enc_tool_db, caller):
    cid = make(enc_tool_db, phone="13900001111", wechat="wx_ok")["id"]
    _put_wrong_key_ciphertext(enc_tool_db, cid)
    if caller == "update_tier":
        r = json.loads(update_tier(customer_id=cid, tier="A"))
    elif caller == "update_customer":
        r = json.loads(update_customer(customer_id=cid, notes="改个备注"))
    else:
        r = json.loads(update_customer_stage(customer_id=cid, stage="interested"))
    blob = json.dumps(r, ensure_ascii=False)
    assert "gAAAA" not in blob, blob[:200]
    assert r["customer"]["phone"] == KEY_MISMATCH_HINT
    assert r["warning_key_mismatch"] and r["cipher_fields"] == ["phone", "wechat"], r


def test_write_path_no_warning_on_normal_data(tool_db):
    cid = make(tool_db, phone="13900002222")["id"]
    r = call_tier(cid, "B")
    assert r["customer"]["phone"] == "13900002222" and "warning_key_mismatch" not in r


def test_read_paths_share_the_same_warning_text():
    """读路径与写入路径共用同一句警告（2026-09-25 起收在 agent/real_estate_display.py）"""
    from agent.real_estate_display import CUSTOMER_KEY_MISMATCH_WARNING
    assert CUSTOMER_KEY_MISMATCH_WARNING.startswith("客户联系方式读不出来")
    assert "密钥文件" in CUSTOMER_KEY_MISMATCH_WARNING


# ---------- ③ 描述 ----------
def test_update_tier_description_states_when_and_what():
    desc = registry.get_entry("update_tier").schema.get("description", "")
    assert desc == mod.TOOLS[4]["description"]
    for word in ("高意向", "变更历史", "明确要求"):
        assert word in desc, (word, desc)


# ---------- 客户不存在等既有契约 ----------
def test_unknown_customer_still_says_not_exists(tool_db):
    r = call_tier(999999, "A")
    assert r["success"] is False and "不存在" in r["error"]
