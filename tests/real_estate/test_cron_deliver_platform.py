"""定时任务的推送通道（2026-10-03）

真实事故：任务推送地址在 `agent/coco_cron.py` 里写死 `feishu:<会话ID>` —— 微信 / 企业微信装的
Coco 里，经纪人开得了定时任务（回执说成功），但早报、逾期提醒、收工小结一条都发不出来。
修法：按当前会话所在通道拼（`session_platform()` 读网关会话上下文，与 enable_cron 取会话地址
同源），并在同步时纠正「同一会话、通道写错」的老任务。

取证按真实网关形态：会话上下文用 `set_session_vars()` 绑定，任务记录走真实 cron 存储
（HERMES_HOME 由根 conftest 隔离）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gateway.session_context import reset_session_vars, set_session_vars
from hermes_state_ids import new_session_id

import agent.coco_cron as cc

CHAT_ID = "wxid_1f2e3d4c5b6a7988"
OTHER_CHAT_ID = "wxid_0011223344556677"

EXPECTED_JOBS = {"coco_daily_report", "coco_overdue_sentinel", "coco_opportunity",
                 "coco_day_end", "coco_weekly_report"}


def _bind_turn(platform: str, chat_id: str) -> str:
    """复刻网关一轮对话：绑定会话上下文，返回本轮的时间戳式会话编号"""
    session_id = new_session_id()
    set_session_vars(
        platform=platform, source=platform, chat_id=chat_id, chat_type="dm",
        chat_name=chat_id, user_id="ou_test_user",
        session_key=f"agent:main:{platform}:dm:{chat_id}", session_id=session_id,
    )
    return session_id


def _dispatch(name: str, args: dict, **runtime):
    from tools.registry import registry
    return json.loads(registry.dispatch(name, args, **runtime))


def _jobs() -> dict:
    from cron.jobs import list_jobs
    return {j.get("name"): j for j in list_jobs(include_disabled=True)}


@pytest.fixture(autouse=True)
def _clean_gateway_session(monkeypatch):
    """每个用例从干净的会话现场开始，且只允许操作 HERMES_HOME 下的 cron 存储"""
    import tools.real_estate_cron_tools  # noqa: F401  注册 enable_cron / disable_cron
    from cron.jobs import get_cron_output_dir

    monkeypatch.delenv("COCO_CHAT_ID", raising=False)
    monkeypatch.delenv("HERMES_SESSION_PLATFORM", raising=False)
    reset_session_vars()
    home = Path(os.environ["HERMES_HOME"]).resolve()
    store = Path(get_cron_output_dir()).resolve()
    assert store != home and home in store.parents, f"cron 存储不在隔离目录内：{store}"
    yield
    reset_session_vars()


class TestDeliverTarget:
    def test_target_is_channel_plus_conversation(self):
        assert cc.deliver_target(CHAT_ID, "weixin") == f"weixin:{CHAT_ID}"
        assert cc.deliver_target(CHAT_ID, "wecom") == f"wecom:{CHAT_ID}"

    def test_session_channel_used_when_caller_does_not_say(self, monkeypatch):
        monkeypatch.setattr(cc, "session_platform", lambda default="": "weixin")
        assert cc.deliver_target(CHAT_ID) == f"weixin:{CHAT_ID}"


class TestDeliverNeedsFix:
    """只改「同一个会话、通道写错」的地址；经纪人自己设过的一律不碰"""

    def test_same_conversation_wrong_channel(self):
        assert cc._deliver_needs_fix(f"feishu:{CHAT_ID}", f"weixin:{CHAT_ID}") is True

    def test_correct_target_left_alone(self):
        assert cc._deliver_needs_fix(f"weixin:{CHAT_ID}", f"weixin:{CHAT_ID}") is False

    @pytest.mark.parametrize("current", [
        "origin", "", None,
        f"feishu:{OTHER_CHAT_ID}",      # 别的会话
        f"feishu:{CHAT_ID}:thread-1",   # 带话题
    ])
    def test_everything_else_is_left_alone(self, current):
        assert cc._deliver_needs_fix(current, f"weixin:{CHAT_ID}") is False


class TestEnableCronOnWeixin:
    def test_jobs_deliver_to_the_current_channel(self):
        session_id = _bind_turn("weixin", CHAT_ID)
        out = _dispatch("enable_cron", {}, session_id=session_id, task_id=session_id)

        assert out.get("success") is True, out
        jobs = _jobs()
        assert set(jobs) == EXPECTED_JOBS, jobs
        for name, job in jobs.items():
            assert job.get("deliver") == f"weixin:{CHAT_ID}", f"{name}: {job}"

    def test_old_jobs_addressed_to_the_wrong_channel_are_repaired(self):
        """写死飞书那版注册的老任务，在微信里再开一次就该被纠正过来（不用先关再开）"""
        session_id = _bind_turn("feishu", CHAT_ID)
        assert _dispatch("enable_cron", {}, session_id=session_id, task_id=session_id)["success"] is True
        assert _jobs()["coco_daily_report"]["deliver"] == f"feishu:{CHAT_ID}"

        session_id = _bind_turn("weixin", CHAT_ID)
        out = _dispatch("enable_cron", {}, session_id=session_id, task_id=session_id)

        assert out.get("success") is True, out
        for name, job in _jobs().items():
            assert job.get("deliver") == f"weixin:{CHAT_ID}", f"{name}: {job}"

    def test_sync_reports_the_jobs_whose_target_it_rewrote(self):
        """同步要如实报出改了哪些任务的推送地址（排查时不靠猜）"""
        session_id = _bind_turn("feishu", CHAT_ID)
        assert _dispatch("enable_cron", {}, session_id=session_id, task_id=session_id)["success"] is True

        synced = cc._sync_coco_jobs("weixin", CHAT_ID)

        assert set(synced["delivers"]) == EXPECTED_JOBS, synced
        assert synced["prompts"] == [] and synced["schedules"] == [], synced

    def test_other_conversation_target_is_not_rewritten(self):
        """经纪人把提醒发到别的会话时，换个通道再开一次不能把它改到当前会话来"""
        from cron.jobs import update_job

        session_id = _bind_turn("weixin", CHAT_ID)
        assert _dispatch("enable_cron", {}, session_id=session_id, task_id=session_id)["success"] is True
        job = _jobs()["coco_daily_report"]
        update_job(job["id"], {"deliver": f"weixin:{OTHER_CHAT_ID}"})

        assert _dispatch("enable_cron", {}, session_id=session_id, task_id=session_id)["success"] is True
        assert _jobs()["coco_daily_report"]["deliver"] == f"weixin:{OTHER_CHAT_ID}"
