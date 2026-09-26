"""list_deals 回归（2026-09-26 第六组「成交」第 52 项，F226–F231）

背景（实测，见 /root/coco-tool-audit/results/raw/t57.before.log，25 项 13 过 12 红）：
① 返回形状只有 `count`，缺 `total`/`truncated` —— `limit=1` 时不说一共有几单、被截断也不说；
② 空态分不清：真空库、有成交单但筛选没命中**都只给空列表**；
③ 阶段筛选不归一：`stage='签约'` 筛不到，`stage='乱写的阶段'` 静默返回空列表（假零）；
④ 逐行懒加载 N+1：20 条各不同客户/房源 → **41 次 SQL**（200 条 → 401 次）；
⑤ 展示口径没复用：列表每行只有 `stage_label`，缺金额「万」口径、日期可读 label、孤儿标注；
⑥ 描述 12 字。
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, text


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_deal as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


def _customer(db, name="列表客户", phone="13500001111"):
    return db.add_customer(name=name, phone=phone, tier="A",
                           customer_type="buy_second_hand")["id"]


def _property(db, title="列表房源 1号楼101"):
    return db.add_property(title=title, price=1_500_000, area=80.0,
                           property_type="second_hand", status="available")["id"]


def _deal(db, cid=None, pid=None, stage="deposit", price=1_500_000, days_ago=0):
    cid = cid or _customer(db, phone=f"13500{days_ago:06d}")
    pid = pid or _property(db, title=f"列表房源{days_ago} 1号楼101")
    did = db.add_deal(customer_id=cid, property_id=pid, stage=stage, price=price)["id"]
    if stage != "deposit":
        db.update_deal(did, stage=stage, signing_date=datetime(2026, 10, 1))
    if days_ago:
        with db.get_session() as s:
            s.execute(text("UPDATE re_deals SET created_at = :t WHERE id = :i"),
                      {"t": datetime.now() - timedelta(days=days_ago), "i": did})
            s.commit()
    return did, cid, pid


def _list(**kwargs):
    import tools.real_estate_deal as m

    return json.loads(m.list_deals(**kwargs))


# ==================== ① 形状三件齐 + 空态（F226 / F227） ====================

class TestShapeAndEmpty:
    def test_total_is_full_count_not_page_size(self, wired):
        for i in range(3):
            _deal(wired, days_ago=i)
        out = _list(limit=1)
        assert out["count"] == 1 and out["total"] == 3 and out["truncated"] is True, out
        assert out["message"] == "共 3 单成交，这里列最近 1 单（要我多列就说一声）", out

    def test_no_truncation_message_when_everything_fits(self, wired):
        _deal(wired)
        out = _list()
        assert out["total"] == 1 and out["truncated"] is False and "message" not in out, out

    def test_empty_library_message(self, wired):
        out = _list()
        assert out["count"] == out["total"] == 0, out
        assert out["message"] == "还没有任何成交单，开单后这里就能看到", out

    def test_no_match_for_stage_says_so_with_total(self, wired):
        _deal(wired)
        out = _list(stage="交房完成")
        assert out["total"] == 0 and out["message"] == "没有「交房完成」阶段的成交单（库里共 1 单）", out

    def test_no_match_for_customer_says_so(self, wired):
        cid = _customer(wired)
        _deal(wired, cid=cid)
        other = _customer(wired, name="没成交的客户", phone="13500009999")
        out = _list(customer_id=other)
        assert out["total"] == 0 and out["message"] == "这位客户还没有成交单", out
        out2 = _list(customer_id=other, stage="签约")
        assert out2["message"] == "这位客户没有「签约」阶段的成交单", out2

    def test_default_order_is_newest_first(self, wired):
        did_old, _, _ = _deal(wired, days_ago=5)
        did_new, _, _ = _deal(wired, days_ago=1)
        ids = [d["id"] for d in _list()["deals"]]
        assert ids == [did_new, did_old], ids


# ==================== ② 阶段筛选（F228） ====================

class TestStageFilter:
    def test_chinese_stage_filters(self, wired):
        did_sign, _, _ = _deal(wired, stage="signing", days_ago=2)
        _deal(wired, days_ago=1)
        out = _list(stage="签约")
        assert [d["id"] for d in out["deals"]] == [did_sign], out
        assert out["total"] == 1, out

    def test_english_stage_still_works(self, wired):
        _deal(wired, stage="signing", days_ago=2)
        _deal(wired, days_ago=1)
        assert _list(stage="signing")["total"] == 1

    def test_bad_stage_gets_chinese_hint_not_empty_list(self, wired):
        _deal(wired)
        out = _list(stage="乱写的阶段")
        assert out.get("success") is not True, out
        assert "阶段没能识别" in out["error"] and "签约(signing)" in out["error"], out

    def test_stage_total_matches_stats(self, wired):
        for i in range(2):
            _deal(wired, stage="transfer", days_ago=i)
        _deal(wired, days_ago=3)
        assert _list(stage="过户")["total"] == 2


# ==================== ③ customer_id 筛选（F231 新增能力） ====================

class TestCustomerFilter:
    def test_filters_by_customer(self, wired):
        cid_a = _customer(wired, name="甲", phone="13500001111")
        cid_b = _customer(wired, name="乙", phone="13500002222")
        did_a, _, _ = _deal(wired, cid=cid_a, days_ago=2)
        _deal(wired, cid=cid_b, days_ago=1)
        out = _list(customer_id=cid_a)
        assert [d["id"] for d in out["deals"]] == [did_a] and out["total"] == 1, out

    def test_accepts_numeric_string(self, wired):
        cid = _customer(wired)
        _deal(wired, cid=cid)
        assert _list(customer_id=str(cid))["total"] == 1

    def test_missing_customer_is_reported_not_silent(self, wired):
        _deal(wired)
        out = _list(customer_id=999999)
        assert out.get("success") is not True, out
        assert "客户不存在" in out["error"] and "999999" in out["error"], out

    def test_non_numeric_customer_gets_hint(self, wired):
        out = _list(customer_id="abc")
        assert out.get("success") is not True and "客户编号没能识别" in out["error"], out


# ==================== ④ 展示口径与详情一致（F230） ====================

class TestDisplay:
    def test_list_row_matches_detail_labels(self, wired):
        import tools.real_estate_deal as m

        did, _, _ = _deal(wired, stage="signing")
        row = next(d for d in _list()["deals"] if d["id"] == did)
        detail = json.loads(m.get_deal(deal_id=did))["deal"]
        for key in ("stage_label", "price_label", "signing_date_label", "customer_name",
                    "property_title"):
            assert row.get(key) == detail.get(key), (key, row.get(key), detail.get(key))
        assert row["price_label"] == "150万" and row["signing_date_label"] == "2026-10-01", row

    def test_orphan_row_gets_readable_labels(self, wired):
        did, _, _ = _deal(wired)
        with wired.get_session() as s:
            s.execute(text("UPDATE re_deals SET customer_id = 999999, property_id = 888888"
                           " WHERE id = :i"), {"i": did})
            s.commit()
        row = next(d for d in _list()["deals"] if d["id"] == did)
        assert row["customer_name"] == "已删除客户（id=999999）", row
        assert row["property_title"] == "已删除房源（id=888888）", row


# ==================== ⑤ 只读 + N+1 钉子 ====================

def test_list_does_not_write(wired):
    _deal(wired)
    before = (len(wired.list_deals(limit=50)), len(wired.get_customer_changes(1, limit=50)))
    _list()
    after = (len(wired.list_deals(limit=50)), len(wired.get_customer_changes(1, limit=50)))
    assert before == after, "列成交单不该改库"


def test_query_count_is_bounded(wired):
    """20 条各不同客户/房源 → 查询次数必须是个位数（原先 41 次逐行懒加载）

    夹具**必须让每行挂不同客户与房源**：同一对会被 SQLAlchemy identity map 折叠成 3 次，量出假的"全绿"。
    """
    for i in range(20):
        _deal(wired, days_ago=i)
    stmts = []

    def rec(conn, cursor, statement, parameters, context, executemany):
        stmts.append(statement)

    engine = wired.engine
    event.listen(engine, "before_cursor_execute", rec)
    try:
        out = _list(limit=20)
    finally:
        event.remove(engine, "before_cursor_execute", rec)
    assert len(out["deals"]) == 20, out
    assert len(stmts) <= 5, f"20 条用了 {len(stmts)} 次 SQL（N+1 回来了）"
