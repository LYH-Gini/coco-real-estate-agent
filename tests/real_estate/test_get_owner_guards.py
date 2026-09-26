"""get_owner 房东详情：密钥不一致不露密文、编号形态给中文提示（2026-09-25）

覆盖 F89–F90：
① F89 密钥不一致时读出来是密文 —— 绝不能把 `gAAAA…` 当房东电话交给上层（客户侧 F46、
   房源侧 F16 同口径）；要带 warning_key_mismatch + cipher_fields 让上层如实转述，非加密字段照常。
② F90 描述只有 6 个字 → 换成"给什么、什么时候用"（模型靠 description 判断能力）。
③ 编号形态（'abc' / true / 超大数字串 / 不存在）都给中文提示，别静默说"房东不存在"。
"""
import json

import pytest

from tools.real_estate_owner import add_owner, get_owner


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_owner._get_db", lambda: db)
    return db


@pytest.fixture
def enc_tool_db(enc_db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_owner._get_db", lambda: enc_db)
    return enc_db


def _call(**kwargs):
    return json.loads(get_owner(**kwargs))


def _add(name="房东陈六", **kw):
    return json.loads(add_owner(name=name, **kw))["owner"]["id"]


# ---------- ① 密钥不一致（密文防御） ----------

def test_ciphertext_phone_is_not_shown_when_key_mismatch(enc_tool_db, monkeypatch, tmp_path):
    """真换一把密钥（换机器/恢复备份没带密钥）→ 读出来的是密文，必须展示成可读提示"""
    oid = _add(name="房东陈六", phone="13800002222", wechat="wx_chenliu")
    monkeypatch.setenv("COCO_ENC_KEY", "bWlzbWF0Y2gta2V5LXVzZWQtaW4tdGVzdHMtMzJieXQ=")
    r = _call(owner_id=oid)
    blob = json.dumps(r, ensure_ascii=False)
    assert "gAAAA" not in blob, r
    assert r["owner"]["phone"] != "13800002222"
    assert "读取失败" in (r["owner"]["phone"] or ""), r      # KEY_MISMATCH_HINT：可读提示而非乱码
    assert "读不出来" in (r.get("warning_key_mismatch") or ""), r
    assert set(r.get("cipher_fields") or []) == {"phone", "wechat"}, r


def test_plaintext_mode_does_not_warn(enc_tool_db):
    """密钥一致（正常情况）时不得误报 warning —— 否则用户会以为联系方式坏了"""
    oid = _add(name="房东正常", phone="13800003333", wechat="wx_ok")
    r = _call(owner_id=oid)
    assert r["owner"]["phone"] == "13800003333"
    assert r["owner"]["wechat"] == "wx_ok"
    assert "warning_key_mismatch" not in r, r


def test_non_encrypted_fields_still_returned_on_key_mismatch(enc_tool_db, monkeypatch):
    """密钥不一致只影响加密字段：姓名/脱敏证件/备注照常给"""
    oid = _add(name="房东陈六", phone="13800002222", id_number="460005199001011234",
               trust_note="配合带看")
    monkeypatch.setenv("COCO_ENC_KEY", "bWlzbWF0Y2gta2V5LXVzZWQtaW4tdGVzdHMtMzJieXQ=")
    r = _call(owner_id=oid)
    assert r["success"] is True
    assert r["owner"]["name"] == "房东陈六"
    assert r["owner"]["id_masked"] == "4600" + "*" * 10 + "1234"
    assert r["owner"]["trust_note"] == "配合带看"


def test_missing_contact_is_null_not_empty_string(tool_db):
    oid = _add(name="只有名字")
    r = _call(owner_id=oid)
    assert r["owner"]["phone"] is None and r["owner"]["wechat"] is None
    assert "warning_key_mismatch" not in r


# ---------- ③ 编号形态 ----------

def test_nonnumeric_id_gets_readable_hint(tool_db):
    """编号传 'abc' → 说清"编号是数字"，而不是假称"房东不存在"（t29c 实测 7 个工具都只说"不存在"）"""
    r = _call(owner_id="abc")
    assert r["success"] is False, r
    assert "房东编号没能识别" in r["error"] and "abc" in r["error"], r
    assert "房东不存在" not in r["error"], r


@pytest.mark.parametrize("value", [True, False])
def test_bool_id_gets_readable_hint(tool_db, value):
    """`owner_id=true` 原先会被当成 1、返回 id=1 那位房东的资料（张冠李戴）"""
    _add(name="第一位房东", phone="13800001111")
    r = _call(owner_id=value)
    assert r["success"] is not True, r
    assert "没能识别" in r["error"] and "是数字" in r["error"], r
    assert "房东不存在" not in r["error"], r


def test_numeric_string_id_still_works(tool_db):
    """数字形态的字符串要能认出同一个编号（'3' 与 3 是同一人）"""
    _add(name="房东一")
    oid = _add(name="房东三")
    for form in (str(oid), f" {oid} "):
        r = _call(owner_id=form)
        assert r["success"] is True and r["owner"]["id"] == oid, (form, r)


def test_nonexistent_id_says_not_found(tool_db):
    r = _call(owner_id=999999)
    assert r["success"] is False and "房东不存在" in r["error"], r


def test_huge_numeric_string_is_rejected_by_range_check(tool_db):
    """20 位数字串：形态归一成整数后被框架层越界校验拦住（原先 sqlite 静默说"不存在"、PG 报错）"""
    from tools.registry import registry
    raw = registry.dispatch("get_owner", {"owner_id": "99999999999999999999"}, session_id="pytest")
    r = json.loads(raw) if isinstance(raw, str) else raw
    assert r.get("success") is not True, r
    assert "参数超出范围" in (r.get("error") or ""), r


# ---------- ② 描述 ----------

def test_description_names_capabilities():
    from tools.registry import registry
    desc = registry.get_entry("get_owner").schema["description"]
    for word in ("姓名", "手机号", "微信号", "身份证", "备注", "房东不存在"):
        assert word in desc, (word, desc)
