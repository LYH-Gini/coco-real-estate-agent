"""referral_stats 回归（2026-09-24）：成交按人数、榜单不静默截断、已删除介绍人不显示 None、文案去表情

覆盖四处修复（2026-09-24 拍板）：
① F80 `deals_from_referrals` 原数**成交笔数**（一位被介绍人两笔成交算 2），文案却说"N 人成交"
   → 改为按 `distinct` 客户数计**人数**，与"介绍 N 人"对称。
② F81 榜单写死 20 条、静默截断 → 工具暴露 `limit`（默认 20/上限 200）+ `total`/`truncated`/截断说明。
③ F82 介绍人被删除后榜单显示 None → 标注"已删除客户（id=N）" + `referrer_missing: true`。
④ F83 标题带 🏆 表情（产品规则"不使用表情符号"）→ 去掉表情，行文案固化为可核对表述。
"""
import json

import pytest

from tools.real_estate_customer import add_referral, referral_stats


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
    return db


def make(db, name, phone=None, tier="C"):
    return db.add_customer(name=name, phone=phone, tier=tier, customer_type="rent",
                           status="active")["id"]


def refer(db, referrer, name, phone):
    return json.loads(add_referral(referrer_customer_id=referrer, referred_name=name,
                                   referred_phone=phone))


def stats(**kwargs):
    return json.loads(referral_stats(**kwargs))


def board_by_name(payload):
    return {r["referrer_name"]: r for r in payload["leaderboard"]}


def seed_deal(db, cid, price=3_000_000):
    prop = db.add_property(title=f"回归房源{cid} 1号楼101", price=price, area=100, rooms=3,
                           halls=2, community="回归小区", property_type="second_hand")
    db.add_deal(customer_id=cid, property_id=prop["id"], price=price)


# ---------- ① 成交按人数 ----------
def test_deals_count_people_not_deal_rows(tool_db):
    rid = make(tool_db, "介绍人甲", "13900001001", tier="A")
    r = refer(tool_db, rid, "被介绍人", "13900002001")
    cid = r["referral"]["referred_customer_id"]
    seed_deal(tool_db, cid, 3_000_000)
    seed_deal(tool_db, cid, 3_100_000)          # 同一个人两笔
    row = board_by_name(stats())["介绍人甲"]
    assert row["referrals"] == 1 and row["deals_from_referrals"] == 1, row


def test_two_referred_people_one_deal(tool_db):
    rid = make(tool_db, "介绍人甲", "13900001001")
    a = refer(tool_db, rid, "被介绍A", "13900002001")["referral"]["referred_customer_id"]
    refer(tool_db, rid, "被介绍B", "13900002002")
    seed_deal(tool_db, a)
    row = board_by_name(stats())["介绍人甲"]
    assert row["referrals"] == 2 and row["deals_from_referrals"] == 1, row


# ---------- ② 条数与截断 ----------
def test_total_and_truncation_reported(tool_db):
    for i in range(25):
        rid = make(tool_db, f"介绍人{i:02d}", f"1390001{i:04d}")
        refer(tool_db, rid, f"被介绍{i:02d}", f"1390002{i:04d}")
    r = stats()
    assert r["count"] == 20 and r["total"] == 25 and r["truncated"] is True, r
    assert "共 25 位介绍人" in r["message"] and "前 20 位" in r["message"], r["message"][-80:]


def test_no_truncation_message_when_all_listed(tool_db):
    rid = make(tool_db, "介绍人甲", "13900001001")
    refer(tool_db, rid, "被介绍", "13900002001")
    r = stats()
    assert r["count"] == 1 and r["total"] == 1 and r["truncated"] is False
    assert "共 " not in r["message"], r["message"]


@pytest.mark.parametrize("bad", [0, -1, -99, "abc", "", None])
def test_limit_bad_values_fall_back_to_default(tool_db, bad):
    for i in range(25):
        rid = make(tool_db, f"介绍人{i:02d}", f"1390001{i:04d}")
        refer(tool_db, rid, f"被介绍{i:02d}", f"1390002{i:04d}")
    r = stats(limit=bad)
    assert r["success"] is True and r["count"] == 20, (bad, r.get("count"))


def test_limit_is_capped(tool_db):
    for i in range(25):
        rid = make(tool_db, f"介绍人{i:02d}", f"1390001{i:04d}")
        refer(tool_db, rid, f"被介绍{i:02d}", f"1390002{i:04d}")
    r = stats(limit=9999)
    assert r["count"] == 25 and r["total"] == 25, r["count"]


# ---------- ③ 已删除的介绍人 ----------
def test_normal_delete_customer_cleans_its_referrals(tool_db):
    """走正式删除路径（delete_customer）时，转介绍记录会被一并清掉 —— 榜单不会出现孤儿行"""
    keep = make(tool_db, "还在的介绍人", "13900001001")
    gone = make(tool_db, "要删的介绍人", "13900001002")
    refer(tool_db, keep, "被介绍1", "13900002001")
    refer(tool_db, gone, "被介绍2", "13900002002")
    tool_db._delete_one("customer", customer_id=gone, force=True)
    names = [x["referrer_name"] for x in stats()["leaderboard"]]
    assert names == ["还在的介绍人"], names


def test_orphan_referrer_row_is_labelled_not_none(tool_db):
    """库被外部手段直接改过（不走删除工具）时，榜单也要给可读标注而不是 None"""
    from sqlalchemy import text

    keep = make(tool_db, "还在的介绍人", "13900001001")
    gone = make(tool_db, "被外部删掉的介绍人", "13900001002")
    refer(tool_db, keep, "被介绍1", "13900002001")
    refer(tool_db, gone, "被介绍2", "13900002002")
    with tool_db.get_session() as s:            # 绕过工具直接删客户，模拟外部改动
        s.execute(text("DELETE FROM re_customers WHERE id = :i"), {"i": gone})
        s.commit()
    r = stats()
    names = [x["referrer_name"] for x in r["leaderboard"]]
    assert None not in names and "None" not in r["message"], (names, r["message"])
    assert any("已删除客户" in n for n in names), names
    missing_rows = [x for x in r["leaderboard"] if x.get("referrer_missing")]
    assert missing_rows and missing_rows[0]["referrals"] == 1, missing_rows   # 历史贡献保留


# ---------- ④ 文案 ----------
def test_message_has_no_emoji_and_is_checkable(tool_db):
    rid = make(tool_db, "介绍人甲", "13900001001", tier="A")
    refer(tool_db, rid, "被介绍", "13900002001")
    msg = stats()["message"]
    assert not any(ch in msg for ch in "🏆🎉✨⭐️❗️"), msg
    assert "介绍人甲（A级）：介绍 1 人，其中 0 人成交" in msg, msg


def test_empty_library_reports_no_records(tool_db):
    r = stats()
    assert r["success"] is True and r["leaderboard"] == [] and r["total"] == 0
    assert r["message"] == "暂无转介绍记录", r


# ---------- 排序与口径 ----------
def test_order_by_referrals_desc_then_id(tool_db):
    rid_a = make(tool_db, "介绍人甲", "13900001001")
    rid_b = make(tool_db, "介绍人乙", "13900001002")
    for i in range(3):
        refer(tool_db, rid_a, f"甲的被介绍{i}", f"1390003{i:04d}")
    refer(tool_db, rid_b, "乙的被介绍", "13900004001")
    names = [x["referrer_name"] for x in stats()["leaderboard"]]
    assert names == ["介绍人甲", "介绍人乙"], names


def test_counts_match_referral_table(tool_db):
    rid = make(tool_db, "介绍人甲", "13900001001")
    for i in range(4):
        refer(tool_db, rid, f"被介绍{i}", f"1390005{i:04d}")
    r = stats()
    assert sum(x["referrals"] for x in r["leaderboard"]) == 4       # 该介绍人介绍了 4 人
    assert r["total"] == 1                                          # 介绍人总数是 1 位


def test_description_states_return_shape():
    from tools.registry import registry
    desc = registry.get_entry("referral_stats").schema.get("description", "")
    assert "total" in desc and "成交" in desc, desc
    props = (registry.get_entry("referral_stats").schema.get("parameters") or {}).get("properties", {})
    assert "limit" in props, props
