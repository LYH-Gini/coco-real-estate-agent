"""必填参数传 null 要给中文提示，不能崩到数据库约束（2026-09-24）

模型常把"没问出来的字段"显式写成 null。网关必填校验若只看"键在不在"，就会放行到 handler ——
实测 add_customer / add_followup / mortgage_calculator / tax_calculator / roi_calculator /
update_birthday / generate_poster_grid / loan_compare / tax_breakdown_report 这 9 个会直接崩，
模型看到"执行失败"就会自己编答案（历史教训）。

本文件对全部带必填参数的工具逐个验证：传 null → 必须给"缺少必填参数"提示，且不得出现执行失败。
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


def _required_of(tool):
    entry = registry.get_entry(tool)
    assert entry is not None, tool
    return ((entry.schema or {}).get("parameters") or {}).get("required") or []


def _all_tools_with_required():
    """从工具集清单取所有带必填参数的工具（不解析源码）"""
    names = []
    from toolsets import TOOLSETS
    for name in TOOLSETS["real_estate"]["tools"]:
        if _required_of(name):
            names.append(name)
    return names


@pytest.mark.parametrize("tool", _all_tools_with_required())
def test_null_required_arg_gets_hint_not_crash(tool):
    required = _required_of(tool)
    raw = registry.dispatch(tool, {k: None for k in required}, session_id="pytest")
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    assert "Tool execution failed" not in text, f"{tool} 传 null 崩了：{text[:200]}"
    assert "缺少必填参数" in text, f"{tool} 传 null 应给必填提示：{text[:200]}"
