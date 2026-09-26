"""midday_check 回归（2026-09-25 第四组 F165–F168）+ 框架层 F169（无参工具不再吞多余参数）

背景（实测）：
① 逾期明细只有 `{customer_id, content, next_date}` —— 经纪人只看到编号；只列前 5 条也不说清；
② 空库只回一串 0，没有一句说明；
③ 流失名单**全量**塞进返回（1.2 万客户 = 1654KB）；
④ 描述 4 个字，也没说会顺手自动降级；
⑤ **框架层**：18 个「不声明参数」的工具里 10 个会把多余参数**静默吞掉**
   （handler 写成 `lambda args, **kw: fn()`，永远不抛 TypeError，异常路径的提示永不生效）。
"""
import glob
import importlib
import json
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_followup as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


def _call():
    import tools.real_estate_followup as m

    return json.loads(m.midday_check())


def _customer(db, name, tier="B", created_days_ago=0):
    return db.add_customer(name=name, tier=tier, customer_type="rent",
                           created_at=datetime.now() - timedelta(days=created_days_ago))["id"]


def _overdue(db, cid, days_ago=2, content="该回访了"):
    return db.add_followup(customer_id=cid, type="note", content=content,
                           created_at=datetime.now() - timedelta(days=days_ago),
                           next_date=datetime.now() - timedelta(days=days_ago),
                           next_time="09:00")


# ==================== ① 逾期明细可读 ====================

class TestOverdueDetail:
    def test_carries_customer_name_and_tier(self, wired):
        cid = _customer(wired, "张三", tier="A")
        _overdue(wired, cid)
        row = _call()["check"]["overdue_customers"][0]
        assert row["customer_name"] == "张三"
        assert row["customer_tier"] == "A"
        assert row["content"] == "该回访了"

    def test_deleted_customer_is_labelled(self, wired):
        cid = _customer(wired, "会被删掉的客户")
        _overdue(wired, cid)
        with wired.get_session() as s:
            s.execute(text("DELETE FROM re_customers WHERE id = :i"), {"i": cid})
            s.commit()
        row = _call()["check"]["overdue_customers"][0]
        assert row["customer_name"] == f"已删除客户（id={cid}）"
        assert row["customer_missing"] is True

    def test_truncation_is_disclosed(self, wired):
        for i in range(8):
            cid = _customer(wired, f"逾期客户{i}")
            _overdue(wired, cid, days_ago=2)
        out = _call()
        check = out["check"]
        assert check["overdue_count"] == 8
        assert len(check["overdue_customers"]) == 5
        assert out["message"] == "当前有 8 条逾期跟进，这里列最急的 5 条"

    def test_no_note_when_everything_fits(self, wired):
        cid = _customer(wired, "就一位逾期")
        _overdue(wired, cid)
        out = _call()
        assert "message" not in out

    def test_more_severe_first(self, wired):
        newer = _customer(wired, "刚逾期")
        _overdue(wired, newer, days_ago=1)
        older = _customer(wired, "逾期很久")
        _overdue(wired, older, days_ago=10)
        rows = _call()["check"]["overdue_customers"]
        assert [r["customer_name"] for r in rows] == ["逾期很久", "刚逾期"]


# ==================== ② 空态与流失名单 ====================

class TestEmptyAndStale:
    def test_empty_library_says_so(self, wired):
        out = _call()
        assert out["message"] == "库里还没有客户，先登记客户再看午间情况"
        assert out["check"]["total_customers"] == 0

    def test_stale_list_capped_with_total(self, wired):
        for i in range(25):
            _customer(wired, f"久未联系{i}", tier="C", created_days_ago=120)
        out = _call()
        check = out["check"]
        assert check["stale_total"] == 25
        assert len(check["stale_customers"]) == 20
        assert "共 25 位客户长期无互动，这里列最久的 20 位" in out["message"]

    def test_stale_list_small_stays_whole(self, wired):
        for i in range(3):
            _customer(wired, f"久未联系{i}", tier="C", created_days_ago=120)
        check = _call()["check"]
        assert len(check["stale_customers"]) == 3 and check["stale_total"] == 3


# ==================== ③ 降级如实说明 ====================

class TestDowngrade:
    def test_downgrade_reports_who_and_why(self, wired):
        _customer(wired, "久未联系客户", tier="S", created_days_ago=40)
        item = _call()["check"]["downgrades"][0]
        assert item["name"] == "久未联系客户"
        assert (item["from"], item["to"]) == ("S", "A")
        assert item["threshold"] == 5

    def test_once_per_day(self, wired):
        _customer(wired, "久未联系客户", tier="S", created_days_ago=40)
        _call()
        for _ in range(3):
            assert _call()["check"].get("downgrades") in (None, [])


# ==================== ④ 描述 ====================

class TestDescription:
    def test_description_states_capabilities(self):
        from tools.registry import registry

        desc = registry.get_entry("midday_check").schema["description"]
        assert len(desc) >= 40, desc
        for word in ("午间", "自动降一级", "downgrades", "stale_total"):
            assert word in desc, (word, desc)

    def test_description_not_written_twice_with_drift(self):
        from tools.registry import registry
        import tools.real_estate_followup as m

        assert registry.get_entry("midday_check").schema["description"] == m.TOOLS[5]["description"]


# ==================== ⑤ 框架层：无参工具不再静默吞参数（F169）====================

def _no_param_coco_tools():
    import tools.registry as registry_mod

    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for f in sorted(glob.glob(os.path.join(repo, 'tools', 'real_estate_*.py'))):
        importlib.import_module('tools.' + os.path.basename(f)[:-3])
    names = []
    for name in registry_mod.registry.get_all_tool_names():
        params = ((registry_mod.registry.get_entry(name).schema or {}).get('parameters') or {})
        if not params.get('properties') and not params.get('required'):
            names.append(name)
    return registry_mod.registry, names


def _bind_session():
    from gateway.session_context import set_session_vars
    from hermes_state_ids import new_session_id

    sid = new_session_id()
    set_session_vars(platform='feishu', source='feishu', chat_id='oc_x', chat_type='dm',
                     session_key='agent:main:feishu:dm:oc_x', session_id=sid)
    return sid


def test_no_param_tools_reject_extra_args():
    """不声明参数 + handler 不看 args 的工具收到参数 → 中文提示（不再静默按全量跑还返回成功）"""
    from tools.registry import ToolRegistry

    registry, names = _no_param_coco_tools()
    ignored = [n for n in names if not ToolRegistry._handler_reads_args(registry.get_entry(n))]
    assert len(ignored) >= 8, f"夹具失效：只找到 {len(ignored)} 个（{ignored}）"
    sid = _bind_session()
    for name in ignored:
        out = json.loads(registry.dispatch(name, {'period': 'week'}, session_id=sid, task_id='t'))
        assert 'error' in out and '不认识参数' in out['error'], (name, out)
        assert '（该工具没有可传参数）' in out['error'], (name, out['error'])


def test_handlers_that_read_args_keep_their_undeclared_params():
    """对照：handler 真的会读 args 的工具放行（`enable_cron` 的 chat_id 是 schema 未声明但要用的）"""
    from tools.registry import ToolRegistry

    registry, names = _no_param_coco_tools()
    reading = [n for n in names if ToolRegistry._handler_reads_args(registry.get_entry(n))]
    assert 'enable_cron' in reading, reading
    sid = _bind_session()
    out = json.loads(registry.dispatch('enable_cron', {'chat_id': 'oc_explicit'},
                                       session_id=sid, task_id='t'))
    assert '不认识参数' not in json.dumps(out, ensure_ascii=False), out


def test_no_param_tools_still_work_without_args(monkeypatch):
    """对照：不传参数时照常工作（别把正常调用拦了）"""
    registry, names = _no_param_coco_tools()
    sid = _bind_session()
    for name in ('midday_check', 'daily_report', 'get_coco_version'):
        assert name in names
        out = json.loads(registry.dispatch(name, {}, session_id=sid, task_id='t'))
        assert out.get('success') is not False, (name, out)


def test_tool_with_params_still_lists_available_params(monkeypatch):
    """对照：有参数的工具仍用「可用参数：…」那句（框架层没被改坏）"""
    registry, _ = _no_param_coco_tools()
    sid = _bind_session()
    out = json.loads(registry.dispatch('add_customer', {'name': '张三', 'budget': 500},
                                       session_id=sid, task_id='t'))
    assert '不认识参数' in out['error'] and 'budget_max' in out['error'], out
