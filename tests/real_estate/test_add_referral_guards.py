"""add_referral 回归（2026-09-24）：登记前认人，不造重复客户；手机号归一；重复登记不重复计数

覆盖两处修复（2026-09-24 拍板）：
① F78 转介绍登记原本直接 new 一条客户档案，绕过整套判重 —— 实测被介绍人已在库里（或手机号是介绍人自己）
   时会悄悄造出重复客户；现在：给了手机号只按手机号认人（命中则复用档案、不新建）、手机号等于介绍人自己
   直接拦下、同一介绍人重复登记同一位被介绍人给 `duplicate` 提示且不再新增 referral 行。
② F79 被介绍人手机号入库前归一（与建档同口径）。
"""
import json

import pytest

from tools.real_estate_customer import add_customer, add_referral


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
    return db


def make(db, name, phone=None, **overrides):
    data = dict(tier="C", customer_type="buy_second_hand", status="active")
    data.update(overrides)
    if phone:
        data["phone"] = phone
    return db.add_customer(name=name, **data)["id"]


def referral(db, **kwargs):
    return json.loads(add_referral(**kwargs))


def count_customers(db):
    return len(db.list_customers(limit=1000))


# ---------- ① 认人：不造重复客户 ----------
def test_existing_customer_is_reused_not_duplicated(tool_db):
    rid = make(tool_db, "老客户甲", "13900001001")
    existing = make(tool_db, "已有客户乙", "13900002001")
    before = count_customers(tool_db)
    r = referral(tool_db, referrer_customer_id=rid, referred_name="已有客户乙",
                 referred_phone="13900002001")
    assert r["success"] is True, r
    assert count_customers(tool_db) == before, "不该新建重复客户"
    assert r["referral"]["referred_customer_id"] == existing
    assert r.get("reused_existing_customer") is True and r.get("note"), r


def test_phone_variants_still_match_existing(tool_db):
    """写法不同的同一个号也要认出来（与建档判重同口径）"""
    rid = make(tool_db, "老客户甲", "13900001001")
    existing = make(tool_db, "已有客户乙", "13900002001")
    before = count_customers(tool_db)
    r = referral(tool_db, referrer_customer_id=rid, referred_name="已有客户乙",
                 referred_phone="139-0000-2001")
    assert r["referral"]["referred_customer_id"] == existing and count_customers(tool_db) == before, r


def test_referring_yourself_is_rejected(tool_db):
    rid = make(tool_db, "老客户甲", "13900001001")
    before = count_customers(tool_db)
    r = referral(tool_db, referrer_customer_id=rid, referred_name="老客户甲",
                 referred_phone="139 0000 1001")
    assert r["success"] is False and "不能是介绍人自己" in r["error"], r
    assert count_customers(tool_db) == before


def test_duplicate_referral_reported_without_new_row(tool_db):
    rid = make(tool_db, "老客户甲", "13900001001")
    first = referral(tool_db, referrer_customer_id=rid, referred_name="新客乙",
                     referred_phone="13900003001")
    second = referral(tool_db, referrer_customer_id=rid, referred_name="新客乙",
                      referred_phone="13900003001")
    assert first["success"] is True and second["success"] is False, (first, second)
    assert second["duplicate"] is True and "已经把这位客户" in second["error"], second
    assert tool_db.count_referrals() if hasattr(tool_db, "count_referrals") else True
    rows = tool_db.referral_stats(limit=50)
    ref = [x for x in rows if x["referrer_customer_id"] == rid]
    assert ref and ref[0]["referrals"] == 1, ref        # 贡献榜只算 1 人


def test_different_referred_customer_still_allowed(tool_db):
    rid = make(tool_db, "老客户甲", "13900001001")
    referral(tool_db, referrer_customer_id=rid, referred_name="新客乙", referred_phone="13900003001")
    r2 = referral(tool_db, referrer_customer_id=rid, referred_name="新客丙", referred_phone="13900003002")
    assert r2["success"] is True, r2


def test_same_name_without_phone_attaches_to_name_match_with_note(tool_db):
    """没给手机号时按姓名找；命中就复用并提示（同名可能是两个人）"""
    rid = make(tool_db, "老客户甲", "13900001001")
    existing = make(tool_db, "张伟", "13900004001")
    before = count_customers(tool_db)
    r = referral(tool_db, referrer_customer_id=rid, referred_name="张伟")
    assert r["referral"]["referred_customer_id"] == existing and count_customers(tool_db) == before, r
    assert "同名客户" in r["note"] and "带上手机号" in r["note"], r


def test_same_name_but_different_phone_makes_new_customer(tool_db):
    """给了手机号但库里没有这个号 → 按新客建档（只按手机号认人，不因同名合并）"""
    rid = make(tool_db, "老客户甲", "13900001001")
    other = make(tool_db, "张伟", "13900004001")
    before = count_customers(tool_db)
    r = referral(tool_db, referrer_customer_id=rid, referred_name="张伟", referred_phone="13900004002")
    assert r["referral"]["referred_customer_id"] != other, r
    assert count_customers(tool_db) == before + 1


# ---------- ② 手机号归一 + 新客档案 ----------
def test_referred_phone_normalized(tool_db):
    rid = make(tool_db, "老客户甲", "13900001001")
    r = referral(tool_db, referrer_customer_id=rid, referred_name="写法客", referred_phone="139 0000 5001")
    cid = r["referral"]["referred_customer_id"]
    assert tool_db.get_customer(cid)["phone"] == "13900005001", tool_db.get_customer(cid)["phone"]


def test_new_referred_customer_profile_fields(tool_db):
    rid = make(tool_db, "老客户甲", "13900001001")
    r = referral(tool_db, referrer_customer_id=rid, referred_name="新客丁", referred_phone="13900006001")
    c = tool_db.get_customer(r["referral"]["referred_customer_id"])
    assert c["source"] == "转介绍" and c["status"] == "active"
    assert c["customer_type"] == "unspecified" and c["tier"] == "C"


def test_referrer_missing_does_not_create_customer(tool_db):
    before = count_customers(tool_db)
    r = referral(tool_db, referrer_customer_id=999999, referred_name="无主新客")
    assert r["success"] is False and "介绍人客户不存在" in r["error"], r
    assert count_customers(tool_db) == before


def test_empty_name_rejected(tool_db):
    rid = make(tool_db, "老客户甲", "13900001001")
    for bad in ("", "   "):
        r = referral(tool_db, referrer_customer_id=rid, referred_name=bad)
        assert r["success"] is False and "姓名" in r["error"], r


# ---------- 既有契约不回退 ----------
def test_normal_referral_still_records_reward_note(tool_db):
    rid = make(tool_db, "老客户甲", "13900001001")
    r = referral(tool_db, referrer_customer_id=rid, referred_name="新客戊",
                 referred_phone="13900007001", reward_note="成交后请吃饭")
    assert r["referral"]["reward_note"] == "成交后请吃饭"
    assert r["referral"]["status"] == "registered"
    assert r["referral"]["referrer_customer_id"] == rid
