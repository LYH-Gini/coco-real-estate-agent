"""update_property 的入口归一/校验、楼层推断不覆盖已有值、可改类型与租客要求（2026-09-24）

覆盖这一项查出的 6 条问题：
- 只改价格时不再按房号重推覆盖已有楼层（推断只在库里没楼层时补）
- 价格/面积归一（"185万"不再崩）、非正面积被拒
- 非法状态被拒（原来崩在数据库 CHECK 约束上）
- 新增 property_type / tenant_requirements：类型录错能纠正、租客要求能改
- fill_missing_only 仍然只补空缺
"""
import json

import pytest
from conftest import make_property  # noqa: F401

from tools.real_estate_property import update_property


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
    return db


def _upd(**kw):
    return json.loads(update_property(**kw))


def _mk(db, **kw):
    data = dict(title="更新小区 1号楼1802", price=1_000_000, area=100.0, property_type="second_hand")
    data.update(kw)
    return make_property(db, **data)


# ---------- 楼层：推断是补空缺，不是覆盖 ----------
def test_price_only_update_keeps_existing_floor(tool_db):
    p = _mk(tool_db, floor="3层（共18层）")
    r = _upd(property_id=p["id"], price=1_200_000)
    assert r["property"]["price"] == 1_200_000
    assert tool_db.get_property(p["id"])["floor"] == "3层（共18层）", "手工纠过的楼层不能被房号推断覆盖"


def test_floor_inferred_when_missing(tool_db):
    p = _mk(tool_db, floor=None)
    r = _upd(property_id=p["id"], price=1_300_000)
    assert tool_db.get_property(p["id"])["floor"] == "18层"
    assert "floor" in (r.get("inferred") or {}), "推断要带依据让经纪人核对"


# ---------- 归一与校验 ----------
@pytest.mark.parametrize("raw,expected", [("185万", 1_850_000), ("一百五十万", 1_500_000), (1_850_000, 1_850_000)])
def test_price_normalized(tool_db, raw, expected):
    p = _mk(tool_db)
    r = _upd(property_id=p["id"], price=raw)
    assert r["success"] is True, r
    assert tool_db.get_property(p["id"])["price"] == expected


@pytest.mark.parametrize("area", [0, -5, "很大"])
def test_invalid_area_rejected(tool_db, area):
    p = _mk(tool_db, area=100.0)
    r = _upd(property_id=p["id"], area=area)
    assert r["success"] is False and "面积" in r["error"]
    assert tool_db.get_property(p["id"])["area"] == 100.0, "拒绝时库里原值不能被动"


def test_invalid_status_rejected(tool_db):
    p = _mk(tool_db)
    r = _upd(property_id=p["id"], status="unknown")
    assert r["success"] is False and "状态" in r["error"]
    assert tool_db.get_property(p["id"])["status"] == "available"


def test_invalid_property_type_rejected(tool_db):
    p = _mk(tool_db)
    r = _upd(property_id=p["id"], property_type="house")
    assert r["success"] is False and "类型" in r["error"]
    assert tool_db.get_property(p["id"])["property_type"] == "second_hand"


# ---------- 新能力：改类型 / 改租客要求 ----------
def test_property_type_can_be_corrected(tool_db):
    """录错类型要能纠正（比如把一手房录成二手房）"""
    p = _mk(tool_db, property_type="second_hand")
    r = _upd(property_id=p["id"], property_type="new")
    assert r["success"] is True, r
    assert tool_db.get_property(p["id"])["property_type"] == "new"


def test_tenant_requirements_can_be_updated(tool_db):
    p = _mk(tool_db, property_type="rental", price=2200)
    r = _upd(property_id=p["id"], tenant_requirements="不吸烟、学生优先")
    assert r["success"] is True, r
    assert tool_db.get_property(p["id"])["tenant_requirements"] == "不吸烟、学生优先"


# ---------- fill_missing_only 仍然只补空缺 ----------
def test_fill_missing_only_keeps_existing(tool_db):
    p = _mk(tool_db, renovation="精装")
    r = _upd(property_id=p["id"], renovation="毛坯", bathrooms=2, fill_missing_only=True)
    row = tool_db.get_property(p["id"])
    assert row["renovation"] == "精装"
    assert row["bathrooms"] == 2
    assert r["kept_existing"] == {"renovation": "精装"}
