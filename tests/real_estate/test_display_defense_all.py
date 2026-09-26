"""联系方式展示防御收口回归（2026-09-25，同族收口批任务 3）

背景：`EncryptedString` 在密钥不一致（换机器、恢复备份没带密钥文件）时会把库里的密文
**原样返回**。此前客户侧 / 业主侧 / 房源侧各写了一套 `_safe_contact` / `_mask_*_contacts` /
`_attach_key_warning`（三份实现、三处容易漏改），实测每次都只补了一半：
结构体没走、写路径没走。现在收成 `agent/real_estate_display.py` 一份，工具模块只 import。

本文件锁住两件事：
① 共用实现本身（三种文案都要在、空值/明文不被改坏）；
② **所有会展示联系方式的出口**在密钥不一致时都不许出现 `gAAAA`，且要给可读提示
   （读路径 + 写入路径 + 结构体）；正常密钥时联系方式照旧给全。
"""
import json

import pytest
from cryptography.fernet import Fernet

from agent import real_estate_display as disp
from tools import (real_estate_birthday as m_birthday, real_estate_customer as m_customer,
                   real_estate_followup as m_followup, real_estate_owner as m_owner,
                   real_estate_property as m_property)

MODULES = [m_birthday, m_customer, m_followup, m_owner, m_property]
CPHONE, OPHRONE = "13900001234", "13700005678"


@pytest.fixture
def wired(enc_db, monkeypatch):
    for mod in MODULES:
        monkeypatch.setattr(mod, "_get_db", lambda _db=enc_db: _db)
    return enc_db


@pytest.fixture
def mismatched(monkeypatch):
    """把密钥换成另一把 → 库里内容解不开（真实的"换了机器/丢了密钥文件"场景）"""
    monkeypatch.setenv("COCO_ENC_KEY", Fernet.generate_key().decode())


@pytest.fixture
def data(wired):
    c = wired.add_customer(name="防御客户", phone=CPHONE, wechat="wx_fangyu",
                           tier="B", customer_type="buy_second_hand", status="active")
    o = wired.add_owner(name="防御房东", phone=OPHRONE, wechat="wx_fd")
    p = wired.add_property(title="防御房源", price=1_500_000, area=80.0,
                           property_type="second_hand", status="available",
                           owner_id=o["id"])
    wired.add_referral(referrer_customer_id=c["id"], referred_name="被介绍人",
                       referred_phone="13600009876")
    assert p["title"] == "防御房源" and p["owner_id"] == o["id"]   # 夹具真的落地了
    return {"customer": c["id"], "owner": o["id"], "property": p["id"]}


# ==================== ① 共用实现 ====================
def test_three_warning_texts_kept():
    """三种文案刻意保留：不同实体说不同的话（别合并成一句）"""
    assert disp.CUSTOMER_KEY_MISMATCH_WARNING.startswith("客户联系方式读不出来")
    assert disp.OWNER_KEY_MISMATCH_WARNING.startswith("房东联系方式读不出来")
    assert disp.PERSON_KEY_MISMATCH_WARNING.startswith("客户/业主的联系方式读不出来")
    for text in (disp.CUSTOMER_KEY_MISMATCH_WARNING, disp.OWNER_KEY_MISMATCH_WARNING,
                 disp.PERSON_KEY_MISMATCH_WARNING):
        assert "密钥文件" in text and "不要把这条联系方式给客户" in text


def test_safe_contact_keeps_plaintext_and_blanks_ciphertext():
    assert disp.safe_contact("13900001234") == "13900001234"
    assert disp.safe_contact(None) is None and disp.safe_contact("") is None
    cipher = Fernet(Fernet.generate_key()).encrypt(b"13900001234").decode()   # 真密文形态
    assert disp.safe_contact(cipher) == "读取失败（密钥不一致，请检查备份的密钥文件）"


def test_mask_contacts_reports_only_changed_fields():
    row = {"phone": "13900001234", "wechat": None, "name": "甲"}
    out, masked = disp.mask_contacts(row)
    assert masked == [] and out["phone"] == "13900001234" and out["wechat"] is None


def test_attach_key_warning_noop_without_masked_fields():
    payload = {"success": True}
    assert disp.attach_key_warning(payload, []) == {"success": True}


def test_attach_key_warning_text_is_pickable():
    payload = disp.attach_key_warning({"success": True}, ["phone"], disp.OWNER_KEY_MISMATCH_WARNING)
    assert payload["warning_key_mismatch"] == disp.OWNER_KEY_MISMATCH_WARNING
    assert payload["cipher_fields"] == ["phone"]


# ==================== ② 全库出口：密钥不一致不泄密文 ====================
# (用例名, 调用, 会不会展示联系方式)
EXITS = [
    ("get_customer", lambda d: m_customer.get_customer(customer_id=d["customer"]), True),
    ("list_customers", lambda d: m_customer.list_customers(), True),
    ("update_customer", lambda d: m_customer.update_customer(customer_id=d["customer"],
                                                             notes="x"), True),
    ("update_tier", lambda d: m_customer.update_tier(customer_id=d["customer"], tier="A"), True),
    ("update_birthday", lambda d: m_birthday.update_birthday(customer_id=d["customer"],
                                                             birthday="1990-01-01"), True),
    ("churn_warning", lambda d: m_followup.churn_warning(min_risk=30), True),
    ("get_owner", lambda d: m_owner.get_owner(owner_id=d["owner"]), True),
    ("list_owners", lambda d: m_owner.list_owners(), True),
    ("owner_portfolio", lambda d: m_owner.owner_portfolio(owner_id=d["owner"]), True),
    ("get_property_owners", lambda d: m_owner.get_property_owners(property_ids=[d["property"]]),
     True),
    ("get_property_detail", lambda d: m_property.get_property_detail(property_id=d["property"]),
     True),
    ("find_person_by_name", lambda d: m_owner.find_person_by_name(name="防御"), True),
    ("match_property", lambda d: m_property.match_property(customer_id=d["customer"]), False),
    ("price_drop_alerts", lambda d: m_property.price_drop_alerts(), False),
]

EXIT_IDS = [e[0] for e in EXITS]


@pytest.mark.parametrize("name,call,shows_contact", EXITS, ids=EXIT_IDS)
def test_normal_key_still_shows_contacts(data, name, call, shows_contact):
    """正常密钥：该给的完整联系方式照旧给（别为了防御把功能删了）"""
    blob = json.dumps(json.loads(call(data)), ensure_ascii=False)
    assert "gAAAA" not in blob, (name, blob[:200])
    if shows_contact:
        assert CPHONE in blob or OPHRONE in blob, (name, blob[:200])


@pytest.mark.parametrize("name,call,shows_contact", EXITS, ids=EXIT_IDS)
def test_mismatched_key_never_leaks_ciphertext(data, mismatched, name, call, shows_contact):
    """密钥不一致：任何出口都不许出现密文；会展示联系方式的出口必须给可读提示"""
    out = json.loads(call(data))
    blob = json.dumps(out, ensure_ascii=False)
    assert "gAAAA" not in blob, (name, blob[:300])
    assert "Tool execution failed" not in blob, (name, blob[:200])
    if shows_contact:
        assert out.get("warning_key_mismatch"), (name, blob[:300])
        assert "读取失败（密钥不一致" in blob, (name, blob[:300])


def test_warning_text_matches_the_entity(data, mismatched):
    """客户出口说客户、房东出口说房东、按姓名查人说两家 —— 文案不能串台"""
    assert json.loads(m_customer.get_customer(customer_id=data["customer"]))[
        "warning_key_mismatch"] == disp.CUSTOMER_KEY_MISMATCH_WARNING
    assert json.loads(m_owner.get_owner(owner_id=data["owner"]))[
        "warning_key_mismatch"] == disp.OWNER_KEY_MISMATCH_WARNING
    assert json.loads(m_owner.find_person_by_name(name="防御"))[
        "warning_key_mismatch"] == disp.PERSON_KEY_MISMATCH_WARNING
    assert json.loads(m_property.get_property_detail(property_id=data["property"]))[
        "warning_key_mismatch"] == disp.OWNER_KEY_MISMATCH_WARNING


def test_churn_warning_message_has_no_internal_tool_names(data, wired):
    """流失预警的对外文案里不许出现内部工具名/模板键（Coco 转述给经纪人时会被看到）"""
    out = json.loads(m_followup.churn_warning(min_risk=30))
    assert out.get("customers"), out
    message = out.get("message") or ""
    assert "use_template" not in message and "winback_" not in message, message
    assert "挽回模板" in message, message
