"""Coco 支持的通道都要挂上房产工具集（2026-10-03）

真实事故：微信通道装出来的 Coco，92 个房产工具一个都用不了 —— 房产工具集只挂在飞书通道的
平台工具集上（`hermes-feishu` 的 includes），微信 / 企业微信 / 命令行的平台工具集里都没有它。
同一份配置下实测：飞书会话解析出 92 个房产工具、微信会话 0 个。原因是 Coco 起先只做飞书，
后加微信通道时把消息收发配通了，没把房产工具接进去。

修法：`hermes-weixin` / `hermes-wecom` / `hermes-wecom-callback` 也挂 `includes: ["real_estate"]`。
本文件守这个不变量 —— 同步官方时 `toolsets.py` 会被官方版覆盖，这些断言就是提醒。
"""
from __future__ import annotations

import pytest

from toolsets import TOOLSETS, get_toolset_info, resolve_toolset

# Coco 支持的通道 → 该通道的平台工具集（与 hermes_cli/platforms.py 的 default_toolset 同源）
COCO_CHANNELS = {
    "feishu": "hermes-feishu",
    "weixin": "hermes-weixin",
    "wecom": "hermes-wecom",
    "wecom_callback": "hermes-wecom-callback",
}


def _real_estate_tools() -> set:
    return set(get_toolset_info("real_estate")["resolved_tools"])


@pytest.mark.parametrize("platform,toolset", sorted(COCO_CHANNELS.items()))
def test_channel_toolset_carries_real_estate(platform, toolset):
    """通道工具集必须带上全部房产工具（漏挂 = 那个通道里一个房产工具都没有）"""
    tools = _real_estate_tools()
    assert tools, "real_estate 工具集不该是空的"
    missing = tools - set(resolve_toolset(toolset))
    assert not missing, f"{toolset}（{platform}）漏了 {len(missing)} 个房产工具：{sorted(missing)[:5]}"


@pytest.mark.parametrize("platform", sorted(COCO_CHANNELS))
def test_platform_session_resolves_real_estate_toolset(platform):
    """更贴近真实链路：网关按通道解析出的工具集里必须有 real_estate（不是只写在定义里）"""
    from hermes_cli.tools_config import _get_platform_tools

    enabled = _get_platform_tools({}, platform, include_default_mcp_servers=False)
    assert "real_estate" in enabled, f"{platform} 通道解析出的工具集里没有 real_estate：{sorted(enabled)}"


def test_unrelated_channels_are_not_widened():
    """没对外承诺的通道不顺手加宽：每多挂一个通道，那边每轮都要多发一份房产工具定义"""
    for toolset in ("hermes-telegram", "hermes-discord", "hermes-slack", "hermes-qqbot"):
        assert "real_estate" not in (TOOLSETS[toolset].get("includes") or []), toolset
