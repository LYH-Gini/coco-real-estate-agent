"""海报标识行、改价同步标题、单价舍入、指代不清先问（2026-09-29）

实测三处：
① 海报上那行小字印的是整条标题（含旧价），太长还被截断成"…1602 1…"；
② 改价后标题里仍写着旧价，Coco 只回一句"要改标题我可以一并处理"，把活留给了经纪人；
③ 185万÷128㎡ 显示 14453.12（Python 的 round 是银行家舍入），对账的人按四舍五入看是 14453.13。
另外钉住"问房号只给中文说法"（把 full/unit/none 念给经纪人那次的复发防线）。
"""
import json
import shutil
from pathlib import Path

import pytest

from agent.real_estate_db import init_real_estate_db

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT = (REPO_ROOT / "agent" / "real_estate_prompt.py").read_text(encoding="utf-8")
MANUAL = (REPO_ROOT / "skills" / "real_estate" / "SKILL.md").read_text(encoding="utf-8")

needs_rsvg = pytest.mark.skipif(shutil.which("rsvg-convert") is None,
                                reason="未安装 rsvg-convert（librsvg2-bin）")

TITLE_WITH_PRICE = "海口美兰区桂林洋海阔天空 7号楼2单元1602 128平 3室2厅 185万"


def _registry(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/line_price.db"
    monkeypatch.setenv("DATABASE_URL", url)
    init_real_estate_db(url)
    import model_tools  # noqa: F401 —— 触发工具发现与注册

    from tools.registry import registry

    return registry


def _add(registry, **payload):
    payload.setdefault("area", 128.0)
    out = registry.get_entry("add_property").handler(payload, session_id="agent:main:feishu:dm:oc_x")
    return json.loads(out)


def _update(registry, payload):
    out = registry.get_entry("update_property").handler(payload, session_id="agent:main:feishu:dm:oc_x")
    return json.loads(out)


class TestIdentityLine:
    """海报上那行标识 = 小区 + 楼栋/单元/房号"""

    def test_community_plus_room(self):
        from tools.real_estate_poster_svg import _identity_line

        p = {"title": TITLE_WITH_PRICE, "community": "海阔天空", "address": "7号楼2单元1602"}
        assert _identity_line(p) == "海阔天空 7号楼2单元1602"

    def test_without_room_falls_back_to_community(self):
        from tools.real_estate_poster_svg import _identity_line

        assert _identity_line({"title": "望京花园三居 精装", "community": "望京花园"}) == "望京花园"


class TestTitlePriceSync:
    def test_price_change_rewrites_title(self, tmp_path, monkeypatch):
        registry = _registry(tmp_path, monkeypatch)
        pid = _add(registry, title=TITLE_WITH_PRICE, price=1850000)["property"]["id"]
        updated = _update(registry, {"property_id": pid, "price": 1780000})
        assert "178万" in updated["property"]["title"]
        assert "185万" not in updated["property"]["title"]

    def test_receipt_says_title_was_changed(self, tmp_path, monkeypatch):
        registry = _registry(tmp_path, monkeypatch)
        pid = _add(registry, title=TITLE_WITH_PRICE, price=1850000)["property"]["id"]
        updated = _update(registry, {"property_id": pid, "price": 1780000})
        assert "178万" in updated["title_price_synced"]

    def test_title_without_price_is_untouched(self, tmp_path, monkeypatch):
        registry = _registry(tmp_path, monkeypatch)
        pid = _add(registry, title="望京花园 10号楼1006", price=2900000)["property"]["id"]
        updated = _update(registry, {"property_id": pid, "price": 2850000})
        assert updated["property"]["title"] == "望京花园 10号楼1006"
        assert "title_price_synced" not in updated

    def test_explicit_title_from_agent_wins(self, tmp_path, monkeypatch):
        """经纪人自己给了标题就不要自作主张改他的话"""
        registry = _registry(tmp_path, monkeypatch)
        pid = _add(registry, title=TITLE_WITH_PRICE, price=1850000)["property"]["id"]
        updated = _update(registry, {"property_id": pid, "price": 1780000,
                                     "title": "海阔天空 1602 急售 185万"})
        assert updated["property"]["title"] == "海阔天空 1602 急售 185万"


class TestUnitPriceRounding:
    def test_rounds_half_up(self, db):
        p = db.add_property(title="海阔天空 1602", price=1850000, area=128.0)
        assert p["unit_price"] == 14453.13, p["unit_price"]

    def test_exact_value_unchanged(self, db):
        p = db.add_property(title="望京花园 1006", price=1780000, area=128.0)
        assert p["unit_price"] == 13906.25


class TestRoomNoAskWording:
    def test_grid_error_text_is_chinese(self, tmp_path, monkeypatch):
        registry = _registry(tmp_path, monkeypatch)
        out = json.loads(registry.get_entry("generate_poster_grid").handler(
            {"property_ids": "1", "show_room_no": "随便写"}, session_id="agent:main:feishu:dm:oc_x"))
        assert out["success"] is False
        for internal in ("full", "unit", "none"):
            assert internal not in out["error"], out["error"]

    def test_prompt_and_manual_ask_in_chinese(self):
        assert "`full` 写完整" not in PROMPT
        assert "别把内部值念给他" in PROMPT
        assert "full 完整 / unit" not in MANUAL


class TestPronounGuard:
    """指代不清（"第几条/那套/再发一次"）时先问一句，不猜着执行"""

    def test_prompt_has_the_rule(self):
        assert "指代不清先问一句" in PROMPT
        assert "不要挑一个猜着执行" in PROMPT

    def test_manual_has_the_rule(self):
        assert "指代不清先问一句" in MANUAL
