"""定时任务自助开关的会话寻址回归（2026-09-21）

背景（真实事故）：`enable_cron` 曾假设网关交给工具的 `session_id` 是
`agent:main:feishu:dm:oc_xxx` 这种复合格式，从中切出 `oc_` 段当推送目标；而网关实际传的是
时间戳式**会话编号**（如 `20260921_213802_4bf4a40d`），真正的会话地址在框架会话上下文
`HERMES_SESSION_CHAT_ID` 里。结果：真实飞书会话里说「开启定时任务」永远报
「无法确定推送会话」，只有手工在 `.env.db` 配 `COCO_CHAT_ID` 并重启才能用。

本文件按**真实网关形态**取证：会话编号由 `new_session_id()` 生成、会话地址由
`set_session_vars()` 绑定 —— 不手写框架产物的格式。手写格式正是这个 bug 藏了一个月的
原因：旧用例传的是 `agent:main:feishu:dm:oc_x`，与生产不符却一直是绿的。
"""
import json
import os
from pathlib import Path

import pytest

from gateway.session_context import reset_session_vars, set_session_vars
from hermes_state_ids import new_session_id

CHAT_ID = "oc_1f2e3d4c5b6a79887766554433221100"
OTHER_CHAT_ID = "oc_00112233445566778899aabbccddeeff"
EXPLICIT_CHAT_ID = "oc_ffeeddccbbaa99887766554433221100"

# 2026-09-23 重设计后的任务表（5 条；午间检查与每 30 分钟检查已取消）
EXPECTED_JOBS = {"coco_daily_report", "coco_overdue_sentinel", "coco_opportunity",
                 "coco_day_end", "coco_weekly_report"}
PURE_SCRIPT_JOBS = {"coco_overdue_sentinel"}  # 纯脚本任务不叫模型、不带工具集


def _store_guard() -> None:
    """守卫：本文件的用例只允许操作 HERMES_HOME 下的 cron 存储，绝不碰真实存储"""
    from cron.jobs import get_cron_output_dir
    home = Path(os.environ["HERMES_HOME"]).resolve()
    store = Path(get_cron_output_dir()).resolve()
    assert store != home and home in store.parents, f"cron 存储不在隔离目录内：{store}"


@pytest.fixture(autouse=True)
def _gateway_session(monkeypatch):
    """每个用例从「干净的会话现场」开始：清空会话上下文 + 清掉环境变量兜底"""
    import tools.real_estate_cron_tools  # noqa: F401  注册 enable_cron / disable_cron
    monkeypatch.delenv("COCO_CHAT_ID", raising=False)
    reset_session_vars()
    _store_guard()
    yield
    reset_session_vars()


def _bind_gateway_turn(chat_id: str) -> str:
    """复刻网关一轮对话：绑定会话上下文，返回本轮的时间戳式会话编号"""
    session_id = new_session_id()
    set_session_vars(
        platform="feishu", source="feishu", chat_id=chat_id, chat_type="dm",
        chat_name=chat_id, user_id="ou_test_user",
        session_key=f"agent:main:feishu:dm:{chat_id}", session_id=session_id,
    )
    return session_id


def _dispatch(name: str, args: dict, **runtime):
    from tools.registry import registry
    return json.loads(registry.dispatch(name, args, **runtime))


def _jobs() -> dict:
    from cron.jobs import list_jobs
    return {j.get("name"): j for j in list_jobs(include_disabled=True)}


def test_gateway_turn_enables_cron_to_current_chat():
    """真实网关形态（只传会话编号 + 会话上下文已绑定）必须能开启，并推送到当前对话"""
    session_id = _bind_gateway_turn(CHAT_ID)
    out = _dispatch("enable_cron", {}, session_id=session_id, task_id=session_id)

    assert out.get("success") is True, out
    jobs = _jobs()
    assert set(jobs) == EXPECTED_JOBS, jobs
    for name, job in jobs.items():
        assert job.get("deliver") == f"feishu:{CHAT_ID}", f"{name}: {job}"
        if name in PURE_SCRIPT_JOBS:
            assert job.get("no_agent") is True, f"{name}: {job}"
        else:
            assert job.get("enabled_toolsets") == ["real_estate"], f"{name}: {job}"


def test_disable_cron_removes_the_jobs_it_enabled():
    """开关成对：开启后关闭必须清干净（避免重复任务越堆越多）"""
    session_id = _bind_gateway_turn(CHAT_ID)
    assert _dispatch("enable_cron", {}, session_id=session_id, task_id=session_id)["success"] is True
    assert _dispatch("disable_cron", {}, session_id=session_id, task_id=session_id)["success"] is True
    assert _jobs() == {}


def test_no_session_source_reports_plain_language_error():
    """任何来源都拿不到时必须如实报错，且不给用户看内部配置项"""
    out = _dispatch("enable_cron", {}, session_id=new_session_id(), task_id="t1")

    assert out.get("success") is False, out
    error = out.get("error", "")
    assert "没识别到当前对话" in error, error
    assert "开启定时任务" in error, error
    assert "COCO_CHAT_ID" not in error and ".env.db" not in error, error
    assert _jobs() == {}


def test_env_var_still_works_as_last_resort(monkeypatch):
    """环境变量兜底（老部署用 COCO_CHAT_ID）不能被改坏"""
    monkeypatch.setenv("COCO_CHAT_ID", OTHER_CHAT_ID)
    out = _dispatch("enable_cron", {}, session_id=new_session_id(), task_id="t1")

    assert out.get("success") is True, out
    assert _jobs()["coco_daily_report"].get("deliver") == f"feishu:{OTHER_CHAT_ID}"


def test_explicit_chat_id_argument_wins():
    """显式传入的会话 ID 优先于会话上下文（保留原有优先级）"""
    session_id = _bind_gateway_turn(CHAT_ID)
    out = _dispatch("enable_cron", {"chat_id": EXPLICIT_CHAT_ID}, session_id=session_id, task_id=session_id)

    assert out.get("success") is True, out
    assert _jobs()["coco_daily_report"].get("deliver") == f"feishu:{EXPLICIT_CHAT_ID}"


def test_legacy_composite_session_id_still_supported():
    """老形态（复合 session_id）继续可用，作为会话上下文之外的一道兜底"""
    out = _dispatch("enable_cron", {}, session_id=f"agent:main:feishu:dm:{CHAT_ID}", task_id="t1")

    assert out.get("success") is True, out
    assert _jobs()["coco_daily_report"].get("deliver") == f"feishu:{CHAT_ID}"


def test_enable_reply_uses_real_schedule_and_plain_names():
    """开启回执照任务表说话，且不把内部任务名/旧时间表甩给经纪人

    2026-09-24 修：回执曾写死「早报 09:00 / 午间 13:00 / 逾期每 30 分钟」，
    与重设计后的 5 条任务（无午间、无 30 分钟）对不上，经纪人会以为提醒没生效。
    """
    from agent.coco_cron import _AVAILABLE_JOBS, job_label

    session_id = _bind_gateway_turn(CHAT_ID)
    out = _dispatch("enable_cron", {}, session_id=session_id, task_id=session_id)
    message = out.get("message", "")

    assert out.get("success") is True, out
    for item in _AVAILABLE_JOBS:
        assert job_label(item[2]) in message, f"{item[2]} 的中文名没出现在回执里：{message}"
    for stale in ("13:00", "30 分钟", "midday", "coco_"):
        assert stale not in message, f"回执里还有旧口径/内部名 {stale!r}：{message}"


def test_enable_tool_description_follows_the_job_table():
    """工具说明里的时间表来自任务表，不许写死（否则模型照旧时间表回答）"""
    from agent.coco_cron import _AVAILABLE_JOBS, job_schedule_summary
    from tools.registry import registry

    import tools.real_estate_cron_tools  # noqa: F401  触发注册

    description = registry.get_schema("enable_cron")["description"]
    assert job_schedule_summary() in description, description
    assert description.count(" / ") == len(_AVAILABLE_JOBS) - 1, description  # 说明覆盖全部任务
    for stale in ("13:00", "30 分钟", "午间"):
        assert stale not in description, f"工具说明里还有旧口径 {stale!r}：{description}"

