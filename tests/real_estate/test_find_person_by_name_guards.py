"""find_person_by_name 按姓名查人：结构体密文防御、预算单位、通配符、上限（2026-09-25）

覆盖 F105–F108：
① F105 结构体里的客户/业主联系方式仍是 `gAAAA…`（message 走了 `_safe_contact`、结构体没走）
   → 两段各过自己的掩码函数 + 合并版 warning + `cipher_fields`；
② F106 客户预算把元当万（`预算 3000000-5000000万`）→ 按万说、低于 1 万按元说；
③ F107 姓名走 LIKE 未转义：传 `%`/`_` 会把全库名单捞出来 → 改用 `instr()`/`strpos()` 子串匹配；
④ F108 命中无上限、无 total/truncated（1.2 万+1.2 万库返回 8.5MB）→ 加 limit（默认 20/最多 200）
   与两表各自的 total/truncated + 说明。
"""
import json

import pytest

from tools.real_estate_owner import find_person_by_name

_MISMATCH_KEY = "bWlzbWF0Y2gta2V5LXVzZWQtaW4tdGVzdHMtMzJieXQ="


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_owner._get_db", lambda: db)
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)   # 口径对照要读客户详情
    return db


@pytest.fixture
def enc_tool_db(enc_db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_owner._get_db", lambda: enc_db)
    return enc_db


def _call(**kwargs):
    return json.loads(find_person_by_name(**kwargs))


def _fixture(db, n_customers=0):
    db.add_customer(name="欧阳先生", phone="13900001111", wechat="wx_oy", tier="A",
                    customer_type="buy_second_hand", budget_min=3_000_000, budget_max=5_000_000,
                    location="美兰区", layout_pref="3室2厅")
    db.add_owner(name="欧阳先生", phone="13900002222", wechat="wx_oy_owner",
                 id_masked="4600" + "*" * 10 + "1234", trust_note="价格坚挺")
    for i in range(n_customers):
        db.add_customer(name=f"欧阳同{i}", customer_type="unspecified")


def _line(msg, tag):
    return next((ln for ln in msg.splitlines() if tag in ln), "")


# ---------- ① 结构体密文防御 ----------

def test_structures_have_no_ciphertext_on_key_mismatch(enc_tool_db, monkeypatch):
    _fixture(enc_tool_db)
    monkeypatch.setenv("COCO_ENC_KEY", _MISMATCH_KEY)
    r = _call(name="欧阳")
    blob = json.dumps(r, ensure_ascii=False)
    assert "gAAAA" not in blob, r
    for row in r["customers"] + r["owners"]:
        assert "读取失败" in (row["phone"] or ""), row
    assert "客户/业主的联系方式读不出来" in (r.get("warning_key_mismatch") or ""), r
    assert set(r.get("cipher_fields") or []) == {"phone", "wechat"}, r


def test_no_warning_when_key_is_fine(enc_tool_db):
    _fixture(enc_tool_db)
    r = _call(name="欧阳")
    assert r["customers"][0]["phone"] == "13900001111"
    assert r["owners"][0]["phone"] == "13900002222"
    assert "warning_key_mismatch" not in r, r


# ---------- ② 预算口径 ----------

@pytest.mark.parametrize("raw_min,raw_max,expected", [
    (3_000_000, 5_000_000, "预算 300万-500万"),
    (5000, None, "预算 5000元"),
    (None, None, "预算 -"),
    (1_850_000, None, "预算 185万"),
])
def test_budget_display_uses_wan_for_large_and_yuan_for_small(tool_db, raw_min, raw_max, expected):
    tool_db.add_customer(name="预算客户", phone="13900008888", customer_type="unspecified",
                         budget_min=raw_min, budget_max=raw_max)
    line = _line(_call(name="预算客户")["message"], "【客户】")
    assert expected in line, line
    assert "万万" not in line and "元万" not in line and "-万" not in line, line


# ---------- ③ 通配符 ----------

@pytest.mark.parametrize("wildcard", ["%", "_", "%王%", "王_", "_阳", "欧%"])
def test_wildcards_are_literal_not_patterns(tool_db, wildcard):
    """传通配符不得把全库名单捞出来（原先 LIKE 未转义，一个 % 就能拿到全部）"""
    _fixture(tool_db)
    tool_db.add_customer(name="王五", phone="13900004444", customer_type="rent")
    r = _call(name=wildcard)
    assert r["count_customers"] == 0 and r["count_owners"] == 0, (wildcard, r["count_customers"], r["count_owners"])


def test_normal_substring_still_matches(tool_db):
    _fixture(tool_db)
    r = _call(name="欧阳")
    assert r["count_customers"] == 1 and r["count_owners"] == 1, r
    assert _call(name="阳先")["count_owners"] == 1, _call(name="阳先")


# ---------- ④ 上限与形状 ----------

@pytest.mark.parametrize("bad", [0, -1, -100, "abc", None, ""])
def test_bad_limit_falls_back_to_default(tool_db, bad):
    _fixture(tool_db, n_customers=40)
    r = _call(name="欧阳", limit=bad)
    assert r["count_customers"] == 20, (bad, r["count_customers"])
    assert r["truncated"] is True and r["total_customers"] == 41, r


def test_huge_limit_capped_and_totals_are_full(tool_db):
    _fixture(tool_db, n_customers=260)
    r = _call(name="欧阳", limit=100000)
    assert r["count_customers"] == 200, r["count_customers"]
    assert r["total_customers"] == 261 and r["truncated"] is True, r
    assert "共命中 客户 261 人" in r["message"] and "跟我说一声我多列一些" in r["message"], r["message"].splitlines()[:2]


def test_no_truncation_note_when_few(tool_db):
    _fixture(tool_db)
    r = _call(name="欧阳")
    assert r["count_customers"] == 1 and r["truncated"] is False, r
    assert "共命中" not in r["message"], r


def test_shape_has_two_sets_of_count_and_total(tool_db):
    _fixture(tool_db)
    r = _call(name="欧阳")
    for key in ("count_customers", "count_owners", "total_customers", "total_owners", "truncated"):
        assert key in r, (key, sorted(r))


# ---------- 空结果与口径一致 ----------

def test_both_tables_empty_says_so(tool_db):
    _fixture(tool_db)
    r = _call(name="查无此人XYZ")
    assert r["success"] is True and r["count_customers"] == 0 and r["count_owners"] == 0, r
    assert "均无此人" in r["message"], r
    assert "13900001111" not in json.dumps(r, ensure_ascii=False), r


def test_customer_block_matches_get_customer(tool_db):
    from tools.real_estate_customer import get_customer
    _fixture(tool_db)
    cid = tool_db.list_customers(limit=1)[0]["id"]
    one = json.loads(get_customer(customer_id=cid))["customer"]
    block = next(c for c in _call(name="欧阳")["customers"] if c["id"] == cid)
    for k in ("name", "phone", "wechat", "tier", "customer_type"):
        assert one[k] == block[k], (k, one[k], block[k])


def test_schema_declares_limit_and_matching_semantics():
    from tools.registry import registry
    schema = registry.get_entry("find_person_by_name").schema
    props = schema["parameters"]["properties"]
    assert props["limit"]["type"] == "integer" and "最多 200" in props["limit"]["description"], props
    assert "子串" in props["name"]["description"], props
    assert "truncated" in schema["description"], schema["description"]
