"""清空对话类命令的确认框兜底口径（Coco 侧回归）。

网关判定「要不要先弹确认框」读的是 ``approvals.destructive_slash_confirm``。
官方兜底是「缺键也要问」；Coco 的兜底是「缺键、读配置失败都直接执行」——
经纪人不会输入 /always，官方兜底一旦生效就会把「开新会话」卡住。
只有把该键显式写成 true 时才走确认流程。
"""

import pytest

from gateway.run_busy import GatewayBusySessionMixin


class _FakeRunner:
    """只提供网关读配置这一处依赖的假 runner。"""

    def __init__(self, cfg):
        self._cfg = cfg

    def _read_user_config(self):
        if isinstance(self._cfg, Exception):
            raise self._cfg
        return self._cfg


async def _call(cfg, calls):
    async def execute():
        calls.append("ran")
        return "ok"

    return await GatewayBusySessionMixin._maybe_confirm_destructive_slash(
        _FakeRunner(cfg),
        event=object(),
        command="new",
        title="/new",
        detail="Discards history.",
        execute=execute,
    )


@pytest.mark.asyncio
async def test_config_without_key_runs_without_prompt():
    """配置里没有 approvals 键：直接执行，不弹框。"""
    calls = []
    assert await _call({"model": {"default": "x"}}, calls) == "ok"
    assert calls == ["ran"]


@pytest.mark.asyncio
async def test_empty_approvals_block_runs_without_prompt():
    """approvals 存在但没有这个键：直接执行，不弹框。"""
    calls = []
    await _call({"approvals": {}}, calls)
    assert calls == ["ran"]


@pytest.mark.asyncio
async def test_config_read_failure_runs_without_prompt():
    """读配置抛错：也直接执行（不因为读不到配置就把开新会话卡住）。"""
    calls = []
    await _call(RuntimeError("boom"), calls)
    assert calls == ["ran"]


@pytest.mark.asyncio
async def test_explicit_true_does_not_run_immediately():
    """显式写成 true 时才走确认流程：关键断言是「没有立刻执行」。"""
    calls = []
    try:
        await _call({"approvals": {"destructive_slash_confirm": True}}, calls)
    except Exception:
        # 假 runner 缺确认流程需要的东西，会抛错；这里只关心 execute 有没有被直接跑掉。
        pass
    assert calls == []
