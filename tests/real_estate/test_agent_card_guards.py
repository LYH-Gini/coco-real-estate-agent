"""save_agent_card 回归（2026-09-27，第十二组第 5 项，F413–F416）

背景（实测证据：`/root/coco-tool-audit/results/raw/t88.log`、`results/save_agent_card.json`）：
① **传布尔直接崩**：`company=true` → `AttributeError: 'bool' object has no attribute 'strip'`
   （与 `update_birthday` 同一族：所有直接 `.strip()` 的参数都会被 true/false 打崩）；
② **手机号 / 微信不归一**：`137 0000 9001`、`137-0000-9001`、`+86137000009001` 全部原样入库 ——
   海报与名片照印这些写法（客户那族早就归一了，这里没有）；
③ **超长不截断**：600 字公司名照存 → 海报品牌栏被撑坏；
④ **给经纪人的话里夹带给模型的指令**：缺项时 message 尾巴是「（需要时问经纪人要，不要编）」。

口径（2026-09-27 老板批准）：复用共用件 `clean_text` / `clip_text` / `norm_phone`；
长度上限 姓名 10 / 电话 20 / 微信 30 / 公司名 30（截断要说）；`message` 只给经纪人，
「不要编」这类口径挪进 `note_for_model`。
"""
import json

import pytest


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_settings as m
    monkeypatch.setattr(m, "_get_db", lambda: db)
    return m


def _save(m, **kw):
    return json.loads(m.save_agent_card(**kw))


def _card(db):
    rows = db.get_settings_map() if hasattr(db, "get_settings_map") else None
    if rows is None:
        out = {}
        for key in ("agent_name", "agent_phone", "agent_wechat", "brand_name"):
            out[key] = db.get_setting(key)
        return out
    return rows


def _stored(db, key):
    return db.get_setting(key)


# ── ① 非文本参数不再崩 ────────────────────────────────────────────────
@pytest.mark.parametrize("field", ["name", "phone", "wechat", "company"])
@pytest.mark.parametrize("bad", [True, False])
def test_bool_field_is_rejected_in_chinese(wired, db, field, bad):
    out = _save(wired, **{field: bad})
    text = json.dumps(out, ensure_ascii=False)
    assert out["success"] is False, text
    assert "要填文字" in text and "是/否" in text, text[:160]
    assert "TOOL_ERROR" not in text and "Traceback" not in text


# ── ② 手机号 / 微信归一 ───────────────────────────────────────────────
@pytest.mark.parametrize("raw", ["137 0000 9001", "137-0000-9001", "+8613700009001", "13700009001"])
def test_phone_is_normalised(wired, db, raw):
    out = _save(wired, phone=raw)
    assert out["success"] is True, json.dumps(out, ensure_ascii=False)[:160]
    assert _stored(db, "agent_phone") == "13700009001", _stored(db, "agent_phone")
    if raw != "13700009001":
        assert "已按 13700009001 记下" in (out.get("message") or ""), out.get("message")


def test_wechat_prefix_and_spaces_stripped(wired, db):
    out = _save(wired, wechat="  微信：li-888  ")
    assert out["success"] is True
    assert _stored(db, "agent_wechat") == "li-888", _stored(db, "agent_wechat")


def test_non_phone_like_value_is_kept_but_flagged(wired, db):
    out = _save(wired, phone="不是我电话")
    assert out["success"] is True, "名片是展示用，不像号码也给存"
    assert _stored(db, "agent_phone") == "不是我电话"
    assert "看着不像完整手机号" in (out.get("message") or ""), out.get("message")


# ── ③ 超长截断 + 说明 ─────────────────────────────────────────────────
def test_overlong_company_is_clipped_with_note(wired, db):
    long_name = "宇恒房产" * 20
    out = _save(wired, company=long_name)
    assert out["success"] is True
    stored = _stored(db, "brand_name")
    assert len(stored) == 30, f"应按 30 字截断，实际 {len(stored)}"
    assert "太长" in (out.get("message") or "") and "前 30 字" in (out.get("message") or ""), out.get("message")


# ── ④ 两个受众分开：message 给经纪人，note_for_model 给模型 ─────────────
def test_message_has_no_model_directive(wired, db):
    out = _save(wired, name="李经理")
    msg = out.get("message") or ""
    for bad in ("不要编", "需要时问经纪人要", "参数", "字段"):
        assert bad not in msg, f"message 不该出现给模型的指令 {bad!r}：{msg!r}"
    assert "还差" in msg, f"缺项要用经纪人能懂的话说：{msg!r}"


def test_note_for_model_carries_the_directive(wired, db):
    out = _save(wired, name="李经理")
    note = out.get("note_for_model") or ""
    assert "不要编" in note, f"给模型的口径应放在 note_for_model：{json.dumps(out, ensure_ascii=False)[:200]}"
    assert "电话" in note and "微信" in note


def test_no_missing_no_note_noise(wired, db):
    out = _save(wired, name="李经理", phone="13700009002", wechat="li-888", company="宇恒房产")
    assert out.get("still_missing") == [], out.get("still_missing")
    assert "还差" not in (out.get("message") or "")
    assert not out.get("note_for_model"), "没有缺项就不要给模型补刀"


# ── ⑤ 落库与读回一致 ──────────────────────────────────────────────────
def test_saved_card_reads_back_same_values(wired, db):
    _save(wired, name="李经理", phone="137 0000 9003", wechat="li-888", company="宇恒房产")
    card = json.loads(wired.get_agent_card()).get("card") or {}
    assert card.get("phone") == "13700009003", card
    assert card.get("company") == "宇恒房产"
    assert wired.get_agent_card_or_empty().get("phone") == "13700009003"


def test_blank_fields_are_not_written(wired, db):
    _save(wired, name="李经理", phone="13700009004")
    _save(wired, name="   ")
    assert _stored(db, "agent_name") == "李经理", "纯空白按未提供，不许把姓名清成空"
    assert _stored(db, "agent_phone") == "13700009004"


def test_all_blank_reports_nothing_to_save(wired, db):
    out = _save(wired, name="  ", phone=" ", wechat="", company=None)
    assert out["success"] is False and "没有可保存的内容" in json.dumps(out, ensure_ascii=False)
