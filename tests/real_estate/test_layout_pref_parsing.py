"""户型偏好的写法与判定（2026-09-30 实测）

起因：经纪人写「客户想租个一居」，Coco 存了 `1居`，而匹配引擎只认半角数字 +「室/厅」——
`_parse_layout_pref('1居')` 返回 None ⇒ 户型这个"硬过滤"等于没设，**2 室 1 厅还被标成"完全匹配"**。
同族的写法还有：两居/三居室/一房/两房/三房/一房一厅/三室两厅（中文数字与「居」「房」）。
本文件钉住三件事：① 这些写法都要认；② 认不出的写法不许谎报"户型匹配/完全匹配"；
③ 认不出时工具要给一句能转述给经纪人的话（"不限"这类明确不设限的不算认不出）。
"""
import json

import pytest

import tools.real_estate_property  # noqa: F401  导入即注册
from tools.registry import registry

SID = "20260930_000000_abcdef12"


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
    # 建档后的自动匹配走的是客户模块的入口，两边都要指到同一个临时库
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
    return db


def _rental_customer(db, layout_pref):
    return db.add_customer(name="租房客户甲", customer_type="rent", layout_pref=layout_pref,
                           budget_min=2000, budget_max=3000, location="海口龙华区")["id"]


def _properties(db):
    """一套一居 + 一套两居（都在预算内、同区域）"""
    one = db.add_property(title="一居房源", price=2200, area=45.0, rooms=1, halls=1,
                          property_type="rental", district="海口龙华区")
    two = db.add_property(title="两居房源", price=2500, area=60.0, rooms=2, halls=1,
                          property_type="rental", district="海口龙华区")
    return one, two


def _call(name, args):
    return json.loads(registry.dispatch(name, args, session_id=SID, task_id="layout"))


# ---------- ① 写法都要认 ----------
@pytest.mark.parametrize("pref,expect", [
    ("1居", {"rooms": ("exact", 1)}),
    ("一居", {"rooms": ("exact", 1)}),
    ("一居室", {"rooms": ("exact", 1)}),
    ("两居", {"rooms": ("exact", 2)}),
    ("2居", {"rooms": ("exact", 2)}),
    ("三居室", {"rooms": ("exact", 3)}),
    ("一房", {"rooms": ("exact", 1)}),
    ("两房", {"rooms": ("exact", 2)}),
    ("三房", {"rooms": ("exact", 3)}),
    ("一房一厅", {"rooms": ("exact", 1), "halls": ("exact", 1)}),
    ("三房两厅", {"rooms": ("exact", 3), "halls": ("exact", 2)}),
    ("一室一厅", {"rooms": ("exact", 1), "halls": ("exact", 1)}),
    ("三室两厅", {"rooms": ("exact", 3), "halls": ("exact", 2)}),
    ("1室", {"rooms": ("exact", 1)}),
    ("1-2室", {"rooms": ("range", 1, 2)}),
    ("3室2厅", {"rooms": ("exact", 3), "halls": ("exact", 2)}),
])
def test_layout_writings_are_recognized(db, pref, expect):
    plan = db._parse_layout_pref(pref)
    assert plan is not None, f"「{pref}」没被认出来"
    for key, value in expect.items():
        assert plan.get(key) == value, (pref, plan)


# ---------- ② 硬过滤真的生效 ----------
def test_one_room_customer_does_not_get_two_room_flat(tool_db):
    """客户要一居（写法「1居」），两居室不该进匹配名单"""
    cid = _rental_customer(tool_db, "1居")
    one, two = _properties(tool_db)
    out = _call("match_property", {"customer_id": cid})
    ids = [m["id"] for m in out["matches"]]
    assert one["id"] in ids, out
    assert two["id"] not in ids, out
    assert out["matches"][0]["perfect_match"] is True


def test_recognized_two_room_customer_gets_two_room_flat(tool_db):
    cid = _rental_customer(tool_db, "两房")
    one, two = _properties(tool_db)
    ids = [m["id"] for m in _call("match_property", {"customer_id": cid})["matches"]]
    assert two["id"] in ids and one["id"] not in ids


# ---------- ③ 认不出时不撒谎、并给一句人话 ----------
def test_unparsed_layout_never_claims_full_match(tool_db):
    cid = _rental_customer(tool_db, "套三")   # 我们不认的写法
    _properties(tool_db)
    out = _call("match_property", {"customer_id": cid})
    assert out["matches"], "认不出写法时房源照给（按不限户型筛）"
    for m in out["matches"]:
        assert "户型匹配" not in m["match_reasons"], m
        assert m["perfect_match"] is False, m
    assert "我没认出来" in out["message"], out
    assert "套三" in out["message"], out


def test_open_ended_layout_is_not_reported_as_unparsed(tool_db):
    """客户写「不限」是真不设限，别回来说"没看懂" """
    cid = _rental_customer(tool_db, "不限")
    _properties(tool_db)
    out = _call("match_property", {"customer_id": cid})
    assert "我没认出来" not in json.dumps(out, ensure_ascii=False)


def test_add_customer_receipt_carries_the_note(tool_db):
    """建档时自动匹配也带上这句话（老板实测就是在这条路径上被误导的）"""
    _properties(tool_db)
    out = _call("add_customer", {"name": "客户乙", "customer_type": "rent",
                                 "layout_pref": "大开间", "budget_min": 2000,
                                 "budget_max": 3000, "location": "海口龙华区"})
    assert "我没认出来" in out.get("message", ""), out
