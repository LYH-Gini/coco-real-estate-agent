"""Coco 首次对话的固定文案（2026-10-03 加）

为什么单独放一处：同一段欢迎语原先在网关开场白（`gateway/run_turn.py`）与飞书欢迎语
（`plugins/platforms/feishu/adapter.py`）里各写了一份，改一处漏一处 —— 实测事故：定时任务表
9 月 23 日改成 5 条后，两处欢迎语还印着旧的「午间检查 13:00 / 逾期提醒每 30 分钟」，
经纪人收到的第一条消息里就是过期的时间表。

这里只留一份，且**时间表从任务表现取**（`agent/coco_cron.job_schedule_summary()`），
以后加任务、改时间不用再改文案。示例里的客户与区域一律用占位符（不写真实城市/片区名）。

同步官方时这两个调用点会被官方版覆盖，按 patches/06、07 重新应用。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _schedule_text() -> str:
    """定时任务时间表（取自任务表；取不到就整段不带时间，绝不写死一份会过期的）"""
    try:
        from agent.coco_cron import job_schedule_summary
        return job_schedule_summary()
    except Exception as e:
        logger.debug("[Coco] 读取任务表失败，欢迎语省略时间表: %s", e)
        return ""


def welcome_text() -> str:
    """首次对话欢迎语（飞书欢迎语与网关开场白共用同一份）"""
    schedule = _schedule_text()
    tail = "另外提醒：定时任务默认关闭；需要时跟我说一句「开启定时任务」即可"
    if schedule:
        tail += f"（{schedule}）"
    return (
        "你好，我是 Coco，你的客户和房源管家。\n"
        "可以直接发给我：\n"
        "\"登记客户：X 先生，预算300万，想买XX区3室\"\n"
        "\"添加房源：XX小区，200万，110平，3室2厅\"\n"
        "我就能帮你建档、提醒跟进。目前你的数据库还是空的，先登记客户、添加房源，之后我就能自动帮你匹配房源。\n"
        f"{tail}。"
    )


def first_contact_note() -> str:
    """网关首次对话的 System note（模板里嵌同一段欢迎语，模型照抄输出）"""
    return (
        "[System note: This is the user's very first message ever. "
        "开场白严格按下面模板输出，不要增删字词：\n"
        f"'{welcome_text()}\n"
        "只输出这段话，不要加其他内容。]"
    )
