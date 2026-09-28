"""add_owner 房东登记：写法归一、按手机号查重、force 逃生舱、列宽截断、证件脱敏（2026-09-25）

房东批 F84–F88 的回归：
① F84 手机号/微信号写法归一；并与 add_property 的业主认人（link_owner_to_property）对齐 ——
   "138 0013 8000" 与 "13800138000" 是同一个房东，不再把一个人拆成两条。
② F85 同号重复登记给中文提示、不静默新建；**姓名不参与判重**（同名不同号是两个人，都要能建）；
   force=true 是同一个人的另一个号时的逃生舱。
③ F87 文本超列宽按列宽截断并在返回里说明（PostgreSQL 上 varchar 超长会让整次登记失败）。
④ 身份证只存脱敏串，原号不落库（表里、库文件里都不出现）。
"""
import json

import pytest
from conftest import make_property

from tools.real_estate_owner import add_owner


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_owner._get_db", lambda: db)
    monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
    return db


def call_add(**kwargs):
    return json.loads(add_owner(**kwargs))


def owner_count(db):
    return len(db.list_owners(limit=1000))


# ---------- ① 写法归一 ----------

@pytest.mark.parametrize("raw,expected", [
    ("138 0013 8000", "13800138000"),
    ("+86 137-0000-1234", "13700001234"),
    ("13900001111", "13900001111"),
    ("0086 138 0000 1234", "13800001234"),
])
def test_phone_written_in_any_style_is_normalized(tool_db, raw, expected):
    """经纪人怎么写就怎么记 —— 落库统一成纯号码，否则同一个人会被当成两个人"""
    r = call_add(name=f"归一房东{raw}", phone=raw)
    assert r["success"] is True, r
    assert r["owner"]["phone"] == expected, r["owner"]["phone"]


def test_wechat_trimmed_and_empty_values_stored_as_unset(tool_db):
    """微信号去首尾空白；空串/空白按"未填"存，不留下会让人误以为录过的空字符串"""
    r = call_add(name="空白房东", phone="", wechat="  wx_pad  ", notes="   ")
    assert r["success"] is True, r
    got = tool_db.get_owner(r["owner"]["id"])
    assert got["wechat"] == "wx_pad"
    assert got["phone"] is None and got["notes"] is None


# ---------- ② 按手机号查重 / force 逃生舱 ----------

def test_same_phone_second_registration_is_blocked_with_hint(tool_db):
    first = call_add(name="房东王五", phone="13800001111")
    dup = call_add(name="王五（另一写法）", phone="138-0000-1111")
    assert dup["success"] is False and dup["duplicate"] is True, dup
    assert dup["existing_owner"]["id"] == first["owner"]["id"]
    assert "该房东已在库里" in dup["error"] and "我另建一条" in dup["error"], dup["error"]
    assert owner_count(tool_db) == 1


def test_identical_registration_does_not_suggest_force(tool_db):
    """一字不差地再登记一次：直接说不用重复登记，不劝他用 force"""
    call_add(name="房东王五", phone="13800001111", wechat="wx_wangwu", notes="两套房")
    dup = call_add(name="房东王五", phone="13800001111", wechat="wx_wangwu", notes="两套房")
    assert dup["identical"] is True and dup["success"] is False, dup
    assert "同号同名，不用重复登记" in dup["error"], dup["error"]


def test_force_creates_a_second_record(tool_db):
    call_add(name="房东王五", phone="13800001111")
    forced = call_add(name="房东王五（同一人的另一个号）", phone="13800001111", force=True)
    assert forced["success"] is True, forced
    assert owner_count(tool_db) == 2


def test_same_name_different_phone_both_allowed(tool_db):
    """同名不同号 = 两个人（房东没有客户类型那样的维度），不能按姓名误合"""
    a = call_add(name="同名不同人", phone="13500007777")
    b = call_add(name="同名不同人", phone="13500008888")
    assert a["success"] is True and b["success"] is True, (a, b)
    assert owner_count(tool_db) == 2


def test_no_phone_is_not_blocked_by_name(tool_db):
    """没给手机号时不按姓名拦 —— 同名可能是不同人，宁可都存下来"""
    a = call_add(name="无号房东")
    b = call_add(name="无号房东")
    assert a["success"] is True and b["success"] is True, (a, b)


def test_wechat_used_for_dedupe_when_no_phone(tool_db):
    first = call_add(name="微友房东", wechat="wx_only")
    dup = call_add(name="另一个名字", wechat="  wx_only  ")
    assert dup["duplicate"] is True and dup["success"] is False, dup
    assert dup["existing_owner"]["id"] == first["owner"]["id"]


def test_ciphertext_contact_refuses_to_deduplicate(tool_db):
    """密钥不一致时读出来是乱码串：不强行判重，对经纪人只说人话（与客户侧同口径）"""
    tool_db.add_owner(name="密钥坏了的房东", phone="gAAAAA" + "x" * 40)
    r = call_add(name="正常新房东", phone="13900008000")
    assert r["success"] is False and r["duplicate"] is False, r
    assert "先不给你判重结果" in r["error"], r
    assert "COCO_ENC_KEY" not in r["error"], "对外文案不提服务器上的密钥变量"
    assert "COCO_ENC_KEY" in r["note_for_model"], "系统口径进给模型看的字段"
    assert r["key_mismatch"] is True, r
    assert "Ava" not in r["error"], r


# ---------- ③ 列宽截断 ----------

def test_trust_note_clipped_to_column_width_with_warning(tool_db):
    """信任度备注列宽 200：PostgreSQL 上超长会让整次登记失败 → 截断并明说"""
    r = call_add(name="长备注房东", trust_note="信" * 300)
    assert r["success"] is True, r
    assert len(tool_db.get_owner(r["owner"]["id"])["trust_note"]) == 200
    assert any("信任度备注超过 200 字" in w for w in r["warnings"]), r


def test_name_clipped_to_column_width_with_warning(tool_db):
    r = call_add(name="长" * 120)
    assert r["success"] is True, r
    assert r["owner"]["name"] == "长" * 100
    assert any("房东姓名超过 100 字" in w for w in r["warnings"]), r


def test_notes_is_long_text_not_clipped(tool_db):
    """备注列是长文本，不该被截断"""
    long_note = "备" * 500
    r = call_add(name="长备注房东2", notes=long_note)
    assert tool_db.get_owner(r["owner"]["id"])["notes"] == long_note


# ---------- ④ 证件脱敏 ----------

def test_id_number_masked_and_original_never_stored(tool_db, tmp_path):
    r = call_add(name="证件房东", id_number="110101200001015678")
    assert r["owner"]["id_masked"] == "1101" + "*" * 10 + "5678", r
    assert "原号未落库" in r["message"], r
    assert "110101200001015678" not in json.dumps(r, ensure_ascii=False)
    db_bytes = (tmp_path / "re_test.db").read_bytes()
    assert b"110101200001015678" not in db_bytes, "原身份证号不该出现在库文件里"


# ---------- 与房源侧认人对齐（同一套归一）----------

def test_link_owner_matches_owner_saved_by_add_owner(tool_db):
    """add_owner 存的号，add_property 换写法引用时要认到同一个房东，不新建"""
    o = call_add(name="归一房东", phone="138 0013 8000")["owner"]
    p = make_property(tool_db, title="归一房源", price=1_500_000, area=80.0)
    owner, info = tool_db.link_owner_to_property(
        p["id"], name="归一房东", phone="13800138000", return_info=True)
    assert info["created"] is False and info["matched_by"] == "phone", info
    assert owner["id"] == o["id"]
    assert owner_count(tool_db) == 1


def test_add_owner_matches_owner_created_by_property_side(tool_db):
    """反向：房源侧先建的房东（写法带空格），add_owner 换写法登记要认出同一人"""
    p = make_property(tool_db, title="反向房源", price=1_600_000, area=88.0)
    owner, info = tool_db.link_owner_to_property(
        p["id"], name="反向房东", phone="136 0000 5555", return_info=True)
    assert info["created"] is True
    assert owner["phone"] == "13600005555", owner  # 房源侧落库也归一

    dup = call_add(name="反向房东", phone="136-0000-5555")
    assert dup["duplicate"] is True and dup["success"] is False, dup
    assert dup["existing_owner"]["id"] == owner["id"]
    assert owner_count(tool_db) == 1


# ---------- ⑤ 描述（模型靠它判断能力）----------

def test_description_names_fields_and_dedupe():
    from tools.registry import registry
    desc = registry.get_entry("add_owner").schema["description"]
    for word in ("姓名", "手机号", "微信号", "身份证", "信任度", "备注", "查重", "force"):
        assert word in desc, (word, desc)
