"""save_agent_brand 回归（2026-09-27，第十二组第 7 项，F420–F423）

背景（实测证据：`/root/coco-tool-audit/results/raw/t90.log`、`results/save_agent_brand.json`）：
① **传布尔直接崩**：`brand_name=true` → `AttributeError: 'bool' object has no attribute 'strip'`；
② **超长不截断**：80 字品牌名原样入库 —— 但**同一个键**在 `save_agent_card(company=…)` 已按 30 字截断，
   两个入口两套规则；
③ **内部连续空格 / 换行原样入库**（`宇恒  房产`、`宇恒房产\n门店`），海报排版与回执都会被带歪；
④ **描述没说它和名片里的公司名是同一个位置**，模型可能把同一件事问两遍。

口径（2026-09-27 老板批准）：文本整理抽成**一个共用函数**，品牌入口与名片的公司名入口都用它 ——
非文本给中文提示、去首尾空白、**折叠内部空白与换行**、按 30 字截断并说明。
"""
import json

import pytest


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_settings as m
    monkeypatch.setattr(m, "_get_db", lambda: db)
    return m


def _save(m, value):
    return json.loads(m.save_agent_brand(brand_name=value))


def _stored(db):
    return db.get_setting("brand_name")


# ── ① 非文本参数不再崩 ────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [True, False])
def test_bool_is_rejected_in_chinese(wired, bad):
    out = _save(wired, bad)
    text = json.dumps(out, ensure_ascii=False)
    assert out["success"] is False, text
    assert "要填文字" in text and "是/否" in text, text[:160]
    assert "TOOL_ERROR" not in text and "Traceback" not in text


def test_empty_value_has_actionable_hint(wired):
    out = _save(wired, "   ")
    text = json.dumps(out, ensure_ascii=False)
    assert out["success"] is False
    assert "品牌名没填" in text and "宇恒房产" in text, text[:160]


# ── ② 超长按海报栏位截断并说明 ────────────────────────────────────────
def test_overlong_is_clipped_with_note(wired, db):
    long_name = "宇恒房产有限公司海南分公司海口市龙华区国贸大道分店一号" * 3   # 78 字
    out = _save(wired, long_name)
    assert out["success"] is True
    assert len(_stored(db)) == 30, f"应按 30 字截断，实际 {len(_stored(db))}"
    msg = out.get("message") or ""
    assert "太长" in msg and "前 30 字" in msg, msg
    assert out.get("brand_name") == _stored(db), "回执里的品牌名要与库里一致"


# ── ③ 写法归一：内部空白与换行 ────────────────────────────────────────
def test_internal_spaces_collapse(wired, db):
    _save(wired, "宇恒  房产")
    assert _stored(db) == "宇恒 房产", _stored(db)


def test_newlines_become_single_space(wired, db):
    out = _save(wired, "宇恒房产\n门店")
    assert _stored(db) == "宇恒房产 门店", _stored(db)
    assert "\n" not in (out.get("message") or ""), "回执里不该出现换行"


def test_trimming_still_works(wired, db):
    _save(wired, "  宇恒房产  ")
    assert _stored(db) == "宇恒房产"


# ── ④ 回执与描述口径 ──────────────────────────────────────────────────
def test_message_wording(wired):
    out = _save(wired, "宇恒房产")
    assert out.get("message") == "品牌名已保存：宇恒房产（海报品牌栏会显示它）。", out.get("message")


def test_description_mentions_same_slot_as_card():
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[2]
    src = (repo_root / "tools" / "real_estate_settings.py").read_text(encoding="utf-8")
    idx = src.find('name="save_agent_brand"')
    seg = src[idx:src.find("\nregistry.register(", idx)]
    assert "同一个位置" in seg, f"描述要说清与名片的公司名同源：{seg[:240]}"


# ── ⑤ 与名片入口同一套规则（同输入 → 同结果）──────────────────────────
@pytest.mark.parametrize("value,expect", [
    ("宇恒房产有限公司海南分公司海口市龙华区国贸大道分店一号", "宇恒房产有限公司海南分公司海口市龙华区国贸大道分店一号"[:30]),
    ("宇恒  房产", "宇恒 房产"),
    ("宇恒房产\n门店", "宇恒房产 门店"),
])
def test_same_rules_as_card_entry(wired, db, value, expect):
    _save(wired, value)
    via_brand = _stored(db)
    wired.save_agent_card(company="旧值")
    wired.save_agent_card(company=value)
    via_card = _stored(db)
    assert via_brand == expect, f"品牌入口：{via_brand!r}"
    assert via_card == expect, f"名片入口：{via_card!r}"


def test_numeric_value_is_textified(wired, db):
    _save(wired, 123)
    assert _stored(db) == "123"
