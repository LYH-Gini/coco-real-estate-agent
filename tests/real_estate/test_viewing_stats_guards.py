"""viewing_stats 回归（2026-09-25 第五组「带看」第 47 项，F200–F202）

背景（实测，见 /root/coco-tool-audit/results/raw/t50.before.log 与 t50b.log）：
① 只回一串裸英文键（`total_viewings`/`done`/`scheduled`/`cancelled`/`interested`/`interest_rate`），
   没有一句能直接复述的中文说法 —— Coco 得自己翻译；
② 三种"空/0"混成一句：真空库、有带看但一次结果都没记（没有"已看"）、有已看但确实 0 人感兴趣，
   返回的东西一模一样（全是 0、占比都 0），Coco 会说"客户感兴趣占比 0%"；
③ 描述 21 字，没说占比口径（按"已看"算）与统计范围（全部历史、不按月）。

本文件钉住修好之后的行为（`stats` 原字段保留；`summary` 给中文键名；`message` 给整句）。
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_viewing as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


def _call():
    import tools.real_estate_viewing as m

    return json.loads(m.viewing_stats())


def _customer(db, name="统计客户", phone="13800001111"):
    return db.add_customer(name=name, phone=phone, tier="A",
                           customer_type="buy_second_hand")["id"]


def _property(db, title="统计房源 1号楼101"):
    return db.add_property(title=title, price=1500000, area=80,
                           property_type="second_hand", status="available")["id"]


def _schedule(cid, pid, days=3):
    import tools.real_estate_viewing as m

    when = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d 10:00")
    out = json.loads(m.schedule_viewing(customer_id=cid, property_id=pid, viewing_time=when))
    assert out["success"] is True, out
    return out["viewing"]["id"]


def _set(db, vid, status, result=None):
    with db.get_session() as s:
        s.execute(text("UPDATE re_viewings SET status = :s, result = :r WHERE id = :i"),
                  {"s": status, "r": result, "i": vid})
        s.commit()


@pytest.fixture
def mixed(wired):
    """3 已看（2 感兴趣 + 1 不感兴趣）、2 已取消、3 待带看"""
    cid1, cid2 = _customer(wired), _customer(wired, "统计客户乙", "13800002222")
    pid1, pid2 = _property(wired), _property(wired, "统计房源B")
    ids = [_schedule(cid, pid, days=i + 1)
           for i, (cid, pid) in enumerate([(cid1, pid1), (cid1, pid2), (cid2, pid1), (cid1, pid1),
                                           (cid2, pid2), (cid1, pid1), (cid2, pid2), (cid2, pid1)])]
    _set(wired, ids[0], "done", "interested")
    _set(wired, ids[1], "done", "interested")
    _set(wired, ids[2], "done", "not_interested")
    _set(wired, ids[3], "cancelled", None)
    _set(wired, ids[4], "cancelled", "interested")     # 取消却留着"感兴趣"（脏数据，不该计入）
    return {"ids": ids, "cid1": cid1, "cid2": cid2, "pid1": pid1, "pid2": pid2}


# ==================== ① 中文说法与形状 ====================
class TestMessageAndSummary:
    def test_message_is_a_full_sentence(self, wired, mixed):
        out = _call()
        assert out["message"] == ("共 8 次带看：待带看 3 次、已看 3 次（其中 2 位客户感兴趣）、"
                                 "已取消 2 次；感兴趣占比 66.7%（按已看算）"
                                 "；客户意向：感兴趣 2 位、不感兴趣 1 位、再考虑 0 位"
                                 "；看房最多的是 统计房源 1号楼101（5 次，其中 1 位感兴趣）"), out["message"]

    def test_summary_uses_chinese_keys(self, wired, mixed):
        out = _call()
        assert out["summary"] == {
            "总带看": 8, "待带看": 3, "已看": 3, "已取消": 2,
            "感兴趣的客户": 2, "感兴趣占比": "66.7%",
            "客户意向": {"感兴趣": 2, "不感兴趣": 1, "再考虑": 0, "没记意向": 0},
            "看房最多的房源": [{"房源": "统计房源 1号楼101", "带看次数": 5, "感兴趣": 1},
                          {"房源": "统计房源B", "带看次数": 3, "感兴趣": 2}],
        }, out["summary"]

    def test_raw_stats_keys_kept(self, wired, mixed):
        out = _call()
        for key in ("total_viewings", "done", "scheduled", "cancelled", "interested",
                    "interest_rate"):
            assert key in out["stats"], (key, out["stats"])

    def test_message_has_no_internals(self, wired, mixed):
        out = _call()
        for word in ("total_viewings", "interest_rate", "stats", "%s"):
            assert word not in out["message"], (word, out["message"])


# ==================== ② 口径与对账 ====================
class TestNumbers:
    def test_matches_sql(self, wired, mixed):
        out = _call()
        st = out["stats"]
        with wired.get_session() as s:
            def n(where="1=1"):
                return s.execute(text(f"SELECT count(*) FROM re_viewings WHERE {where}")).scalar()
            assert st["total_viewings"] == n()
            assert st["done"] == n("status = 'done'")
            assert st["scheduled"] == n("status = 'scheduled'")
            assert st["cancelled"] == n("status = 'cancelled'")
            assert st["interested"] == n("status = 'done' AND result = 'interested'")

    def test_interest_rate_denominator_is_done(self, wired, mixed):
        st = _call()["stats"]
        assert st["interest_rate"] == round(2 / 3 * 100, 1), st      # 按已看 3 算，不是按总数 8

    def test_cancelled_interested_not_counted(self, wired, mixed):
        st = _call()["stats"]
        assert st["interested"] == 2, st                              # 取消那条带 interested 的不算

    def test_counts_add_up(self, wired, mixed):
        st = _call()["stats"]
        assert st["done"] + st["scheduled"] + st["cancelled"] == st["total_viewings"], st

    def test_matches_list_viewings(self, wired, mixed):
        import tools.real_estate_viewing as m

        st = _call()["stats"]
        assert json.loads(m.list_viewings(limit=1))["total"] == st["total_viewings"]
        assert json.loads(m.list_viewings(status="已完成", limit=1))["total"] == st["done"]
        assert json.loads(m.list_viewings(status="已取消", limit=1))["total"] == st["cancelled"]


# ==================== ③ 三种"空/0"分开说 ====================
class TestEmptyAndZero:
    def test_empty_db(self, wired):
        out = _call()
        assert out["message"] == "还没有带看记录 —— 先约一次带看再看统计", out
        assert out["summary"]["感兴趣占比"] is None, out["summary"]

    def test_viewings_without_any_done(self, wired):
        cid, pid = _customer(wired), _property(wired)
        _schedule(cid, pid, days=1)
        _schedule(cid, pid, days=2)
        out = _call()
        assert out["message"].startswith("已经有 2 次带看，但还没记过带看结果"), out["message"]
        assert "感兴趣占比要等有「已看」才有意义" in out["message"], out["message"]
        assert "客户意向" not in out["message"], out["message"]      # 没有已看就不谈意向分布
        assert out["summary"]["已看"] == 0 and out["summary"]["感兴趣占比"] is None, out["summary"]

    def test_done_but_nobody_interested(self, wired):
        cid, pid = _customer(wired), _property(wired)
        vid = _schedule(cid, pid)
        _set(wired, vid, "done", "not_interested")
        out = _call()
        assert out["message"].startswith("已看 1 次，暂时没有客户感兴趣（占比 0%）"), out["message"]
        assert "客户意向：感兴趣 0 位、不感兴趣 1 位、再考虑 0 位" in out["message"], out["message"]
        assert out["summary"]["感兴趣占比"] == "0.0%", out["summary"]

    def test_normal_message_names_denominator(self, wired, mixed):
        assert "（按已看算）" in _call()["message"]


# ==================== ③b 增强：客户意向分布 + 看房最多的房源 ====================
class TestIntentAndTopProperties:
    def test_intent_breakdown_shape(self, wired, mixed):
        out = _call()
        assert out["intent"] == {"interested": 2, "not_interested": 1, "pending": 0,
                                 "unknown": 0}, out["intent"]

    def test_intent_counts_add_up_to_done(self, wired, mixed):
        out = _call()
        assert sum(out["intent"].values()) == out["stats"]["done"], out

    def test_unknown_intent_is_counted_not_dropped(self, wired, mixed):
        """已看但没记意向（或记了认不出的值）→ 归到 unknown，不静默丢"""
        vid = _schedule(mixed["cid1"], mixed["pid1"], days=20)
        _set(wired, vid, "done", None)
        vid2 = _schedule(mixed["cid1"], mixed["pid1"], days=21)
        _set(wired, vid2, "done", "weird")
        out = _call()
        assert out["intent"]["unknown"] == 2, out["intent"]
        assert sum(out["intent"].values()) == out["stats"]["done"], out
        assert "另外 2 次没记意向" in out["message"], out["message"]

    def test_top_properties_sorted_by_viewings(self, wired, mixed):
        out = _call()
        tops = out["top_properties"]
        assert tops[0] == {"property_id": mixed["pid1"], "property_title": "统计房源 1号楼101",
                           "viewings": 5, "interested": 1}, tops[0]
        assert [x["viewings"] for x in tops] == sorted([x["viewings"] for x in tops], reverse=True), tops

    def test_top_properties_total_and_truncation_note(self, wired):
        """超过 5 套房源有带看时：给总数 + 一句截断说明"""
        cid = _customer(wired)
        for i in range(7):
            pid = _property(wired, f"排行房源{i}")
            _schedule(cid, pid, days=i + 1)
        out = _call()
        assert len(out["top_properties"]) == 5, out["top_properties"]
        assert out["top_properties_total"] == 7 and out["top_properties_truncated"] is True, out
        assert "这里列看房最多的 5 套（要我列全就说一声）" in out["message"], out["message"]

    def test_top_properties_deleted_property_labelled(self, wired, mixed):
        with wired.get_session() as s:
            s.execute(text("DELETE FROM re_properties WHERE id = :i"), {"i": mixed["pid1"]})
            s.commit()
        out = _call()
        titles = [x["property_title"] for x in out["top_properties"]]
        assert any("已删除房源" in t for t in titles), titles

    def test_message_mentions_top_property(self, wired, mixed):
        assert "看房最多的是 统计房源 1号楼101（5 次，其中 1 位感兴趣）" in _call()["message"]

    def test_no_top_properties_when_no_viewings(self, wired):
        out = _call()
        assert out["top_properties"] == [] and out["top_properties_total"] == 0, out
        assert "看房最多" not in out["message"], out["message"]

    def test_description_mentions_enhancements(self):
        from tools.registry import registry

        desc = registry.get_entry("viewing_stats").schema["description"]
        for word in ("客户意向分布", "看房最多的前 5 套", "不感兴趣"):
            assert word in desc, (word, desc)


# ==================== ④ 参数与描述 ====================
class TestArgsAndSchema:
    def test_extra_param_is_rejected(self, wired):
        import tools.real_estate_viewing as m

        out = json.loads(m.viewing_stats())
        assert out["success"] is True, out

        from tools.registry import registry
        entry = registry.get_entry("viewing_stats")
        assert entry.schema["parameters"].get("properties") == {}, entry.schema["parameters"]

    def test_description_states_scope_and_ratio(self):
        from tools.registry import registry

        desc = registry.get_entry("viewing_stats").schema["description"]
        assert len(desc) >= 60, desc
        for word in ("全部历史", "已看", "占比", "message"):
            assert word in desc, (word, desc)
