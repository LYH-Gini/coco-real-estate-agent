"""get_overdue 回归（2026-09-25 第四组，F140–F146）

背景（全部有实测）：
① 1.2 万客户库下工具层 **17.13 秒**：慢在 `get_stale_customers` 对每位活跃客户单独查一次
   最新跟进（N+1 ≈7.7 秒），而且同一请求里被跑了两遍（降级内部一遍、工具层一遍）；
② **反复调用会连降多级**：S 级 40 天无互动的客户，第 1 次 S→A、第 2 次 A→B、第 3 次 B→C ——
   早报 + 手动问两次就让好客户一天掉到 C；
③ 输出顺序与逾期严重度无关（逾期 3 天的排在逾期 1 天的后面）；
④ 逾期明细只有 `customer_id`；`type` 直出英文；
⑤ 存量孤儿客户只给裸编号；
⑥ 无 `total`/`truncated`（1.2 万条逾期返回 2.1MB）。

本文件钉住修好之后的行为。
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from tools.real_estate_followup import OVERDUE_DOC_KEEP


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_followup as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


def _add_customer(db, name, tier="B", created_days_ago=0, status="active"):
    c = db.add_customer(name=name, tier=tier, customer_type="rent", status=status)
    if created_days_ago:
        with db.get_session() as s:
            s.execute(text("UPDATE re_customers SET created_at = :t WHERE id = :i"),
                      {"t": datetime.now() - timedelta(days=created_days_ago), "i": c["id"]})
            s.commit()
    return c["id"]


def _add_followup(db, cid, content="跟进", days_ago=None, next_days_ago=None):
    created = datetime.now() - timedelta(days=days_ago) if days_ago is not None else datetime.now()
    f = db.add_followup(customer_id=cid, type="note", content=content, created_at=created,
                        next_date=(datetime.now() - timedelta(days=next_days_ago)).replace(microsecond=0)
                        if next_days_ago is not None else None)
    return f["id"]


def _call(args=None):
    import tools.real_estate_followup as m

    return json.loads(m.get_overdue(**(args or {})))


def _tier(db, cid):
    with db.get_session() as s:
        return s.execute(text("SELECT tier FROM re_customers WHERE id = :i"), {"i": cid}).fetchone()[0]


def _tier_changes(db, cid=None):
    sql = "SELECT customer_id, old_value, new_value FROM re_customer_changes WHERE field = 'tier'"
    params = {}
    if cid is not None:
        sql += " AND customer_id = :i"
        params["i"] = cid
    with db.get_session() as s:
        return s.execute(text(sql), params).fetchall()


# ==================== ① 降级：一天最多一级 ====================

class TestDowngradeOncePerDay:
    def test_repeated_calls_do_not_cascade(self, wired):
        """S 级 40 天无互动：反复调用只能降一级（改前第 3 次会掉到 C）"""
        cid = _add_customer(wired, "久未联系", tier="S", created_days_ago=40)
        seen = []
        for _ in range(3):
            _call()
            seen.append(_tier(wired, cid))
        assert seen[0] == "A", seen                  # 第一次调用降一级
        assert set(seen[1:]) == {"A"}, seen          # 后面两次不再降
        assert len(_tier_changes(wired, cid)) == 1

    def test_next_day_can_downgrade_again(self, wired):
        """跨天后可以再降一级（把降级记录挪到昨天模拟）"""
        cid = _add_customer(wired, "久未联系", tier="S", created_days_ago=40)
        _call()
        assert _tier(wired, cid) == "A"
        with wired.get_session() as s:
            s.execute(text("UPDATE re_customer_changes SET created_at = :t WHERE customer_id = :i"),
                      {"t": datetime.now() - timedelta(days=1), "i": cid})
            s.commit()
        _call()
        assert _tier(wired, cid) == "B"

    def test_manual_tier_change_today_blocks_auto(self, wired):
        """当天人工调过等级 → 当天不再被自动降级（人改过的优先）"""
        cid = _add_customer(wired, "人工调过级", tier="S", created_days_ago=40)
        wired.update_customer(cid, tier="B")
        _call()
        assert _tier(wired, cid) == "B"

    def test_downgrades_are_reported_truthfully(self, wired):
        cid = _add_customer(wired, "久未联系", tier="S", created_days_ago=40)
        first = _call()
        assert [d["customer_id"] for d in first["downgrades"]] == [cid]
        assert first["downgrades"][0]["from"] == "S" and first["downgrades"][0]["to"] == "A"
        second = _call()
        assert second.get("downgrades") in (None, []), second


# ==================== ② 流失名单口径（批量聚合 vs 逐客户查询）====================

class TestStaleList:
    def test_batch_scan_matches_per_customer_scan(self, wired):
        """一次聚合算出来的超期名单必须与"逐客户取最新跟进"逐个算出来的完全一致"""
        stale_id = _add_customer(wired, "40天没互动", tier="B", created_days_ago=100)
        _add_followup(wired, stale_id, days_ago=40)
        fresh_id = _add_customer(wired, "今天刚跟进", tier="B", created_days_ago=100)
        _add_followup(wired, fresh_id, days_ago=0)
        no_fu_id = _add_customer(wired, "从没跟进", tier="A", created_days_ago=20)

        got = {i["customer_id"] for i in wired.get_stale_customers()}
        assert got == {stale_id, no_fu_id}, got

    def test_thresholds_by_tier(self, wired):
        s_id = _add_customer(wired, "S级6天", tier="S", created_days_ago=6)
        a_id = _add_customer(wired, "A级6天", tier="A", created_days_ago=6)
        names = {i["customer_id"] for i in wired.get_stale_customers()}
        assert s_id in names and a_id not in names      # S>5 / A>10


# ==================== ③ 排序：逾期最久在前 ====================

class TestOrder:
    def test_oldest_overdue_first(self, wired):
        newer = _add_customer(wired, "逾期1天", tier="B")
        _add_followup(wired, newer, days_ago=1, next_days_ago=1)
        older = _add_customer(wired, "逾期3天", tier="B")
        _add_followup(wired, older, days_ago=5, next_days_ago=3)   # 记录更早创建
        out = _call()
        assert [o["customer_id"] for o in out["overdue"]] == [older, newer]

    def test_order_is_stable(self, wired):
        for days in (4, 2, 6):
            cid = _add_customer(wired, f"逾期{days}天", tier="B")
            _add_followup(wired, cid, days_ago=10, next_days_ago=days)
        first = [(o["id"]) for o in _call()["overdue"]]
        second = [(o["id"]) for o in _call()["overdue"]]
        assert first == second

    def test_old_overdue_cleared_by_new_followup(self, wired):
        """口径不变：每位客户只看最新一条跟进，记了新跟进就不再报旧逾期"""
        cid = _add_customer(wired, "补了跟进", tier="B")
        _add_followup(wired, cid, days_ago=3, next_days_ago=3)
        _add_followup(wired, cid, content="新的跟进", days_ago=0)
        assert cid not in [o["customer_id"] for o in _call()["overdue"]]


# ==================== ④ 可读性：客户名 / 中文类型 / 孤儿标注 ====================

class TestReadability:
    def test_customer_name_and_tier_attached(self, wired):
        cid = _add_customer(wired, "张三", tier="A")
        _add_followup(wired, cid, days_ago=2, next_days_ago=2)
        item = _call()["overdue"][0]
        assert item["customer_name"] == "张三" and item["customer_tier"] == "A"
        assert item["type_label"] == "备注" and item["type"] == "note"

    def test_deleted_customer_is_labelled(self, wired):
        cid = _add_customer(wired, "会被删掉的客户", tier="B")
        _add_followup(wired, cid, days_ago=2, next_days_ago=2)
        with wired.get_session() as s:
            s.execute(text("DELETE FROM re_customers WHERE id = :i"), {"i": cid})
            s.commit()
        item = _call()["overdue"][0]
        assert item["customer_name"] == f"已删除客户（id={cid}）"
        assert item["customer_missing"] is True

    def test_legacy_type_falls_back(self, wired):
        cid = _add_customer(wired, "历史脏值", tier="B")
        fid = _add_followup(wired, cid, days_ago=2, next_days_ago=2)
        with wired.get_session() as s:
            s.execute(text("UPDATE re_followups SET type = 'phone' WHERE id = :i"), {"i": fid})
            s.commit()
        assert _call()["overdue"][0]["type_label"] == "phone"


# ==================== ⑤ 条数与空态 ====================

class TestPagingAndEmpty:
    def test_total_and_truncated(self, wired):
        for i in range(55):
            cid = _add_customer(wired, f"逾期客户{i}", tier="B")
            _add_followup(wired, cid, days_ago=3, next_days_ago=3)
        out = _call()
        assert out["total"] == 55 and out["count"] == 50 and out["truncated"] is True

    def test_limit_default_is_50(self, wired):
        for i in range(55):
            cid = _add_customer(wired, f"逾期客户{i}", tier="B")
            _add_followup(wired, cid, days_ago=3, next_days_ago=3)
        assert len(_call()["overdue"]) == 50

    def test_limit_has_no_cap(self, wired):
        """2026-09-25 定：limit 不设上限（要看全部走文档，不砍数据）"""
        for i in range(55):
            cid = _add_customer(wired, f"逾期客户{i}", tier="B")
            _add_followup(wired, cid, days_ago=3, next_days_ago=3)
        out = _call({"limit": 2000})
        assert out["count"] == 55 and out["truncated"] is False

    @pytest.mark.parametrize("bad", [0, -1, "abc", None])
    def test_bad_limit_falls_back_to_default(self, wired, bad):
        cid = _add_customer(wired, "逾期客户", tier="B")
        _add_followup(wired, cid, days_ago=3, next_days_ago=3)
        assert _call({"limit": bad})["count"] == 1

    def test_empty_library_says_so(self, wired):
        assert _call()["message"] == "库里还没有客户，先登记客户再设跟进"

    def test_no_overdue_with_customers(self, wired):
        cid = _add_customer(wired, "正常客户", tier="B")
        _add_followup(wired, cid, days_ago=0)
        out = _call()
        assert out["total"] == 0 and out["message"] == "暂无逾期跟进"

    def test_downgrade_note_is_appended_to_message(self, wired):
        _add_customer(wired, "久未联系", tier="S", created_days_ago=40)
        out = _call()
        assert "已自动降级" in out["message"]


# ==================== ⑥ 只读语义与性能 ====================

class TestReadToolSideEffects:
    def test_downgrade_does_not_touch_other_fields(self, wired):
        cid = _add_customer(wired, "久未联系", tier="S", created_days_ago=40)
        before = wired.get_customer(cid)
        _call()
        after = wired.get_customer(cid)
        assert after["tier"] == "A"
        for key in ("name", "stage", "status", "customer_type"):
            assert before[key] == after[key], key


# ==================== ⑦ 完整清单文档（2026-09-25 定：>50 条走文档）====================

def _overdue_customers(db, n, start=0):
    for i in range(start, start + n):
        cid = _add_customer(db, f"逾期客户{i}", tier="B")
        _add_followup(db, cid, days_ago=3, next_days_ago=3)
    return n


def _doc_dir():
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "doc_cache"


class TestDocumentExport:
    def test_default_does_not_write_any_file(self, wired):
        _overdue_customers(wired, 55)
        out = _call()
        assert "document_path" not in out
        assert not list(_doc_dir().glob("逾期跟进_*.md"))

    def test_document_contains_every_row(self, wired):
        _overdue_customers(wired, 55)
        out = _call({"document": True})
        assert out["document_path"], out
        from pathlib import Path

        doc_text = Path(out["document_path"]).read_text(encoding="utf-8")
        assert "共 55 条" in doc_text.splitlines()[0]
        rows = [line for line in doc_text.splitlines() if line.startswith("- **")]
        assert len(rows) == 55, len(rows)          # 不受 limit 限制，含全部条目
        assert "逾期 3 天" in rows[0]

    def test_document_still_caps_the_chat_reply(self, wired):
        _overdue_customers(wired, 55)
        out = _call({"document": True})
        assert out["count"] == 50 and out["total"] == 55 and out["truncated"] is True

    def test_truncation_message_is_agent_facing(self, wired):
        """截断那句话是 Coco 说给经纪人听的：不能出现参数名/工具名（2026-09-25 实测指出）"""
        _overdue_customers(wired, 55)
        msg = _call()["message"]
        assert msg == ("共 55 条逾期跟进，这里先列逾期最久的 50 条。"
                       "要看全部跟我说一声，我会生成一份 Markdown 清单（含全部条目）发给你"), msg
        for internal in ("document", "limit", "truncated", "total", "get_overdue", "参数"):
            assert internal not in msg, (internal, msg)

    def test_document_message_is_agent_facing(self, wired):
        _overdue_customers(wired, 55)
        msg = _call({"document": True})["message"]
        assert msg == "完整清单已生成（共 55 条），我把它发给你", msg

    def test_document_not_generated_when_nothing_overdue(self, wired):
        cid = _add_customer(wired, "正常客户", tier="B")
        _add_followup(wired, cid, days_ago=0)
        out = _call({"document": True})
        assert "document_path" not in out and out["message"] == "暂无逾期跟进"

    def test_keeps_only_recent_documents(self, wired):
        _overdue_customers(wired, 3)
        doc_dir = _doc_dir()
        doc_dir.mkdir(parents=True, exist_ok=True)
        for i in range(OVERDUE_DOC_KEEP + 5):
            (doc_dir / f"逾期跟进_202001{i:02d}_000000.md").write_text("旧文档", encoding="utf-8")
        out = _call({"document": True})
        assert out["document_path"]
        kept = list(doc_dir.glob("逾期跟进_*.md"))
        assert len(kept) == OVERDUE_DOC_KEEP, len(kept)
        assert any(p.name == out["document_path"].split("/")[-1] for p in kept)

    def test_deleted_customer_shows_in_document(self, wired):
        cid = _add_customer(wired, "会被删的客户", tier="B")
        _add_followup(wired, cid, days_ago=2, next_days_ago=2)
        with wired.get_session() as s:
            s.execute(text("DELETE FROM re_customers WHERE id = :i"), {"i": cid})
            s.commit()
        from pathlib import Path

        doc_text = Path(_call({"document": True})["document_path"]).read_text(encoding="utf-8")
        assert f"已删除客户（id={cid}）" in doc_text


# ==================== ⑧ 描述 ====================

class TestOverdueDescription:
    def test_description_states_capabilities(self):
        from tools.registry import registry

        desc = registry.get_entry("get_overdue").schema["description"]
        assert len(desc) >= 40, desc
        for word in ("逾期", "逾期最久在前", "document", "MEDIA:", "自动降一"):
            assert word in desc, (word, desc)

    def test_description_not_written_twice_with_drift(self):
        from tools.registry import registry
        import tools.real_estate_followup as m

        assert registry.get_entry("get_overdue").schema["description"] == m.TOOLS[2]["description"]

    def test_params_declared(self):
        from tools.registry import registry

        props = registry.get_entry("get_overdue").schema["parameters"]["properties"]
        assert set(props) == {"limit", "document"}
        assert "50" in props["limit"]["description"]
        assert "MEDIA:" in props["document"]["description"]
