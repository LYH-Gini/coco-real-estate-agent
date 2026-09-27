"""压缩默认值必须全口径一致：默认值、兜底值、示例配置三处同一个数。

背景：8 月把 ``hermes_cli/config_defaults.py`` 的 ``threshold`` 改成 0.8、``protect_last_n``
改成 40 时，漏了 ``agent/agent_init.py`` 里两处兜底（官方 0.50 / 20）。后果是：配置里没写
这个键、或配置读取失败时，实际生效的仍是官方口径；官方用例
``tests/agent/test_compression_config_defaults.py`` 断言「兜底值必须等于默认值」，因此常红。
"""

from types import SimpleNamespace

from agent.agent_init import _parse_compression_config
from hermes_cli.config import DEFAULT_CONFIG


def _agent():
    return SimpleNamespace(
        model="m", provider="openrouter", api_mode="chat_completions", quiet_mode=True)


def test_fallback_matches_shipped_defaults():
    """兜底值 == 出厂默认值（配置缺失/读取失败时走的就是这条）。"""
    cs = _parse_compression_config(_agent(), {})
    assert cs.threshold == DEFAULT_CONFIG["compression"]["threshold"]
    assert cs.protect_last == DEFAULT_CONFIG["compression"]["protect_last_n"]


def test_shipped_defaults_are_the_coco_values():
    """出厂默认值就是 Coco 口径（0.8 / 40），不是官方 0.50 / 20。"""
    assert DEFAULT_CONFIG["compression"]["threshold"] == 0.8
    assert DEFAULT_CONFIG["compression"]["protect_last_n"] == 40
