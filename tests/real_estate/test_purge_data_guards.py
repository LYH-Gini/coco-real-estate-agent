"""purge_data 的口径守护（2026-09-27 第十二组 F430–F437）

改前实测证据：/root/coco-tool-audit/results/raw/t95.before.log
  · kind/mode 不认中文；错值文案裸写参数名与英文枚举（mode 还回内部码 bad_mode）；
  · before 非法日期与相对日期都会抛异常逃出工具层（[TOOL_ERROR]）；
  · statuses 传中文被静默忽略（matched 0，不报错）—— 最容易让经纪人以为清干净了；
  · 清单条目的 kind/status 值是英文枚举；预演明细没有条数上限（1200 条时返回 287KB）。
"""
import json

import pytest

from conftest import make_customer, make_property

import agent.real_estate_db as dbmod


@pytest.fixture
def purge(db, monkeypatch):
    monkeypatch.setattr(dbmod, "_db_instance", db)
    import tools.real_estate_purge as mod  # noqa: F401 —— import 时注册工具
    return mod, db


def _call(name, args):
    from tools.registry import registry
    entry = registry.get_entry(name)
    assert entry is not None, f"{name} 没注册（模型看不到）"
    return json.loads(entry.handler(args, session_id="test:session", task_id="t"))


def _seed(db, n=1, status="sold"):
    ids = []
    for i in range(n):
        p = make_property(db, title=f"批量房源{i}", status=status)
        ids.append(p["id"])
    return ids


class TestKindAndModeAliases:
    """F430/F431：枚举参数认中文，错值给中文提示"""

    def test_kind_accepts_chinese(self, purge):
        _, db = purge
        _seed(db, 1, "sold")
        for word in ("房源", "property"):
            out = _call("purge_data", {"kind": word, "dry_run": True})
            assert out["success"] is True, f"kind={word} 被拒：{out}"
            assert out["matched"] >= 1, out

    def test_kind_chinese_customer_and_both(self, purge):
        _, db = purge
        make_customer(db, name="批量客户", phone="13700006001", status="closed")
        for word in ("客户", "两者", "全部"):
            out = _call("purge_data", {"kind": word, "dry_run": True})
            assert out["success"] is True, f"kind={word} 被拒：{out}"
        assert _call("purge_data", {"kind": "客户", "dry_run": True})["matched"] >= 1

    def test_kind_bad_value_message_is_chinese(self, purge):
        out = _call("purge_data", {"kind": "乱值"})
        assert out["success"] is False
        msg = out.get("message") or out.get("error") or ""
        assert "房源" in msg and "客户" in msg, f"没给中文可选项：{msg}"
        assert "property" not in msg and "customer" not in msg, f"文案里还有英文枚举：{msg}"

    def test_mode_accepts_chinese(self, purge):
        _, db = purge
        _seed(db, 1, "sold")
        for word in ("彻底删除", "删除", "delete"):
            out = _call("purge_data", {"mode": word, "dry_run": True})
            assert out["success"] is True, f"mode={word} 被拒：{out}"
            assert out["mode"] == "彻底删除", f"模式值也该用中文：{out['mode']}"
        for word in ("只改状态", "归档", "archive"):
            out = _call("purge_data", {"mode": word, "kind": "房源", "dry_run": True})
            assert out["success"] is True, f"mode={word} 被拒：{out}"
            assert out["archived"] is not None, f"mode={word} 没走归档：{out}"

    def test_mode_bad_value_message_is_chinese(self, purge):
        out = _call("purge_data", {"mode": "乱值"})
        assert out["success"] is False
        msg = out.get("message") or out.get("error") or ""
        assert "彻底删除" in msg and "只改状态" in msg, f"没给中文可选项：{msg}"
        assert "delete" not in msg and "archive" not in msg, f"文案里还有英文枚举：{msg}"
        assert out.get("error") != "bad_mode", f"别把内部错误码丢给模型：{out}"


class TestStatusesAndDates:
    """F432/F433：状态认中文（认不出要报，不许静默漏删）；日期走共用归一"""

    def test_statuses_accept_chinese(self, purge):
        _, db = purge
        _seed(db, 1, "sold")
        out = _call("purge_data", {"kind": "房源", "statuses": ["已售"], "dry_run": True})
        assert out["success"] is True, out
        assert out["matched"] >= 1, f"中文「已售」被静默忽略：{out}"

    def test_statuses_accept_chinese_rented_and_closed(self, purge):
        _, db = purge
        _seed(db, 1, "rented")
        make_customer(db, name="关闭客户", phone="13700006002", status="closed")
        assert _call("purge_data", {"kind": "房源", "statuses": ["已租"], "dry_run": True})["matched"] >= 1
        assert _call("purge_data", {"kind": "客户", "statuses": ["已关闭"], "dry_run": True})["matched"] >= 1

    def test_statuses_unknown_reports_in_chinese(self, purge):
        _, db = purge
        _seed(db, 3, "sold")
        out = _call("purge_data", {"kind": "房源", "statuses": ["乱值"], "dry_run": True})
        assert out["success"] is False, f"认不出的状态必须报出来，不能静默 0 条：{out}"
        msg = out.get("message") or out.get("error") or ""
        assert "已售" in msg and "已租" in msg, f"要给出可用的中文状态：{msg}"

    def test_before_relative_date_works(self, purge):
        _, db = purge
        _seed(db, 1, "sold")
        for word in ("昨天", "前天", "上个月", "2026-01-01"):
            out = _call("purge_data", {"kind": "房源", "before": word, "dry_run": True})
            assert out["success"] is True, f"before={word} 崩了：{out}"
            assert "Tool execution failed" not in json.dumps(out, ensure_ascii=False), out

    def test_before_bad_value_reports_in_chinese(self, purge):
        out = _call("purge_data", {"kind": "房源", "before": "不是日期", "dry_run": True})
        assert out["success"] is False, f"非法日期不能崩：{out}"
        msg = json.dumps(out, ensure_ascii=False)
        assert "Tool execution failed" not in msg, f"异常逃出来了：{msg}"
        assert "不是日期" in msg, f"要说清收到的是什么：{msg}"


class TestEntryLabelsAndReceipt:
    """F434/F435/F437：条目值中文、批量删除要说清取不回来、预演与执行分辨得清"""

    def test_entry_values_are_chinese(self, purge):
        _, db = purge
        _seed(db, 1, "sold")
        make_customer(db, name="中文标签客户", phone="13700006003", status="closed")
        out = _call("purge_data", {"dry_run": True})
        entries = out["deleted_entries"]
        assert entries, out
        for e in entries:
            assert e["kind"] in ("房源", "客户"), f"kind 还是英文：{e['kind']}"
            assert e["status"] in ("已售", "已租", "已关闭", "在售", "在租", "在跟", "潜在"), e["status"]

    def test_delete_receipt_says_irreversible(self, purge):
        _, db = purge
        _seed(db, 2, "sold")
        out = _call("purge_data", {"kind": "房源", "dry_run": False})
        msg = out["message"]
        assert "彻底删除" in msg, msg
        assert "取不回来" in msg or "无法恢复" in msg, f"批量删除也要说清后果：{msg}"
        assert "2" in msg, msg

    def test_skip_sentence_tells_how_to_delete(self, purge):
        _, db = purge
        from datetime import datetime
        p = make_property(db, title="有带看房源", status="sold")
        c = make_customer(db, name="带看客户", phone="13700006004")
        db.add_viewing(customer_id=c["id"], property_id=p["id"], viewing_time=datetime.now())
        out = _call("purge_data", {"kind": "房源", "dry_run": False})
        msg = out["message"]
        assert "跳过" in msg, msg
        assert "连历史" in msg, f"要说清怎么才能删掉被跳过的：{msg}"

    def test_dry_run_and_real_are_distinguishable(self, purge):
        _, db = purge
        _seed(db, 1, "sold")
        dry = _call("purge_data", {"kind": "房源", "dry_run": True})
        real = _call("purge_data", {"kind": "房源", "dry_run": False})
        assert dry["message"].startswith("预演"), dry["message"]
        assert not real["message"].startswith("预演"), f"真执行不该说预演：{real['message']}"
        assert "已彻底删除" in real["message"], real["message"]

    def test_entries_capped_and_flagged(self, purge):
        _, db = purge
        _seed(db, 25, "sold")
        out = _call("purge_data", {"kind": "房源", "dry_run": True})
        assert out["matched"] == 25, out["matched"]
        assert len(out["deleted_entries"]) == 20, f"清单该封顶 20 条：{len(out['deleted_entries'])}"
        assert out.get("truncated") is True, f"要标明被截断：{out.get('truncated')}"
        assert "前 20 条" in out["message"] or "20 条" in out["message"], out["message"]
