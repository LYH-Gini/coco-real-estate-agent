"""price_history 的数字口径与边界（2026-09-24）

F23：次数与"累计变动"必须按全部调价统计，不能只数返回的最近 N 条
     （实测库里 25 次调价时报"共 20 次、+20.0万"，真实是 25 次、+25.0万）。
F24：房源不存在要与"没调过价"区分开，否则模型会对不存在的房源说"这套房没调过价"。
F25：limit 传 0/负数一律按默认，不再出现 limit=0 时谎报"暂无调价记录"、负数时全量拉取。
"""
import json

import pytest
from conftest import make_property  # noqa: F401

from tools.real_estate_property import price_history, update_property


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
    return db


def _ph(pid, **kw):
    return json.loads(price_history(property_id=pid, **kw))


def test_counts_cover_all_changes_not_just_returned_page(tool_db):
    p = make_property(tool_db, title="调价小区 1号楼101", price=2_000_000.0, area=100.0)
    for i in range(1, 26):
        update_property(property_id=p["id"], price=2_000_000 + i * 10_000)

    r = _ph(p["id"])                                  # 默认 limit=20
    assert r["total_changes"] == 25, r
    assert "共 25 次" in r["message"], r["message"]
    assert "+25.0万" in r["message"], r["message"]
    assert len(r["history"]) == 20, "明细仍按 limit 返回"
    assert "最近 20 次" in r["message"], "被截断要说明"


def test_missing_property_is_not_found_not_no_history(tool_db):
    r = _ph(999999)
    assert r["success"] is False and r.get("not_found") is True
    assert "没有编号为" in r["error"]


def test_no_history_message(tool_db):
    p = make_property(tool_db, title="调价小区 2号楼201", price=2_000_000.0, area=100.0)
    r = _ph(p["id"])
    assert r["success"] is True and r["history"] == [] and "暂无调价" in r["message"]


@pytest.mark.parametrize("bad_limit", [0, -3, "abc", None])
def test_bad_limit_falls_back_to_default(tool_db, bad_limit):
    p = make_property(tool_db, title="调价小区 3号楼301", price=2_000_000.0, area=100.0)
    for i in range(1, 6):
        update_property(property_id=p["id"], price=2_000_000 + i * 10_000)
    r = _ph(p["id"], limit=bad_limit)
    assert len(r["history"]) == 5, r              # 只有 5 条，按默认 20 取就是 5 条（且不该谎报"暂无"）
    assert "暂无" not in r["message"]
