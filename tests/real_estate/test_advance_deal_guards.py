"""advance_deal 回归（2026-09-26 第六组「成交」第 50 项，F216–F220）

背景（实测，见 /root/coco-tool-audit/results/raw/t52a.before_advance.log）：
① **交房日期存不进去**（A 级）：`advance_deal(deal_id, stage='finalized', date='2026-12-01')` 回 `success=true`、
   阶段也变成「交房完成」，但库里的 `finalize_date` **仍是 NULL** —— 代码按 `f'{stage}_date'` 拼列名，
   终态拼出的 `finalized_date` 在模型里不存在（真列叫 `finalize_date`），被 `update_deal` 的 `hasattr` 静默丢掉。
   其余四档列名恰好对得上，所以只试一两档看不出来（本文件按**五档各自**钉住）。
② 阶段只认英文，提示也只给英文枚举（`阶段必须是 deposit/signing/...`）。
③ 越级（定金→交房完成）与回退（交房完成→定金）**静默**，一句提醒都没有。
④ 推进时传备注会**整段覆盖**开单备注（`客户要求留车位` → `已签合同`，原信息消失）。
⑤ 交房完成后客户阶段停在「成交中」，没人把它挪到「售后维护」（拍板：自动挪，照带看档 3 的先例）。
"""
import json
import re
from datetime import datetime, timedelta

import pytest

_FORBIDDEN = ("参数", "advance_deal", "start_deal", "deal_id", "replace_notes", "customer_id")
_CODE_CALL = re.compile(r"[a-z_]{4,}\s*\(")


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_deal as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


def _customer(db, name="成交客户", phone="13800001111", stage=None, status=None):
    cid = db.add_customer(name=name, phone=phone, tier="A",
                          customer_type="buy_second_hand")["id"]
    if stage:
        db.update_stage(cid, stage)
    if status:
        db.update_customer(cid, status=status)
    return cid


def _property(db, title="成交房源 1号楼101"):
    return db.add_property(title=title, price=1_500_000, area=80.0,
                           property_type="second_hand", status="available")["id"]


def _deal(db, cid=None, pid=None, notes=None, stage="deposit"):
    cid = cid or _customer(db)
    pid = pid or _property(db)
    kwargs = {"customer_id": cid, "property_id": pid, "stage": stage, "price": 1_500_000}
    if notes:
        kwargs["notes"] = notes
    return db.add_deal(**kwargs)["id"], cid


def _advance(**kwargs):
    import tools.real_estate_deal as m

    return json.loads(m.advance_deal(**kwargs))


def _deal_row(db, did, cols):
    from sqlalchemy import text

    with db.get_session() as s:
        row = s.execute(text(f"SELECT {', '.join(cols)} FROM re_deals WHERE id = :i"),
                        {"i": did}).fetchone()
    return dict(zip(cols, row)) if row else None


def _assert_agent_facing(out):
    texts = [out.get("message"), out.get("error")] + list(out.get("warnings") or [])
    texts = [t for t in texts if isinstance(t, str)]
    assert texts, f"这条用例没产出给人看的文本：{out}"
    for text in texts:
        for word in _FORBIDDEN:
            assert word not in text, f"出现内部口径「{word}」：{text}"
        assert not _CODE_CALL.search(text), f"出现代码写法：{text}"


# ==================== ① 五档阶段日期都必须落库（F216） ====================

class TestStageDates:
    @pytest.mark.parametrize("stage,column", [
        ("deposit", "deposit_date"), ("signing", "signing_date"), ("loan", "loan_date"),
        ("transfer", "transfer_date"), ("finalized", "finalize_date"),
    ])
    def test_each_stage_date_is_stored(self, wired, stage, column):
        did, _ = _deal(wired)
        out = _advance(deal_id=did, stage=stage, date="2026-12-01")
        assert out["success"] is True, out
        row = _deal_row(wired, did, ["stage", column])
        assert row["stage"] == stage, row
        assert row[column] is not None, f"{column} 没落库：{row}"

    def test_finalized_date_is_the_one_the_agent_gave(self, wired):
        """终态这一档尤其要钉死：列名映射写错时它静默丢日期（其余四档察觉不到）"""
        did, _ = _deal(wired)
        _advance(deal_id=did, stage="finalized", date="2026-12-31")
        row = _deal_row(wired, did, ["finalize_date"])
        assert str(row["finalize_date"])[:10] == "2026-12-31", row


# ==================== ② 阶段认中文说法（F217） ====================

class TestStageEnum:
    @pytest.mark.parametrize("raw,expect", [
        ("签约", "signing"), ("交房完成", "finalized"), ("贷款审批", "loan"),
        ("signing", "signing"), ("SIGNING", "signing"),
    ])
    def test_chinese_and_english_forms(self, wired, raw, expect):
        did, _ = _deal(wired)
        out = _advance(deal_id=did, stage=raw)
        assert out["success"] is True, out
        assert wired.get_deal(did)["stage"] == expect, out

    def test_bad_stage_gives_chinese_options(self, wired):
        did, _ = _deal(wired)
        out = _advance(deal_id=did, stage="乱写的")
        err = out.get("error") or ""
        assert out.get("success") is not True, out
        assert "阶段没能识别" in err and "签约(signing)" in err and "交房完成(finalized)" in err, err
        assert wired.get_deal(did)["stage"] == "deposit", "拦下了就不该动阶段"

    def test_empty_stage_gets_hint(self, wired):
        did, _ = _deal(wired)
        out = _advance(deal_id=did, stage="")
        assert out.get("success") is not True and "哪个阶段" in (out.get("error") or ""), out


# ==================== ③ 越级与回退只提醒不拦（F218） ====================

class TestSkipAndRollback:
    def test_normal_step_has_no_warning(self, wired):
        did, _ = _deal(wired)
        out = _advance(deal_id=did, stage="signing", date="2026-10-01")
        assert out["success"] is True and not out.get("warnings"), out

    def test_skipping_steps_is_warned(self, wired):
        did, _ = _deal(wired)
        out = _advance(deal_id=did, stage="finalized")
        assert out["success"] is True, out
        joined = " ".join(out.get("warnings") or [])
        assert "跳过了" in joined, out
        for label in ("签约", "贷款审批", "过户"):
            assert label in joined, out
        _assert_agent_facing(out)

    def test_rollback_is_warned(self, wired):
        did, _ = _deal(wired, stage="finalized")
        out = _advance(deal_id=did, stage="deposit")
        assert out["success"] is True, out
        joined = " ".join(out.get("warnings") or [])
        assert "退回" in joined and "交房完成" in joined and "意向金/定金" in joined, out
        _assert_agent_facing(out)


# ==================== ④ 备注默认追加，replace_notes 才覆盖（F219） ====================

class TestNotes:
    def test_note_is_appended_with_stage_stamp(self, wired):
        did, _ = _deal(wired, notes="客户要求留车位")
        out = _advance(deal_id=did, stage="signing", date="2026-10-01", notes="已签合同")
        assert out["success"] is True, out
        notes = wired.get_deal(did)["notes"]
        assert notes.startswith("客户要求留车位"), notes
        assert "[签约 2026-10-01] 已签合同" in notes, notes
        assert "备注已追加" in out["message"], out
        _assert_agent_facing(out)

    def test_replace_notes_overwrites(self, wired):
        did, _ = _deal(wired, notes="客户要求留车位")
        out = _advance(deal_id=did, stage="signing", notes="备注写错了，改成：客户不要车位",
                       replace_notes=True)
        assert out["success"] is True, out
        assert wired.get_deal(did)["notes"] == "备注写错了，改成：客户不要车位", wired.get_deal(did)["notes"]
        assert "替换" in out["message"], out

    def test_first_note_on_empty_deal_is_plain(self, wired):
        did, _ = _deal(wired)
        _advance(deal_id=did, stage="signing", notes="已签合同")
        assert wired.get_deal(did)["notes"] == "已签合同"

    def test_no_notes_no_change(self, wired):
        did, _ = _deal(wired, notes="客户要求留车位")
        _advance(deal_id=did, stage="signing")
        assert wired.get_deal(did)["notes"] == "客户要求留车位"


# ==================== ⑤ 交房完成 → 客户阶段挪到「售后维护」（F220） ====================

class TestFinalizeAdvancesCustomerStage:
    def test_customer_moves_to_aftercare(self, wired):
        did, cid = _deal(wired)
        out = _advance(deal_id=did, stage="finalized")
        assert out["success"] is True, out
        assert wired.get_customer(cid)["stage"] == "maintain", wired.get_customer(cid)
        assert out["stage_advance"]["to_label"] == "售后维护", out
        assert "售后维护" in out["message"], out
        _assert_agent_facing(out)

    def test_stage_change_is_recorded(self, wired):
        did, cid = _deal(wired)
        _advance(deal_id=did, stage="finalized")
        rows = wired.get_customer_changes(cid, limit=50)
        assert any(r.get("field") == "stage" and r.get("new_value") == "maintain" for r in rows), rows

    def test_non_final_stage_does_not_touch_customer(self, wired):
        did, cid = _deal(wired)
        _advance(deal_id=did, stage="signing")
        assert wired.get_customer(cid)["stage"] == "dealing", wired.get_customer(cid)

    def test_customer_already_in_aftercare_is_left_alone(self, wired):
        cid = _customer(wired)
        did, _ = _deal(wired, cid=cid)
        wired.update_stage(cid, "maintain")
        before = len(wired.get_customer_changes(cid, limit=50))
        out = _advance(deal_id=did, stage="finalized")
        assert out["success"] is True and "stage_advance" not in out, out
        assert len(wired.get_customer_changes(cid, limit=50)) == before

    def test_closed_customer_is_not_moved(self, wired):
        cid = _customer(wired, status="closed")
        did, _ = _deal(wired, cid=cid)
        out = _advance(deal_id=did, stage="finalized")
        assert out["success"] is True and "stage_advance" not in out, out
        assert wired.get_customer(cid)["stage"] == "dealing", wired.get_customer(cid)


# ==================== ⑥ 框架层契约（走真实 dispatch） ====================

def test_dispatch_accepts_chinese_stage(wired, monkeypatch):
    import tools.real_estate_deal as m

    monkeypatch.setattr(m, "_get_db", lambda: wired)
    from tools.registry import registry

    did, _ = _deal(wired)
    out = json.loads(registry.dispatch("advance_deal", {"deal_id": did, "stage": "签约"},
                                       session_id="t", task_id="t"))
    assert out.get("success") is True, out
    assert wired.get_deal(did)["stage"] == "signing"
