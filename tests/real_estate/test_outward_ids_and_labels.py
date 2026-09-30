"""对外回执里不出现内部写法：编号就说"编号"、类型说中文（2026-09-30 实测）

起因：经纪人问「还有哪些信息没填」，Coco 回「（ID:37）」「类型 buy_second_hand」——
它只是照抄了按姓名查人那句给人看的 message。全库同类写法 8 处（房东侧 6 处、房源侧 2 处）。
本文件把每个出口真跑一遍，断言对外文案里只有「客户编号 / 房源编号 / 业主编号」与中文类型名。
"""
import json
from datetime import datetime, timedelta

import pytest

import tools.real_estate_owner as owner_mod
import tools.real_estate_property as prop_mod
from tools.real_estate_owner import (exclusive_expiring, find_person_by_name,
                                     get_property_owners, owner_portfolio)
from tools.real_estate_property import find_alternatives, price_drop_alerts


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr(owner_mod, "_get_db", lambda: db)
    monkeypatch.setattr(prop_mod, "_get_db", lambda: db)
    return db


def _call(fn, **kwargs):
    return json.loads(fn(**kwargs))


def _message_has_no_internal_wording(message):
    assert "ID:" not in message, message
    for bad in ("customer_type", "buy_second_hand", "buy_new", "unspecified"):
        assert bad not in message, message


def _one_property(db, title="房源甲", price=2_000_000, **kw):
    data = dict(area=100.0, property_type="second_hand", district="美兰区", rooms=3, halls=2)
    data.update(kw)
    return db.add_property(title=title, price=price, **data)


# ---------- 房东侧 ----------
def test_find_person_by_name_says_id_in_chinese(tool_db):
    db = tool_db
    db.add_customer(name="客户甲", phone="13900001111", customer_type="buy_second_hand",
                    budget_min=1_500_000, budget_max=2_000_000, location="美兰区",
                    layout_pref="3室")
    o = db.add_owner(name="客户甲", phone="13900002222")
    out = _call(find_person_by_name, name="客户甲")
    msg = out["message"]
    assert "客户编号" in msg, msg
    assert "业主编号" in msg, msg
    assert "买二手房" in msg, msg
    _message_has_no_internal_wording(msg)


def test_get_property_owners_says_id_in_chinese(tool_db):
    db = tool_db
    o = db.add_owner(name="业主甲", phone="13900003333")
    p = _one_property(db)
    db.update_property(p["id"], owner_id=o["id"])
    out = _call(get_property_owners, property_ids=[p["id"]])
    assert "房源编号" in out["message"], out["message"]
    _message_has_no_internal_wording(out["message"])


def test_owner_portfolio_says_id_in_chinese(tool_db):
    db = tool_db
    o = db.add_owner(name="业主乙", phone="13900004444")
    p = _one_property(db, title="名下房源甲")
    db.update_property(p["id"], owner_id=o["id"])
    out = _call(owner_portfolio, owner_id=o["id"])
    assert "房源编号" in out["message"], out["message"]
    _message_has_no_internal_wording(out["message"])


def test_exclusive_expiring_says_id_in_chinese(tool_db):
    db = tool_db
    p = _one_property(db, title="独家房源甲")
    db.update_property(p["id"], exclusive_until=datetime.now() + timedelta(days=10))
    out = _call(exclusive_expiring, days=30)
    assert "房源编号" in out["message"], out["message"]
    _message_has_no_internal_wording(out["message"])


# ---------- 房源侧 ----------
def test_price_drop_alerts_says_id_in_chinese(tool_db):
    db = tool_db
    p = _one_property(db, title="降价房源甲", price=2_000_000)
    db.update_property(p["id"], price=1_800_000)   # 写调价历史
    db.add_customer(name="客户乙", customer_type="buy_second_hand",
                    budget_min=1_700_000, budget_max=1_760_000, location="美兰区",
                    layout_pref="3室")
    out = _call(price_drop_alerts, days=30)
    assert "房源编号" in out["message"], out["message"]
    _message_has_no_internal_wording(out["message"])


def test_find_alternatives_says_id_in_chinese(tool_db):
    db = tool_db
    src = _one_property(db, title="被抢房源甲")
    db.update_property(src["id"], status="sold")
    _one_property(db, title="替代房源乙", price=1_850_000, area=98.0)
    out = _call(find_alternatives, property_id=src["id"])
    assert "房源编号" in out["message"], out["message"]
    _message_has_no_internal_wording(out["message"])
