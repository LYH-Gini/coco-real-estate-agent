"""save_script 话术入库：同名不许静默并存、场景归一、空白与超长拦住（2026-09-26，第十一组第 75 项）

F376–F382 的回归（改前：`t76a` 该行 4 格 ⚠️、`t79` 52 例 **36 红**）：
① F376 **同名静默新建** —— 实测存两版同名的，`get_script_by_name` 永远返回**最早**那条，
   经纪人以为改了版、发给客户的还是旧话术；20 次同名就 20 条；
② F377 回执不带编号、不带场景名（`话术已保存: xx`），事后要改/删只能靠还能重复的名字；
③ F378 `name='   '`/`content='   '`/`name='\\n\\t '` 都存得进去（库里出现"名字是空格"的话术）；
④ F379 `name` 传 120 字符照存 —— 生产 PostgreSQL 是 varchar(100)，超长会让**整次保存失败**；
⑤ F380 场景**完全不归一**：`开场白`/`逼定`/`Greeting`/`乱写的场景`/空串/`123` 全原样入库，
   按场景筛话术永远筛不到；
⑥ F381 场景/内容传列表或字典 → 崩，**还把 SQL 语句、表名列名、要写入的参数回给模型**；
⑦ F382 描述 11 字、参数说明 2–3 字。

**2026-09-26 拍板**：同名**默认拒存** + 中文提示 + `force=true` 覆盖原来那条（编号不变）；
名称超 100 字**截断并说明**；场景不填/空串按默认「自定义」。
"""
import json

import pytest
from conftest import make_property  # noqa: F401  （与同目录其它工具守卫用例保持一致的夹具入口）

from tools.real_estate_scripts import save_script


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_scripts._get_db", lambda: db)
    return db


def call(**kwargs):
    return json.loads(save_script(**kwargs))


def rows(db, name):
    return [s for s in db.list_scripts(limit=1000) if s["name"] == name]


def only(db):
    items = db.list_scripts(limit=1000)
    assert len(items) == 1, items
    return items[0]


# ---------- ① 正常路径：回查库 + 回执 ----------

def test_saved_row_is_really_in_the_database(tool_db):
    r = call(name="议价话术", content="这套价格我再帮您跟业主争取一下。", scenario="closing")
    assert r["success"] is True, r
    row = only(tool_db)
    assert (row["id"], row["name"], row["scenario"], row["content"]) == (
        r["script"]["id"], "议价话术", "closing", "这套价格我再帮您跟业主争取一下。")


def test_receipt_carries_id_and_chinese_scenario(tool_db):
    r = call(name="议价话术", content="内容", scenario="closing")
    assert r["message"] == f"话术已保存：「议价话术」（编号 {r['script']['id']}，场景：逼定成交）。", r["message"]
    assert r["script"]["scenario_label"] == "逼定成交", r["script"]
    assert r["updated"] is False, r


def test_legacy_script_fields_are_kept(tool_db):
    r = call(name="议价话术", content="内容")
    assert set(r["script"]) >= {"id", "name", "scenario", "content", "created_at"}, sorted(r["script"])


def test_scenario_defaults_to_custom(tool_db):
    for kwargs in ({}, {"scenario": ""}, {"scenario": "   "}, {"scenario": None}):
        call(name=f"默认场景{sorted(kwargs.items())}", content="内容", **{})
    r = call(name="默认场景", content="内容")
    assert r["script"]["scenario"] == "custom", r["script"]


# ---------- ② 同名：默认拒存 / force 覆盖 ----------

def test_same_name_second_save_is_refused_with_the_id(tool_db):
    first = call(name="议价话术", content="第一版", scenario="closing")
    r = call(name="议价话术", content="第二版", scenario="closing")
    assert r.get("success") is not True, r
    assert len(rows(tool_db, "议价话术")) == 1, rows(tool_db, "议价话术")
    assert str(first["script"]["id"]) in r["error"] and "议价话术" in r["error"], r["error"]
    assert "覆盖" in r["error"] and "改" in r["error"], r["error"]


def test_force_overwrites_the_same_row_and_keeps_the_id(tool_db):
    first = call(name="议价话术", content="第一版", scenario="closing")
    r = call(name="议价话术", content="第三版", scenario="greeting", force=True)
    assert r["success"] is True and r["updated"] is True, r
    assert r["script"]["id"] == first["script"]["id"], r
    row = only(tool_db)
    assert (row["content"], row["scenario"]) == ("第三版", "greeting"), row
    assert r["message"] == f"已更新同名话术「议价话术」（编号 {row['id']}，场景：开场白）。", r["message"]


def test_what_you_read_back_by_name_is_the_latest_version(tool_db):
    """同名并存时"按名字取"永远拿到最早那条 —— 覆盖后才与保存的内容一致"""
    from tools.real_estate_scripts import get_script_by_name

    call(name="议价话术", content="第一版")
    call(name="议价话术", content="第二版", force=True)
    got = json.loads(get_script_by_name(name="议价话术"))
    assert got["script"]["content"] == "第二版", got
    listed = [s for s in json.loads(json.dumps(tool_db.list_scripts(limit=1000))) if s["name"] == "议价话术"]
    assert len(listed) == 1, listed


@pytest.mark.parametrize("force", [True, "true", "是", "覆盖"])
def test_force_accepts_textual_switch(tool_db, force):
    call(name="议价话术", content="第一版")
    r = call(name="议价话术", content="第二版", force=force)
    assert r["success"] is True and r["updated"] is True, r
    assert len(rows(tool_db, "议价话术")) == 1


def test_twenty_same_name_saves_leave_one_row(tool_db):
    call(name="议价话术", content="第0版")
    for i in range(20):
        call(name="议价话术", content=f"第{i}版")
    assert len(tool_db.list_scripts(limit=1000)) == 1


# ---------- ③ 空白与超长 ----------

@pytest.mark.parametrize("name", ["", "   ", "\n\t "])
def test_blank_name_is_refused_and_names_the_field(tool_db, name):
    r = call(name=name, content="内容")
    assert r.get("success") is not True and "话术名称不能为空" in r["error"], r
    assert tool_db.list_scripts(limit=1000) == [], "空白名不该写进库"


@pytest.mark.parametrize("content", ["", "   ", "\n\t "])
def test_blank_content_is_refused_and_names_the_field(tool_db, content):
    r = call(name="议价话术", content=content)
    assert r.get("success") is not True and "话术内容不能为空" in r["error"], r
    assert tool_db.list_scripts(limit=1000) == []


def test_name_is_trimmed(tool_db):
    r = call(name="  议价话术  ", content="内容")
    assert r["script"]["name"] == "议价话术", r["script"]


def test_overlong_name_is_clipped_to_the_column_width_and_stated(tool_db):
    r = call(name="A" * 120, content="内容")
    assert r["success"] is True and len(r["script"]["name"]) == 100, r
    assert r["truncated"] is True and r["note"] == "名称超过 100 字，只保留了前 100 字。", r
    assert len(only(tool_db)["name"]) == 100


# ---------- ④ 场景归一 / 拒存 / 非文字 ----------

@pytest.mark.parametrize("raw,expected", [
    ("greeting", "greeting"), ("Greeting", "greeting"), ("开场白", "greeting"), ("开场", "greeting"),
    ("异议处理", "objection_handling"), ("处理异议", "objection_handling"),
    ("逼定", "closing"), ("促单", "closing"), ("跟进", "follow_up"), ("跟进 维护", "follow_up"),
    ("自定义", "custom"),
])
def test_scenario_written_in_chinese_is_stored_as_the_key(tool_db, raw, expected):
    r = call(name=f"场景{raw}", content="内容", scenario=raw)
    assert r["script"]["scenario"] == expected, r["script"]
    assert r["script"]["scenario_label"], r["script"]


@pytest.mark.parametrize("bad", ["乱写的场景", "xyz", "123"])
def test_unknown_scenario_is_refused_not_silently_stored(tool_db, bad):
    r = call(name="乱场景话术", content="内容", scenario=bad)
    assert r.get("success") is not True and "未知场景" in r["error"], r
    assert "①开场白（greeting）" in r["error"] and "⑤自定义（custom）" in r["error"], r["error"]
    assert rows(tool_db, "乱场景话术") == [], "乱值不许入库"


@pytest.mark.parametrize("bad", [["greeting"], {"a": 1}])
def test_non_text_scenario_gives_chinese_hint(tool_db, bad):
    r = call(name="类型话术", content="内容", scenario=bad)
    assert r.get("success") is not True, r
    assert "话术场景要给一个说法（如「开场白」）" in r["error"] and "没法用" in r["error"], r
    assert "ProgrammingError" not in json.dumps(r, ensure_ascii=False), r
    assert rows(tool_db, "类型话术") == []


# ---------- ⑤ 内容类型 ----------

def test_numeric_content_becomes_text(tool_db, monkeypatch):
    """数字→文本是框架层的口径（F86），所以这条必须走 dispatch 而不是直接调函数"""
    import tools.real_estate_scripts  # noqa: F401
    from tools.registry import registry

    monkeypatch.setattr("tools.real_estate_scripts._get_db", lambda: tool_db)
    r = json.loads(registry.dispatch("save_script", {"name": "数字内容", "content": 12345}))
    assert r["success"] is True and r["script"]["content"] == "12345", r


@pytest.mark.parametrize("bad", [["a", "b"], {"a": 1}])
def test_non_text_content_gives_chinese_hint_without_leaking_sql(tool_db, bad):
    r = call(name="类型内容", content=bad)
    assert r.get("success") is not True, r
    assert "话术内容要给一段文字" in r["error"] and "没法用" in r["error"], r
    blob = json.dumps(r, ensure_ascii=False)
    for leak in ("ProgrammingError", "INSERT INTO", "re_scripts", "sqlite", "parameters"):
        assert leak not in blob, blob


# ---------- ⑥ 描述与参数说明 ----------

def test_schema_description_and_params_are_substantial():
    from tools.registry import registry

    schema = registry._tools["save_script"].schema
    desc = schema["description"]
    assert "同名" in desc and "100 字" in desc and "get_script" in desc, desc
    props = schema["parameters"]["properties"]
    for key in ("name", "content", "scenario", "force"):
        assert len(props[key]["description"]) >= 12, (key, props[key])
    assert props["force"]["type"] == "boolean"
    assert props["scenario"]["enum"] == ["greeting", "objection_handling", "closing", "follow_up", "custom"]
