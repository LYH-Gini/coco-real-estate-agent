"""list_customers 按标签筛（2026-09-24，F75）：精确匹配、不误命中、可与其它筛选叠加

背景：标签能打能删能看，但没有"把标了某个标签的客户列出来"的入口（`list_customers` 只有
tier/status/type 三个筛选），标签打完就成了摆设；而 `add_customer_tag` 的描述还宣称"用于按特征筛客户"。
本文件钉住新增能力的行为契约。
"""
import json

import pytest

from tools.real_estate_customer import add_customer_tag, list_customers


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
    return db


def make(db, name, **overrides):
    data = dict(tier="C", customer_type="buy_second_hand", status="active")
    data.update(overrides)
    return db.add_customer(name=name, **data)["id"]


def tag(db, cid, value):
    add_customer_tag(customer_id=cid, tag=value)


def call(**kwargs):
    return json.loads(list_customers(**kwargs))


def names(resp):
    return [c["name"] for c in resp["customers"]]


# ---------- 基本筛选 ----------
def test_filter_by_tag_returns_only_matching(tool_db):
    a = make(tool_db, "有学区房的")
    b = make(tool_db, "没标签的")
    tag(tool_db, a, "学区房")
    r = call(tag="学区房")
    assert names(r) == ["有学区房的"] and r["total"] == 1, r


def test_tag_match_is_exact_not_substring(tool_db):
    """'地铁房' 不能命中 '近地铁房'（LIKE 粗筛后要精确比对）"""
    a = make(tool_db, "近地铁")
    b = make(tool_db, "纯地铁房")
    tag(tool_db, a, "近地铁房")
    tag(tool_db, b, "地铁房")
    assert names(call(tag="地铁房")) == ["纯地铁房"]
    assert names(call(tag="近地铁房")) == ["近地铁"]


def test_multiple_tags_mean_all_of_them(tool_db):
    a = make(tool_db, "两个都有")
    b = make(tool_db, "只有一个")
    tag(tool_db, a, "学区房,急售")
    tag(tool_db, b, "学区房")
    assert names(call(tag="学区房,急售")) == ["两个都有"]
    assert set(names(call(tag="学区房"))) == {"两个都有", "只有一个"}


def test_tag_filter_combines_with_other_filters(tool_db):
    a = make(tool_db, "租房带标签", customer_type="rent")
    b = make(tool_db, "买房带标签", customer_type="buy_second_hand")
    make(tool_db, "租房没标签", customer_type="rent")
    tag(tool_db, a, "急售")
    tag(tool_db, b, "急售")
    r = call(tag="急售", customer_type="rent")
    assert names(r) == ["租房带标签"] and r["total"] == 1, r
    r2 = call(tag="急售", tier="S")
    assert r2["total"] == 0 and r2["customers"] == []


def test_tag_filter_respects_closed_scope(tool_db):
    a = make(tool_db, "关掉的带标签", status="closed")
    b = make(tool_db, "在跟的带标签")
    tag(tool_db, a, "急售")
    tag(tool_db, b, "急售")
    assert names(call(tag="急售")) == ["在跟的带标签"]
    assert set(names(call(tag="急售", include_closed=True))) == {"在跟的带标签", "关掉的带标签"}


# ---------- 写法归一与存量脏数据 ----------
@pytest.mark.parametrize("passed", ["学区房", "学区房 ", " 学区房", "学区房，", "学区房、"])
def test_tag_arg_is_normalized(tool_db, passed):
    cid = make(tool_db, "有标签的")
    tag(tool_db, cid, "学区房")
    assert names(call(tag=passed)) == ["有标签的"], passed


def test_legacy_dirty_tags_are_findable(tool_db):
    """库里存全角/顿号写法时也能筛到（读取侧同一套分隔符）"""
    a = make(tool_db, "存量脏标签")
    b = make(tool_db, "存量脏标签2")
    with tool_db.get_session() as s:
        from sqlalchemy import text
        s.execute(text("UPDATE re_customers SET tags = '学区房，地铁房' WHERE id = :i"), {"i": a})
        s.execute(text("UPDATE re_customers SET tags = '学区房 、急售' WHERE id = :i"), {"i": b})
        s.commit()
    assert set(names(call(tag="学区房"))) == {"存量脏标签", "存量脏标签2"}
    assert names(call(tag="地铁房")) == ["存量脏标签"]
    assert names(call(tag="急售")) == ["存量脏标签2"]


# ---------- 统计口径 ----------
def test_total_count_and_truncation_with_tag(tool_db):
    for i in range(5):
        cid = make(tool_db, f"标签客户{i}")
        tag(tool_db, cid, "急售")
    r = call(tag="急售", limit=2)
    assert r["count"] == 2 and r["total"] == 5 and r["truncated"] is True, r
    assert "含标签：急售" in r["count_scope"], r["count_scope"]


def test_unknown_tag_returns_empty_not_error(tool_db):
    make(tool_db, "随便一个客户")
    r = call(tag="不存在的标签")
    assert r["success"] is True and r["customers"] == [] and r["total"] == 0, r


@pytest.mark.parametrize("bad", ["", "   ", "A" * 30])
def test_invalid_tag_gets_hint(tool_db, bad):
    r = call(tag=bad)
    assert r["success"] is False and "标签" in r["error"], r


def test_no_tag_argument_keeps_old_behavior(tool_db):
    make(tool_db, "客户一")
    make(tool_db, "客户二")
    r = call()
    assert r["total"] == 2 and "含标签" not in r["count_scope"]


# ---------- 描述 ----------
def test_description_mentions_tag_filter():
    from tools.registry import registry
    desc = registry.get_entry("list_customers").schema.get("description", "")
    assert "标签" in desc, desc
    from tools.registry import registry as r2
    props = (r2.get_entry("list_customers").schema.get("parameters") or {}).get("properties", {})
    assert "tag" in props and "同时包含" in props["tag"]["description"], props.get("tag")
