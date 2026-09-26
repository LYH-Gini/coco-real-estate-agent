"""start_deal 回归（2026-09-26 第六组「成交」第 49 项，F206–F213）

背景（实测，见 /root/coco-tool-audit/results/raw/t52a.before.log）：
① **没认房**：`property_id=999999` 照样建成交单 → 库里落孤儿单、详情里房源名是 `null`（客户侧有校验，房源侧漏了）；
② **重复开单静默建第二单**：同客户同房源再开一次 → 库里 2 单、回执毫无提示；
③ 已售/已租房源、已关闭客户开单**一句提醒都没有**（带看侧同类情况会给 warnings）；
④ **金额不归一**：`price="185万"` 被原样写进整数列（SQLite 存成文本，生产 PostgreSQL 上整单失败）；
⑤ 金额无基础校验：成交价 `0`/`-100万`、定金 `-5000` 都入库，**定金 500万 > 成交价 400万** 也照收；
⑥ 回执不带成交单编号、硬编码"已标记售出/出租"、不提客户阶段被推进到「成交中」；
⑦ 日期参数只认 `YYYY-MM-DD`（`明天`、`2026/12/31`、`2026年12月31日` 全被拒）。

本文件钉住修好之后的行为（含"同一对不重复开单"与"旧日期写法不许改坏"两条契约钉子）。
"""
import json
import re
from datetime import datetime, timedelta

import pytest

# 给经纪人看的文本里不许出现的内部口径（与 test_message_wording_guards 同一口径的轻量版）
_FORBIDDEN = ("参数", "start_deal", "advance_deal", "update_customer", "list_deals",
              "customer_id", "property_id", "deposit_amount")
_CODE_CALL = re.compile(r"[a-z_]{4,}\s*\(")


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_deal as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


def _customer(db, name="成交客户", phone="13800001111", **kw):
    return db.add_customer(name=name, phone=phone, tier="A",
                           customer_type="buy_second_hand", **kw)["id"]


def _property(db, title="成交房源 1号楼101", ptype="second_hand", status="available", price=1_500_000):
    return db.add_property(title=title, price=price, area=80.0,
                           property_type=ptype, status=status)["id"]


def _start(**kwargs):
    import tools.real_estate_deal as m

    return json.loads(m.start_deal(**kwargs))


def _advance(**kwargs):
    import tools.real_estate_deal as m

    return json.loads(m.advance_deal(**kwargs))


def _assert_agent_facing(out):
    """message / error / warnings 里不许出现参数名、内部工具名与代码写法"""
    texts = [out.get("message"), out.get("error")] + list(out.get("warnings") or [])
    texts = [t for t in texts if isinstance(t, str)]
    assert texts, f"这条用例没产出给人看的文本：{out}"
    for text in texts:
        for word in _FORBIDDEN:
            assert word not in text, f"出现内部口径「{word}」：{text}"
        assert not _CODE_CALL.search(text), f"出现代码写法：{text}"


def _deals(db):
    return db.list_deals(limit=200)


# ==================== ① 认人认房（F206） ====================

class TestTargetChecks:
    def test_missing_property_is_refused(self, wired):
        cid = _customer(wired)
        out = _start(customer_id=cid, property_id=999999, price=1_500_000)
        assert out.get("success") is not True, out
        assert "房源不存在" in out["error"] and "999999" in out["error"], out
        assert _deals(wired) == [], "不许留下孤儿成交单"

    def test_missing_customer_is_refused(self, wired):
        pid = _property(wired)
        out = _start(customer_id=999999, property_id=pid, price=1_500_000)
        assert out.get("success") is not True and "客户不存在" in out["error"], out
        assert _deals(wired) == []

    def test_non_numeric_property_id_gets_hint_not_orphan(self, wired):
        cid = _customer(wired)
        out = _start(customer_id=cid, property_id="abc", price=1_500_000)
        assert out.get("success") is not True, out
        assert "房源编号没能识别" in out["error"] and "不存在" not in out["error"], out
        assert _deals(wired) == []


# ==================== ② 重复开单（F207） ====================

class TestDuplicateDeal:
    def test_same_pair_does_not_create_second_deal(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        first = _start(customer_id=cid, property_id=pid, price=1_500_000)
        assert first["success"] is True, first
        again = _start(customer_id=cid, property_id=pid, price=1_600_000)
        assert again.get("success") is True, again
        assert again.get("already_started") is True, again
        assert again["deal"]["id"] == first["deal"]["id"], again
        assert len(_deals(wired)) == 1, "同一客户同一房源不该有第二条未完结成交单"
        assert str(first["deal"]["id"]) in again["message"], again
        _assert_agent_facing(again)

    def test_finalized_deal_allows_a_new_one(self, wired):
        """已交房完成的成交单不算重复（真实的再成交场景）"""
        cid = _customer(wired)
        pid = _property(wired)
        first = _start(customer_id=cid, property_id=pid, price=1_500_000)
        did = first["deal"]["id"]
        assert _advance(deal_id=did, stage="finalized")["success"] is True
        again = _start(customer_id=cid, property_id=pid, price=1_600_000)
        assert again.get("success") is True and not again.get("already_started"), again
        assert again["deal"]["id"] != did
        assert len(_deals(wired)) == 2


# ==================== ③ 连带副作用的提醒（F208） ====================

class TestSideEffectWarnings:
    def test_sold_property_gets_warning_and_honest_message(self, wired):
        cid = _customer(wired)
        pid = _property(wired, status="sold")
        out = _start(customer_id=cid, property_id=pid, price=1_500_000)
        assert out["success"] is True, out
        assert any("已售" in w for w in out.get("warnings") or []), out
        assert "此前已是「已售」" in out["message"], out
        _assert_agent_facing(out)

    def test_closed_customer_gets_warning(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        wired.update_customer(cid, status="closed")
        out = _start(customer_id=cid, property_id=pid, price=1_500_000)
        assert out["success"] is True, out
        assert any("已关闭" in w for w in out.get("warnings") or []), out
        _assert_agent_facing(out)

    def test_rental_property_becomes_rented_and_message_says_so(self, wired):
        cid = _customer(wired)
        pid = _property(wired, title="出租房源 2号楼202", ptype="rental", price=2800)
        out = _start(customer_id=cid, property_id=pid, price=2800)
        assert out["success"] is True, out
        assert wired.get_property(pid)["status"] == "rented"
        assert "已租" in out["message"], out

    def test_message_reports_customer_stage_advance(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        wired.update_stage(cid, "negotiating")
        out = _start(customer_id=cid, property_id=pid, price=1_500_000)
        assert wired.get_customer(cid)["stage"] == "dealing"
        assert "成交中" in out["message"], out
        assert "推进" in out["message"], out
        _assert_agent_facing(out)

    def test_customer_already_dealing_is_reported_as_unchanged(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        wired.update_stage(cid, "dealing")
        out = _start(customer_id=cid, property_id=pid, price=1_500_000)
        assert "已在「成交中」" in out["message"], out

    def test_no_warning_when_everything_is_normal(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        out = _start(customer_id=cid, property_id=pid, price=1_500_000, deposit_amount=50_000)
        assert out["success"] is True, out
        assert not out.get("warnings"), out
        assert "已标记为已售" in out["message"], out


# ==================== ④⑤ 金额（F209–F210） ====================

class TestMoney:
    def test_wan_text_is_normalized_to_yuan(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        out = _start(customer_id=cid, property_id=pid, price="185万", deposit_amount="5万")
        assert out["success"] is True, out
        row = _deals(wired)[0]
        assert row["price"] == 1_850_000 and isinstance(row["price"], int), row
        assert row["deposit_amount"] == 50_000, row
        assert "「185万」按 1850000 元记的" in out["message"], out
        _assert_agent_facing(out)

    def test_price_zero_or_negative_is_refused(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        for bad in (0, -1_000_000):
            out = _start(customer_id=cid, property_id=pid, price=bad)
            assert out.get("success") is not True, out
            assert "成交价要大于 0" in out["error"], out
        assert _deals(wired) == []

    def test_deposit_negative_is_refused(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        out = _start(customer_id=cid, property_id=pid, price=1_500_000, deposit_amount=-5000)
        assert out.get("success") is not True and "定金要大于 0" in out["error"], out
        assert _deals(wired) == []

    def test_deposit_above_price_is_refused(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        out = _start(customer_id=cid, property_id=pid, price=4_000_000, deposit_amount=5_000_000)
        assert out.get("success") is not True, out
        assert "定金 500万" in out["error"] and "成交价 400万" in out["error"], out
        assert _deals(wired) == []

    def test_deposit_equal_to_price_is_allowed(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        out = _start(customer_id=cid, property_id=pid, price=1_500_000, deposit_amount=1_500_000)
        assert out["success"] is True, out

    def test_deposit_without_price_is_allowed(self, wired):
        """只收定金、成交价还没谈定是真实场景，不拿它跟 0 比"""
        cid = _customer(wired)
        pid = _property(wired)
        out = _start(customer_id=cid, property_id=pid, deposit_amount=50_000)
        assert out["success"] is True, out
        assert _deals(wired)[0]["deposit_amount"] == 50_000

    def test_unrecognizable_amount_gets_chinese_hint(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        out = _start(customer_id=cid, property_id=pid, price="三万五左右")
        assert out.get("success") is not True, out
        assert "成交价没能识别" in out["error"] and "三万五左右" in out["error"], out
        assert _deals(wired) == []

    def test_integer_price_gets_no_normalization_noise(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        out = _start(customer_id=cid, property_id=pid, price=1_500_000, deposit_amount=50_000)
        assert "元记的" not in out["message"], out
        assert out["message"].count("成交价 150万") == 1, out


# ==================== ⑥ 日期归一（F213） ====================

class TestDateNormalization:
    def test_relative_date_is_accepted(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        out = _start(customer_id=cid, property_id=pid, price=1_500_000, deposit_date="明天")
        assert out["success"] is True, out
        stored = _deals(wired)[0]["deposit_date"][:10]
        assert stored == (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"), stored

    @pytest.mark.parametrize("raw", ["2026/12/31", "2026年12月31日", "2026.12.31"])
    def test_common_writings_are_accepted(self, wired, raw):
        cid = _customer(wired)
        pid = _property(wired)
        out = _start(customer_id=cid, property_id=pid, price=1_500_000, deposit_date=raw)
        assert out["success"] is True, out
        assert _deals(wired)[0]["deposit_date"][:10] == "2026-12-31"

    @pytest.mark.parametrize("raw,hour", [("2026-12-31 10:00", 10), ("2026-12-31T10:00", 10)])
    def test_old_formats_are_not_broken(self, wired, raw, hour):
        """契约：归一化只许扩能力 —— 旧实现认的写法一条都不能少"""
        cid = _customer(wired)
        pid = _property(wired)
        out = _start(customer_id=cid, property_id=pid, price=1_500_000, deposit_date=raw)
        assert out["success"] is True, out
        stored = _deals(wired)[0]["deposit_date"]
        assert stored[:10] == "2026-12-31" and int(stored[11:13]) == hour, stored

    def test_bad_date_quotes_the_original_value(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        out = _start(customer_id=cid, property_id=pid, price=1_500_000, deposit_date="abc")
        assert out.get("success") is not True, out
        assert "定金日期没能识别" in out["error"] and "abc" in out["error"], out
        assert _deals(wired) == []

    def test_advance_deal_accepts_relative_date(self, wired):
        """成交日期与推进日期共用同一套归一（一处修好两个出口）"""
        cid = _customer(wired)
        pid = _property(wired)
        did = _start(customer_id=cid, property_id=pid, price=1_500_000)["deal"]["id"]
        out = _advance(deal_id=did, stage="signing", date="明天")
        assert out["success"] is True, out
        stored = wired.get_deal(did)["signing_date"][:10]
        assert stored == (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"), stored


# ==================== ⑦ 回执与文案（F211） ====================

class TestReceipt:
    def test_receipt_carries_id_money_and_side_effects(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        out = _start(customer_id=cid, property_id=pid, price=1_500_000, deposit_amount=50_000)
        msg = out["message"]
        assert f"成交单编号 {out['deal']['id']}" in msg, msg
        assert "成交价 150万" in msg and "定金 5万" in msg, msg
        assert "意向金/定金" in msg, msg
        assert "不再对外推荐" in msg and "成交中" in msg, msg
        _assert_agent_facing(out)

    def test_receipt_without_money_says_nothing_about_money(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        out = _start(customer_id=cid, property_id=pid)
        assert out["success"] is True, out
        assert "｜成交价" not in out["message"] and "｜定金" not in out["message"], out


# ==================== ⑨ 阶段推进留痕（F214） ====================

class TestStageTrail:
    def test_deal_advance_is_recorded_in_change_history(self, wired):
        """开单推进阶段必须走带留痕的路径（原先直接赋值 → 变更历史空白）"""
        cid = _customer(wired)
        pid = _property(wired)
        wired.update_stage(cid, "negotiating")
        before = len(wired.get_customer_changes(cid, limit=100))
        out = _start(customer_id=cid, property_id=pid, price=1_500_000)
        assert out["success"] is True, out
        rows = wired.get_customer_changes(cid, limit=100)
        assert len(rows) == before + 1, rows
        stage_row = next(r for r in rows if r.get("field") == "stage")
        assert stage_row.get("old_value") == "negotiating" and stage_row.get("new_value") == "dealing", stage_row

    def test_change_history_tool_shows_the_move(self, wired, monkeypatch):
        """用户视角：问「这位客户改过什么」必须看得到这次推进"""
        import tools.real_estate_customer as m_cust

        monkeypatch.setattr(m_cust, "_get_db", lambda: wired)
        cid = _customer(wired)
        pid = _property(wired)
        assert _start(customer_id=cid, property_id=pid, price=1_500_000)["success"] is True
        hist = json.loads(m_cust.customer_change_history(customer_id=cid))
        fields = [c.get("field") for c in (hist.get("changes") or [])]
        assert "stage" in fields, hist
        row = next(c for c in hist["changes"] if c.get("field") == "stage")
        assert row.get("new_display") == "成交中", row

    def test_customer_already_dealing_gets_no_extra_trail(self, wired):
        cid = _customer(wired)
        pid = _property(wired)
        wired.update_stage(cid, "dealing")
        before = len(wired.get_customer_changes(cid, limit=100))
        assert _start(customer_id=cid, property_id=pid, price=1_500_000)["success"] is True
        assert len(wired.get_customer_changes(cid, limit=100)) == before

    def test_missing_customer_message_names_the_number(self, wired):
        """客户侧与房源侧同一句式（业主侧也一样）"""
        pid = _property(wired)
        out = _start(customer_id=999999, property_id=pid, price=1_500_000)
        assert out.get("success") is not True, out
        assert "客户不存在" in out["error"] and "999999" in out["error"], out


# ==================== ⑩ 框架层契约（走真实 dispatch） ====================

def test_dispatch_boolean_id_does_not_hit_id_one(wired, monkeypatch):
    """框架层：bool 编号要拦成中文提示，不许张冠李戴命中 id=1"""
    import tools.real_estate_deal as m

    monkeypatch.setattr(m, "_get_db", lambda: wired)
    from tools.registry import registry

    cid = _customer(wired)
    pid = _property(wired)
    out = json.loads(registry.dispatch("start_deal", {"customer_id": True, "property_id": pid},
                                       session_id="t", task_id="t"))
    assert out.get("success") is not True, out
    assert "customer_id" in (out.get("error") or ""), out
    assert _deals(wired) == []
    out2 = json.loads(registry.dispatch("start_deal", {"customer_id": cid, "property_id": pid,
                                                       "price": "185万"},
                                       session_id="t", task_id="t"))
    assert out2.get("success") is True, out2
    assert _deals(wired)[0]["price"] == 1_850_000
