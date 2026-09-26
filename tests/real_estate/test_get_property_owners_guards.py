"""get_property_owners 按房源反查业主：结构体密文防御、数组元素形态、not_found（2026-09-25）

覆盖 F101–F103：
① F101 密钥不一致时 `properties[].owner` 结构体仍带 `gAAAA…`（message 那句早就走了 `_safe_contact`
   —— F46 那次的形态「只有文案走了、结构体没走」原样重演）；warning 也缺 `cipher_fields`；
② F102 `property_ids=[True]` 会命中 id=1（张冠李戴）、`['abc']` 与整个传字符串会崩、
   `[2**63]` 崩 OverflowError —— 数组元素与顶层标量同一套口径：数字串转整数、布尔拦下、越界点名；
③ F103 编号不存在时静默返回空列表 → 补 `not_found` 与一句「编号 N 没有对应房源」，
   与「未录入业主信息」分开说。
"""
import json

import pytest

from tools.real_estate_owner import get_property_owners

_MISMATCH_KEY = "bWlzbWF0Y2gta2V5LXVzZWQtaW4tdGVzdHMtMzJieXQ="


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_owner._get_db", lambda: db)
    monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
    return db


@pytest.fixture
def enc_tool_db(enc_db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_owner._get_db", lambda: enc_db)
    return enc_db


def _call(**kwargs):
    return json.loads(get_property_owners(**kwargs))


def _fixture(db, owner_phone="13800001111"):
    o = db.add_owner(name="反查业主甲", phone=owner_phone, wechat="wx_fc", trust_note="价格坚挺")
    p = db.add_property(title="反查房源A", price=1_500_000, area=80.0,
                        property_type="second_hand")
    db.update_property(p["id"], owner_id=o["id"], viewing_note="钥匙在门店")
    orphan = db.add_property(title="反查房源B（无业主）", price=1_600_000, area=88.0,
                             property_type="second_hand")
    return o, p, orphan


# ---------- ① 结构体密文防御 ----------

def test_owner_struct_has_no_ciphertext_on_key_mismatch(enc_tool_db, monkeypatch):
    o, p, _ = _fixture(enc_tool_db)
    monkeypatch.setenv("COCO_ENC_KEY", _MISMATCH_KEY)
    r = _call(property_ids=[p["id"]])
    blob = json.dumps(r, ensure_ascii=False)
    struct_owner = r["properties"][0]["owner"]
    assert "gAAAA" not in blob, r
    assert "读取失败" in (struct_owner["phone"] or ""), struct_owner
    assert "读取失败" in (struct_owner["wechat"] or ""), struct_owner
    assert "读取失败" in r["message"], r["message"]
    assert r.get("warning_key_mismatch"), r
    assert set(r.get("cipher_fields") or []) == {"phone", "wechat"}, r


def test_no_warning_when_key_is_fine(enc_tool_db):
    o, p, _ = _fixture(enc_tool_db)
    r = _call(property_ids=[p["id"]])
    assert r["properties"][0]["owner"]["phone"] == "13800001111"
    assert "warning_key_mismatch" not in r, r


def test_non_encrypted_fields_still_returned_on_mismatch(enc_tool_db, monkeypatch):
    o, p, _ = _fixture(enc_tool_db)
    monkeypatch.setenv("COCO_ENC_KEY", _MISMATCH_KEY)
    r = _call(property_ids=[p["id"]])
    assert r["properties"][0]["owner"]["name"] == "反查业主甲"
    assert r["properties"][0]["owner"]["trust_note"] == "价格坚挺"
    assert r["properties"][0]["viewing_note"] == "钥匙在门店"


# ---------- ② 数组元素形态 ----------

def _dispatch(tool, args):
    """框架层行为（布尔/越界）必须走 dispatch —— 直接调函数会绕过前置校验"""
    from tools.registry import registry
    raw = registry.dispatch(tool, args, session_id="pytest")
    return json.loads(raw) if isinstance(raw, str) else raw


@pytest.mark.parametrize("bad", [True, False])
def test_bool_element_is_rejected_with_hint(tool_db, bad):
    """[true] 原先被当成 [1] → 返回 id=1 那套房源（把别人的房当这套房回答）"""
    _fixture(tool_db)
    r = _dispatch("get_property_owners", {"property_ids": [bad]})
    assert r.get("success") is not True, r
    # 数组元素的提示措辞与标量不同（标量：「参数 X 要是数字，收到的是 Y」；数组：「参数 X 里出现了 Y —— 编号要传数字」）
    assert "里出现了" in (r.get("error") or "") and "要传数字" in (r.get("error") or ""), r


@pytest.mark.parametrize("bad", ["abc", 1.5])
def test_non_numeric_element_gets_hint_not_crash(tool_db, bad):
    _fixture(tool_db)
    r = _call(property_ids=[bad])
    assert r.get("success") is not True, r
    assert "房源编号没能识别" in (r.get("error") or ""), r
    assert "Tool execution failed" not in json.dumps(r, ensure_ascii=False), r


def test_non_list_input_is_wrapped_not_crashed(tool_db):
    _fixture(tool_db)
    r = _call(property_ids="abc")
    assert r.get("success") is not True, r
    assert "房源编号没能识别" in (r.get("error") or ""), r


def test_huge_element_gets_range_hint(tool_db):
    _fixture(tool_db)
    r = _dispatch("get_property_owners", {"property_ids": [2 ** 63]})
    assert r.get("success") is not True, r
    assert "参数超出范围" in (r.get("error") or ""), r


def test_numeric_string_element_still_matches(tool_db):
    o, p, _ = _fixture(tool_db)
    r = _call(property_ids=[str(p["id"])])
    assert r["success"] is True and r["properties"][0]["id"] == p["id"], r


def test_over_three_ids_still_rejected(tool_db):
    o, p, orphan = _fixture(tool_db)
    r = _call(property_ids=[p["id"], orphan["id"], 999997, 999998])
    assert r.get("success") is not True and "3 套" in (r.get("error") or ""), r


# ---------- ③ not_found ----------

def test_missing_id_is_reported_separately(tool_db):
    o, p, orphan = _fixture(tool_db)
    r = _call(property_ids=[p["id"], 999999])
    assert r["not_found"] == [999999], r
    assert "999999" in r["message"] and "没有对应房源" in r["message"], r["message"]
    assert r["count"] == 1, r


def test_linked_and_unlinked_owner_are_distinguishable(tool_db):
    o, p, orphan = _fixture(tool_db)
    r = _call(property_ids=[p["id"], orphan["id"]])
    assert r["not_found"] == [], r
    rows = {x["id"]: x for x in r["properties"]}
    assert rows[p["id"]]["owner"]["name"] == "反查业主甲"
    assert rows[orphan["id"]]["owner"] is None
    assert "未录入业主信息" in r["message"], r["message"]


# ---------- 与其它读路径口径一致 ----------

def test_owner_block_matches_other_read_paths(tool_db):
    from tools.real_estate_owner import get_owner
    from tools.real_estate_property import get_property_detail
    o, p, _ = _fixture(tool_db)
    one = _call(property_ids=[p["id"]])["properties"][0]["owner"]
    detail = json.loads(get_property_detail(property_id=p["id"]))["owner"]
    own = json.loads(get_owner(owner_id=o["id"]))["owner"]
    for k in ("name", "phone", "wechat", "trust_note"):
        assert one[k] == detail[k] == own[k], (k, one[k], detail[k], own[k])
