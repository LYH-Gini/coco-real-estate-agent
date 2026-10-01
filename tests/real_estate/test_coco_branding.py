"""Coco 品牌口径回归（2026-09-23）

守三件事：
  ① 主页频道提示走语言包，中文/英文都是 Coco 品牌、不出现 Hermes；
  ② 17 个语言包都带 coco.home_channel_missing（官方有键集一致性测试，缺一个就红）；
  ③ 提示词里的【品牌口径】规则在位（对外一律称 Coco）。
"""
from pathlib import Path

import pytest
import yaml

from agent.i18n import SUPPORTED_LANGUAGES, t

REPO_ROOT = Path(__file__).resolve().parents[2]
KEY = "coco.home_channel_missing"
PLACEHOLDERS = {"platform": "飞书", "sethome_cmd": "/sethome"}


class TestHomeChannelNotice:
    def test_chinese_notice_uses_coco_brand(self):
        text = t(KEY, lang="zh", **PLACEHOLDERS)
        assert "Coco" in text
        assert "Hermes" not in text
        assert "飞书" in text and "/sethome" in text

    def test_traditional_chinese_notice_uses_coco_brand(self):
        text = t(KEY, lang="zh-hant", **PLACEHOLDERS)
        assert "Coco" in text and "Hermes" not in text

    def test_english_notice_uses_coco_brand(self):
        text = t(KEY, lang="en", **PLACEHOLDERS)
        assert "Coco" in text and "Hermes" not in text

    @pytest.mark.parametrize("lang", list(SUPPORTED_LANGUAGES))
    def test_key_present_in_every_catalog(self, lang):
        """语言包被上游替换后这一步会红——处理办法：跑 scripts/coco_locales_patch.py"""
        raw = (REPO_ROOT / "locales" / f"{lang}.yaml").read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
        assert (data.get("coco") or {}).get("home_channel_missing"), f"{lang}.yaml 缺 {KEY}"

    def test_no_catalog_leaks_hermes_in_this_key(self):
        for lang in SUPPORTED_LANGUAGES:
            text = t(KEY, lang=lang, **PLACEHOLDERS)
            assert "Hermes" not in text, f"{lang} 的这条提示仍带 Hermes"

    def test_run_turn_uses_the_key(self):
        """官方文件里这行改写（漏了就退回官方英文原文，翻译后又会带 Hermes）"""
        src = (REPO_ROOT / "gateway" / "run_turn.py").read_text(encoding="utf-8")
        assert 't("coco.home_channel_missing"' in src
        assert "Hermes delivers" not in src


class TestBrandRuleInPrompt:
    def test_prompt_has_brand_rule(self):
        src = (REPO_ROOT / "agent" / "real_estate_prompt.py").read_text(encoding="utf-8")
        assert "【品牌口径】" in src
        assert "不得出现 Hermes" in src


BRANDED_NOTICE_FILES = {
    "gateway/run_notifications.py": ["Coco update finished", "Coco is back and ready"],
    "gateway/run_busy.py": ["Coco wasn't paused", "Coco is already paused"],
    "hermes_cli/setup_platforms.py": ["主页频道：Coco 把定时任务结果"],
    # 官方 v0.21.5 把 Mattermost 向导从 hermes_cli/gateway.py 搬到 gateway_setup_wizard.py
    "hermes_cli/gateway_setup_wizard.py": ["where Coco delivers"],
}


class TestUserFacingNotices:
    @pytest.mark.parametrize("rel,needles", sorted(BRANDED_NOTICE_FILES.items()))
    def test_notice_strings_are_branded(self, rel, needles):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in src, f"{rel} 里的用户可见文案没有品牌化：{needle!r}"

    def test_update_notice_points_at_coco_command(self):
        src = (REPO_ROOT / "gateway" / "run_notifications.py").read_text(encoding="utf-8")
        # 官方 v0.21.5 把失败提示收成一条常量 _UPDATE_FAILED_NOTICE（措辞也改了）
        assert "`coco update` on the" in src


def _align_module():
    import importlib.util

    path = REPO_ROOT / "scripts" / "coco_config_align.py"
    spec = importlib.util.spec_from_file_location("coco_config_align_t", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestInterfaceLanguage:
    """语言口径：Coco 面向中文经纪人 —— 未设置时按官方默认处理并拉回中文"""

    def test_standard_language_is_chinese(self):
        mod = _align_module()
        assert mod.STANDARD["display.language"] == "zh"
        assert "en" in mod.OFFICIAL_DEFAULTS["display.language"]

    def test_official_default_en_is_pulled_back_to_chinese(self):
        mod = _align_module()
        eff = dict(mod.STANDARD)
        eff["display.language"] = "en"  # 官方向导写回的默认值
        state = {"written": {k: v for k, v in mod.STANDARD.items() if k != "display.language"}}
        items, preserved = mod.plan(eff, state)
        assert ("display.language", "en", "zh") in items
        assert not [k for k, _ in preserved if k == "display.language"]

    def test_operator_chosen_language_is_preserved(self):
        mod = _align_module()
        eff = dict(mod.STANDARD)
        eff["display.language"] = "ja"  # 使用者自己挑的语言
        state = {"written": {k: v for k, v in mod.STANDARD.items() if k != "display.language"}}
        items, preserved = mod.plan(eff, state)
        assert ("display.language", "ja") in preserved
        assert not [k for k, _g, _w in items if k == "display.language"]
