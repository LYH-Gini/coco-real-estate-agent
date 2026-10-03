"""首次对话文案：时间表跟着任务表走 + 示例用占位符（2026-10-03）

真实事故：定时任务表 9 月 23 日改成 5 条（取消午间检查与每 30 分钟提醒）后，网关开场白与
飞书欢迎语里还印着旧时间表「早报 09:00 / 午间检查 13:00 / 逾期提醒每 30 分钟」——经纪人收到的
第一条消息就是过期信息；而且两处各写了一份，改一处漏一处。

现在两处共用 `agent/coco_welcome.py` 一份文案，时间表从任务表现取
（`agent.coco_cron.job_schedule_summary()`），示例里的人名与区域一律用占位符。
"""
from __future__ import annotations

from pathlib import Path

import agent.coco_welcome as cw
from agent.coco_cron import _AVAILABLE_JOBS, job_label, job_schedule_summary
from agent.coco_welcome import first_contact_note, welcome_text

STALE_SCHEDULE = ("午间检查", "13:00", "每 30 分钟", "30 分钟")

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestScheduleFollowsTheJobTable:
    def test_welcome_prints_the_current_table(self):
        assert job_schedule_summary() in welcome_text(), welcome_text()

    def test_every_job_shows_up(self):
        text = welcome_text()
        missing = [item[2] for item in _AVAILABLE_JOBS if job_label(item[2]) not in text]
        assert not missing, f"这些任务没出现在欢迎语里：{missing}"

    def test_no_stale_schedule(self):
        for text in (welcome_text(), first_contact_note()):
            for stale in STALE_SCHEDULE:
                assert stale not in text, f"欢迎语里还有旧时间表 {stale!r}：{text}"

    def test_task_table_change_flows_through(self, monkeypatch):
        """任务表改了文案跟着改（挡的是\"又抄一份写死\"）"""
        monkeypatch.setattr(cw, "_schedule_text", lambda: "XX 时间表")
        assert "XX 时间表" in cw.welcome_text()

    def test_missing_table_does_not_claim_a_schedule(self, monkeypatch):
        """读不到任务表时就整段不带时间，绝不印一份可能过期的"""
        monkeypatch.setattr(cw, "_schedule_text", lambda: "")
        text = cw.welcome_text()
        assert "开启定时任务」即可。" in text, text
        for stale in STALE_SCHEDULE:
            assert stale not in text, text


class TestFirstContactNote:
    def test_note_carries_the_same_text(self):
        assert welcome_text() in first_contact_note()

    def test_note_keeps_the_template_wrapper(self):
        note = first_contact_note()
        assert "不要增删字词" in note and "只输出这段话" in note


class TestPlaceholdersInExamples:
    def test_examples_use_placeholders(self):
        text = welcome_text()
        assert '"登记客户：X 先生，预算300万，想买XX区3室"' in text, text
        assert "XX小区" in text, text

    def test_no_real_place_or_person_names(self):
        text = welcome_text()
        for name in ("美兰", "海口", "张先生"):
            assert name not in text, f"示例里还有具体地名/人名 {name!r}：{text}"


class TestHealthcheckUsesTheSameSource:
    def test_healthcheck_has_no_stale_schedule(self):
        src = (REPO_ROOT / "scripts" / "healthcheck.py").read_text(encoding="utf-8")
        for stale in STALE_SCHEDULE:
            assert stale not in src, f"体检输出里还有旧时间表 {stale!r}"
        assert "job_schedule_summary" in src, "体检的时间表应取自任务表"
