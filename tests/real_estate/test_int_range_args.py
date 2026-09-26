"""整数参数超出 int64 要给中文提示，不能崩（2026-09-24）

模型偶尔把一长串数字当编号（截图识别、把手机号/金额当 id）。这种值在本系统里没有任何合法
含义，但会让数据库驱动抛 `OverflowError: Python int too large to convert to SQLite INTEGER`，
上层只看到英文异常 → 转而自己编答案（与必填 null、参数名写错同一个病根）。

本文件对全部带整数参数的房产工具逐个验证：把该参数传成 2^63 → 必须给"参数超出范围"中文提示，
且不得出现执行失败。必填参数用类型占位填满，确保越界校验不会先被其它校验短路（越界校验在
handler 之前触发，占位值不会被真正用到）。
"""
import glob
import importlib
import json
import os

import pytest

from tools.registry import registry

_TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools")
for _f in sorted(glob.glob(os.path.join(_TOOLS_DIR, "real_estate_*.py"))):
    importlib.import_module("tools." + os.path.basename(_f)[:-3])

_OVERFLOW = 2 ** 63


def _params_of(tool):
    entry = registry.get_entry(tool)
    assert entry is not None, tool
    return (entry.schema or {}).get("parameters") or {}


def _placeholder(spec):
    kind = (spec or {}).get("type")
    if kind in ("integer", "number"):
        return 1
    if kind == "boolean":
        return False
    if kind == "array":
        return []
    if kind == "object":
        return {}
    return "x"


def _required_args(tool):
    params = _params_of(tool)
    props = params.get("properties") or {}
    return {name: _placeholder(props.get(name)) for name in (params.get("required") or [])}


def _cases():
    """(工具, 整数参数名) —— 从 schema 取，覆盖客户/房源/跟进/带看/成交/计算全部路径"""
    from toolsets import TOOLSETS
    out = []
    for name in TOOLSETS["real_estate"]["tools"]:
        props = (_params_of(name).get("properties") or {})
        for param, spec in props.items():
            if (spec or {}).get("type") in ("integer", "number"):
                out.append((name, param))
                break          # 每个工具挑第一个整数参数就够
    return out


@pytest.mark.parametrize("tool,param", _cases())
def test_huge_integer_gets_chinese_hint(tool, param):
    args = {**_required_args(tool), param: _OVERFLOW}
    raw = registry.dispatch(tool, args, session_id="pytest")
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    assert "Tool execution failed" not in text, f"{tool}({param}) 传 2^63 崩了：{text[:200]}"
    assert "参数超出范围" in text and param in text, f"{tool}({param}) 应给越界提示：{text[:200]}"
    assert str(_OVERFLOW) in text, f"{tool}({param}) 提示里应带上越界的值：{text[:200]}"


def test_normal_sized_ids_still_pass_through():
    """正常范围的编号不受影响（别把越界校验做成到处报错）"""
    raw = registry.dispatch("get_customer", {"customer_id": 12345}, session_id="pytest")
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    assert "参数超出范围" not in text, text[:200]
