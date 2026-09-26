"""channel_stats 回归（2026-09-26 第十组「报表与数据」第 71 项，F350–F352）

背景（实测见 /root/coco-tool-audit/results/raw/t74a.before.log、t74b.before.log）：
① **同一个渠道的不同写法被算成两个渠道**：`贝壳找房` 与 `贝壳` 各占一行（各 1 人、各 100% 成交率），
   经纪人据此判断投放性价比会看错；函数说明里却承诺"来源为固定选项"；
② **`deals` 的口径没露出来**：它是"该渠道**有成交的客户数**"（不是成交单数），成交率分母是在跟客户数 ——
   这句只在函数体里写着，给经纪人看的话里一字未提；
③ **空库照样回话术**（`各渠道线索量与成交率一览，可据此判断广告投放性价比`），没有"库里还没有客户"；
④ 返回体只有 `total_channels`，没有客户数/已关闭的合计，对不上账。

本文件钉住修后的行为：同义写法合并 + 如实说明合并了什么/还有什么没合并、口径写进 message、
空库分开说、合计字段能对账。
"""
import json

import pytest


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_analytics as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


def _stats():
    import tools.real_estate_analytics as m

    return json.loads(m.channel_stats())


def _cust(db, name, source=None, i=0, status="active", tier="A"):
    return db.add_customer(name=name, phone=f"13700{i:06d}", source=source, tier=tier,
                           status=status, customer_type="buy_second_hand")["id"]


# ==================== ① 同义写法合并（F350） ====================

class TestSourceMerge:
    def test_same_channel_spellings_merge_into_one_row(self, wired):
        _cust(wired, "客1", source="贝壳", i=1)
        _cust(wired, "客2", source="贝壳找房", i=2)
        _cust(wired, "客3", source="贝壳网", i=3)
        out = _stats()
        rows = {c["source"]: c for c in out["channels"]}
        assert list(rows) == ["贝壳"], rows          # 不再各占一行
        assert rows["贝壳"]["customers"] == 3, rows
        assert rows["贝壳"]["spellings"] == ["贝壳找房", "贝壳网"], rows

    def test_message_says_what_was_merged(self, wired):
        _cust(wired, "客1", source="贝壳找房", i=1)
        _cust(wired, "客2", source="贝壳", i=2)
        msg = _stats()["message"]
        assert "已合并同一渠道的不同写法" in msg and "贝壳找房→贝壳" in msg, msg

    def test_unknown_spelling_kept_and_reported(self, wired):
        _cust(wired, "客1", source="小红书", i=1)
        _cust(wired, "客2", source="门店到访", i=2)
        out = _stats()
        rows = {c["source"]: c for c in out["channels"]}
        assert set(rows) == {"小红书", "门店"}, rows   # 门店到访 → 门店
        assert "没合并" in out["message"] and "小红书" in out["message"], out["message"]

    def test_any_existing_tests_still_see_plain_channels(self, wired):
        """老的 db 层契约别改坏：没写别名时 source 原样"""
        _cust(wired, "客1", source="抖音", i=1)
        assert wired.get_channel_stats()[0]["source"] == "抖音"

    def test_blank_source_goes_to_unspecified(self, wired):
        _cust(wired, "客1", source=None, i=1)
        _cust(wired, "客2", source="   ", i=2)
        rows = {c["source"]: c for c in _stats()["channels"]}
        assert rows["未填写"]["customers"] == 2, rows


# ==================== ② 口径（F351） ====================

class TestScopeIsSpelledOut:
    def test_conversion_rate_scope_is_in_message_and_description(self, wired):
        from tools.registry import registry

        cid = _cust(wired, "成交客户", source="贝壳", i=1)
        _cust(wired, "另一个客户", source="贝壳", i=2)
        pid = wired.add_property(title="渠道房 1号楼101", price=1_500_000, area=90.0,
                                 property_type="second_hand", status="available")["id"]
        wired.add_deal(customer_id=cid, property_id=pid, price=1_500_000)
        out = _stats()
        row = out["channels"][0]
        assert row["customers"] == 2 and row["deals"] == 1, row
        assert row["conversion_rate"] == 50.0, row
        assert "有成交的客户数 ÷ 该渠道在跟客户数" in out["message"], out["message"]
        desc = registry.get_entry("channel_stats").schema["description"]
        assert "有成交的客户数与客户成交率" in desc, desc
        assert "已合并" in desc, desc

    def test_deal_orders_counts_orders_not_customers(self, wired):
        """`deals` 是"有成交的客户数"、`成交单数` 是"单数"—— 一位客户两单时两者必须不同（F359）"""
        cid = _cust(wired, "复购客户", source="贝壳", i=1)
        for i in range(2):
            pid = wired.add_property(title=f"复购房源{i} 1号楼101", price=1_500_000 + i, area=90.0,
                                     property_type="second_hand", status="available")["id"]
            wired.add_deal(customer_id=cid, property_id=pid, price=1_500_000 + i)
        row = _stats()["channels"][0]
        assert row["deals"] == 1, row          # 1 位客户有成交
        assert row["成交单数"] == 2, row        # 但开了 2 张单

    def test_totals_add_up(self, wired):
        _cust(wired, "客1", source="贝壳", i=1)
        _cust(wired, "客2", source="抖音", i=2)
        _cust(wired, "客3", source="抖音", i=3, status="closed")
        out = _stats()
        assert out["在跟客户合计"] == 2, out
        assert out["已关闭合计"] == 1, out
        assert (out["在跟客户合计"] + out["已关闭合计"]) == 3, out
        assert sum(c["customers"] for c in out["channels"]) == out["在跟客户合计"], out
        assert out["渠道数"] == out["total_channels"] == len(out["channels"]), out

    def test_sorted_by_customers_desc(self, wired):
        _cust(wired, "客1", source="贝壳", i=1)
        for i in range(3):
            _cust(wired, f"抖音客{i}", source="抖音", i=10 + i)
        rows = _stats()["channels"]
        assert [c["source"] for c in rows] == ["抖音", "贝壳"], rows


# ==================== ③ 空库与只读（F352） ====================

class TestEmptyAndReadOnly:
    def test_empty_library_message(self, wired):
        out = _stats()
        assert out["channels"] == [] and out["渠道数"] == 0, out
        assert out["message"] == "库里还没有客户，先登记客户再看渠道数据", out

    def test_message_has_no_english_keys_or_enums(self, wired):
        _cust(wired, "客1", source="贝壳", i=1)
        msg = _stats()["message"]
        for word in ("source", "customers", "conversion_rate", "channels", "total_channels", "closed"):
            assert word not in msg, (word, msg)

    def test_channel_stats_does_not_write(self, wired):
        _cust(wired, "客1", source="贝壳", i=1)
        before = (wired.get_stats()["total_customers"], wired.count_customers())
        _stats()
        after = (wired.get_stats()["total_customers"], wired.count_customers())
        assert before == after

    def test_unexpected_param_is_rejected(self, wired, monkeypatch):
        from tools.registry import registry

        out = json.loads(registry.dispatch("channel_stats", {"period": "month"},
                                           session_id="t", task_id="t"))
        assert out.get("success") is not True and "period" in (out.get("error") or ""), out
