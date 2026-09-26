"""get_followups 回归（2026-09-25 第四组 F135–F139）

背景（实测）：
① 「客户不存在」与「客户存在但没跟进」返回**完全一样**（都是 success + 空列表）——
   经纪人编号打错时，Coco 会说成"这位客户没有跟进记录"；
② 只有 `count`（本次条数）、没有 `total`/`truncated`：250 条跟进的客户默认回 20 条却不说被截断，
   Coco 会把"最近 20 条"当成"一共 20 次"；
③ 描述只有 8 个字、`customer_id` 连说明都没有；
④ `type` 直出英文枚举（同族写入侧已定中文说法）；
⑤ 关联房源只有裸编号 `property_id`，没有标题。

本文件钉住修好之后的行为。
"""
import json
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_followup as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


@pytest.fixture
def fixtures(wired):
    c = wired.add_customer(name="跟进客户", tier="A", customer_type="buy_second_hand")
    empty = wired.add_customer(name="没跟进的客户", tier="C", customer_type="rent")
    p = wired.add_property(title="跟进房源", price=1_500_000, area=80.0,
                           property_type="second_hand", status="available")
    return {"customer": c, "empty": empty, "property": p}


def _call(args):
    import tools.real_estate_followup as m

    return json.loads(m.get_followups(**args))


def _add(db, **kwargs):
    return db.add_followup(**kwargs)


# ==================== ① 空态：不存在与没数据要分开说 ====================

class TestEmptyStates:
    def test_missing_customer_is_reported(self, wired, fixtures):
        out = _call({"customer_id": 999999})
        assert out["success"] is False
        assert out["error"] == "客户不存在，请先在客户列表里核对编号"

    def test_customer_without_followups_says_so(self, wired, fixtures):
        out = _call({"customer_id": fixtures["empty"]["id"]})
        assert out["success"] is True
        assert out["followups"] == [] and out["count"] == 0 and out["total"] == 0
        assert out["truncated"] is False
        assert out["message"] == "这位客户还没有跟进记录"

    def test_two_states_are_distinguishable(self, wired, fixtures):
        missing = _call({"customer_id": 999999})
        empty = _call({"customer_id": fixtures["empty"]["id"]})
        assert missing != empty
        assert missing["success"] is False and empty["success"] is True


# ==================== ② 条数三件齐 ====================

class TestPagingShape:
    def test_total_counts_all_rows_not_the_page(self, wired, fixtures):
        cid = fixtures["customer"]["id"]
        now = datetime.now()
        for i in range(25):
            _add(wired, customer_id=cid, type="note", content=f"第{i + 1}条",
                 created_at=now + timedelta(minutes=i))
        out = _call({"customer_id": cid})
        assert out["count"] == 20 and out["total"] == 25 and out["truncated"] is True, out
        assert out["message"] == ("共 25 条跟进，这里列最近 20 条（最新在前）。"
                                 "要我列全就说一声。")

    def test_no_message_when_everything_fits(self, wired, fixtures):
        cid = fixtures["customer"]["id"]
        _add(wired, customer_id=cid, type="note", content="只有一条")
        out = _call({"customer_id": cid})
        assert out["total"] == 1 and out["truncated"] is False
        assert "message" not in out

    def test_limit_cap_still_reported(self, wired, fixtures):
        cid = fixtures["customer"]["id"]
        _add(wired, customer_id=cid, type="note", content="只有一条")
        out = _call({"customer_id": cid, "limit": 200})
        assert out["count"] == 1 and out["total"] == 1 and out["truncated"] is False

    @pytest.mark.parametrize("bad", [0, -1, "abc", None])
    def test_bad_limit_falls_back_to_default(self, wired, fixtures, bad):
        cid = fixtures["customer"]["id"]
        _add(wired, customer_id=cid, type="note", content="一条")
        out = _call({"customer_id": cid, "limit": bad})
        assert out["success"] is True and out["count"] == 1

    def test_db_layer_keeps_list_shape_for_old_callers(self, wired, fixtures):
        """db.get_followups 不带 with_total 时仍返回列表（cron 脚本按列表用）"""
        cid = fixtures["customer"]["id"]
        _add(wired, customer_id=cid, type="note", content="一条")
        assert isinstance(wired.get_followups(cid), list)
        items, total = wired.get_followups(cid, with_total=True)
        assert len(items) == 1 and total == 1


# ==================== ③ 排序 ====================

class TestOrder:
    def test_newest_first(self, wired, fixtures):
        cid = fixtures["customer"]["id"]
        now = datetime.now()
        for i in range(3):
            _add(wired, customer_id=cid, type="note", content=f"第{i + 1}条",
                 created_at=now + timedelta(minutes=i))
        out = _call({"customer_id": cid})
        assert [f["content"] for f in out["followups"]] == ["第3条", "第2条", "第1条"]

    def test_same_second_is_deterministic(self, wired, fixtures):
        """同一时刻写入的多条按 id 兜底，两次调用顺序必须一致"""
        cid = fixtures["customer"]["id"]
        stamp = datetime.now()
        for i in range(5):
            _add(wired, customer_id=cid, type="note", content=f"同秒{i}", created_at=stamp)
        first = [f["id"] for f in _call({"customer_id": cid})["followups"]]
        second = [f["id"] for f in _call({"customer_id": cid})["followups"]]
        assert first == second == sorted(first, reverse=True)


# ==================== ④ 可读性：中文类型 + 关联房源标题 ====================

class TestReadability:
    @pytest.mark.parametrize("raw,label", [("call", "电话"), ("visit", "带看"), ("deal", "成交"),
                                           ("note", "备注"), ("reminder", "提醒")])
    def test_type_label_is_chinese(self, wired, fixtures, raw, label):
        cid = fixtures["customer"]["id"]
        _add(wired, customer_id=cid, type=raw, content="x")
        out = _call({"customer_id": cid})
        assert out["followups"][0]["type"] == raw          # 机器读的原值保留
        assert out["followups"][0]["type_label"] == label

    def test_legacy_type_falls_back_without_crash(self, wired, fixtures):
        """历史脏值（如 'phone'）原样回落，不 KeyError、不谎报"""
        cid = fixtures["customer"]["id"]
        f = _add(wired, customer_id=cid, type="note", content="x")
        from sqlalchemy import text
        with wired.get_session() as s:
            s.execute(text("UPDATE re_followups SET type = 'phone' WHERE id = :i"), {"i": f["id"]})
            s.commit()
        out = _call({"customer_id": cid})
        assert out["followups"][0]["type_label"] == "phone"

    def test_property_title_is_attached(self, wired, fixtures):
        cid = fixtures["customer"]["id"]
        _add(wired, customer_id=cid, type="visit", content="带看",
             property_id=fixtures["property"]["id"])
        out = _call({"customer_id": cid})
        assert out["followups"][0]["property_title"] == "跟进房源"

    def test_no_property_means_no_key(self, wired, fixtures):
        cid = fixtures["customer"]["id"]
        _add(wired, customer_id=cid, type="note", content="没关联房源")
        out = _call({"customer_id": cid})
        assert "property_title" not in out["followups"][0]

    def test_deleted_property_is_labelled(self, wired, fixtures):
        cid = fixtures["customer"]["id"]
        pid = fixtures["property"]["id"]
        _add(wired, customer_id=cid, type="visit", content="带看", property_id=pid)
        from sqlalchemy import text
        with wired.get_session() as s:
            s.execute(text("DELETE FROM re_properties WHERE id = :i"), {"i": pid})
            s.commit()
        out = _call({"customer_id": cid})
        assert out["followups"][0]["property_title"] == f"已删除房源（id={pid}）"


# ==================== ⑤ 描述 ====================

class TestDescription:
    def test_description_states_paging_and_total(self):
        from tools.registry import registry

        desc = registry.get_entry("get_followups").schema["description"]
        assert len(desc) >= 30, desc
        for word in ("跟进", "最新", "total", "truncated", "客户不存在"):
            assert word in desc, (word, desc)

    def test_description_not_written_twice_with_drift(self):
        from tools.registry import registry
        import tools.real_estate_followup as m

        assert registry.get_entry("get_followups").schema["description"] == m.TOOLS[1]["description"]

    def test_customer_id_has_description(self):
        from tools.registry import registry

        props = registry.get_entry("get_followups").schema["parameters"]["properties"]
        assert props["customer_id"].get("description") == "客户ID"
