"""get_customer 回归（2026-09-24）：密文不当联系方式展示、详情字段与建档 schema 对齐、空资料口径

覆盖两处修复：
① F46 密钥不一致时电话/微信被当成乱码给用户（房源详情 F16 同口径）→ 展示为可读提示 + warning_key_mismatch + cipher_fields。
② F48 description 只有 6 个字（模型靠它判断能力）→ 写清能给什么。
"""
import json

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from agent.real_estate_db import KEY_MISMATCH_HINT
from tools.real_estate_customer import get_customer
from tools.registry import registry


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
    return db


@pytest.fixture
def enc_tool_db(enc_db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: enc_db)
    return enc_db


def add(db, **overrides):
    data = dict(name="客户甲", tier="A", budget_min=3_000_000, budget_max=5_000_000,
                customer_type="buy_second_hand", status="active")
    data.update(overrides)
    return db.add_customer(**data)


def call_get(cid):
    return json.loads(get_customer(customer_id=cid))


def _put_wrong_key_ciphertext(db, cid, plaintext="13900001111"):
    """把 phone/wechat 换成"用别的密钥加密"的密文：读出来就是解不开的原样乱码"""
    cipher = Fernet(Fernet.generate_key()).encrypt(plaintext.encode()).decode()
    with db.get_session() as s:
        s.execute(text("UPDATE re_customers SET phone = :c, wechat = :c WHERE id = :i"),
                  {"c": cipher, "i": cid})
        s.commit()


# ---------- ① 密文防御 ----------
def test_normal_contacts_are_plaintext_without_warning(enc_tool_db):
    cid = add(enc_tool_db, phone="13900000001", wechat="zw_wx")["id"]
    r = call_get(cid)
    assert r["customer"]["phone"] == "13900000001"
    assert r["customer"]["wechat"] == "zw_wx"
    assert "warning_key_mismatch" not in r


def test_ciphertext_is_never_shown_as_contact(enc_tool_db):
    cid = add(enc_tool_db, phone="13900000001", wechat="zw_wx")["id"]
    _put_wrong_key_ciphertext(enc_tool_db, cid)
    r = call_get(cid)
    assert r["customer"]["phone"] == KEY_MISMATCH_HINT, r["customer"]["phone"]
    assert r["customer"]["wechat"] == KEY_MISMATCH_HINT, r["customer"]["wechat"]
    assert r["warning_key_mismatch"], r
    assert set(r["cipher_fields"]) == {"phone", "wechat"}, r.get("cipher_fields")
    assert "gAAAA" not in json.dumps(r, ensure_ascii=False)


def test_other_fields_still_readable_when_key_mismatch(enc_tool_db):
    """密钥坏掉只影响联系方式，档案其他字段照常给（别整条作废）"""
    cid = add(enc_tool_db, phone="13900000001", location="海口美兰区", budget_max=5_000_000)["id"]
    _put_wrong_key_ciphertext(enc_tool_db, cid)
    c = call_get(cid)["customer"]
    assert c["name"] == "客户甲" and c["location"] == "海口美兰区" and c["budget_max"] == 5_000_000


def test_plaintext_mode_never_masks(tool_db):
    """没配密钥（明文模式）时照常返回明文，不误报密钥问题"""
    cid = add(tool_db, phone="13900001111")["id"]
    r = call_get(cid)
    assert r["customer"]["phone"] == "13900001111" and "warning_key_mismatch" not in r


# ---------- 详情字段与建档 schema 对齐（不变量） ----------
def test_detail_covers_every_field_add_customer_can_fill(tool_db):
    cid = add(tool_db)["id"]
    detail = call_get(cid)["customer"]
    props = (registry.get_entry("add_customer").schema.get("parameters") or {}).get("properties", {})
    fillable = set(props) - {"force"}
    assert not (fillable - set(detail)), sorted(fillable - set(detail))
    for sys_field in ("id", "status", "stage", "tags", "created_at", "updated_at"):
        assert sys_field in detail, sys_field


def test_missing_values_are_null_not_strings(tool_db):
    cid = tool_db.add_customer(name="只有姓名", tier="C", customer_type="unspecified",
                              status="active")["id"]
    detail = call_get(cid)["customer"]
    assert detail["phone"] is None and detail["budget_max"] is None and detail["birthday"] is None
    assert not [k for k, v in detail.items() if isinstance(v, str) and v.strip() in ("None", "null", "nan")]


# ---------- 不存在 vs 存在但没数据 ----------
def test_unknown_customer_says_not_exists(tool_db):
    r = call_get(999999)
    assert r["success"] is False and "不存在" in r["error"]


def test_closed_customer_still_readable(tool_db):
    cid = add(tool_db, status="closed")["id"]
    r = call_get(cid)
    assert r["success"] is True and r["customer"]["status"] == "closed"


# ---------- ② description 要写清能给什么 ----------
def test_description_states_what_it_returns():
    desc = registry.get_entry("get_customer").schema.get("description", "")
    assert len(desc) >= 30, desc
    for word in ("预算", "等级", "联系方式", "状态"):
        assert word in desc, (word, desc)
