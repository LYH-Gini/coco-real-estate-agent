"""参数名传错要给中文提示，不能甩英文 TypeError（2026-09-24）

模型会把字段名传错（`budget` 而不是 `budget_max`、`price_max` 而不是 `max_price`、
`owner` 而不是 `owner_name`）。原先 handler 直接抛
`TypeError: xxx() got an unexpected keyword argument 'budget'`，被包成
`Tool execution failed: ...` —— 模型看到英文异常会转而自己编答案。

本文件对代表性工具逐个验证：传一个 schema 里没有的参数 → 必须给"参数名不对 + 可用参数"提示，
且不得出现"执行失败"。必填参数一并传上（值真假无所谓），确保校验不会先短路掉。
"""
import glob
import importlib
import json
import os

import pytest

from tools.registry import registry

# 工具文件靠 import 自注册（与 test_required_null_args.py 同一做法）
_TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools")
for _f in sorted(glob.glob(os.path.join(_TOOLS_DIR, "real_estate_*.py"))):
    importlib.import_module("tools." + os.path.basename(_f)[:-3])

# 覆盖必填/无必填、客户/房源/跟进/计算各类路径
_CASES = [
    ("add_customer", {"name": "测试", "phone": "13900000000"}, "budget"),
    ("update_customer", {"customer_id": 1}, "budget"),
    ("update_customer", {"customer_id": 1}, "stage"),
    ("add_property", {"title": "测试房源", "price": 1000000, "area": 100}, "owner"),
    ("search_property", {}, "price_max"),
    ("add_followup", {"customer_id": 1, "content": "聊了聊"}, "remark"),
    ("mortgage_calculator", {"price": 1000000}, "rate"),
]


@pytest.mark.parametrize("tool,args,bogus", _CASES)
def test_unknown_arg_gets_chinese_hint(tool, args, bogus):
    raw = registry.dispatch(tool, {**args, bogus.strip(): 1}, session_id="pytest")
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    assert "Tool execution failed" not in text, f"{tool} 传错参数名仍崩：{text[:200]}"
    assert "参数名不对" in text and "可用参数" in text, f"{tool} 应给参数提示：{text[:200]}"
    assert bogus.strip() in text, f"提示里应点名传错的参数：{text[:200]}"


def test_hint_lists_the_tool_own_parameters():
    """提示要列出该工具自己的可用参数（模型照着改就能成功）"""
    raw = registry.dispatch("update_customer", {"customer_id": 1, "budget": 1}, session_id="pytest")
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    for name in ("customer_id", "budget_max", "status", "customer_type", "birthday"):
        assert name in text, f"可用参数里应包含 {name}：{text[:300]}"
