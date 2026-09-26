"""市场行情简报测试（功能10，批次七）——联网段 mock，不依赖外网

（原本还测了 weekly_market_report：它的"本周重点"是写死的空话、已在 2026-09-23 删除，
周报改由定时任务的 scripts/coco_cron_weekly.py 用真实统计生成。）
"""
import json
from datetime import datetime, timedelta

from conftest import make_customer, make_property

from tools.real_estate_analytics import market_brief


class TestMarketBrief:
    def test_own_stats_section(self, db, monkeypatch):
        monkeypatch.setattr("tools.real_estate_analytics._get_db", lambda: db)
        make_property(db)
        make_property(db, title="第二套")
        r = json.loads(market_brief())  # 不传 city → 跳过联网
        assert r["success"] is True
        assert r["stats"]["new_listings"] == 2
        assert "自家盘况" in r["message"]
        assert "联网检索" in r["message"]  # 声明来源（2026-09-26 改：标注来源与检索日期）

    def test_no_city_skips_web(self, db, monkeypatch):
        monkeypatch.setattr("tools.real_estate_analytics._get_db", lambda: db)
        r = json.loads(market_brief())
        assert "未指定城市" in r["message"]

    def test_web_failure_not_blocking(self, db, monkeypatch):
        """联网失败不阻塞简报生成"""
        monkeypatch.setattr("tools.real_estate_analytics._get_db", lambda: db)
        import builtins
        r = json.loads(market_brief(city="海口"))
        assert r["success"] is True
        assert "自家盘况" in r["message"]

    def test_advice_low_inventory(self, db, monkeypatch):
        """在售<10 套 → 建议补盘"""
        monkeypatch.setattr("tools.real_estate_analytics._get_db", lambda: db)
        make_property(db)
        r = json.loads(market_brief())
        assert "在售房源偏少" in r["message"]

    def test_advice_healthy(self, db, monkeypatch):
        """无异常 → 节奏健康（在售≥10 套才不提示补盘）"""
        monkeypatch.setattr("tools.real_estate_analytics._get_db", lambda: db)
        for i in range(10):
            make_property(db, title=f"在售房{i}", price=2_000_000 + i * 100_000, area=90.0)
        r = json.loads(market_brief())
        assert "节奏健康" in r["message"]
