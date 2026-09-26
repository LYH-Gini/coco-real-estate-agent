"""customer_change_history 回归（2026-09-24）：可读的字段名与值、变更总条数与截断提示

覆盖两处修复（2026-09-24 拍板）：
① F76 历史里原本只有英文键与裸数字（`budget_max: '3000000'→'5000000'`、`stage: lead→interested`），
   模型/经纪人得自己翻 —— 每条补 `field_label`（中文名）与 `old_display`/`new_display`（值的中文口径），
   原始 `field`/`old_value`/`new_value` 一律保留不动。
② F77 条数被截断时不告知 —— 补 `total`（变更总条数）+ `truncated` + 截断说明（与 list_customers 同一形状）。
"""
import json
from datetime import datetime

import pytest

from tools.real_estate_customer import customer_change_history, update_customer


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
    return db


def make(db, name="历史客"):
    return db.add_customer(name=name, tier="C", phone="13900001111", wechat="his_wx",
                           budget_min=2_000_000, budget_max=3_000_000,
                           location="海口秀英区", customer_type="buy_second_hand")["id"]


def hist(cid, **kwargs):
    return json.loads(customer_change_history(customer_id=cid, **kwargs))


def change(db, cid, **fields):
    return json.loads(update_customer(customer_id=cid, **fields))


def by_field(payload):
    return {c["field"]: c for c in payload["changes"]}


# ---------- ① 可读性 ----------
def test_every_change_has_chinese_label_and_readable_values(tool_db):
    cid = make(tool_db)
    change(tool_db, cid, budget_max=5_000_000, location="海口美兰区", status="paused")
    rows = hist(cid, limit=50)["changes"]
    assert rows, rows
    for r in rows:
        assert r["field_label"] and r["field_label"] != r["field"], r
        assert "old_display" in r and "new_display" in r, r


def test_budget_values_shown_in_wan(tool_db):
    cid = make(tool_db)
    change(tool_db, cid, budget_max=5_000_000)
    r = by_field(hist(cid, limit=50))["budget_max"]
    assert r["old_display"] == "300万" and r["new_display"] == "500万", r
    assert r["old_value"] == "3000000" and r["new_value"] == "5000000"   # 原值保留


def test_status_and_stage_and_type_values_in_chinese(tool_db):
    from tools.real_estate_customer import update_customer_stage
    cid = make(tool_db)
    change(tool_db, cid, status="paused", customer_type="rent")
    update_customer_stage(customer_id=cid, stage="interested")
    rows = by_field(hist(cid, limit=50))
    assert rows["status"]["new_display"] == "暂缓", rows["status"]
    assert rows["customer_type"]["new_display"] == "租房", rows["customer_type"]
    assert rows["stage"]["new_display"] == "意向" and rows["stage"]["old_display"] == "潜在", rows["stage"]
    # 原始英文键值仍在，别破坏既有消费者
    assert rows["stage"]["new_value"] == "interested"


def test_unset_value_shown_as_placeholder(tool_db):
    cid = make(tool_db)
    change(tool_db, cid, notes="只看南北通透")
    r = by_field(hist(cid, limit=50))["notes"]
    assert r["old_display"] == "（未填）" and r["new_display"] == "只看南北通透", r
    assert r["old_value"] is None


def test_masked_contact_kept_as_is(tool_db):
    cid = make(tool_db)
    change(tool_db, cid, phone="13900002222")
    r = by_field(hist(cid, limit=50))["phone"]
    assert r["field_label"] == "手机号"
    assert "****" in r["old_display"] and "13900001111" not in json.dumps(r, ensure_ascii=False), r


def test_long_text_truncated_in_display_only(tool_db):
    cid = make(tool_db)
    long_note = "很长的备注" * 20
    change(tool_db, cid, notes=long_note)
    r = by_field(hist(cid, limit=50))["notes"]
    assert r["new_display"].endswith("…") and len(r["new_display"]) <= 41, r["new_display"]
    assert r["new_value"] == long_note          # 原值不截断


def test_unknown_field_falls_back_to_key(tool_db):
    """留痕里出现没登记的字段名时不能崩、也不能丢信息"""
    cid = make(tool_db)
    with tool_db.get_session() as s:
        from sqlalchemy import text
        s.execute(text("INSERT INTO re_customer_changes (customer_id, field, old_value, new_value, created_at)"
                       " VALUES (:i, 'weird_field', 'a', 'b', :t)"), {"i": cid, "t": datetime.now()})
        s.commit()
    r = by_field(hist(cid, limit=50))["weird_field"]
    assert r["field_label"] == "weird_field" and r["new_display"] == "b", r


# ---------- ② 总数与截断 ----------
def test_total_and_truncation_reported(tool_db):
    cid = make(tool_db)
    for i in range(25):
        change(tool_db, cid, notes=f"第{i}次")
    r = hist(cid)
    assert r["count"] == 20 and r["total"] == 25 and r["truncated"] is True, r
    assert "共 25 条变更" in r["message"] and "最近 20 条" in r["message"], r.get("message")


def test_no_message_when_everything_returned(tool_db):
    cid = make(tool_db)
    change(tool_db, cid, notes="改一次")
    r = hist(cid, limit=50)
    assert r["count"] == 1 and r["total"] == 1 and r["truncated"] is False
    assert "message" not in r


def test_total_is_full_count_not_page(tool_db):
    cid = make(tool_db)
    for i in range(230):
        change(tool_db, cid, notes=f"第{i}次")
    r = hist(cid, limit=9999)
    assert r["count"] == 200 and r["total"] == 230 and r["truncated"] is True, (
        r["count"], r["total"])
    assert len(hist(cid, limit=0)["changes"]) == 20        # ≤0 仍按默认 20（F44 不回归）


def test_empty_history_has_zero_total(tool_db):
    cid = make(tool_db)
    r = hist(cid)
    assert r["success"] is True and r["changes"] == [] and r["total"] == 0 and r["truncated"] is False


def test_description_mentions_display_fields_and_total():
    from tools.registry import registry
    desc = registry.get_entry("customer_change_history").schema.get("description", "")
    for word in ("field_label", "total", "truncated"):
        assert word in desc, (word, desc)
