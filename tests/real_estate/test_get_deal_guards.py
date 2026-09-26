"""get_deal 回归（2026-09-26 第六组「成交」第 51 项，F221–F225）

背景（实测，见 /root/coco-tool-audit/results/raw/t56.before.log，22 项 16 过 6 红）：
① **孤儿成交单不给可读标注**：客户/房源被删（存量数据、外部改库）后 `customer_name`/`property_title` 都是 `null`
   （契约 20 要求「已删除客户（id=N）」这类可读标注）；
② **金额只有裸元值**：`price: 1850000`、`deposit_amount: 50000`，没有任何「万」口径的可读字段；
③ **详情缺中文阶段名**：详情只有 `stage: "signing"`，而**列表里有** `stage_label: "签约"` —— 同一实体两个出口口径不一致；
④ **日期直出 ISO**：`signing_date: "2026-10-01T00:00:00"`（那个 `T` 会被念出来），没有可读日期字段；
⑤ **描述 6 字**。

本文件钉住修好之后的行为（含「只补不替换、原值仍给机器读」「空值不臆造」两条口径）。
"""
import json
from datetime import datetime

import pytest
from sqlalchemy import text


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_deal as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


def _fixture(db, price=1_850_000, deposit=50_000, stage="signing", date="2026-10-01",
             notes="客户要求留车位", ptype="second_hand"):
    cid = db.add_customer(name="详情客户", phone="13800001111", tier="A",
                          customer_type="buy_second_hand")["id"]
    pid = db.add_property(title="详情房源 1号楼101", price=1_500_000, area=80.0,
                          property_type=ptype, status="available")["id"]
    args = {"customer_id": cid, "property_id": pid, "price": price, "notes": notes}
    if deposit is not None:
        args["deposit_amount"] = deposit
    did = db.add_deal(**args)["id"]
    if stage != "deposit":
        db.update_deal(did, stage=stage)
    if date:
        column = "finalize_date" if stage == "finalized" else f"{stage}_date"
        db.update_deal(did, **{column: datetime.strptime(date, "%Y-%m-%d")})
    return did, cid, pid


def _get(did):
    import tools.real_estate_deal as m

    return json.loads(m.get_deal(deal_id=did))


# ==================== ① 金额与日期的可读口径（F222 / F224） ====================

class TestReadableValues:
    def test_labels_are_added_and_raw_kept(self, wired):
        did, _, _ = _fixture(wired)
        deal = _get(did)["deal"]
        assert deal["price_label"] == "185万", deal
        assert deal["deposit_label"] == "5万", deal
        # 原值仍然给机器读
        assert deal["price"] == 1_850_000 and deal["deposit_amount"] == 50_000, deal

    def test_non_round_amount_keeps_two_decimals(self, wired):
        did, _, _ = _fixture(wired, price=1_853_000)
        assert _get(did)["deal"]["price_label"] == "185.30万"

    def test_dates_have_readable_labels(self, wired):
        did, _, _ = _fixture(wired, stage="signing", date="2026-10-01")
        deal = _get(did)["deal"]
        assert deal["signing_date_label"] == "2026-10-01", deal
        assert "T" not in deal["signing_date_label"], deal
        assert deal["signing_date"].startswith("2026-10-01"), "原值不动"

    def test_missing_amount_or_date_gives_none_not_invented(self, wired):
        did, _, _ = _fixture(wired, deposit=None, date=None)
        deal = _get(did)["deal"]
        assert deal["deposit_label"] is None, deal
        assert deal["signing_date_label"] is None, deal
        assert deal["price_label"] == "185万", deal

    def test_all_five_stage_dates_get_labels(self, wired):
        did, _, _ = _fixture(wired)
        wired.update_deal(did, loan_date=datetime(2026, 10, 10),
                          transfer_date=datetime(2026, 11, 1),
                          finalize_date=datetime(2026, 11, 20))
        deal = _get(did)["deal"]
        assert deal["loan_date_label"] == "2026-10-10", deal
        assert deal["transfer_date_label"] == "2026-11-01", deal
        assert deal["finalize_date_label"] == "2026-11-20", deal


# ==================== ② 阶段中文名（F223） ====================

class TestStageLabel:
    def test_detail_and_list_agree(self, wired):
        import tools.real_estate_deal as m

        did, _, _ = _fixture(wired, stage="transfer")
        deal = _get(did)["deal"]
        rows = json.loads(m.list_deals(limit=50))["deals"]
        row = next(x for x in rows if x["id"] == did)
        assert deal["stage_label"] == "过户", deal
        assert deal["stage_label"] == row["stage_label"] == "过户", (deal, row)
        assert deal["stage"] == "transfer", "原值不动"

    @pytest.mark.parametrize("stage,label", [
        ("deposit", "意向金/定金"), ("signing", "签约"), ("loan", "贷款审批"),
        ("transfer", "过户"), ("finalized", "交房完成"),
    ])
    def test_labels_follow_the_shared_table(self, wired, stage, label):
        did, _, _ = _fixture(wired, stage=stage, date=None)
        assert _get(did)["deal"]["stage_label"] == label


# ==================== ③ 孤儿可读标注（F221） ====================

class TestOrphans:
    def test_deleted_customer_and_property_are_labelled(self, wired):
        did, cid, pid = _fixture(wired)
        with wired.get_session() as s:      # 模拟存量数据/外部改库
            s.execute(text("UPDATE re_deals SET customer_id = 999999, property_id = 888888"
                           " WHERE id = :i"), {"i": did})
            s.commit()
        deal = _get(did)["deal"]
        assert deal["customer_name"] == "已删除客户（id=999999）", deal
        assert deal["property_title"] == "已删除房源（id=888888）", deal

    def test_normal_deal_keeps_real_names(self, wired):
        did, cid, pid = _fixture(wired)
        deal = _get(did)["deal"]
        assert deal["customer_name"] == "详情客户", deal
        assert deal["property_title"] == "详情房源 1号楼101", deal


# ==================== ④ 读类工具无副作用 + 幂等 ====================

def test_get_deal_does_not_write(wired):
    did, _, _ = _fixture(wired)
    before = (wired.get_customer_changes(1, limit=50), len(wired.list_deals(limit=50)))
    for _ in range(3):
        _get(did)
    after = (wired.get_customer_changes(1, limit=50), len(wired.list_deals(limit=50)))
    assert before == after, "查详情不该改库"
    assert _get(did) == _get(did), "两次结果必须完全一致"


def test_missing_deal_says_not_exist(wired):
    import tools.real_estate_deal as m

    out = json.loads(m.get_deal(deal_id=999999))
    assert out.get("success") is not True and "成交单不存在" in out["error"], out
