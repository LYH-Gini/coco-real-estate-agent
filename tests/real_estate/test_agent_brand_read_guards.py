"""get_agent_brand 回归（2026-09-27，第十二组第 8 项，F424–F426）

背景（实测证据：`/root/coco-tool-audit/results/raw/t91.log`、`results/get_agent_brand.json`）：
① **有品牌时没有一句中文结论**：裸 `{"success":true,"brand_name":"宇恒房产"}`（名片读工具已有 message）；
② **未配置的文案是"给模型的"**：「品牌未配置，需要先询问经纪人公司名称」——第三人称"经纪人"会被
   模型照抄给经纪人听；
③ **历史超长值无提示**：75 字品牌名原样返回，不说"海报可能显示不全"（名片读工具已有 warnings 口径）。

口径（2026-09-27 老板批准，选 ①）：**未配置改成 `success: true`**（读工具"读到空"不是错误），
新增新键 `configured` 给模型做结构化判断，老键 `brand_name` 保留；文案按"message 给经纪人、
note_for_model 给模型"分开；超长给 warnings。
"""
import json
from pathlib import Path

import pytest


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_settings as m
    monkeypatch.setattr(m, "_get_db", lambda: db)
    return m


def _read(m):
    return json.loads(m.get_agent_brand())


# ── ① 有品牌 ──────────────────────────────────────────────────────────
def test_configured_brand_has_summary(wired):
    wired.save_agent_brand(brand_name="宇恒房产")
    out = _read(wired)
    assert out["success"] is True
    assert out["brand_name"] == "宇恒房产"
    assert out["configured"] is True
    assert out["message"] == "品牌名：宇恒房产（海报品牌栏会显示它）。", out.get("message")
    assert not out.get("note_for_model") and not out.get("warnings")


# ── ② 未配置：success=true + configured=false + 两句分开 ────────────────
def test_unconfigured_is_not_an_error(wired):
    out = _read(wired)
    assert out["success"] is True, "读到空不算错误（与名片读工具同口径）"
    assert out["configured"] is False
    assert out["brand_name"] is None
    assert "还没配置" in out["message"], out.get("message")
    assert "不要编" in out["note_for_model"], out.get("note_for_model")


def test_unconfigured_message_is_speakable(wired):
    """给经纪人的那句不许用第三人称"经纪人"（那是给模型的口径）。"""
    out = _read(wired)
    assert "经纪人" not in out["message"], out["message"]


def test_unconfigured_matches_card_tool_shape(wired):
    card = json.loads(wired.get_agent_card())
    brand = _read(wired)
    assert brand["success"] is True and card["success"] is True
    assert "还没配置" in brand["message"] and "还没配置" in card["message"]


# ── ③ 超长历史值 ──────────────────────────────────────────────────────
def test_overlong_legacy_value_is_kept_but_warned(wired, db):
    long_name = "宇恒房产有限公司海南分公司海口市龙华区国贸大道分店"   # 25 字
    db.set_setting("brand_name", long_name * 3)                        # 75 字
    out = _read(wired)
    assert out["brand_name"] == long_name * 3, "读工具不许悄悄改数据展示（读=写）"
    warn = " ".join(out.get("warnings") or [])
    assert "超过海报栏位" in warn and "30" in warn and "75" in warn, warn


# ── ④ 与名片同源 ──────────────────────────────────────────────────────
def test_same_source_as_card_company(wired):
    wired.save_agent_card(company="名片写的公司")
    out = _read(wired)
    assert out["brand_name"] == "名片写的公司" and out["configured"] is True


def test_read_does_not_write(wired, db):
    before = db.get_setting("brand_name")
    for _ in range(3):
        _read(wired)
    assert db.get_setting("brand_name") == before


# ── ⑤ 描述口径 ────────────────────────────────────────────────────────
def test_description_tells_model_not_to_invent():
    repo_root = Path(__file__).resolve().parents[2]
    src = (repo_root / "tools" / "real_estate_settings.py").read_text(encoding="utf-8")
    idx = src.find('name="get_agent_brand"')
    seg = src[idx:src.find("\nregistry.register(", idx)]
    assert "不要编" in seg, f"描述要写清「没有就如实说、不要编」：{seg[:240]}"
