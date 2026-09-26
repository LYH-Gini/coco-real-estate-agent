"""政策查询工具回归（2026-09-27，第十二组第 1 项，F401–F403）

背景（实测证据：`/root/coco-tool-audit/results/raw/t84.log`、`t83a.family_probe_misc.log`）：
① **对外文案里出现内部工具名**：`get_loan_policy` 每次返回都带「请用 web_search 查询…」——
   给经纪人看的话里不许出现内部工具名，应说「跟我说一声，我联网查给你」；
② **两个政策工具口径自相矛盾**：`get_loan_policy` 如实说"本地库已停用"，而
   `list_policy_cities` 在同样状态下返回 `{"success": true, "cities": [], "count": 0}` ——
   既不说明停用，还让模型以为"收录了 0 个城市"；
③ **本地库已停用、但读库分支还活着**：实测往 `skills/real_estate/references/policies.json`
   塞一条假政策，工具立刻把它当政策返回（含"仅供参考"脚注）—— 政策时效性强，
   这条路必须封死，否则哪天有人填了文件就会悄悄开始给可能过时的数字。

本文件钉住修好后的行为：停用口径统一、文案不带内部工具名、城市名回显有边界、
**即使政策文件有数据也绝不返回固定政策数字**。
"""
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_FILE = REPO_ROOT / "skills" / "real_estate" / "references" / "policies.json"
INTERNAL_TERMS = ("web_search", "hermes ", "DATABASE_URL", ".env.db", "sqlite", "Traceback")


@pytest.fixture
def policy_mod():
    import tools.real_estate_policy as m
    return m


def _call(mod, args):
    return json.loads(mod.get_loan_policy(**args))


def _text(res):
    return json.dumps(res, ensure_ascii=False)


# ── ① 停用文案：不含内部工具名 ───────────────────────────────────────────
def test_stopped_message_has_no_internal_tool_name(policy_mod):
    res = _call(policy_mod, {})
    assert res["success"] is False
    assert "停用" in _text(res), "应如实说明本地政策库已停用"
    for term in INTERNAL_TERMS:
        assert term not in _text(res), f"对外文案不该出现内部术语 {term!r}"


def test_stopped_message_tells_broker_what_to_say(policy_mod):
    """给经纪人的话要说清"跟我说一声我联网查"，而不是丢一个工具名。"""
    res = _call(policy_mod, {"city": "北京"})
    text = _text(res)
    assert res["success"] is False
    assert "联网" in text, "应告诉经纪人可以联网查"


# ── ② 城市名回显有边界 ─────────────────────────────────────────────────
def test_city_is_echoed_when_provided(policy_mod):
    res = _call(policy_mod, {"city": "北京"})
    assert "北京" in _text(res)
    assert "「北京」" in _text(res), "城市名应带书名号回显，便于经纪人核对问的是哪个城市"


@pytest.mark.parametrize("messy", [123, True, "", None, "北京" * 60, {"a": 1}])
def test_messy_city_is_not_echoed(policy_mod, messy):
    res = _call(policy_mod, {"city": messy})
    text = _text(res)
    assert res["success"] is False, "乱值也要给停用结论，不崩"
    assert "Tool execution failed" not in text
    if isinstance(messy, str) and len(messy) > 20:
        assert messy not in text, "超长城市名不许原样回显"
    assert "123" not in text and "True" not in text, "非文本参数不许原样回显进文案"


# ── ③ 政策文件有数据也绝不返回固定数字（本轮的核心钉子）─────────────────
def test_stale_policy_file_never_returns_numbers(policy_mod):
    backup = POLICY_FILE.read_text(encoding="utf-8") if POLICY_FILE.exists() else None
    fake = {"北京": {"首付比例": "首套 35%（回归用例塞的假数据）", "更新日期": "2020-01-01"}}
    try:
        POLICY_FILE.write_text(json.dumps(fake, ensure_ascii=False), encoding="utf-8")
        res = _call(policy_mod, {"city": "北京", "policy_type": "首付比例"})
        text = _text(res)
        assert res["success"] is False, "本地库已停用：即使文件里有数据也不许当政策返回"
        assert "假数据" not in text and "35%" not in text
        assert "停用" in text
    finally:
        if backup is None:
            POLICY_FILE.unlink(missing_ok=True)
        else:
            POLICY_FILE.write_text(backup, encoding="utf-8")


# ── ④ 两个入口口径一致 ─────────────────────────────────────────────────
def test_list_policy_cities_says_stopped(policy_mod):
    res = json.loads(policy_mod.list_policy_cities())
    text = _text(res)
    assert res["success"] is False, "停用时不该报 success=true + 空城市列表"
    assert "停用" in text
    for term in INTERNAL_TERMS:
        assert term not in text


# ── ⑤ 描述（给模型看的那段）也不带内部工具名 ────────────────────────────
def test_schema_description_has_no_internal_tool_name():
    src = (REPO_ROOT / "tools" / "real_estate_policy.py").read_text(encoding="utf-8")
    assert "web_search" not in src, "schema 描述里不许出现内部工具名（模型会转述给经纪人）"
    assert "本地政策库已停用" in src, "描述要说清本工具的真实能力"
