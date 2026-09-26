"""金额上限回归（2026-09-26，拍板：把"金额超过数据库能记的上限"一次修掉 —— F241）

背景（先查了列类型，别再凭印象说"同源"）：
生产库（PostgreSQL）里**成交价 / 定金 / 客户预算**这三类列是 `INTEGER`（int4，最大 2147483647 ≈ 21.4 亿），
超过它写库会**直接报错**，经纪人只看到一句"执行失败"；本地 SQLite 会静默存下，所以本机测不出来（实测 100 亿照样 `success=true`）。

**房源价格是 `BIGINT`（int64），不受 21.4 亿这个限制** —— 所以本次**刻意不改房源侧**：
给它硬套 21.4 亿会拒绝数据库本来存得下的真实数据（一线城市整栋/老洋房有可能很高）。
要不要给房源价格另设一个"离谱值"阈值（阈值定多少）属产品判断，等拍板。

本文件钉住：成交价 / 定金 / 客户预算超限 → 中文提示且不落库；正好 21.4 亿 → 放行；正常值不受影响。
"""
import json

import pytest

LIMIT = 2_147_483_647          # int4 上限 ≈ 21.4 亿
OVER = 10 ** 10                # 100 亿


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_customer as m_c
    import tools.real_estate_deal as m_d
    import tools.real_estate_property as m_p

    monkeypatch.setattr(m_c, "_get_db", lambda: db)
    monkeypatch.setattr(m_d, "_get_db", lambda: db)
    monkeypatch.setattr(m_p, "_get_db", lambda: db)
    return db


def _customer(db, name="上限客户", phone="13800001111"):
    return db.add_customer(name=name, phone=phone, tier="A",
                           customer_type="buy_second_hand")["id"]


def _property(db, title="上限房源 1号楼101"):
    return db.add_property(title=title, price=1_500_000, area=80.0,
                           property_type="second_hand", status="available")["id"]


def _start(**kwargs):
    import tools.real_estate_deal as m

    return json.loads(m.start_deal(**kwargs))


# ==================== ① 成交价 / 定金（int4 列） ====================

class TestDealAmounts:
    def test_over_limit_price_is_refused_and_not_stored(self, wired):
        cid, pid = _customer(wired), _property(wired)
        out = _start(customer_id=cid, property_id=pid, price=OVER)
        assert out.get("success") is not True, out
        assert "21.4 亿" in out["error"] and "成交价" in out["error"], out
        assert wired.list_deals(limit=10) == [], "超限不该落库"

    def test_over_limit_deposit_is_refused(self, wired):
        cid, pid = _customer(wired), _property(wired)
        out = _start(customer_id=cid, property_id=pid, price=4_000_000, deposit_amount=OVER)
        assert out.get("success") is not True, out
        assert "21.4 亿" in out["error"] and "定金" in out["error"], out
        assert wired.list_deals(limit=10) == []

    def test_exactly_at_limit_is_allowed(self, wired):
        """边界值本身不拦（列能存下最大值）"""
        cid, pid = _customer(wired), _property(wired)
        out = _start(customer_id=cid, property_id=pid, price=LIMIT)
        assert out["success"] is True, out
        assert wired.list_deals(limit=10)[0]["price"] == LIMIT

    def test_normal_amounts_unaffected(self, wired):
        cid, pid = _customer(wired), _property(wired)
        out = _start(customer_id=cid, property_id=pid, price=18_500_000, deposit_amount=50_000)
        assert out["success"] is True, out


# ==================== ② 客户预算（同一根因：int4 列） ====================

class TestCustomerBudget:
    def test_add_customer_over_limit_budget(self, wired):
        import tools.real_estate_customer as m

        out = json.loads(m.add_customer(name="超限预算客户", budget_max=OVER))
        assert out.get("success") is not True, out
        assert "21.4 亿" in out["error"] and "预算上限" in out["error"], out

    def test_update_customer_over_limit_budget(self, wired):
        import tools.real_estate_customer as m

        cid = _customer(wired)
        out = json.loads(m.update_customer(customer_id=cid, budget_min=OVER))
        assert out.get("success") is not True, out
        assert "21.4 亿" in out["error"] and "预算下限" in out["error"], out

    def test_normal_budget_unaffected(self, wired):
        import tools.real_estate_customer as m

        out = json.loads(m.add_customer(name="正常预算客户", phone="13800002222",
                                       budget_min=3_000_000, budget_max=5_000_000))
        assert out["success"] is True, out


# ==================== ③ 房源侧：本次刻意不改（BIGINT，不受 int4 限制） ====================

def test_property_price_has_no_int4_limit(wired):
    """房源价格列是 BIGINT —— 数据库存得下，所以**不套** 21.4 亿这个上限。

    这是在钉"刻意不改"的决定：将来若要给房源价格设阈值（多少由拍板），改这条用例时就有对话记录。
    """
    import tools.real_estate_property as m

    out = json.loads(m.add_property(title="大额房源 8号楼801", price=OVER, area=200,
                                    property_type="second_hand", force=True))
    assert out["success"] is True, out
    assert wired.get_property(out["property"]["id"])["price"] == float(OVER)
