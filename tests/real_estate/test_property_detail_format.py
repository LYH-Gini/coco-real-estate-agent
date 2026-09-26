"""房源详情的展示口径（单价两位小数 / 面积整数）与密文兜底（2026-09-24）

F15：单价统一两位小数（20000.0 → 20000.00）、面积整数显示整数（100㎡）。
F16：密钥不一致（换机器/恢复备份没带密钥）时，联系方式读出的是密文 —— 绝不能把乱码展示给经纪人，
     要给可读提示并带 warning 字段，覆盖详情 / 业主查询 / 按姓名查人三条展示路径。
"""
import json

import pytest
from conftest import make_property  # noqa: F401
from sqlalchemy import text

from tools.real_estate_owner import find_person_by_name, get_property_owners
from tools.real_estate_property import get_property_detail

CIPHER = "gAAAAA" + "x" * 100          # Fernet 密文形态（密钥不一致时解密失败会原样返回这种值）


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
    monkeypatch.setattr("tools.real_estate_owner._get_db", lambda: db)
    return db


def _detail(pid):
    return json.loads(get_property_detail(property_id=pid))


def _break_key(db, table="re_owners"):
    with db.engine.begin() as conn:
        conn.execute(text(f"update {table} set phone=:v"), {"v": CIPHER})


# ---------- F15 展示口径 ----------
def test_unit_price_and_area_format(tool_db):
    p = make_property(tool_db, title="口径小区 1号楼101", price=2_000_000, area=100.0)
    msg = _detail(p["id"])["message"]
    assert "单价 20000.00元/㎡" in msg, msg            # 两位小数，不是 20000.0
    assert "面积 100㎡" in msg, msg                     # 整数面积不带 .0


def test_area_keeps_decimal_when_needed(tool_db):
    p = make_property(tool_db, title="口径小区 2号楼201", price=1_850_000, area=128.5)
    msg = _detail(p["id"])["message"]
    assert "面积 128.5㎡" in msg, msg
    assert "单价 14396.89元/㎡" in msg, msg


def test_rental_unit_price_says_per_month(tool_db):
    """出租房按"每平米月租"说（60㎡ / 月租 2500 → 41.67元/㎡/月），与列表/对比同一口径"""
    p = make_property(tool_db, title="口径小区 3号楼501", price=2_500, area=60.0,
                      property_type="rental")
    msg = _detail(p["id"])["message"]
    assert "总价 2500元/月" in msg and "单价 41.67元/㎡/月" in msg, msg
    assert "单价 41.67元/㎡ " not in msg, msg      # 不许漏掉 /月


# ---------- F16 密文兜底 ----------
def test_detail_hides_ciphertext_and_warns(tool_db):
    p = make_property(tool_db, title="密钥小区 3号楼301", price=1_500_000, area=100.0)
    tool_db.link_owner_to_property(p["id"], name="陈志强", phone="13800001111")
    _break_key(tool_db)

    r = _detail(p["id"])
    assert "gAAAA" not in r["message"], "密文不能出现在给经纪人看的文案里"
    assert "13800001111" not in r["message"]
    assert "密钥" in r["message"]
    assert r.get("warning_key_mismatch"), "要让 Coco 能如实转述这条异常"


def test_owner_query_hides_ciphertext_and_warns(tool_db):
    p = make_property(tool_db, title="密钥小区 3号楼302", price=1_500_000, area=100.0)
    tool_db.link_owner_to_property(p["id"], name="陈志强", phone="13800001111")
    _break_key(tool_db)

    r = json.loads(get_property_owners(property_ids=[p["id"]]))
    assert "gAAAA" not in r["message"]
    assert r.get("warning_key_mismatch")


def test_find_person_hides_ciphertext(tool_db):
    p = make_property(tool_db, title="密钥小区 3号楼303", price=1_500_000, area=100.0)
    tool_db.link_owner_to_property(p["id"], name="陈志强", phone="13800001111", wechat="chenzq")
    _break_key(tool_db)

    r = json.loads(find_person_by_name(name="陈志强"))
    assert "gAAAA" not in r["message"], r["message"]
    assert "密钥" in r["message"]


def test_normal_key_shows_plain_contact(tool_db):
    """反例：密钥正常时照常给完整号码（这轮改动不能把正常路径也挡了）"""
    p = make_property(tool_db, title="密钥小区 3号楼304", price=1_500_000, area=100.0)
    tool_db.link_owner_to_property(p["id"], name="陈志强", phone="13800001111")
    r = _detail(p["id"])
    assert "13800001111" in r["message"]
    assert not r.get("warning_key_mismatch")
