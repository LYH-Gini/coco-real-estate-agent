"""update_customer 修复回归（2026-09-24）：漂移数字、留痕加密、改号查重、客户类型、状态、历史条数

覆盖 6 处修复：
① F40 预算漂移预警把"元"当"万"显示，夸 10000 倍（5000000万）→ 改为 500万。
② F41 变更留痕里存明文手机号/微信（历史表不加密）→ 加密字段只留掩码，明文不进历史表。
③ F42 改手机号/微信不查重，能把两个客户改成同一个号 → 改成先查重并给两条可执行路径。
④ F43① 客户类型改不了（建档时"未细分"、后来确认了也补不上）→ 新增 customer_type 参数。
⑤ F44 变更历史 limit=0 谎报"没变更"、负数变全量 → ≤0 按默认 20、上限 200。
⑥ F45 status 乱值静默入库、客户既没关闭也进不了已关闭 → 只认 active/paused/closed（含中文别名）。
"""
import json

import pytest

from tools.real_estate_customer import customer_change_history, update_customer


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
    return db


@pytest.fixture
def enc_tool_db(enc_db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: enc_db)
    return enc_db


def make_customer(db, **overrides):
    data = dict(name="客户甲", tier="C", budget_min=2_000_000, budget_max=5_000_000,
                customer_type="buy_second_hand", status="active")
    data.update(overrides)
    return db.add_customer(**data)


def call_update(**kwargs):
    return json.loads(update_customer(**kwargs))


# ---------- ① 漂移预警的数字 ----------
def test_budget_drift_message_uses_wan_not_yuan(tool_db):
    cid = make_customer(tool_db, budget_max=5_000_000)["id"]
    r = call_update(customer_id=cid, budget_max=3_000_000)
    msg = (r.get("alerts") or [{}])[0].get("message", "")
    assert "从 500万 下调到 300万" in msg, msg
    assert "5000000万" not in msg, msg


def test_budget_drift_not_triggered_for_small_drop(tool_db):
    cid = make_customer(tool_db, budget_max=5_000_000)["id"]
    assert not call_update(customer_id=cid, budget_max=4_900_000).get("alerts")


# ---------- ② 留痕不存明文联系方式 ----------
def test_change_history_masks_encrypted_fields(tool_db):
    cid = make_customer(tool_db, phone="13922220001", wechat="mm_wx")["id"]
    call_update(customer_id=cid, phone="13922220002", wechat="mm_wx2")
    rows = tool_db.get_customer_changes(cid, limit=50)
    contact = {r["field"]: (r["old_value"], r["new_value"]) for r in rows
               if r["field"] in ("phone", "wechat")}
    assert contact, rows
    blob = json.dumps(contact, ensure_ascii=False)
    assert "13922220001" not in blob and "13922220002" not in blob, blob
    assert "mm_wx" not in blob, blob
    assert contact["phone"] == ("139****0001", "139****0002"), contact["phone"]
    assert contact["wechat"] == ("mm****", "mm****"), contact["wechat"]


def test_change_history_keeps_plain_fields_readable(tool_db):
    """非加密字段照常留原文，别把正常业务信息也糊掉"""
    cid = make_customer(tool_db)["id"]
    call_update(customer_id=cid, budget_max=4_000_000, notes="只看南北通透")
    rows = {r["field"]: (r["old_value"], r["new_value"]) for r in tool_db.get_customer_changes(cid, limit=50)}
    assert rows["budget_max"] == ("5000000", "4000000"), rows.get("budget_max")
    assert rows["notes"] == (None, "只看南北通透"), rows.get("notes")


# ---------- ③ 改联系方式先查重 ----------
def test_changing_phone_to_another_customers_number_is_refused(tool_db):
    make_customer(tool_db, name="撞号甲", phone="13911110001")
    keep = make_customer(tool_db, name="撞号乙", phone="13911110002")
    r = call_update(customer_id=keep["id"], phone="13911110001")
    assert r["success"] is False and r["duplicate"] is True, r
    assert r["existing_customer"]["name"] == "撞号甲"
    assert tool_db.get_customer(keep["id"])["phone"] == "13911110002"   # 没被改掉
    assert update_customer_err_mentions(r, "同一个人")


def update_customer_err_mentions(r, text):
    return text in (r.get("error") or "")


def test_setting_same_phone_is_not_a_conflict_with_self(tool_db):
    cid = make_customer(tool_db, phone="13911110003")["id"]
    r = call_update(customer_id=cid, phone="13911110003", notes="顺便改个备注")
    assert r["success"] is True, r
    assert r["customer"]["notes"] == "顺便改个备注"


def test_changing_wechat_to_used_one_is_refused(tool_db):
    make_customer(tool_db, name="微信甲", wechat="wx_taken")
    b = make_customer(tool_db, name="微信乙", wechat="wx_mine")
    r = call_update(customer_id=b["id"], wechat="wx_taken")
    assert r["success"] is False and r["duplicate"] is True and r["existing_customer"]["name"] == "微信甲", r


def test_phone_conflict_check_survives_encryption(enc_tool_db):
    """加密模式下（库内是密文）撞号同样查得出来"""
    make_customer(enc_tool_db, name="加密甲", phone="13911110011")
    b = make_customer(enc_tool_db, name="加密乙", phone="13911110012")
    r = call_update(customer_id=b["id"], phone="139-1111-0011")   # 连写法不同也认得出
    assert r["success"] is False and r["existing_customer"]["name"] == "加密甲", r


# ---------- ④ 客户类型能补 ----------
def test_customer_type_can_be_refined_later(tool_db):
    cid = make_customer(tool_db, customer_type="unspecified")["id"]
    r = call_update(customer_id=cid, customer_type="买二手房")
    assert r["success"] is True and r["customer"]["customer_type"] == "buy_second_hand", r
    rows = {c["field"]: (c["old_value"], c["new_value"]) for c in tool_db.get_customer_changes(cid, limit=50)}
    assert rows["customer_type"] == ("unspecified", "buy_second_hand"), rows


def test_invalid_customer_type_on_update_is_rejected(tool_db):
    cid = make_customer(tool_db, customer_type="unspecified")["id"]
    r = call_update(customer_id=cid, customer_type="买房")
    assert r["success"] is False and "客户类型" in r["error"], r
    assert tool_db.get_customer(cid)["customer_type"] == "unspecified"


# ---------- ⑤ 变更历史条数 ----------
@pytest.mark.parametrize("limit", [None, 0, -1, -99])
def test_change_history_limit_nonpositive_falls_back_to_default(tool_db, limit):
    cid = make_customer(tool_db)["id"]
    for i in range(25):
        tool_db.update_customer(cid, notes=f"第{i}次")
    kwargs = {"customer_id": cid} if limit is None else {"customer_id": cid, "limit": limit}
    r = json.loads(customer_change_history(**kwargs))
    assert len(r["changes"]) == 20, (limit, len(r["changes"]))


def test_change_history_limit_is_capped(tool_db):
    cid = make_customer(tool_db)["id"]
    for i in range(230):
        tool_db.update_customer(cid, notes=f"改{i}")
    r = json.loads(customer_change_history(customer_id=cid, limit=9999))
    assert len(r["changes"]) == 200, len(r["changes"])


# ---------- ⑥ 状态只认合法值 ----------
@pytest.mark.parametrize("raw,expected", [("closed", "closed"), ("closed ", "closed"), ("CLOSED", "closed"),
                                          ("已关闭", "closed"), ("在跟", "active"), ("暂缓", "paused")])
def test_status_normalized(tool_db, raw, expected):
    cid = make_customer(tool_db)["id"]
    r = call_update(customer_id=cid, status=raw)
    assert r["success"] is True and r["customer"]["status"] == expected, (raw, r)


@pytest.mark.parametrize("raw", ["close", "停业", "closure", "closed_status"])
def test_invalid_status_rejected_and_not_stored(tool_db, raw):
    cid = make_customer(tool_db)["id"]
    r = call_update(customer_id=cid, status=raw)
    assert r["success"] is False and "状态" in r["error"], r
    assert tool_db.get_customer(cid)["status"] == "active"


def test_closed_customer_has_no_status_typo_escape(tool_db):
    """真的关闭客户后：既不在"在跟"列表里，也计入已关闭（原先 'close' 两边都不算）"""
    cid = make_customer(tool_db, name="要关掉的")["id"]
    call_update(customer_id=cid, status="关闭")
    active = [c["name"] for c in tool_db.list_customers(limit=50)]
    closed = [c["name"] for c in tool_db.list_customers(limit=50, status="closed")]
    assert "要关掉的" not in active and "要关掉的" in closed, (active, closed)
