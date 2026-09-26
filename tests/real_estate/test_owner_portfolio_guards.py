"""owner_portfolio 房东名下房源组合：密文防御、展示口径、上限与排序、统计口径（2026-09-25）

覆盖 F97–F100：
① F97 密钥不一致时 owner 段联系方式是 `gAAAA…` → 过展示防御 + warning_key_mismatch/cipher_fields；
② F98 展示口径自己另写一套：**出租房源的租金显示成 0 万**、状态直出英文 `[available]`
   → 复用房源侧 `_fmt_price`（出租 → 元/月）与 `_STATUS_LABELS`（在售/已售/已租）；
③ F99 名下房源无上限、无截断告知、最早优先（303 套 → 197.5KB / 607 行）
   → 加 limit（默认 50、最多 200、≤0 与非数字按默认）+ count/truncated + 截断说明 + 最新优先，
   且**统计数字始终按名下全部房源算**（不受明细截断影响）；
④ F100 `owner_id` 传非数字文本时假称「房东不存在」→ 用 `norm_id` 给编号形态提示。
"""
import json

import pytest

from tools.real_estate_owner import add_owner, owner_portfolio

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
    return json.loads(owner_portfolio(**kwargs))


def _owner(db, name="房东周八", phone=None, **kw):
    return json.loads(add_owner(name=name, phone=phone, **kw))["owner"]["id"]


def _props(db, oid, n, prefix="名下房源", ptype="second_hand", status="available", price=1500000):
    from agent.real_estate_db import Property
    s = db.get_session()
    s.bulk_save_objects([Property(title=f"{prefix}{i}", price=price, area=80.0,
                                  property_type=ptype, status=status, owner_id=oid)
                         for i in range(1, n + 1)])
    s.commit()


# ---------- ② 展示口径（复用房源侧） ----------

def test_rental_price_shows_monthly_rent_not_zero_wan(tool_db):
    """出租房源租金 2500 元/月，原先显示成「0万」"""
    oid = _owner(tool_db)
    _props(tool_db, oid, 1, prefix="在租房", ptype="rental", price=2500)
    msg = _call(owner_id=oid)["message"]
    line = next(ln for ln in msg.splitlines() if "在租房" in ln)
    assert "2500元/月" in line, line
    assert "0万" not in line, line


def test_status_is_chinese_not_raw_enum(tool_db):
    oid = _owner(tool_db)
    _props(tool_db, oid, 1, prefix="在售房", status="available")
    _props(tool_db, oid, 1, prefix="已售房", status="sold")
    msg = _call(owner_id=oid)["message"]
    assert "[在售]" in msg and "[已售]" in msg, msg
    assert not any(f"[{s}]" in msg for s in ("available", "sold", "rented")), msg


def test_sale_price_still_in_wan(tool_db):
    oid = _owner(tool_db)
    _props(tool_db, oid, 1, prefix="出售房", price=1500000)
    msg = _call(owner_id=oid)["message"]
    assert "150万" in msg, msg


# ---------- ① 密文防御 ----------

def test_ciphertext_owner_contacts_not_shown(enc_tool_db, monkeypatch):
    oid = _owner(enc_tool_db, name="房东陈六", phone="13800002222", wechat="wx_chenliu")
    monkeypatch.setenv("COCO_ENC_KEY", _MISMATCH_KEY)
    r = _call(owner_id=oid)
    blob = json.dumps(r, ensure_ascii=False)
    assert "gAAAA" not in blob, r
    assert "读取失败" in (r["owner"]["phone"] or ""), r
    assert r.get("warning_key_mismatch") and set(r.get("cipher_fields") or []) == {"phone", "wechat"}, r


def test_no_warning_when_key_is_fine(enc_tool_db):
    oid = _owner(enc_tool_db, phone="13800003333")
    r = _call(owner_id=oid)
    assert r["owner"]["phone"] == "13800003333"
    assert "warning_key_mismatch" not in r, r


# ---------- ③ 上限 / 截断 / 排序 / 统计口径 ----------

@pytest.mark.parametrize("bad", [0, -1, -100, "abc", None, ""])
def test_bad_limit_falls_back_to_default(tool_db, bad):
    oid = _owner(tool_db)
    _props(tool_db, oid, 60)
    r = _call(owner_id=oid, limit=bad)
    assert len(r["properties"]) == 50, (bad, len(r["properties"]))
    assert r["stats"]["total"] == 60, r


def test_huge_limit_capped_at_200_and_stats_are_full(tool_db):
    """明细有上限，但统计必须按名下全部房源算（不能被截断影响）"""
    oid = _owner(tool_db)
    _props(tool_db, oid, 303)
    r = _call(owner_id=oid, limit=100000)
    assert len(r["properties"]) == 200, len(r["properties"])
    assert r["count"] == 200 and r["stats"]["total"] == 303, r
    assert r["truncated"] is True, r
    assert "共 303 套房源" in r["message"] and "最新登记" in r["message"], r["message"].splitlines()[:2]


def test_no_truncation_note_when_few(tool_db):
    oid = _owner(tool_db)
    _props(tool_db, oid, 3)
    r = _call(owner_id=oid)
    assert r["count"] == 3 and r["truncated"] is False, r
    assert "套房源，这里列出" not in r["message"], r


def test_properties_are_newest_first(tool_db):
    oid = _owner(tool_db)
    _props(tool_db, oid, 60)
    r = _call(owner_id=oid)
    assert r["properties"][0]["title"] == "名下房源60", [p["title"] for p in r["properties"]][:3]


def test_stats_closed_and_matching_library(tool_db):
    oid = _owner(tool_db)
    _props(tool_db, oid, 2, prefix="在售", status="available")
    _props(tool_db, oid, 1, prefix="已售", status="sold")
    _props(tool_db, oid, 1, prefix="已租", status="rented")
    stats = _call(owner_id=oid)["stats"]
    assert stats == {"total": 4, "available": 2, "dealed": 2}, stats
    assert stats["available"] + stats["dealed"] == stats["total"]


# ---------- ④ 编号形态与空态 ----------

def test_nonnumeric_id_gets_readable_hint(tool_db):
    r = _call(owner_id="abc")
    assert r["success"] is False, r
    assert "房东编号没能识别" in r["error"], r
    assert "房东不存在" not in r["error"], r


def test_owner_without_properties_is_not_confused_with_missing_owner(tool_db):
    oid = _owner(tool_db, name="名下无房的房东")
    r = _call(owner_id=oid)
    assert r["success"] is True and r["stats"]["total"] == 0, r
    assert "名下暂无房源" in r["message"], r
    assert "房东不存在" not in json.dumps(r, ensure_ascii=False), r
    miss = _call(owner_id=999999)
    assert miss["success"] is False and "房东不存在" in miss["error"], miss


# ---------- 与 get_owner 口径一致 + schema ----------

def test_owner_block_matches_get_owner(tool_db):
    from tools.real_estate_owner import get_owner
    oid = _owner(tool_db, name="口径房东", phone="13800007777", wechat="wx_kj")
    one = json.loads(get_owner(owner_id=oid))["owner"]
    pf = _call(owner_id=oid)["owner"]
    assert all(one[k] == pf[k] for k in ("name", "phone", "wechat", "trust_note", "id_masked")), (one, pf)


def test_schema_advertises_limit_and_capability():
    from tools.registry import registry
    schema = registry.get_entry("owner_portfolio").schema
    desc = schema["description"]
    assert all(w in desc for w in ("房源", "统计", "出租按月租")), desc
    limit_desc = schema["parameters"]["properties"]["limit"]["description"]
    assert "最多 200" in limit_desc and "按默认" in limit_desc and "全部房源" in limit_desc, limit_desc
