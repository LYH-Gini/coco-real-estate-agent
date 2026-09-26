"""看房方式（viewing_note）终于能录进去了（2026-09-25，F104）

原先这个字段**只读不可写**：`get_property_detail`、`get_property_owners`、`owner_portfolio`
三个工具都在展示"看房方式"，而 `add_property`/`update_property` 都不接受该参数 ——
经纪人永远录不进"钥匙在门店""需提前一天预约"这类信息，展示位永远是空的。
本文件钉住：录得进、改得动、三个读路径都看得到、schema 与模板都跟上了（契约 13/14）。
"""
import json

import pytest

from tools.real_estate_property import add_property, get_property_detail, get_property_form, update_property


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
    monkeypatch.setattr("tools.real_estate_owner._get_db", lambda: db)
    return db


def _add(db, **kw):
    args = dict(title="看房方式测试房", price=1_500_000, area=80.0,
                property_type="second_hand", force=True)
    args.update(kw)
    return json.loads(add_property(**args))


def test_add_property_records_viewing_note(tool_db):
    r = _add(tool_db, viewing_note="钥匙在门店")
    assert r["success"] is True, r
    assert r["property"]["viewing_note"] == "钥匙在门店", r["property"]


def test_update_property_can_change_viewing_note(tool_db):
    pid = _add(tool_db, viewing_note="钥匙在门店")["property"]["id"]
    r = json.loads(update_property(property_id=pid, viewing_note="需提前一天预约"))
    assert r["success"] is True, r
    assert tool_db.get_property(pid)["viewing_note"] == "需提前一天预约"


def test_three_read_paths_show_viewing_note(tool_db):
    """三个展示位都要看得到 —— 这正是它原先"展示位永远为空"的根因"""
    from tools.real_estate_owner import get_property_owners, owner_portfolio
    r = _add(tool_db, viewing_note="钥匙在门店", owner_name="看房方式业主",
             owner_phone="13800005555")
    pid = r["property"]["id"]
    oid = tool_db.list_owners()[0]["id"]

    detail = json.loads(get_property_detail(property_id=pid))
    assert "钥匙在门店" in json.dumps(detail, ensure_ascii=False), detail.get("message")

    reverse = json.loads(get_property_owners(property_ids=[pid]))
    assert "钥匙在门店" in reverse["message"], reverse["message"]

    portfolio = json.loads(owner_portfolio(owner_id=oid))
    assert "钥匙在门店" in portfolio["message"], portfolio["message"]


def test_schema_declares_viewing_note_for_both_write_tools():
    """契约 13：新增参数必须同时进 schema（否则模型看不到、永远不传）"""
    from tools.registry import registry
    for tool in ("add_property", "update_property"):
        props = registry.get_entry(tool).schema["parameters"]["properties"]
        assert "viewing_note" in props, tool
        assert "看房方式" in props["viewing_note"]["description"], tool


def test_form_template_has_viewing_note_row(tool_db):
    """契约 14：模板栏位要与录入工具的 schema 对齐"""
    form = json.loads(get_property_form())["form"]
    assert "看房方式" in form, form


def test_blank_viewing_note_is_not_stored(tool_db):
    """空字符串按"未填"处理（与其它文本字段同一口径）"""
    pid = _add(tool_db)["property"]["id"]
    assert tool_db.get_property(pid)["viewing_note"] is None
