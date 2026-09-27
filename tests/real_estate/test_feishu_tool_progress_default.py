"""飞书工具进展默认关：运行时解析与代码默认值（2026-09-27 要求）

官方飞书这一档默认 "new" —— 每调一个工具就发一条进展，经纪人的聊天窗被这些行刷屏。
Coco 三处都改成关：平台档默认值（gateway/display_config.py）、代码默认值
（hermes_cli/config_defaults.py）、更新时对齐（scripts/coco_config_align.py，规则见
tests/real_estate/test_config_align_rules.py）。实例自己显式写过的值仍然优先。
"""

from gateway.display_config import resolve_display_setting, resolve_tool_progress
from hermes_cli.config import DEFAULT_CONFIG


def test_platform_default_is_off():
    """实例配置里什么都没写时，飞书就是关。"""
    assert resolve_display_setting({}, "feishu", "tool_progress") == "off"
    assert resolve_tool_progress({}, "feishu") == ("off", False)


def test_code_default_is_off():
    assert DEFAULT_CONFIG["display"]["platforms"]["feishu"]["tool_progress"] == "off"


def test_explicit_platform_value_wins():
    """显式开回来的实例不受影响。"""
    cfg = {"display": {"platforms": {"feishu": {"tool_progress": "all"}}}}
    assert resolve_display_setting(cfg, "feishu", "tool_progress") == "all"
    assert resolve_tool_progress(cfg, "feishu") == ("all", True)


def test_explicit_global_value_wins():
    cfg = {"display": {"tool_progress": "new"}}
    assert resolve_display_setting(cfg, "feishu", "tool_progress") == "new"


def test_yaml_bare_off_is_normalised():
    """YAML 1.1 把裸 off 解析成 False —— 同样要当成关。"""
    cfg = {"display": {"platforms": {"feishu": {"tool_progress": False}}}}
    assert resolve_display_setting(cfg, "feishu", "tool_progress") == "off"


def test_other_platforms_untouched():
    """只改飞书：跟飞书同属一档的其它平台（mattermost 等）不受牵连。"""
    assert resolve_display_setting({}, "mattermost", "tool_progress") == "new"
    assert resolve_display_setting({}, "telegram", "tool_progress") == "off"
