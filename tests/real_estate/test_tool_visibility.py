"""工具可见性契约：注册表里的房产工具必须全部列进工具集，否则模型看不到。

2026-09-18 发现 12 个工具（调价历史/降价提醒/客户阶段/阶段滞留/流失预警/一键平替/清除缺陷标签/
转介绍登记与统计/贷款对比/税费明细/市场简报）注册了却不在任何工具集里 —— 模型拿不到它们的说明，
等于功能不存在。本测试把这个数字不一致钉死。
"""
import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _registry_tools(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/vis.db"
    monkeypatch.setenv("DATABASE_URL", url)
    from agent.real_estate_db import init_real_estate_db
    init_real_estate_db(url)
    import model_tools  # noqa: F401
    from tools.registry import registry
    return set(registry.get_tool_names_for_toolset("real_estate"))


def test_every_registered_tool_is_exposed_in_toolset(tmp_path, monkeypatch):
    """注册表里的每个工具都必须在 real_estate 工具集清单里"""
    registered = _registry_tools(tmp_path, monkeypatch)
    from toolsets import TOOLSETS
    listed = set(TOOLSETS["real_estate"]["tools"])
    missing = sorted(registered - listed)
    assert not missing, f"以下工具注册了但没进工具集（模型看不到）：{missing}"
    stale = sorted(listed - registered)
    assert not stale, f"工具集里列了但没注册（调用会报未知工具）：{stale}"


def test_smoke_static_list_matches_registry(tmp_path, monkeypatch):
    """冒烟脚本的静态清单必须覆盖全部已注册工具（否则有工具从没被测过）"""
    registered = _registry_tools(tmp_path, monkeypatch)
    text = (REPO_ROOT / "scripts" / "smoke_test_real_estate.py").read_text(encoding="utf-8")
    block = text.split("STATIC_TOOLS = [", 1)[1].split("]", 1)[0]
    names = set(re.findall(r'"([a-z_0-9]+)"', block))
    missing = sorted(registered - names)
    assert not missing, f"冒烟静态清单漏了这些工具：{missing}"
