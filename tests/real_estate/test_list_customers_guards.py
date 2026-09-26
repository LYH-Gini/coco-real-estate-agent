"""list_customers 回归（2026-09-24）：最新优先排序、总数与截断提示、状态归一、limit 边界、密文防御

覆盖 7 处修复：
① F54 默认排序：原先按主键升序（最早优先），刚录入的客户在默认列表里看不见 → 改按最新录入优先。
② F49 返回体补 total(符合条件的总数) / truncated / 被截断时的 message（count 仍是本次返回条数，与房源搜索同形状）。
③ F50 status 筛选归一（大小写/空格/中文说法），非法值给中文提示，不再静默返回空列表。
④ F51 limit ≤0 按默认 20；⑤ F52 limit 非数字按默认 20（原先抛 ValueError）；上限 200。
⑥ F53 密钥不一致时列表不把密文当联系方式展示，并带 warning_key_mismatch / cipher_fields。
⑦ F55 两处 description 统一，并写明排序与返回值含义。
"""
import json

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from tools import real_estate_customer as mod
from tools.real_estate_customer import list_customers
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
    data = dict(name="客户", tier="C", customer_type="buy_second_hand", status="active")
    data.update(overrides)
    return db.add_customer(**data)


def call(**kwargs):
    return json.loads(list_customers(**kwargs))


def seed(db, n=25, prefix="在跟客户"):
    return [make(db, name=f"{prefix}{i:02d}", phone=f"1390000{i:04d}")["id"] for i in range(1, n + 1)]


# ---------- ① 排序 ----------
def test_default_order_is_newest_first(tool_db):
    seed(tool_db, 25)
    newest = make(tool_db, name="刚录入的客户")["id"]
    r = call(limit=50)
    names = [c["name"] for c in r["customers"]]
    assert names[0] == "刚录入的客户", names[:3]
    assert names[-1] == "在跟客户01", names[-3:]


def test_newest_customer_visible_on_first_page(tool_db):
    """客户数超过默认条数时，刚录入的也必须在第一页（这是 B 级缺陷的核心场景）"""
    seed(tool_db, 25)
    make(tool_db, name="刚录入的客户")
    r = call()
    assert "刚录入的客户" in [c["name"] for c in r["customers"]], r["count"]


# ---------- ② 总数与截断 ----------
def test_total_is_full_count_not_page_size(tool_db):
    seed(tool_db, 26)
    r = call()
    assert r["count"] == 20 and r["total"] == 26 and r["truncated"] is True, r
    assert "共 26 位" in r["message"] and "这里列最近 20 位" in r["message"], r.get("message")


def test_no_message_when_everything_returned(tool_db):
    seed(tool_db, 3)
    r = call()
    assert r["count"] == 3 and r["total"] == 3 and r["truncated"] is False
    assert "message" not in r


def test_total_respects_filters(tool_db):
    seed(tool_db, 5, prefix="S级客户")
    for cid in [c["id"] for c in tool_db.list_customers(limit=100)]:
        if tool_db.get_customer(cid)["name"].startswith("S级客户"):
            tool_db.update_customer(cid, tier="S")
    r = call(tier="S", limit=1)
    assert r["total"] == 5 and r["count"] == 1 and r["truncated"] is True, r


# ---------- ③ status 归一 ----------
@pytest.mark.parametrize("raw,expected", [("closed", 3), ("closed ", 3), ("CLOSED", 3), ("已关闭", 3)])
def test_status_filter_normalized(tool_db, raw, expected):
    for i in range(3):
        cid = make(tool_db, name=f"关闭{i}")["id"]
        tool_db.update_customer(cid, status="closed")
    seed(tool_db, 2)
    r = call(status=raw)
    assert r["success"] is True and r["total"] == expected, (raw, r)


@pytest.mark.parametrize("raw", ["close", "closure", "停业"])
def test_invalid_status_filter_returns_hint_not_empty_silence(tool_db, raw):
    seed(tool_db, 3)
    r = call(status=raw)
    assert r["success"] is False and "状态筛选没能识别" in r["error"], r


# ---------- ④⑤ limit 边界 ----------
@pytest.mark.parametrize("raw", [0, -1, -99, "abc", "", None])
def test_limit_bad_values_fall_back_to_default(tool_db, raw):
    seed(tool_db, 25)
    r = call(limit=raw)
    assert r["success"] is True and r["count"] == 20, (raw, r.get("count"), r.get("error"))


def test_limit_is_capped(tool_db):
    seed(tool_db, 60)
    r = call(limit=9999)
    assert r["count"] == 200 or r["count"] == 60, r["count"]     # 上限 200，库内不足则全给
    assert r["count"] <= 200


def test_limit_huge_against_big_library_is_capped(tool_db):
    seed(tool_db, 210)
    assert call(limit=10 ** 6)["count"] == 200


# ---------- ⑥ 密文防御 ----------
def test_ciphertext_not_exposed_in_list(enc_tool_db):
    cid = make(enc_tool_db, name="密钥坏了", phone="13900001111", wechat="wx_ok")["id"]
    cipher = Fernet(Fernet.generate_key()).encrypt(b"13900001111").decode()
    with enc_tool_db.get_session() as s:
        s.execute(text("UPDATE re_customers SET phone = :c WHERE id = :i"), {"c": cipher, "i": cid})
        s.commit()
    r = call()
    blob = json.dumps(r, ensure_ascii=False)
    assert "gAAAA" not in blob, blob[:200]
    assert r["warning_key_mismatch"] and r["cipher_fields"] == ["phone"], r
    assert [c for c in r["customers"] if c["name"] == "密钥坏了"][0]["wechat"] == "wx_ok"


def test_plaintext_mode_has_no_warning(tool_db):
    make(tool_db, phone="13900002222")
    r = call()
    assert "warning_key_mismatch" not in r


# ---------- ⑦ description 一致性 ----------
def test_description_matches_registered_schema():
    registered = registry.get_entry("list_customers").schema.get("description", "")
    assert registered == mod.TOOLS[3]["description"]
    for word in ("最新录入优先", "total", "include_closed"):
        assert word in registered, (word, registered)


# ---------- 既有契约不回退 ----------
def test_default_excludes_closed_and_include_closed_shows_them(tool_db):
    a = make(tool_db, name="在跟的")
    b = make(tool_db, name="关掉的")["id"]
    tool_db.update_customer(b, status="closed")
    assert [c["name"] for c in call()["customers"]] == ["在跟的"]
    all_names = [c["name"] for c in call(include_closed=True)["customers"]]
    assert set(all_names) == {"在跟的", "关掉的"}
