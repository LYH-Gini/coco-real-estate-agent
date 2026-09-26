"""
Coco 房产工具 - 定时任务自助开关（2026-08-12 加）
经纪人一句话开启/关闭定时提醒（早报/逾期提醒/机会提醒/收工小结/周报），无需操作服务器。
"""
import json
import logging
import os

from tools.registry import registry

logger = logging.getLogger(__name__)


def _get_chat_id(task_id: str = None, **kwargs) -> str:
    """获取飞书会话 ID：调用方指定 > 框架会话上下文 > session_id 提取 > task_id > 环境变量

    2026-09-21 修（真实事故）：原实现只从 session_id 里切 oc_/ou_ 段，前提是那个值是
    ``agent:main:feishu:dm:oc_xxx`` 形态；但网关交给工具的是时间戳式**会话编号**
    （如 ``20260921_213802_4bf4a40d``），一段都切不出来 → 真实飞书会话里「开启定时任务」
    永远落到 COCO_CHAT_ID 兜底（要改 .env.db 并重启进程才生效）。真正的会话地址由网关在
    每轮对话开始时绑进会话上下文（``HERMES_SESSION_CHAT_ID``），读它即可，无需任何手工配置。
    """
    for key in ('chat_id', 'channel_id', 'conversation_id'):
        v = kwargs.get(key)
        if v:
            return str(v)
    try:
        from gateway.session_context import get_session_env
        v = get_session_env('HERMES_SESSION_CHAT_ID', '')
        if v:
            return str(v)
    except Exception as e:  # 官方重构或脱离网关运行时，不能连累开关功能
        logger.debug("[Coco] 读取会话上下文失败，改用其它来源: %s", e)
    sid = kwargs.get('session_id')
    if sid:
        for seg in str(sid).split(':'):
            if seg.startswith(('oc_', 'ou_')):
                return seg
    if task_id and str(task_id).startswith(('oc_', 'ou_')):
        return task_id
    return os.getenv('COCO_CHAT_ID', '')


def _schedule_text() -> str:
    """定时任务时间表（取自任务表，任务表改了这里自动跟上）"""
    try:
        from agent.coco_cron import job_schedule_summary
        return job_schedule_summary()
    except Exception as e:  # 取不到也不能让开关功能挂
        logger.debug("[Coco] 读取任务表失败，用兜底时间表: %s", e)
        return "09:00 每天 上班早报 / 10:00、17:00 逾期提醒 / 12:30 每天 机会提醒 / 20:30 每天 收工小结 / 周一 08:30 周报"


def _job_labels(names) -> str:
    """任务名 → 给经纪人看的名字（不把内部名暴露给经纪人）"""
    try:
        from agent.coco_cron import job_label
    except Exception:
        return "、".join(names)
    return "、".join(job_label(n) for n in names)


def enable_cron(task_id: str = None, **kwargs) -> str:
    """开启定时任务（时间表见 _schedule_text）"""
    from agent.coco_cron import enable_coco_cron_jobs
    chat_id = _get_chat_id(task_id, **kwargs)
    if not chat_id:
        logger.warning(
            "[Coco] enable_cron 取不到推送会话（session_id=%r task_id=%r，会话上下文为空）",
            kwargs.get('session_id'), task_id,
        )
        return json.dumps({
            "success": False,
            "error": "没识别到当前对话，无法确定提醒发到哪里。请在飞书里重新发一句「开启定时任务」再试。",
        }, ensure_ascii=False)
    result = enable_coco_cron_jobs(chat_id)
    registered = result.get('registered', [])
    skipped = result.get('skipped', [])
    if result.get('error'):
        return json.dumps({"success": False, "error": result['error']}, ensure_ascii=False)
    if registered:
        return json.dumps({
            "success": True,
            "enabled": registered,
            "message": f"定时任务已开启：{_job_labels(registered)}（{_schedule_text()}）",
        }, ensure_ascii=False)
    if skipped:
        # 区分"已存在跳过"与"注册失败"（2026-08-13 加：注册失败必须如实报错，
        # 不能报"已经在运行中"误导——真实案例：本地缺 croniter 时全部注册失败仍报成功）
        errors = [s for s in skipped if s.endswith('(error)')]
        if errors:
            return json.dumps({
                "success": False,
                "error": f"定时任务注册失败：{'、'.join(errors)}，请稍后重试或检查服务日志",
            }, ensure_ascii=False)
        return json.dumps({
            "success": True,
            "enabled": [],
            "message": "定时任务已经在运行中，无需重复开启",
        }, ensure_ascii=False)
    return json.dumps({"success": False, "error": "没有可开启的定时任务"}, ensure_ascii=False)


def disable_cron(task_id: str = None) -> str:
    """关闭定时任务"""
    from agent.coco_cron import disable_coco_cron_jobs
    result = disable_coco_cron_jobs()
    removed = result.get('removed', [])
    if result.get('error'):
        return json.dumps({"success": False, "error": result['error']}, ensure_ascii=False)
    if removed:
        return json.dumps({
            "success": True,
            "disabled": removed,
            "message": f"定时任务已关闭：{_job_labels(removed)}",
        }, ensure_ascii=False)
    return json.dumps({
        "success": True,
        "disabled": [],
        "message": "当前没有运行中的定时任务",
    }, ensure_ascii=False)


registry.register(
    name="enable_cron",
    toolset="real_estate",
    schema={"name": "enable_cron", "description": f"开启定时任务（{_schedule_text()}），经纪人要求开启定时提醒时调用", "parameters": {
        "type": "object",
        "properties": {},
    }},
    handler=lambda args, **kw: enable_cron(
        **{k: v for k, v in {
            'task_id': kw.get('task_id'),
            'session_id': kw.get('session_id'),
            'chat_id': kw.get('chat_id'),
            'channel_id': kw.get('channel_id'),
            'conversation_id': kw.get('conversation_id'),
        }.items() if v is not None and k not in args},
        **args,
    ),
)

registry.register(
    name="disable_cron",
    toolset="real_estate",
    schema={"name": "disable_cron", "description": f"关闭定时任务（早报/逾期提醒/机会提醒/收工小结/周报全部停止），经纪人要求关闭定时提醒时调用", "parameters": {
        "type": "object",
        "properties": {},
    }},
    handler=lambda args, **kw: disable_cron(**args),
)
