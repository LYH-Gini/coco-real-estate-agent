"""get_agent_card 回归（2026-09-27，第十二组第 6 项，F417–F419）

背景（实测证据：`/root/coco-tool-audit/results/raw/t89.log`、`results/get_agent_card.json`）：
① **空名片是裸结构体**：`{"success":true,"card":{全空},"missing":[四项]}` —— 没有一句中文说明，
   模型只能自己猜该跟经纪人说什么；也没有 `note_for_model` 告诉它"要去问，不要编"；
② **配置齐全时同样没有一句中文结论**（模型得自己拼"名片齐了"）；
③ **历史超长值读侧没有防线**：老库里 100 字的公司名读出来还是 100 字，海报栏位（30 字）会被撑坏。

口径（2026-09-27 老板批准，选 ①）：名片工具保持 `success:true`（"读成功，只是还没配置"），
本轮只把"未配置"的中文说辞统一；`get_agent_brand` 的 `success:false` 留到它自己的轮次再定。
超长值**读侧不截断**（读=写，不悄悄改展示），但给 `warnings` 提示。
"""
import json

import pytest


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_settings as m
    monkeypatch.setattr(m, "_get_db", lambda: db)
    return m


def _card(m):
    return json.loads(m.get_agent_card())


# ── ① 空名片 ──────────────────────────────────────────────────────────
def test_empty_card_explains_itself(wired):
    out = _card(wired)
    msg = out.get("message") or ""
    assert out["success"] is True, "读工具读到空不算错误"
    assert "还没配置" in msg and "海报" in msg, f"要说清现状与下一步：{msg!r}"
    assert out.get("note_for_model"), "要给模型一句可执行的话"
    assert "不要编" in out["note_for_model"], out["note_for_model"]
    assert out.get("missing") == ["姓名", "电话", "微信", "公司/门店名"], out.get("missing")


def test_empty_card_wording_matches_brand_tool(wired):
    """与 get_agent_brand 的"未配置"说辞不打架（两边都明确说未配置）。"""
    card_msg = _card(wired).get("message") or ""
    brand = json.loads(wired.get_agent_brand())
    assert "未配置" in (brand.get("error") or ""), brand
    assert "还没配置" in card_msg, card_msg


# ── ② 齐全时 ──────────────────────────────────────────────────────────
def test_complete_card_has_summary_line(wired):
    wired.save_agent_card(name="李经理", phone="13700001111", wechat="li-888", company="宇恒房产")
    out = _card(wired)
    msg = out.get("message") or ""
    assert "已齐全" in msg, f"齐全时也要给结论：{msg!r}"
    for bit in ("李经理", "13700001111", "li-888", "宇恒房产"):
        assert bit in msg, f"{bit} 应出现在结论里：{msg!r}"
    assert out.get("missing") == []
    assert not out.get("note_for_model"), "无缺项就不要给模型补刀"
    assert not out.get("warnings")


# ── ③ 有缺项 ──────────────────────────────────────────────────────────
def test_partial_card_lists_missing_in_chinese(wired):
    wired.save_agent_card(name="李经理")
    out = _card(wired)
    msg = out.get("message") or ""
    assert "还差" in msg and "电话" in msg, f"缺项要用经纪人能懂的话说：{msg!r}"
    assert "不要编" not in msg, "给经纪人的话不夹带模型口径"
    assert "不要编" in (out.get("note_for_model") or ""), out.get("note_for_model")


# ── ④ 历史超长值：读=写 + 提示 ─────────────────────────────────────────
def test_overlong_legacy_value_is_kept_but_warned(wired, db):
    long_name = "宇恒房产有限公司海南分公司海口市龙华区国贸大道分店" * 3
    db.set_setting("brand_name", long_name)
    out = _card(wired)
    assert out["card"]["company"] == long_name, "读工具不许悄悄改数据展示（读=写）"
    warn = " ".join(out.get("warnings") or [])
    assert "超过海报栏位" in warn and "30" in warn, f"要提示超长：{warn!r}"


def test_normal_length_has_no_warning(wired):
    wired.save_agent_card(name="李经理", phone="13700001111", wechat="li-888", company="宇恒房产")
    assert not _card(wired).get("warnings")


# ── ⑤ 基本契约 ────────────────────────────────────────────────────────
def test_read_does_not_write(wired, db):
    before = {k: db.get_setting(k) for k in ("agent_name", "agent_phone", "agent_wechat", "brand_name")}
    for _ in range(3):
        _card(wired)
    after = {k: db.get_setting(k) for k in ("agent_name", "agent_phone", "agent_wechat", "brand_name")}
    assert before == after
