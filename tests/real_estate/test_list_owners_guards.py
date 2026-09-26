"""list_owners 房东列表：密文防御、limit 边界、默认最新优先、count/total/truncated（2026-09-25）

覆盖 F92–F96：
① F92 密钥不一致时整页联系方式都是 `gAAAA…` → 每行过展示防御 + warning_key_mismatch/cipher_fields；
② F93 limit 边界：0 谎报"没有房东"、非数字直接崩、负数/显式 null/超大都是**全量**
   （1.2 万库实测 2.0MB、0.65 秒）→ 照客户侧口径：默认 50、上限 200、≤0 与非数字按默认；
③ F94 `total` 报的是本页条数、没有 count/truncated → 用 SQL 聚合求总数并告知截断；
④ F95 默认最早优先，刚登记的房东在默认列表里看不见 → 默认最新登记优先；
⑤ F96 描述 4 个字 → 换成写清"给什么、默认顺序、返回口径"。
"""
import json

import pytest

from tools.real_estate_owner import add_owner, list_owners

_MISMATCH_KEY = "bWlzbWF0Y2gta2V5LXVzZWQtaW4tdGVzdHMtMzJieXQ="


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_owner._get_db", lambda: db)
    return db


@pytest.fixture
def enc_tool_db(enc_db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_owner._get_db", lambda: enc_db)
    return enc_db


def _call(**kwargs):
    return json.loads(list_owners(**kwargs))


def _add(db, name, phone=None):
    return db.add_owner(name=name, phone=phone)


def _bulk(db, n, prefix="批量房东"):
    from agent.real_estate_db import Owner
    s = db.get_session()
    s.bulk_save_objects([Owner(name=f"{prefix}{i}") for i in range(1, n + 1)])
    s.commit()


# ---------- ① 密文防御 ----------

def test_page_of_ciphertext_is_never_shown(enc_tool_db, monkeypatch):
    """真换一把密钥 → 整页联系方式都读不出来，必须逐行换成可读提示 + 带警告字段"""
    _add(enc_tool_db, "房东甲", "13800001111")
    _add(enc_tool_db, "房东乙", "13800002222")
    monkeypatch.setenv("COCO_ENC_KEY", _MISMATCH_KEY)
    r = _call()
    blob = json.dumps(r, ensure_ascii=False)
    assert "gAAAA" not in blob, r
    assert all("读取失败" in (o.get("phone") or "") for o in r["owners"]), r
    assert r.get("warning_key_mismatch") and set(r.get("cipher_fields") or []) == {"phone"}, r


def test_no_warning_when_key_is_fine(enc_tool_db):
    _add(enc_tool_db, "房东甲", "13800001111", )
    r = _call()
    assert r["owners"][0]["phone"] == "13800001111"
    assert "warning_key_mismatch" not in r, r


# ---------- ② limit 边界 ----------

@pytest.mark.parametrize("bad", [0, -1, -100, "abc", None, "", "  "])
def test_bad_limit_falls_back_to_default_never_empty_or_full(tool_db, bad):
    _bulk(tool_db, 60)
    r = _call(limit=bad)
    assert r["success"] is True, r
    assert len(r["owners"]) == 50, (bad, len(r["owners"]))       # 默认 50，不是 0 条、也不是全量
    assert r["total"] == 60, r


def test_huge_limit_is_capped_at_200(tool_db):
    """1.2 万库上原先传 100000 会返回全量（2MB）"""
    _bulk(tool_db, 260)
    r = _call(limit=100000)
    assert len(r["owners"]) == 200, len(r["owners"])
    assert r["total"] == 260 and r["truncated"] is True, r


def test_limit_within_range_respected(tool_db):
    _bulk(tool_db, 30)
    assert len(_call(limit=1)["owners"]) == 1
    assert len(_call(limit=7)["owners"]) == 7
    assert len(_call(limit="7")["owners"]) == 7      # 数字形态的字符串


# ---------- ③ total / count / truncated ----------

def test_total_is_library_count_not_page_size(tool_db):
    _bulk(tool_db, 64)
    r = _call()
    assert r["count"] == 50 and r["total"] == 64 and r["truncated"] is True, r
    assert "共 64 位房东" in r["message"] and "这里列最近 50 位" in r["message"], r


def test_no_message_when_not_truncated(tool_db):
    _bulk(tool_db, 3)
    r = _call()
    assert r["count"] == 3 and r["total"] == 3 and r["truncated"] is False, r
    assert "message" not in r, r


# ---------- ④ 默认最新登记优先 ----------

def test_newest_owner_is_on_the_first_page(tool_db):
    """先造 60 位、再登记 1 位：新登记的要出现在默认第一页（原先排在最后一页，看不见）"""
    _bulk(tool_db, 60)
    _add(tool_db, "最新登记的房东", "13900009999")
    r = _call()
    assert r["owners"][0]["name"] == "最新登记的房东", [o["name"] for o in r["owners"]][:3]


def test_order_is_newest_first(tool_db):
    _add(tool_db, "第一个")
    _add(tool_db, "第二个")
    _add(tool_db, "第三个")
    assert [o["name"] for o in _call()["owners"]] == ["第三个", "第二个", "第一个"]


# ---------- ⑤ 描述与口径一致 ----------

def test_description_and_limit_hint():
    from tools.registry import registry
    schema = registry.get_entry("list_owners").schema
    desc = schema["description"]
    assert all(w in desc for w in ("姓名", "手机号", "微信号", "房东总数", "truncated")), desc
    limit_desc = schema["parameters"]["properties"]["limit"]["description"]
    assert "最多 200" in limit_desc and "按默认" in limit_desc, limit_desc


def test_list_and_detail_agree(tool_db):
    oid = json.loads(add_owner(name="口径房东", phone="13800007777", wechat="wx_kj",
                               id_number="460005199001011234"))["owner"]["id"]
    row = _call()["owners"][0]
    assert row["id"] == oid
    assert row["phone"] == "13800007777" and row["wechat"] == "wx_kj"
    assert row["id_masked"] == "4600" + "*" * 10 + "1234", row
