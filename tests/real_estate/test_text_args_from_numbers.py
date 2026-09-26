"""文本参数收到数字要按文本处理，不能让工具崩（2026-09-25）

模型常把手机号（13800001111）、身份证号（110101200001015678）这类长数字串当整数传下来。
schema 声明为 string 的参数拿到 int，handler 会崩在 `'int' object has no attribute 'strip'`
或数据库类型校验上；模型看到英文异常会转去自己编答案（与必填 null、参数名写错、整数越界
同一个病根）。框架层统一转换：声明为文本的参数收到 int/float → 转成文本（数字转文本无损，
正是经纪人原话里那串数字的形态），bool 与已声明为数字的参数不动。
"""
import json

import pytest

from tools.registry import ToolRegistry


def _registry_with(name, handler, props):
    reg = ToolRegistry()
    reg.register(
        name=name, toolset="real_estate",
        schema={"name": name, "description": "test", "parameters":
                {"type": "object", "properties": props, "required": ["text"]}},
        handler=handler,
    )
    return reg


def test_text_param_receives_number_as_text():
    seen = {}

    def handler(args, **kwargs):
        seen.update(args)
        return json.dumps({"ok": True})

    reg = _registry_with("echo_text", handler, {"text": {"type": "string"}})
    reg.dispatch("echo_text", {"text": 110101200001015678})
    assert seen["text"] == "110101200001015678"
    assert isinstance(seen["text"], str)


def test_handler_that_would_crash_on_int_no_longer_crashes():
    """handler 拿文本做事（.strip()）时不再抛 AttributeError，而是正常拿到号码文本"""
    reg = _registry_with("strip_text", lambda args, **kw: json.dumps(
        {"v": args["text"].strip()}), {"text": {"type": "string"}})
    out = json.loads(reg.dispatch("strip_text", {"text": 13800001111}))
    assert out == {"v": "13800001111"}


@pytest.mark.parametrize("value", [123, 1.5])
def test_numbers_are_converted(value):
    reg = _registry_with("echo_num", lambda args, **kw: json.dumps({"v": args["text"]}),
                         {"text": {"type": "string"}})
    assert json.loads(reg.dispatch("echo_num", {"text": value}))["v"] == str(value)


def test_declared_number_params_are_untouched():
    """声明为整数/数值的参数不动（越界另有专门校验），bool 也不当成文本猜"""
    seen = {}

    def handler(args, **kwargs):
        seen.update(args)
        return json.dumps({"ok": True})

    reg = _registry_with("echo_mixed", handler, {"text": {"type": "string"},
                                                 "count": {"type": "integer"},
                                                 "flag": {"type": "boolean"}})
    reg.dispatch("echo_mixed", {"text": True, "count": 7})
    assert seen["count"] == 7 and isinstance(seen["count"], int)
    assert seen["text"] is True  # bool 不猜成 "True"


def test_full_width_text_still_passes_through():
    reg = _registry_with("echo_cn", lambda args, **kw: json.dumps({"v": args["text"]}),
                         {"text": {"type": "string"}})
    assert json.loads(reg.dispatch("echo_cn", {"text": "房东王五"}))["v"] == "房东王五"


# ---------- 真实工具：原先会崩的三处（必须走 registry 派发，才经过框架层转换）----------

@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_owner._get_db", lambda: db)
    monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
    return db


def _dispatch(name, args):
    from tools.registry import registry
    raw = registry.dispatch(name, args, session_id="pytest")
    out = json.loads(raw) if isinstance(raw, str) else raw
    assert "Tool execution failed" not in json.dumps(out, ensure_ascii=False), out
    return out


def test_add_owner_accepts_numeric_id_number(tool_db):
    """身份证 18 位数字几乎必然被当数字传下来 —— 现在能正常脱敏入库"""
    out = _dispatch("add_owner", {"name": "数字证件房东", "id_number": 110101200001015678})
    assert out["success"] is True, out
    assert out["owner"]["id_masked"] == "1101" + "*" * 10 + "5678", out


def test_add_property_accepts_numeric_title(tool_db):
    out = _dispatch("add_property", {"title": 123, "price": 1_000_000, "area": 80.0})
    assert out["success"] is True, out


def test_add_referral_accepts_numeric_referred_name(tool_db):
    rid = _dispatch("add_customer", {"name": "介绍人", "phone": "13900001001"})["customer"]["id"]
    out = _dispatch("add_referral", {"referrer_customer_id": rid, "referred_name": 123})
    assert out["success"] is True, out
