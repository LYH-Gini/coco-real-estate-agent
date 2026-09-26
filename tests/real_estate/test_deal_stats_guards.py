"""deal_stats 回归（2026-09-26 第六组「成交」第 53 项，F232–F233）

背景（实测，见 /root/coco-tool-audit/results/raw/t58.before.log 与 t52b.before.log）：
① **一句说明都没有**：真空库与有数据时 `message` 都是 `None` —— 经纪人问"成交情况"时上层只能干念数字，
   而且 `total_deals` 是"登记在册"的单数（含还在推进中的），很容易被读成"已成交套数"；
② 描述 15 字（`成交统计：各阶段数量、总成交数`），没说口径与范围。

本文件钉住修后的行为：字段键一个不改（经营报告在读），只补一句把口径说清的话 + 空库分开说。
"""
import json

import pytest


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_deal as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


def _deal(db, stage="deposit", i=0):
    cid = db.add_customer(name=f"统计客户{i}", phone=f"13900{i:06d}", tier="A",
                          customer_type="buy_second_hand")["id"]
    pid = db.add_property(title=f"统计房源{i} 1号楼101", price=1_500_000, area=80.0,
                          property_type="second_hand", status="available")["id"]
    return db.add_deal(customer_id=cid, property_id=pid, stage=stage, price=1_500_000)["id"]


def _stats(**kwargs):
    import tools.real_estate_deal as m

    return json.loads(m.deal_stats(**kwargs))


# ==================== ① 字段键与口径（不许改键） ====================

class TestShape:
    def test_keys_unchanged_for_consumers(self, wired):
        """经营报告在读 total_deals / stages —— 键一个都不能变"""
        _deal(wired, stage="signing", i=1)
        stats = _stats()["stats"]
        assert set(("total_deals", "stages", "finalized", "stage_labels")) <= set(stats), stats
        assert set(stats["stages"]) == {"deposit", "signing", "loan", "transfer", "finalized"}, stats

    def test_totals_add_up_and_match_sql(self, wired):
        for i, stage in enumerate(["deposit", "deposit", "signing", "transfer", "finalized"]):
            _deal(wired, stage=stage, i=i)
        stats = _stats()["stats"]
        assert stats["total_deals"] == sum(stats["stages"].values()) == 5, stats
        assert stats["stages"]["deposit"] == 2 and stats["finalized"] == 1, stats

    def test_stage_labels_are_chinese(self, wired):
        stats = _stats()["stats"]
        assert stats["stage_labels"]["finalized"] == "交房完成", stats["stage_labels"]


# ==================== ② message 两种说法（F232） ====================

class TestMessage:
    def test_empty_library_message(self, wired):
        out = _stats()
        assert out["stats"]["total_deals"] == 0, out
        assert out["message"] == "库里还没有成交单，开单后这里就能看到", out

    def test_message_spells_out_the_scope(self, wired):
        for i, stage in enumerate(["deposit", "signing", "finalized"]):
            _deal(wired, stage=stage, i=i)
        out = _stats()
        msg = out["message"]
        assert "登记在册" in msg and "已交房完成 1 单" in msg, msg
        assert "含还在推进中的" in msg, msg
        for label in ("意向金/定金 1", "签约 1", "交房完成 1"):
            assert label in msg, (label, msg)

    def test_message_has_no_internal_terms(self, wired):
        _deal(wired)
        out = _stats()
        for text in (out.get("message"), out.get("error")):
            if not isinstance(text, str):
                continue
            for word in ("total_deals", "stages", "finalized", "stage_labels", "参数", "deal_stats"):
                assert word not in text, f"给人看的话里出现内部口径「{word}」：{text}"


# ==================== ③ 与其他工具对账（逐阶段） ====================

def test_each_stage_matches_list_tool(wired):
    import tools.real_estate_deal as m

    for i, stage in enumerate(["deposit", "deposit", "loan", "transfer"]):
        _deal(wired, stage=stage, i=i)
    stats = _stats()["stats"]
    for stage in ("deposit", "signing", "loan", "transfer", "finalized"):
        listed = json.loads(m.list_deals(stage=stage, limit=1))["total"]
        assert stats["stages"][stage] == listed, (stage, stats["stages"][stage], listed)


def test_stats_does_not_write(wired):
    _deal(wired)
    before = (len(wired.list_deals(limit=50)), len(wired.get_customer_changes(1, limit=50)))
    _stats()
    after = (len(wired.list_deals(limit=50)), len(wired.get_customer_changes(1, limit=50)))
    assert before == after, "看统计不该改库"


def test_unexpected_param_is_rejected(wired, monkeypatch):
    """框架层契约一律走真实 dispatch（直接调工具函数会绕过框架的前置校验，得到 Python 的 TypeError）"""
    import tools.real_estate_deal as m

    monkeypatch.setattr(m, "_get_db", lambda: wired)
    from tools.registry import registry

    out = json.loads(registry.dispatch("deal_stats", {"period": "month"},
                                       session_id="t", task_id="t"))
    assert out.get("success") is not True and "period" in (out.get("error") or ""), out
