"""数据清理工具层测试（2026-09-23）：注册、缺参数、预演/确认、拒删保护

工具通过 get_real_estate_db() 取库实例，这里把它指到测试临时库；
调用走 registry 的 handler（带 session_id/task_id 注入），与网关同一形态。
"""
import json
from datetime import datetime

import pytest

from conftest import make_customer, make_property

import agent.real_estate_db as dbmod


@pytest.fixture
def purge_tools(db, monkeypatch):
    monkeypatch.setattr(dbmod, "_db_instance", db)
    import tools.real_estate_purge as mod  # noqa: F401 —— import 时注册工具
    return mod, db


def _call(name, args):
    from tools.registry import registry
    entry = registry.get_entry(name)
    assert entry is not None, f"{name} 没注册（模型看不到）"
    return json.loads(entry.handler(args, session_id="test:session", task_id="t"))


class TestRegistration:
    def test_three_tools_registered(self, purge_tools):
        from tools.registry import registry
        for name in ("delete_property", "delete_customer", "purge_data"):
            assert registry.get_entry(name) is not None, f"{name} 未注册"

    def test_schema_exposes_dry_run_and_force(self, purge_tools):
        from tools.registry import registry
        props = registry.get_schema("delete_property")["parameters"]["properties"]
        assert "force" in props and "dry_run" in props
        purge_props = registry.get_schema("purge_data")["parameters"]["properties"]
        assert purge_props["mode"]["enum"] == ["delete", "archive"]

    def test_missing_identifier_reports_error_not_crash(self, purge_tools):
        result = _call("delete_property", {})
        assert result["success"] is False
        assert "房源" in result["error"]
        assert _call("delete_customer", {})["success"] is False


class TestDeletePropertyTool:
    def test_preview_then_real_delete(self, purge_tools):
        _, db = purge_tools
        p = make_property(db, title="待删房源", status="sold")
        preview = _call("delete_property", {"property_id": p["id"], "dry_run": True})
        assert preview["success"] is True and preview["dry_run"] is True
        assert db.get_property(p["id"]) is not None
        done = _call("delete_property", {"property_id": p["id"]})
        assert done["success"] is True and done["dry_run"] is False
        assert db.get_property(p["id"]) is None

    def test_refuses_then_force_deletes(self, purge_tools):
        _, db = purge_tools
        p = make_property(db, title="带带看的已售房", status="sold")
        c = make_customer(db, name="看过房的客户")
        db.add_viewing(customer_id=c["id"], property_id=p["id"], viewing_time=datetime.now())
        denied = _call("delete_property", {"property_id": p["id"]})
        assert denied["success"] is False and denied["error"] == "has_history"
        assert db.get_property(p["id"]) is not None
        forced = _call("delete_property", {"property_id": p["id"], "force": True})
        assert forced["success"] is True
        assert db.get_property(p["id"]) is None


class TestDeleteCustomerTool:
    def test_ambiguous_name_returns_candidates(self, purge_tools):
        _, db = purge_tools
        make_customer(db, name="同名客户", phone="13800000001", status="closed")
        make_customer(db, name="同名客户", phone="13800000002", status="closed")
        result = _call("delete_customer", {"name": "同名客户"})
        assert result["success"] is False and result["error"] == "ambiguous"
        assert len(result["candidates"]) == 2

    def test_delete_by_name_and_phone(self, purge_tools):
        _, db = purge_tools
        make_customer(db, name="同名客户", phone="13800000001", status="closed")
        target = make_customer(db, name="同名客户", phone="13800000002", status="closed")
        result = _call("delete_customer", {"name": "同名客户", "phone": "13800000002"})
        assert result["success"] is True
        assert db.get_customer(target["id"]) is None


class TestPurgeDataTool:
    def test_defaults_to_preview_and_changes_nothing(self, purge_tools):
        _, db = purge_tools
        make_customer(db, name="关闭客户", status="closed")
        result = _call("purge_data", {})
        assert result["success"] is True and result["dry_run"] is True
        assert result["deleted"] == 1
        assert len(db.list_customers(include_closed=True)) == 1

    def test_rejects_unknown_kind(self, purge_tools):
        result = _call("purge_data", {"kind": "everything"})
        assert result["success"] is False
        assert "property" in result["error"]

    def test_executes_and_reports_skipped(self, purge_tools):
        _, db = purge_tools
        clean = make_customer(db, name="干净的关闭客户", status="closed")
        protected = make_customer(db, name="有跟进的关闭客户", status="closed")
        db.add_followup(customer_id=protected["id"], type="phone", content="回访")
        result = _call("purge_data", {"kind": "customer", "dry_run": False})
        assert result["deleted"] == 1 and result["skipped_count"] == 1
        assert db.get_customer(clean["id"]) is None
        assert db.get_customer(protected["id"]) is not None
        assert "跳过 1 条" in result["message"]

    def test_archive_mode_marks_status_only(self, purge_tools):
        _, db = purge_tools
        c = make_customer(db, name="活跃客户", status="active")
        result = _call("purge_data", {"kind": "customer", "statuses": ["active"],
                                      "mode": "archive", "dry_run": False})
        assert result["success"] is True and result["archived"] == 1
        assert db.get_customer(c["id"])["status"] == "closed"


class TestDeletePropertyWording:
    """删除类回执的两条硬口径（2026-09-27 第十二组 F428/F429，证据 results/raw/t93.log）

    ① 删除成功只说「已彻底删除这条房源（连同 N 条关联记录）」—— **没说"取不回来"**；
    ② 默认拒删只说要"连历史一起删" —— **没给"只想下架就改状态"的替代方案**。
    """

    def test_receipt_says_irreversible_and_identifies_target(self, purge_tools):
        _, db = purge_tools
        p = make_property(db, title="待删房源-取不回", status="sold")
        out = _call("delete_property", {"property_id": p["id"]})
        msg = out["message"]
        assert out["success"] is True
        assert "彻底删除" in msg, msg
        assert "取不回来" in msg or "无法恢复" in msg, msg
        assert "待删房源-取不回" in msg and str(p["id"]) in msg, f"回执要能核对删的是哪一条：{msg}"

    def test_force_receipt_lists_what_went_with_it(self, purge_tools):
        _, db = purge_tools
        p = make_property(db, title="连带删房源")
        c = make_customer(db)
        db.add_viewing(customer_id=c["id"], property_id=p["id"], viewing_time=datetime.now())
        out = _call("delete_property", {"property_id": p["id"], "force": True})
        msg = out["message"]
        assert out["success"] is True
        assert "带看 1" in msg or "带看1" in msg, f"连带删要说清删了什么：{msg}"

    def test_refusal_offers_status_alternative(self, purge_tools):
        _, db = purge_tools
        p = make_property(db, title="有带看房源")
        c = make_customer(db)
        db.add_viewing(customer_id=c["id"], property_id=p["id"], viewing_time=datetime.now())
        out = _call("delete_property", {"property_id": p["id"]})
        msg = out["message"]
        assert out["success"] is False, out
        assert "带看" in msg, msg
        assert "已售" in msg or "改回来" in msg, f"要给「只下架」的替代方案：{msg}"
        assert "连历史" in msg, msg
