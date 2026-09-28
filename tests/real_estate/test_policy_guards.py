"""政策查询工具回归（2026-09-28 重写口径，F440–F441）

背景（实测证据：`/root/coco-tool-audit/FINDINGS.md` F440/F441；韩国测试实例会话
`20260928_101300_97f7c72d` 消息 263→268）：
经纪人问「你收录了哪些城市的政策？」，模型先 `tool_describe` 读到工具的说明书、再调工具拿到
`{"success": false, "error": "本地政策库已停用…"}`，于是把它**转述**成一段系统说明书发给经纪人
（「结论：本地政策库已停用…当前做法…」）—— 读起来像提示词。

修法：对外只给**一句可以直接回复经纪人的产品语言**（不预存数字、当场联网查、连来源和日期一起发），
把"模型该怎么做"（联网检索、标来源与检索日期、禁止凭记忆报数字）挪进 `note_for_model`。

本文件钉住：
① 两个入口的对外文案都是产品语言，**不出现**「停用 / 收录了…城市 / 本工具 / 知识库 / 参数名 / 工具名」；
② 城市名回显有边界（乱值不回显）；
③ 即使政策文件里塞了数据也绝不返回固定政策数字；
④ 给模型的那句话（`note_for_model`）在位、说清了禁止凭记忆报数字。
"""
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_FILE = REPO_ROOT / "skills" / "real_estate" / "references" / "policies.json"
INTERNAL_TERMS = ("web_search", "hermes ", "DATABASE_URL", ".env.db", "sqlite", "Traceback",
                  "停用", "本工具", "知识库", "收录了", "note_for_model")


@pytest.fixture
def policy_mod():
    import tools.real_estate_policy as m
    return m


def _call(mod, args):
    return json.loads(mod.get_loan_policy(**args))


def _text(res):
    return json.dumps(res, ensure_ascii=False)


def _user_text(res):
    """只取经纪人会读到的那个字段（error）；note_for_model 是给模型看的，不参与判定。"""
    return res.get("error", "")


# ── ① 对外文案：产品语言、无内部口径 ───────────────────────────────────
def test_message_is_broker_facing(policy_mod):
    res = _call(policy_mod, {})
    assert res["success"] is False
    text = _user_text(res)
    assert "联网" in text, "要告诉经纪人「你说城市我当场联网查」"
    assert "来源和日期" in text, "要说明会给来源与日期"
    for term in INTERNAL_TERMS:
        assert term not in text, f"对外文案不该出现内部口径 {term!r}"


def test_both_entries_use_the_same_broker_facing_tone(policy_mod):
    a = _user_text(_call(policy_mod, {"city": "北京"}))
    b = _user_text(json.loads(policy_mod.list_policy_cities()))
    for text in (a, b):
        assert "联网" in text
        for term in INTERNAL_TERMS:
            assert term not in text, f"{term!r} in {text!r}"


def test_note_for_model_gives_the_model_its_rule(policy_mod):
    """给模型的那句话要把"怎么做"说清（对外文案里不再承担这个职责）"""
    for res in (_call(policy_mod, {"city": "北京"}), json.loads(policy_mod.list_policy_cities())):
        note = res.get("note_for_model", "")
        assert "禁止凭记忆" in note, res
        assert "来源" in note and "检索日期" in note, res


# ── ② 城市名回显有边界 ─────────────────────────────────────────────────
def test_city_is_echoed_when_provided(policy_mod):
    res = _call(policy_mod, {"city": "北京"})
    assert "北京" in _text(res)
    assert "「北京」" in _text(res), "城市名应带书名号回显，便于经纪人核对问的是哪个城市"


@pytest.mark.parametrize("messy", [123, True, "", None, "北京" * 60, {"a": 1}])
def test_messy_city_is_not_echoed(policy_mod, messy):
    res = _call(policy_mod, {"city": messy})
    text = _text(res)
    assert res["success"] is False, "乱值也要给同一句答复，不崩"
    assert "Tool execution failed" not in text
    if isinstance(messy, str) and len(messy) > 20:
        assert messy not in text, "超长城市名不许原样回显"
    assert "123" not in text and "True" not in text, "非文本参数不许原样回显进文案"


# ── ③ 政策文件有数据也绝不返回固定数字（核心钉子）──────────────────────
def test_stale_policy_file_never_returns_numbers(policy_mod):
    backup = POLICY_FILE.read_text(encoding="utf-8") if POLICY_FILE.exists() else None
    fake = {"北京": {"首付比例": "首套 35%（回归用例塞的假数据）", "更新日期": "2020-01-01"}}
    try:
        POLICY_FILE.write_text(json.dumps(fake, ensure_ascii=False), encoding="utf-8")
        res = _call(policy_mod, {"city": "北京", "policy_type": "首付比例"})
        text = _text(res)
        assert res["success"] is False, "不预存政策：即使文件里有数据也不许当政策返回"
        assert "假数据" not in text and "35%" not in text
        assert "联网" in _user_text(res)
    finally:
        if backup is None:
            POLICY_FILE.unlink(missing_ok=True)
        else:
            POLICY_FILE.write_text(backup, encoding="utf-8")


# ── ④ 说明书（给模型看的那段）说清能力、且不给经纪人看内部口径 ──────────
def test_schema_descriptions_are_honest_and_model_facing():
    src = (REPO_ROOT / "tools" / "real_estate_policy.py").read_text(encoding="utf-8")
    assert "web_search" not in src, "schema 描述里不许出现内部工具名（模型会转述给经纪人）"
    assert "本工具不预存政策数字" in src, "描述要说清本工具的真实能力"
    assert "可直接回复经纪人" in src, "描述要告诉模型：那句返回就是给经纪人的话"
    # 说明书要交代"不要解释系统机制"（模型照念 description 是这次事故的直接成因）
    assert "不要解释系统机制" in src
