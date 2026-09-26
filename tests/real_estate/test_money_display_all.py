"""金额/单位展示收口回归（2026-09-25，同族收口批任务 4）

起因：8 个文件里散着 49 处 `除以 10000` / 拼"万"，光 `_fmt_price` 就有 4 份副本
（房源/文案/海报/短视频），精度与空值文案各不相同 —— 改一处漏三处，实测踩过两次
（出租显示成 `0万`、状态直出英文）。现在收在 `agent/real_estate_money.py`。

**断言一律用真实业务值**：月租 2500（出租口径）、非整万售价 1853000（两位小数 vs 一位）、
预算 5000 元（低于 1 万按元说）—— 整万值（如 150 万）测不出精度差异。
"""
import json
import re
from datetime import datetime, timedelta

import pytest

from agent.real_estate_money import (fmt_budget, fmt_delta, fmt_price, fmt_unit_price, fmt_wan)
from tools import (real_estate_customer as m_customer, real_estate_listing as m_listing,
                   real_estate_owner as m_owner, real_estate_property as m_property)

RENT_PRICE, SALE_PRICE = 2500, 1_853_000          # 月租 2500 / 非整万售价 185.3 万


@pytest.fixture
def wired(db, monkeypatch):
    for mod in (m_customer, m_listing, m_owner, m_property):
        monkeypatch.setattr(mod, "_get_db", lambda _db=db: _db)
    return db


@pytest.fixture
def data(wired):
    rent = wired.add_property(title="民宿小单间", price=RENT_PRICE, area=35.0,
                              property_type="rental", district="美兰区", status="available")
    sale = wired.add_property(title="非整万二手房", price=SALE_PRICE, area=100.0,
                              property_type="second_hand", district="美兰区", status="available")
    o = wired.add_owner(name="文案房东", phone="13700005678")
    wired.update_property(rent["id"], owner_id=o["id"])
    wired.update_property(sale["id"], owner_id=o["id"])
    wired.update_property(rent["id"], exclusive_until=datetime.now() + timedelta(days=10))
    assert wired.get_property(rent["id"])["exclusive_until"], "独家到期日夹具没落地"
    return {"rent": rent["id"], "sale": sale["id"], "owner": o["id"]}


# ==================== ① 共用实现 ====================
@pytest.mark.parametrize("amount,digits,expected", [
    (1_500_000, 2, "150万"),          # 整数万省略小数
    (1_853_000, 2, "185.30万"),       # 房源详情口径：两位小数
    (1_853_000, 1, "185.3万"),        # 文案/海报口径：一位小数
    (2_996_000, 1, "299.6万"),
])
def test_fmt_wan(amount, digits, expected):
    assert fmt_wan(amount, digits) == expected


@pytest.mark.parametrize("amount,expected", [(299_600, "30万"), (289_600, "29万"),
                                            (289_400, "28.9万")])
def test_fmt_wan_near_int_for_poster(amount, expected):
    """海报大字：与整数万相差 <0.05 万时取整（免得大字里出现零头）"""
    assert fmt_wan(amount, 1, near_int=True) == expected


@pytest.mark.parametrize("prop,digits,empty,expected", [
    ({"price": RENT_PRICE, "property_type": "rental"}, 2, "未录入", "2500元/月"),
    ({"price": SALE_PRICE, "property_type": "second_hand"}, 2, "未录入", "185.30万"),
    ({"price": SALE_PRICE, "property_type": "second_hand"}, 1, "价格待定", "185.3万"),
    ({"price": None, "property_type": "second_hand"}, 2, "未录入", "未录入"),
    ({"price": None, "property_type": "second_hand"}, 1, "价格待定", "价格待定"),
    ({"price": "看情况", "property_type": "second_hand"}, 2, "未录入", "未录入"),
    # 价格列是 NOT NULL，"没填价"在库里的实际形态就是 0 —— 不许说成"0万"（2026-09-26）
    ({"price": 0, "property_type": "second_hand"}, 1, "价格待定", "价格待定"),
    ({"price": 0, "property_type": "rental"}, 2, "未录入", "未录入"),
    ({"price": 0.0, "property_type": "second_hand"}, 2, "未录入", "未录入"),
    ({"price": -100, "property_type": "second_hand"}, 2, "未录入", "未录入"),
])
def test_fmt_price(prop, digits, empty, expected):
    assert fmt_price(prop, digits, empty) == expected


@pytest.mark.parametrize("value,expected", [(5_000, "5000元"), (6_000, "6000元"),
                                            (3_000_000, "300万"), (1_853_000, "185.3万"),
                                            (None, None), ("看情况", "看情况")])
def test_fmt_budget(value, expected):
    """低于 1 万按元说（租房预算常见 5000 元，说成"0万"是 bug）"""
    assert fmt_budget(value) == expected


def test_fmt_delta_and_unit_price():
    assert fmt_delta(-153_000, 1) == "-15.3万" and fmt_delta(153_000, 1) == "+15.3万"
    assert fmt_unit_price(18_750.0) == "18750.00" and fmt_unit_price(71.4285) == "71.43"


# ==================== ② 各出口的展示口径（真实业务值） ====================
def test_property_detail_message_money(data):
    rent = json.loads(m_property.get_property_detail(property_id=data["rent"]))["message"]
    assert "总价 2500元/月" in rent, rent
    assert "万万" not in rent and "元/月/月" not in rent, rent
    assert not re.search(r"(?<![\d.])0万", rent), rent       # 出租不许说成"0万"
    sale = json.loads(m_property.get_property_detail(property_id=data["sale"]))["message"]
    assert "总价 185.30万" in sale and "单价 18530.00元/㎡" in sale, sale


def test_owner_portfolio_reuses_the_same_rendering(data):
    msg = json.loads(m_owner.owner_portfolio(owner_id=data["owner"]))["message"]
    assert "2500元/月" in msg and "185.30万" in msg, msg
    assert not re.search(r"(?<![\d.])0万", msg), msg


def test_exclusive_expiring_rent_uses_per_month(data):
    msg = json.loads(m_owner.exclusive_expiring(days=30))["message"]
    assert "2500元/月" in msg and not re.search(r"(?<![\d.])0万", msg), msg


def test_listing_copy_money(data):
    rent = json.loads(m_listing.generate_listing_copy(property_id=data["rent"],
                                                      platform="friends"))["copy"]
    assert "价格 2500元/月" in rent, rent
    sale = json.loads(m_listing.generate_listing_copy(property_id=data["sale"],
                                                      platform="friends"))["copy"]
    assert "价格 185.3万" in sale, sale          # 文案口径一位小数
    for text in (rent, sale):
        assert "万万" not in text and not re.search(r"(?<![\d.])0万", text), text


def test_budget_warning_speaks_yuan_below_ten_thousand(wired):
    c = wired.add_customer(name="写反客户", budget_min=5_000, budget_max=3_000,
                           customer_type="buy_second_hand")
    out = json.loads(m_customer.update_customer(customer_id=c["id"], notes="x"))
    warnings = json.dumps(out.get("warnings"), ensure_ascii=False)
    assert "预算下限 5000元 大于上限 3000元" in warnings, warnings
    assert not re.search(r"(?<![\d.])0万", warnings), warnings


def test_budget_drift_warning_uses_same_rendering(wired):
    c = wired.add_customer(name="漂移客户", budget_min=1_000_000, budget_max=3_000_000,
                           customer_type="buy_second_hand")
    out = json.loads(m_customer.update_customer(customer_id=c["id"], budget_max=800_000))
    alerts = json.dumps(out.get("alerts"), ensure_ascii=False)
    assert "预算上限从 300万 下调到 80万" in alerts, alerts


def test_price_history_message_uses_signed_delta(wired):
    p = wired.add_property(title="调价房源", price=2_000_000, area=90.0,
                           property_type="second_hand", status="available")
    wired.update_property(p["id"], price=1_847_000)      # 降价写进调价历史
    msg = json.loads(m_property.price_history(property_id=p["id"]))["message"]
    assert "累计变动 -15.3万" in msg, msg
