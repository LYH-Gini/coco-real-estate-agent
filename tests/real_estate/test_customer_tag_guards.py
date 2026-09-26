"""客户标签三件套回归（2026-09-24）：写法归一、拆分、去重、不再谎报、存量脏标签可删

覆盖三处修复（2026-09-24 拍板）：
① F71 标签归一：去首尾空白（含全角空格）；按中英文逗号/顿号/分号/斜杠拆成多个（存储层用逗号分隔，
   含逗号的标签本身不合法）；丢空元素、按序去重；单个标签超 20 字拒绝。写读必须对称。
② F72 重复添加不再谎报"已添加"：返回真正新增的 added 与已存在的 already 两部分。
③ F73 三件套描述写清能给什么（原为 6 个字）。
"""
import json

import pytest

from agent.real_estate_input import clean_tags, norm_tags
from tools.real_estate_customer import (add_customer_tag, list_customer_tags,
                                        remove_customer_tag)
from tools.registry import registry


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
    return db


def make(db, name="标签客"):
    return db.add_customer(name=name, tier="C", customer_type="buy_second_hand",
                           status="active")["id"]


def add(cid, tag):
    return json.loads(add_customer_tag(customer_id=cid, tag=tag))


def remove(cid, tag):
    return json.loads(remove_customer_tag(customer_id=cid, tag=tag))


def tags_of(cid):
    return json.loads(list_customer_tags(customer_id=cid))["tags"]


def raw(db, cid):
    with db.get_session() as s:
        from sqlalchemy import text
        return s.execute(text("SELECT tags FROM re_customers WHERE id = :i"), {"i": cid}).scalar()


# ---------- ① 归一 ----------
@pytest.mark.parametrize("raw,expected", [
    ("学区房", ["学区房"]),
    ("  学区房  ", ["学区房"]),
    ("\u3000学区房\u3000", ["学区房"]),          # 全角空格
    ("学区房,地铁房", ["学区房", "地铁房"]),
    ("学区房，地铁房", ["学区房", "地铁房"]),      # 全角逗号
    ("学区房、近地铁", ["学区房", "近地铁"]),      # 顿号
    ("学区房；地铁房", ["学区房", "地铁房"]),
    ("学区房/地铁房", ["学区房", "地铁房"]),
    ("学区房,学区房", ["学区房"]),                # 去重
    (" , , ", []),
])
def test_norm_tags(raw, expected):
    tags, problem = norm_tags(raw)
    assert tags == expected, (raw, tags, problem)


@pytest.mark.parametrize("bad", ["", "   ", "\u3000", None])
def test_empty_tag_rejected(bad):
    tags, problem = norm_tags(bad)
    assert tags == [] and problem == "标签不能为空"


def test_overlong_tag_rejected():
    tags, problem = norm_tags("A" * 21)
    assert tags == [] and "太长" in problem


def test_clean_tags_filters_dirty_legacy_values():
    assert clean_tags("学区房, ,学区房,地铁房") == ["学区房", "地铁房"]
    assert clean_tags("") == [] and clean_tags(None) == []


# ---------- ② 添加：写读对称、不谎报 ----------
def test_add_multiple_tags_in_one_call(tool_db):
    cid = make(tool_db)
    r = add(cid, "学区房,地铁房")
    assert r["success"] is True and r["tags"] == ["学区房", "地铁房"], r
    assert r["added"] == ["学区房", "地铁房"] and r["already"] == []
    assert tags_of(cid) == ["学区房", "地铁房"]
    assert raw(tool_db, cid) == "学区房,地铁房"       # 写读对称：库里就是逗号串


def test_whitespace_variants_are_the_same_tag(tool_db):
    cid = make(tool_db)
    add(cid, "学区房")
    r = add(cid, " 学区房 ")
    assert r["added"] == [] and r["already"] == ["学区房"], r
    assert tags_of(cid) == ["学区房"]
    assert tags_of(cid).count("学区房") == 1


def test_duplicate_add_does_not_claim_added(tool_db):
    cid = make(tool_db)
    add(cid, "学区房")
    r = add(cid, "学区房")
    assert "未重复添加" in r["message"] and "已添加" not in r["message"], r


def test_partial_duplicate_reports_both(tool_db):
    cid = make(tool_db)
    add(cid, "学区房")
    r = add(cid, "学区房,地铁房")
    assert r["added"] == ["地铁房"] and r["already"] == ["学区房"], r
    assert "已添加标签：地铁房" in r["message"] and "学区房 已有" in r["message"], r["message"]


@pytest.mark.parametrize("bad", ["", "   ", "A" * 30])
def test_invalid_tag_rejected_with_hint(tool_db, bad):
    cid = make(tool_db)
    r = add(cid, bad)
    assert r["success"] is False and "标签" in r["error"], r
    assert raw(tool_db, cid) in (None, "")


def test_tag_containing_comma_never_stored_as_one(tool_db):
    """写一个含逗号的标签 → 拆成两个，读回也是两个（原先写 1 读 2）"""
    cid = make(tool_db)
    add(cid, "学区房,地铁房")
    assert not any("," in t for t in tags_of(cid))


# ---------- ③ 移除：能删旧脏标签、提示带现有标签 ----------
def test_remove_normalizes_legacy_dirty_tag(tool_db):
    cid = make(tool_db)
    with tool_db.get_session() as s:
        from sqlalchemy import text
        s.execute(text("UPDATE re_customers SET tags = '学区房 ,地铁房' WHERE id = :i"), {"i": cid})
        s.commit()
    r = remove(cid, "学区房")            # 库里那条带空格，按规范写法也要能删掉
    assert r["success"] is True and r["removed"] == ["学区房"], r
    assert tags_of(cid) == ["地铁房"]


def test_remove_unknown_tag_lists_current_tags(tool_db):
    cid = make(tool_db)
    add(cid, "急售")
    r = remove(cid, "不存在")
    assert r["success"] is False and "现有：急售" in r["error"], r
    assert tags_of(cid) == ["急售"]


def test_remove_from_customer_without_tags(tool_db):
    cid = make(tool_db)
    r = remove(cid, "急售")
    assert r["success"] is False and "还没有标签" in r["error"], r


def test_remove_all_tags_leaves_empty_list(tool_db):
    cid = make(tool_db)
    add(cid, "急售,钥匙在我这")
    remove(cid, "急售")
    remove(cid, "钥匙在我这")
    assert tags_of(cid) == [] and json.loads(list_customer_tags(customer_id=cid))["count"] == 0


def test_unknown_customer_says_not_exists(tool_db):
    assert add(999999, "x")["success"] is False
    assert remove(999999, "x")["success"] is False
    assert json.loads(list_customer_tags(customer_id=999999))["success"] is False


# ---------- ④ 列表：过滤空元素 + count ----------
def test_list_filters_empty_elements_from_legacy_data(tool_db):
    cid = make(tool_db)
    with tool_db.get_session() as s:
        from sqlalchemy import text
        s.execute(text("UPDATE re_customers SET tags = '学区房, ,地铁房,' WHERE id = :i"), {"i": cid})
        s.commit()
    r = json.loads(list_customer_tags(customer_id=cid))
    assert r["tags"] == ["学区房", "地铁房"] and r["count"] == 2, r


# ---------- ⑤ 描述 ----------
@pytest.mark.parametrize("tool", ["add_customer_tag", "remove_customer_tag", "list_customer_tags"])
def test_description_states_what_it_does(tool):
    desc = registry.get_entry(tool).schema.get("description", "")
    assert len(desc) >= 10, (tool, desc)


def test_add_and_remove_descriptions_state_behavior():
    """描述要写清关键行为（一次多个 / 重复不重复添加 / 写法归一）"""
    add_desc = registry.get_entry("add_customer_tag").schema["description"]
    rm_desc = registry.get_entry("remove_customer_tag").schema["description"]
    assert "多个" in add_desc and "重复" in add_desc, add_desc
    assert "归一" in rm_desc, rm_desc

# ---------- ⑥ 读取侧对存量脏值兜底（F74） ----------
@pytest.mark.parametrize("legacy,expected", [
    ("学区房，地铁房、急售", ["学区房", "地铁房", "急售"]),   # 全角逗号 + 顿号
    ("A/B|C", ["A", "B", "C"]),
    ("学区房, ,学区房", ["学区房"]),
])
def test_clean_tags_splits_legacy_separators(legacy, expected):
    assert clean_tags(legacy) == expected


def test_remove_works_on_legacy_fullwidth_separators(tool_db):
    """库里存全角写法时，也要能按规范标签名删掉（并在写入时顺带清洗该行）"""
    cid = make(tool_db)
    with tool_db.get_session() as s:
        from sqlalchemy import text
        s.execute(text("UPDATE re_customers SET tags = '学区房，地铁房、急售' WHERE id = :i"), {"i": cid})
        s.commit()
    assert tags_of(cid) == ["学区房", "地铁房", "急售"]        # 读取侧不再把整串当一个标签
    r = remove(cid, "地铁房")
    assert r["success"] is True and r["removed"] == ["地铁房"], r
    assert raw(tool_db, cid) == "学区房,急售"                  # 写入时顺带清洗成规范写法
