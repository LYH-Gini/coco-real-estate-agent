"""整数参数形态归一：数字字符串转整数、bool 拦成中文提示（2026-09-25）

模型偶尔把编号/条数传成文本或布尔：
- `owner_id=true` 会被当成 1 → 返回 **id=1 那条记录**（把别人的资料当这位客户的资料念出来），
  实测 10 个读类工具全中；
- `'2'` / `' 2 '` 这类数字串此前依赖数据库的隐式转换（SQLite 与 PostgreSQL 口径不同）；
- `'abc'` 认不出，但 `limit` 这类参数的口径是"非数字按默认"，所以框架层**不拦**，
  语义交给工具自己判。
"""
import json

import pytest

from tools.registry import ToolRegistry


def _registry_with(name, handler, props, required=("num",)):  # noqa: D401 —— 数组用例显式传 required=(条)
    reg = ToolRegistry()
    reg.register(
        name=name, toolset="real_estate",
        schema={"name": name, "description": "test",
                "parameters": {"type": "object", "properties": props, "required": list(required)}},
        handler=handler,
    )
    return reg


def _echo(seen):
    def handler(args, **kwargs):
        seen.update(args)
        return json.dumps({"ok": True})
    return handler


INT_PROP = {"num": {"type": "integer"}}
NUM_PROP = {"num": {"type": "number"}}


@pytest.mark.parametrize("raw,expected", [("12", 12), (" 12 ", 12), ("+12", 12), ("-3", -3)])
def test_numeric_string_becomes_integer(raw, expected):
    seen = {}
    reg = _registry_with("echo_int", _echo(seen), INT_PROP)
    reg.dispatch("echo_int", {"num": raw})
    assert seen["num"] == expected and isinstance(seen["num"], int), (raw, seen)


@pytest.mark.parametrize("value", [True, False])
def test_bool_id_is_blocked_with_chinese_hint(value):
    """bool 当编号没有任何含义，却会命中 id=1 —— 必须拦成中文提示，而不是返回别人的资料"""
    seen = {}
    reg = _registry_with("echo_bool", _echo(seen), INT_PROP)
    out = reg.dispatch("echo_bool", {"num": value})
    text = json.dumps(out, ensure_ascii=False)
    assert "要是数字" in text, out
    assert ("true" if value else "false") in text, out
    assert "Tool execution failed" not in text, out
    assert seen == {}, "handler 不该被调用"


def test_nonnumeric_string_is_left_to_the_tool():
    """`limit` 类参数的口径是"非数字按默认" —— 框架层不许一刀切拦死"""
    seen = {}
    reg = _registry_with("echo_str", _echo(seen), INT_PROP)
    reg.dispatch("echo_str", {"num": "abc"})
    assert seen["num"] == "abc"


def test_huge_numeric_string_hits_range_check():
    """20 位数字串：先归一成整数，再被越界校验拦住（原先 sqlite 静默说"不存在"、PG 报错）"""
    reg = _registry_with("echo_huge", _echo({}), INT_PROP)
    out = reg.dispatch("echo_huge", {"num": "99999999999999999999"})
    text = json.dumps(out, ensure_ascii=False)
    assert "参数超出范围" in text and "num" in text, out


def test_number_type_param_also_normalized():
    seen = {}
    reg = _registry_with("echo_number", _echo(seen), NUM_PROP)
    reg.dispatch("echo_number", {"num": "7"})
    assert seen["num"] == 7


def test_limit_nonnumeric_still_falls_back_to_default(db, monkeypatch):
    """`limit` 传非数字文本仍按默认处理（不改动已定契约）；传 bool 才被框架层拦成提示"""
    import tools.real_estate_customer as tc
    monkeypatch.setattr(tc, "_get_db", lambda: db)
    tc.add_customer(name="条数客户", phone="13800007777", customer_type="rent")
    from tools.registry import registry
    ok = registry.dispatch("list_customers", {"limit": "abc"}, session_id="pytest")
    ok_out = json.loads(ok) if isinstance(ok, str) else ok
    assert ok_out.get("success") is True, ok_out
    assert len(ok_out.get("customers") or []) == 1, ok_out

    bad = registry.dispatch("list_customers", {"limit": True}, session_id="pytest")
    bad_text = bad if isinstance(bad, str) else json.dumps(bad, ensure_ascii=False)
    assert "要是数字" in bad_text, bad_text


# ---------- 整数数组的元素（2026-09-25 补：数组元素原先不受任何形态/越界校验保护）----------

INT_ARRAY_PROP = {"ids": {"type": "array", "items": {"type": "integer"}}}


@pytest.mark.parametrize("value", [True, False])
def test_bool_element_in_int_array_is_blocked(value):
    """`property_ids=[true]` 会被当成 [1] → 返回 id=1 那条记录（把别人的数据当这条回答）"""
    seen = {}
    reg = _registry_with("echo_arr_bool", _echo(seen), INT_ARRAY_PROP, required=("ids",))
    out = reg.dispatch("echo_arr_bool", {"ids": [1, value]})
    text = json.dumps(out, ensure_ascii=False)
    assert "里出现了" in text and ("true" if value else "false") in text, out
    assert "要传数字" in text and "Tool execution failed" not in text, out
    assert seen == {}, "handler 不该被调用"


def test_huge_element_hits_range_check():
    reg = _registry_with("echo_arr_huge", _echo({}), INT_ARRAY_PROP, required=("ids",))
    text = json.dumps(reg.dispatch("echo_arr_huge", {"ids": [2 ** 63]}), ensure_ascii=False)
    assert "参数超出范围" in text and "ids" in text, text


@pytest.mark.parametrize("raw,expected", [("12", 12), (" 12 ", 12), ("+3", 3)])
def test_numeric_string_element_becomes_integer(raw, expected):
    seen = {}
    reg = _registry_with("echo_arr_str", _echo(seen), INT_ARRAY_PROP, required=("ids",))
    reg.dispatch("echo_arr_str", {"ids": [raw]})
    assert seen["ids"] == [expected], seen


def test_nonnumeric_element_is_left_to_the_tool():
    """认不出的元素不拦（与标量口径一致：`limit` 类按默认、编号类由工具给提示）"""
    seen = {}
    reg = _registry_with("echo_arr_abc", _echo(seen), INT_ARRAY_PROP, required=("ids",))
    reg.dispatch("echo_arr_abc", {"ids": ["abc"]})
    assert seen["ids"] == ["abc"], seen


def test_string_array_is_untouched():
    seen = {}
    reg = _registry_with("echo_arr_of_str", _echo(seen),
                         {"names": {"type": "array", "items": {"type": "string"}}}, required=("names",))
    reg.dispatch("echo_arr_of_str", {"names": [1, "张三"]})   # 元素类型不限：数组本身不是整数数组
    assert seen["names"] == [1, "张三"], seen


def test_real_tool_array_end_to_end(db, monkeypatch):
    import tools.real_estate_owner as to
    monkeypatch.setattr(to, "_get_db", lambda: db)
    import tools.real_estate_property as tp
    monkeypatch.setattr(tp, "_get_db", lambda: db)
    pid = json.loads(tp.add_property(title="数组形态房", price=1_000_000, area=80.0,
                                     force=True))["property"]["id"]

    from tools.registry import registry
    bad = registry.dispatch("get_property_owners", {"property_ids": [True]}, session_id="pytest")
    bad_text = bad if isinstance(bad, str) else json.dumps(bad, ensure_ascii=False)
    assert "要传数字" in bad_text, bad_text

    ok = registry.dispatch("get_property_owners", {"property_ids": [str(pid)]}, session_id="pytest")
    ok_out = json.loads(ok) if isinstance(ok, str) else ok
    assert ok_out.get("success") is True and ok_out["properties"][0]["id"] == pid, ok_out


def test_real_tools_end_to_end(db, monkeypatch):
    """真实工具：bool 编号给中文提示、数字串编号照常查到人"""
    import tools.real_estate_customer as tc
    import tools.real_estate_owner as to
    monkeypatch.setattr(to, "_get_db", lambda: db)
    monkeypatch.setattr(tc, "_get_db", lambda: db)
    oid = json.loads(to.add_owner(name="形态房东", phone="13800009999"))["owner"]["id"]
    cid = json.loads(tc.add_customer(name="形态客户", phone="13800008888"))["customer"]["id"]

    from tools.registry import registry
    for tool, param, real_id in (("get_owner", "owner_id", oid),
                                 ("get_customer", "customer_id", cid)):
        bad = registry.dispatch(tool, {param: True}, session_id="pytest")
        bad_text = bad if isinstance(bad, str) else json.dumps(bad, ensure_ascii=False)
        assert "要是数字" in bad_text, (tool, bad_text)
        ok = registry.dispatch(tool, {param: str(real_id)}, session_id="pytest")
        ok_out = json.loads(ok) if isinstance(ok, str) else ok
        assert ok_out.get("success") is True, (tool, ok_out)
