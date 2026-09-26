"""market_brief 回归（2026-09-26 第十组「报表与数据」第 72 项，F353–F358）

背景（实测见 /root/coco-tool-audit/results/raw/t74a.before.log、t74d_market_brief_web.log、t74b.before.log）：
① **联网行情从来没成功过**：它 `from hermes_tools import web_search` —— 那是 execute_code 沙箱里才有的模块，
   工具进程里必然 `ModuleNotFoundError`，于是每次都输出「联网检索暂不可用（No module named 'hermes_tools'）」，
   还把英文报错念给经纪人；正确入口是 `tools.web_tools.web_search_tool`（本机实测能返回真实政策链接）；
② **「本周」全篇没写日期区间**（第一句只有"一、自家盘况（系统数据）"）；
③ 描述写着"**可直接转发朋友圈/客户群**"，正文却是内部盘况 + 「在售房源偏少：联系房东补盘」+「N 位客户流失风险高危」
   —— 转给客户等于把自家盘况和客户情况透出去；
④ **空库也报"在售房源偏少：联系房东补盘"**（库里一套房都没有）；
⑤ `district` 参数说明只有"区域（可选）"；⑥ 带看转化率的分母口径一字未说。

本文件钉住修后的行为：真联网（mock 掉检索入口，失败/无结果都如实说、不编）、区间写明、
内部用标注、空库分开说、参数说明与口径写清。
"""
import json
import re
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_analytics as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


def _brief(**kwargs):
    import tools.real_estate_analytics as m

    return json.loads(m.market_brief(**kwargs))


def _property(db, title="简报房源 1号楼101", i=0, created_days_ago=0):
    pid = db.add_property(title=title, price=1_500_000, area=90.0, property_type="second_hand",
                          status="available")["id"]
    if created_days_ago:
        from sqlalchemy import text

        with db.get_session() as s:
            s.execute(text("update re_properties set created_at = :ts where id = :i"),
                      {"ts": datetime.now() - timedelta(days=created_days_ago), "i": pid})
            s.commit()
    return pid


def _fake_search(monkeypatch, payload=None, boom=False):
    """替掉真检索入口（本文件不联网）：payload 为 None 表示"检索没返回结果" """
    import tools.web_tools as wt

    def _fake(query, limit=5):
        if boom:
            raise RuntimeError("provider down")
        if payload is None:
            return json.dumps({"success": True, "data": {"web": []}}, ensure_ascii=False)
        return json.dumps({"success": True, "data": {"web": payload}}, ensure_ascii=False)

    monkeypatch.setattr(wt, "web_search_tool", _fake)


# ==================== ① 联网真的走检索入口（F353） ====================

class TestWebSearch:
    def test_uses_in_process_search_entry(self, wired, monkeypatch):
        """曾经 import 沙箱专用模块 → 现在必须真调到 tools.web_tools.web_search_tool"""
        calls = []

        def _fake(query, limit=5):
            calls.append((query, limit))
            return json.dumps({"success": True, "data": {"web": [
                {"title": "某市优化房地产政策", "url": "https://example.gov.cn/a"}]}}, ensure_ascii=False)

        import tools.web_tools as wt

        monkeypatch.setattr(wt, "web_search_tool", _fake)
        out = _brief(city="海口")
        assert calls, "没有调用检索入口"
        assert calls[0][0].startswith("海口 楼市"), calls
        assert "某市优化房地产政策" in out["message"], out["message"]
        assert "https://example.gov.cn/a" in out["message"], out["message"]

    def test_source_and_search_date_are_labeled(self, wired, monkeypatch):
        _fake_search(monkeypatch, [{"title": "某市房价动态", "url": "https://example.com/x"}])
        msg = _brief(city="海口", district="美兰")["message"]
        assert f"检索日期 {datetime.now().strftime('%m-%d')}" in msg, msg
        assert "海口 楼市 最新政策 房价 美兰" in msg, msg

    def test_empty_result_is_not_fabricated(self, wired, monkeypatch):
        _fake_search(monkeypatch, None)
        msg = _brief(city="海口")["message"]
        assert "这次没查到相关动态" in msg, msg
        assert "· 无结果" not in msg, msg

    def test_search_failure_is_readable_and_has_no_internal_error(self, wired, monkeypatch):
        _fake_search(monkeypatch, boom=True)
        msg = _brief(city="海口")["message"]
        assert "这次没查到相关动态" in msg, msg
        for leak in ("ModuleNotFoundError", "hermes_tools", "provider down", "Traceback"):
            assert leak not in msg, (leak, msg)

    def test_web_import_failure_is_not_fatal(self, wired, monkeypatch):
        """整条检索链路不可用也不许崩、不许编"""
        import builtins

        real_import = builtins.__import__

        def _no_web(name, *a, **kw):
            if name == "tools.web_tools":
                raise ImportError("no web tool")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _no_web)
        out = _brief(city="海口")
        assert out["success"] is True, out
        assert "这次没查到相关动态" in out["message"], out["message"]


# ==================== ② 区间与口径（F354） ====================

class TestScope:
    def test_period_is_written_out(self, wired):
        out = _brief()
        msg = out["message"]
        assert re.search(r"近 7 天：\d{2}-\d{2} ~ \d{2}-\d{2}", msg), msg
        assert re.search(r"近 7 天（\d{2}-\d{2} ~ \d{2}-\d{2}）", out["统计区间"]), out["统计区间"]

    def test_conversion_rate_uses_done_viewings_only(self, wired):
        """分母是本周**已完成**的带看（F360）：3 场已看 + 2 场未来预约 + 1 场已取消 + 1 张新单
        → 率按 1/3 算；括号里同时给"本周带看 6 场、已完成 3 场"便于对账"""
        cid = wired.add_customer(name="简报客户", phone="13700009001", tier="A",
                                customer_type="buy_second_hand")["id"]
        pids = [_property(wired, title=f"简报房源{i} 1号楼101", i=i) for i in range(6)]
        for i in range(3):          # 已完成（前 1-3 天）
            wired.add_viewing(customer_id=cid, property_id=pids[i],
                              viewing_time=datetime.now() - timedelta(days=i + 1),
                              status="done", result="interested")
        for i in range(3, 5):       # 未来预约（不会算进分母）
            wired.add_viewing(customer_id=cid, property_id=pids[i],
                              viewing_time=datetime.now() + timedelta(days=i - 2),
                              status="scheduled")
        wired.add_viewing(customer_id=cid, property_id=pids[5],   # 已取消（也不算）
                          viewing_time=datetime.now() - timedelta(days=2), status="cancelled")
        wired.add_deal(customer_id=cid, property_id=pids[0], price=1_500_000)
        msg = _brief()["message"]
        assert "本周带看: 6 场（已完成 3 场）" in msg, msg
        assert "带看转化率 33% = 本周新开成交单 ÷ 本周已完成带看" in msg, msg
        assert "17%" not in msg, msg      # 6 场全算会得到 17%（旧口径）

    def test_no_done_viewing_says_no_data(self, wired):
        """本周只有未来预约 / 已取消时，不许报一个"分母里没有真看过的"转化率（F360）"""
        cid = wired.add_customer(name="简报客户2", phone="13700009002", tier="A",
                                customer_type="buy_second_hand")["id"]
        pids = [_property(wired, title=f"简报房源{i} 2号楼202", i=10 + i) for i in range(2)]
        wired.add_viewing(customer_id=cid, property_id=pids[0],
                          viewing_time=datetime.now() + timedelta(days=1), status="scheduled")
        wired.add_viewing(customer_id=cid, property_id=pids[1],
                          viewing_time=datetime.now() - timedelta(days=1), status="cancelled")
        wired.add_deal(customer_id=cid, property_id=pids[0], price=1_500_000)
        msg = _brief()["message"]
        assert "本周带看: 2 场（已完成 0 场）" in msg, msg
        assert "带看转化率暂无数据" in msg and "本周还没有已完成的带看" in msg, msg
        assert "%" not in [ln for ln in msg.splitlines() if "带看转化率" in ln][0], msg

    def test_rate_over_100_is_explained(self, wired):
        """成交来自更早的带看时率会超过 100%，要解释一句，别让人以为报错了（F360）"""
        cid = wired.add_customer(name="简报客户3", phone="13700009003", tier="A",
                                customer_type="buy_second_hand")["id"]
        pids = [_property(wired, title=f"简报房源{i} 3号楼303", i=20 + i) for i in range(3)]
        wired.add_viewing(customer_id=cid, property_id=pids[0],
                          viewing_time=datetime.now() - timedelta(days=1), status="done")
        for i in range(2):
            wired.add_deal(customer_id=cid, property_id=pids[i], price=1_500_000 + i)
        msg = _brief()["message"]
        assert "带看转化率 200%" in msg, msg
        assert "超过 100%：说明有成交来自更早的带看" in msg, msg

    def test_no_city_skips_web_with_chinese_note(self, wired):
        msg = _brief()["message"]
        assert "未指定城市，这次跳过联网行情" in msg, msg


# ==================== ③ 内部用标注（F355） ====================

def test_described_as_internal_not_forwardable(wired):
    from tools.registry import registry

    desc = registry.get_entry("market_brief").schema["description"]
    assert "内部用" in desc and "别直接转发给客户" in desc, desc
    assert "可直接转发" not in desc, desc
    msg = _brief()["message"]
    assert "三、本周行动建议（内部参考，别直接转发给客户）" in msg, msg


def test_params_are_described(wired):
    from tools.registry import registry

    props = registry.get_entry("market_brief").schema["parameters"]["properties"]
    assert "城市名" in props["city"]["description"], props
    assert len(props["district"]["description"]) > 6, props


# ==================== ④ 空态与本期为空（F356–F358） ====================

class TestEmpty:
    def test_empty_library_does_not_advise_restocking(self, wired):
        out = _brief()
        assert out["success"] is True
        msg = out["message"]
        assert "库里还没有客户和房源" in msg, msg
        assert "联系房东补盘" not in msg, msg
        assert "在售房源偏少" not in msg, msg

    def test_has_data_but_empty_period_says_so(self, wired):
        _property(wired, title="半年前的房源", created_days_ago=60)
        msg = _brief()["message"]
        assert "没有新增房源、带看与成交" in msg, msg
        # 库里确实有房 → 这时"在售偏少、联系房东补盘"是合理的
        assert "联系房东补盘" in msg, msg

    def test_stats_failure_is_labeled(self, wired, monkeypatch):
        import agent.real_estate_db as redb

        class _Boom:
            def __ge__(self, other):
                raise RuntimeError("模拟统计失败")

        class _FakeViewing:
            viewing_time = _Boom()

        monkeypatch.setattr(redb, "Viewing", _FakeViewing)
        out = _brief()
        assert out["success"] is True and "warning_stats" in out, out
        assert "统计失败" in out["message"], out["message"]

    def test_not_writing_to_db(self, wired):
        _property(wired)
        before = (wired.get_stats()["total_properties"], wired.count_customers())
        _brief()
        _brief(city="海口")
        after = (wired.get_stats()["total_properties"], wired.count_customers())
        assert before == after
