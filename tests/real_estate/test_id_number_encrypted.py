"""身份证号加密存储（与手机号同一套口径）：2026-09-29

背景：原先身份证号只存脱敏串（`id_masked`）、原号不落库 —— 出发点是不让备份/导出的人读到全号，
但经纪人的真实用途是网签、贷款、备案，下次要用时库里没有全号，等于白记。
现在与手机号完全同级：
① 加密列存全号（COCO_ENC_KEY），读取自动解密；房东详情、房东列表、按房源反查业主、
   按姓名查人（客户与业主两边）、客户详情、客户列表都给完整号；
② 密钥不一致时走展示防御（不给 `gAAAA…` 乱码，给可读提示 + cipher_fields）；
③ 变更留痕（re_owner_changes / re_customer_changes）仍然只留脱敏串 —— 备份与看库的人读不到完整号；
④ 改动前登记的老房东只有脱敏号 → 如实提示补录一次，不假装库里有。
"""
import json
import sqlite3

import pytest
from cryptography.fernet import Fernet

from tools import real_estate_customer as m_customer, real_estate_owner as m_owner

ID_FULL = "460005199001011234"
ID_MASKED = "4600" + "*" * 10 + "1234"
CID_FULL = "110101200001015678"
KEY_MISMATCH_HINT = "读取失败（密钥不一致，请检查备份的密钥文件）"


@pytest.fixture
def wired(enc_db, monkeypatch):
    """加密临时库 + 两个工具的 _get_db 都指向它"""
    monkeypatch.setattr(m_owner, "_get_db", lambda _db=enc_db: _db)
    monkeypatch.setattr(m_customer, "_get_db", lambda _db=enc_db: _db)
    return enc_db


def _db_path(instance):
    return instance.engine.url.database


def _raw(table, column, rid, instance):
    con = sqlite3.connect(_db_path(instance))
    try:
        row = con.execute(f"SELECT {column} FROM {table} WHERE id=?", (rid,)).fetchone()
        return row[0] if row else None
    finally:
        con.close()


# ==================== ① 房东：加密入库、接口给全号 ====================

def test_owner_id_stored_as_ciphertext_and_returned_in_full(wired):
    r = json.loads(m_owner.add_owner(name="加密房东", phone="13800001111", id_number=ID_FULL))
    assert r["success"] is True, r
    oid = r["owner"]["id"]
    stored = _raw("re_owners", "id_number", oid, wired)
    assert stored and stored.startswith("gAAAA") and ID_FULL not in stored, stored
    assert _raw("re_owners", "id_masked", oid, wired) == ID_MASKED      # 掩码列仍在（展示兜底）
    assert r["owner"]["id_number"] == ID_FULL                           # 回执就给全号
    assert "身份证已加密存储" in r["message"], r["message"]

    detail = json.loads(m_owner.get_owner(owner_id=oid))
    assert detail["owner"]["id_number"] == ID_FULL, detail
    assert "note_id_full_missing" not in detail                         # 新记录不该出补录提示


def test_owner_id_written_in_any_style_is_normalized(wired):
    """空格、横线、末位小写 x 都归一成一个写法（不改写法就没法核对是同一张证件）"""
    r = json.loads(m_owner.add_owner(name="归一证件房东", id_number="4600 0519-9001.01123x"))
    assert r["owner"]["id_number"] == "46000519900101123X", r["owner"]["id_number"]


def test_owner_id_never_leaks_into_db_file_when_encrypted(wired):
    r = json.loads(m_owner.add_owner(name="文件房东", id_number=ID_FULL))
    assert r["success"] is True
    data = open(_db_path(wired), "rb").read()
    assert ID_FULL.encode() not in data, "加密模式下全号不该出现在库文件里"


def test_owner_list_and_property_lookup_give_full_id(wired):
    o = json.loads(m_owner.add_owner(name="列表房东", phone="13800002222", id_number=ID_FULL))["owner"]
    p = wired.add_property(title="证件房源", community="某小区", district="美兰", price=1_000_000,
                           area=80.0, property_type="second_hand", status="available",
                           owner_id=o["id"])
    lst = json.loads(m_owner.list_owners())
    assert [x for x in lst["owners"] if x["id"] == o["id"]][0]["id_number"] == ID_FULL
    assert "note_id_full_missing" not in lst
    by_prop = json.loads(m_owner.get_property_owners(property_ids=[p["id"]]))
    assert by_prop["properties"][0]["owner"]["id_number"] == ID_FULL, by_prop
    assert ID_FULL in by_prop["message"]


def test_owner_id_change_history_keeps_only_mask(wired):
    oid = json.loads(m_owner.add_owner(name="留痕房东", id_number=ID_FULL))["owner"]["id"]
    new_id = "460005199002022345"
    r = json.loads(m_owner.update_owner(owner_id=oid, id_number=new_id))
    assert r["success"] is True and "身份证" in r["message"], r
    con = sqlite3.connect(_db_path(wired))
    try:
        rows = con.execute("SELECT field, old_value, new_value FROM re_owner_changes "
                           "WHERE owner_id=? AND field IN ('id_number','id_masked')",
                           (oid,)).fetchall()
    finally:
        con.close()
    assert rows, "改证件号必须留痕"
    for field, old_value, new_value in rows:
        for value in (old_value, new_value):
            assert value is None or ("*" in value and ID_FULL not in value and new_id not in value), \
                (field, value)
    assert _raw("re_owners", "id_number", oid, wired).startswith("gAAAA")
    assert json.loads(m_owner.get_owner(owner_id=oid))["owner"]["id_number"] == new_id


def test_legacy_owner_without_full_id_gets_backfill_hint(wired):
    """改动前登记的房东只有脱敏号：如实说明要补录，不能假装库里有全号"""
    legacy = wired.add_owner(name="老房东", phone="13800003333", id_masked=ID_MASKED)
    detail = json.loads(m_owner.get_owner(owner_id=legacy["id"]))
    assert detail["owner"]["id_number"] is None
    assert "补录" in detail["note_id_full_missing"], detail
    lst = json.loads(m_owner.list_owners())
    assert "补录" in lst["note_id_full_missing"], lst
    # 补录一次之后，提示消失、全号可查
    json.loads(m_owner.update_owner(owner_id=legacy["id"], id_number=ID_FULL))
    after = json.loads(m_owner.get_owner(owner_id=legacy["id"]))
    assert after["owner"]["id_number"] == ID_FULL and "note_id_full_missing" not in after


# ==================== ② 客户：同一套口径 ====================

def test_customer_id_ciphertext_and_full_everywhere(wired):
    c = json.loads(m_customer.add_customer(name="加密客户", phone="13900004444",
                                           id_number=CID_FULL))["customer"]
    assert _raw("re_customers", "id_number", c["id"], wired).startswith("gAAAA")
    assert json.loads(m_customer.get_customer(customer_id=c["id"]))["customer"]["id_number"] == CID_FULL
    listed = json.loads(m_customer.list_customers())["customers"]
    assert [x for x in listed if x["id"] == c["id"]][0]["id_number"] == CID_FULL


def test_customer_id_change_history_keeps_only_mask(wired):
    cid = json.loads(m_customer.add_customer(name="留痕客户", id_number=CID_FULL))["customer"]["id"]
    new_id = "110101200002021234"
    r = json.loads(m_customer.update_customer(customer_id=cid, id_number=new_id))
    assert r["success"] is True and r["customer"]["id_number"] == new_id, r
    con = sqlite3.connect(_db_path(wired))
    try:
        rows = con.execute("SELECT field, old_value, new_value FROM re_customer_changes "
                           "WHERE customer_id=? AND field='id_number'", (cid,)).fetchall()
    finally:
        con.close()
    assert rows, "改客户证件号必须留痕"
    for _field, old_value, new_value in rows:
        for value in (old_value, new_value):
            assert value is None or ("*" in value and CID_FULL not in value and new_id not in value)


# ==================== ③ 密钥不一致：走展示防御，不给乱码 ====================

def test_wrong_key_defends_id_instead_of_printing_ciphertext(enc_db, monkeypatch):
    monkeypatch.setattr(m_owner, "_get_db", lambda _db=enc_db: _db)
    monkeypatch.setattr(m_customer, "_get_db", lambda _db=enc_db: _db)
    oid = json.loads(m_owner.add_owner(name="换钥房东", id_number=ID_FULL))["owner"]["id"]
    cid = json.loads(m_customer.add_customer(name="换钥客户", id_number=CID_FULL))["customer"]["id"]

    monkeypatch.setenv("COCO_ENC_KEY", Fernet.generate_key().decode())   # 换了机器/丢了密钥文件

    detail = json.loads(m_owner.get_owner(owner_id=oid))
    assert detail["owner"]["id_number"] == KEY_MISMATCH_HINT, detail
    assert "id_number" in detail["cipher_fields"]
    assert detail["warning_key_mismatch"].startswith("房东联系方式读不出来")

    cust = json.loads(m_customer.get_customer(customer_id=cid))
    assert cust["customer"]["id_number"] == KEY_MISMATCH_HINT, cust
    assert "id_number" in cust["cipher_fields"]

    lst = json.loads(m_owner.list_owners())
    assert all("gAAAA" not in json.dumps(x, ensure_ascii=False) for x in lst["owners"])
