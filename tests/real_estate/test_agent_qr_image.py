"""经纪人自己发来的微信二维码图（2026-09-29）

实测：经纪人从没发过二维码，海报上却出现一个——拿名片里的微信号现生成的，扫出来只是一串
文字、微信里加不上好友。现在的口径：只印经纪人**真发来的那张图**，没发过就不显示那一块。
"""
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

import tools.real_estate_poster as poster
import tools.real_estate_settings as settings

needs_rsvg = pytest.mark.skipif(shutil.which("rsvg-convert") is None,
                                reason="未安装 rsvg-convert（librsvg2-bin）")


@pytest.fixture
def wired(db, tmp_path, monkeypatch):
    monkeypatch.setattr(poster, "_get_db", lambda: db)
    monkeypatch.setattr(settings, "_get_db", lambda: db)
    monkeypatch.setattr(poster, "_poster_dir", lambda: str(tmp_path))
    return db


def _qr_png(tmp_path):
    path = tmp_path / "my_wx_qr.png"
    Image.new("RGB", (240, 240), (0, 0, 0)).save(path)
    return str(path)


class TestSaveQrImage:
    def test_saved_and_kept_long_term(self, wired, tmp_path):
        res = json.loads(settings.save_agent_card(name="李经理", qr_image_path=_qr_png(tmp_path)))
        assert res["success"] is True
        assert res["saved"]["qr_image"]
        assert "二维码已存好" in res["message"]
        kept = settings.get_agent_qr_path()
        assert kept and Path(kept).exists()

    def test_missing_file_told_plainly(self, wired):
        res = json.loads(settings.save_agent_card(qr_image_path="/tmp/not-there-xyz.png"))
        assert res["success"] is False
        assert "重新发一次" in res["error"]

    def test_card_reports_whether_qr_is_configured(self, wired, tmp_path):
        assert json.loads(settings.get_agent_card())["qr_image"] is False
        settings.save_agent_card(qr_image_path=_qr_png(tmp_path))
        assert json.loads(settings.get_agent_card())["qr_image"] is True

    def test_only_qr_is_enough_to_save(self, wired, tmp_path):
        res = json.loads(settings.save_agent_card(qr_image_path=_qr_png(tmp_path)))
        assert res["success"] is True, res


class TestPosterUsesTheUploadedImage:
    def _prop(self, db):
        return db.add_property(title="海阔天空 7号楼2单元1602", community="海阔天空",
                               price=1780000, area=128.0, rooms=3, halls=2,
                               property_type="second_hand", status="available")

    @needs_rsvg
    def test_poster_prints_it(self, wired, tmp_path):
        p = self._prop(wired)
        settings.save_agent_card(name="李经理", phone="13006050090", company="浩宇房产",
                                 qr_image_path=_qr_png(tmp_path))
        res = json.loads(poster.generate_property_poster(property_id=p["id"], template="A",
                                                         show_room_no="full", poster_title="好房"))
        assert res["success"] is True
        svg = Path(res["poster_path"] + ".svg").read_text(encoding="utf-8")
        assert "扫码加我微信" in svg

    @needs_rsvg
    def test_legacy_engine_also_prints_it(self, wired, tmp_path):
        """回落引擎（Pillow）也认这张图，不是只改了 SVG 那条路"""
        p = self._prop(wired)
        settings.save_agent_card(name="李经理", phone="13006050090", company="浩宇房产",
                                 qr_image_path=_qr_png(tmp_path))
        out = poster._render_legacy({**p, "title": "海阔天空 1602"}, settings.get_agent_qr_path(), "modern")
        assert Path(out).exists()
