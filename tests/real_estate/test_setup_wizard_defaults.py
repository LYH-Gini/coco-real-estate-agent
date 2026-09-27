"""设置向导的默认值必须是 Coco 标准（轮次 500 / 压缩阈值 0.8 / 保留 40 条）。

背景：`_apply_default_agent_settings()` 里官方那句 `config["compression"]["threshold"] = 0.50`
紧跟在我们的 `= 0.8` 后面，会把刚写好的值覆盖回 0.5；终端提示也印着「150 轮 / 阈值 0.50」。
向导有两条调用路径（`hermes setup` 与快速向导），重跑一次就会把 Coco 的设定冲掉。
"""

import pytest

from hermes_cli.setup import _apply_default_agent_settings


@pytest.fixture
def wizard_env(tmp_path, monkeypatch):
    """隔离 HOME，并把写盘与 .env 清理换成空实现。"""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("hermes_cli.setup.save_config", lambda *args, **kwargs: None)
    monkeypatch.setattr("hermes_cli.setup.remove_env_value", lambda key: True)
    return tmp_path


def _target():
    return {"agent": {"max_turns": 500}, "display": {"tool_progress": "all"}, "compression": {}}


def test_wizard_writes_coco_standard(wizard_env):
    config = _target()

    _apply_default_agent_settings(config)

    assert config["agent"]["max_turns"] == 500
    assert config["compression"]["threshold"] == 0.8
    assert config["compression"]["protect_last_n"] == 40


def test_wizard_pulls_official_values_back_to_coco_standard(wizard_env):
    """官方向导写进去的 150 / 0.50 / 20 一律拉回 Coco 标准。"""
    config = {
        "agent": {"max_turns": 150},
        "display": {"tool_progress": "all"},
        "compression": {"threshold": 0.50, "protect_last_n": 20},
    }

    _apply_default_agent_settings(config)

    assert config["agent"]["max_turns"] == 500
    assert config["compression"]["threshold"] == 0.8
    assert config["compression"]["protect_last_n"] == 40


def test_wizard_leaves_hygiene_at_official_default(wizard_env):
    """网关卫生上限不写死在向导里：官方默认就是 5000，写了反而会被当成自定义值。"""
    config = _target()

    _apply_default_agent_settings(config)

    assert "hygiene_hard_message_limit" not in config["compression"]


def test_wizard_summary_matches_written_values(wizard_env, capsys):
    """提示文案必须报实写值，不能还印 150 / 0.50。"""
    _apply_default_agent_settings(_target())

    out = capsys.readouterr().out
    assert "Max iterations: 500" in out
    assert "Compression threshold: 0.8" in out
    assert "Max iterations: 150" not in out
    assert "Compression threshold: 0.50" not in out
