"""定时任务的同步与脚本安装（2026-09-23 定时任务重设计）

问题：定时任务的定义是**注册时写进任务记录**的（cron/jobs.json），代码里改文案/改时间
不会覆盖已经存在的任务——注册路径遇到同名任务一律跳过，于是"改了代码，早报还按旧口径报"。
真实教训：早报指令原文只点"S/A级客户状态"，改成四级全列后，经纪人机器上那条老任务
仍是旧文案，早报继续漏 B 级。

修法：`_sync_coco_jobs()` 在注册/开启时把名字与 `_AVAILABLE_JOBS` 一致的任务对齐到代码
最新定义（提示词、时间、脚本、是否纯脚本），并删掉废弃任务；同时把 cron 脚本装到
HERMES_HOME/scripts/（官方调度器只在那里执行脚本）。
"""
from __future__ import annotations

import cron.jobs as cron_jobs

import agent.coco_cron as cc


def _job(name: str):
    return next(item for item in cc._AVAILABLE_JOBS if item[2] == name)


def _record(item, prompt=None, expr=None, script=None, no_agent=False):
    """按 cron 存储的真实形状造一条任务记录"""
    spec = item[3] if isinstance(item[3], dict) else {}
    return {
        "id": f"id-{item[2]}",
        "name": item[2],
        "prompt": prompt if prompt is not None else item[3] if isinstance(item[3], str) else "",
        "schedule": {"kind": "cron", "expr": expr or item[1]},
        "script": script if script is not None else spec.get("script"),
        "no_agent": bool(no_agent or spec.get("no_agent")),
    }


def _prompt_of(item):
    """该任务在代码里的提示词（脚本型任务没有提示词）"""
    spec = item[3]
    return spec if isinstance(spec, str) else spec.get("prompt")

class TestJobSync:
    def test_stale_prompt_is_refreshed(self, monkeypatch):
        item = _job("coco_daily_report")
        calls = []
        monkeypatch.setattr(cc, "_install_cron_scripts", lambda: [])
        monkeypatch.setattr(cron_jobs, "list_jobs",
                           lambda include_disabled=False: [_record(item, prompt="旧文案：只点 S/A 级")])
        monkeypatch.setattr(cron_jobs, "update_job",
                           lambda jid, updates: calls.append((jid, updates)) or {"id": jid})

        result = cc._sync_coco_jobs()
        assert result["prompts"] == [item[2]]
        assert calls == [(f"id-{item[2]}", {"prompt": item[3]})]

    def test_matching_job_is_left_alone(self, monkeypatch):
        item = _job("coco_daily_report")
        calls = []
        monkeypatch.setattr(cc, "_install_cron_scripts", lambda: [])
        monkeypatch.setattr(cron_jobs, "list_jobs", lambda include_disabled=False: [_record(item)])
        monkeypatch.setattr(cron_jobs, "update_job", lambda jid, updates: calls.append(jid))

        assert cc._sync_coco_jobs()["prompts"] == []
        assert calls == []

    def test_schedule_change_is_applied(self, monkeypatch):
        """时间改了也要跟着变（不然"取消午间检查、哨兵改 10/17 点"在已装机器上不生效）"""
        item = _job("coco_overdue_sentinel")
        calls = []
        monkeypatch.setattr(cc, "_install_cron_scripts", lambda: [])
        monkeypatch.setattr(cron_jobs, "list_jobs",
                           lambda include_disabled=False: [_record(item, expr="*/30 * * * *")])
        monkeypatch.setattr(cron_jobs, "update_job",
                           lambda jid, updates: calls.append(updates) or {"id": jid})

        result = cc._sync_coco_jobs()
        assert result["schedules"] == [item[2]]
        assert calls[0]["schedule"] == item[1]

    def test_script_job_script_change_is_applied(self, monkeypatch):
        item = _job("coco_overdue_sentinel")
        calls = []
        monkeypatch.setattr(cc, "_install_cron_scripts", lambda: [])
        monkeypatch.setattr(cron_jobs, "list_jobs",
                           lambda include_disabled=False: [_record(item, script="old_watchdog.py")])
        monkeypatch.setattr(cron_jobs, "update_job",
                           lambda jid, updates: calls.append(updates) or {"id": jid})

        result = cc._sync_coco_jobs()
        assert result["scripts"] == [item[2]]
        assert calls[0]["script"] == "coco_cron_overdue.py"

    def test_only_coco_jobs_are_touched(self, monkeypatch):
        item = _job("coco_daily_report")
        calls = []
        monkeypatch.setattr(cc, "_install_cron_scripts", lambda: [])
        monkeypatch.setattr(
            cron_jobs, "list_jobs",
            lambda include_disabled=False: [
                {"id": "id-own", "name": "my_own_job", "prompt": "经纪人自己建的任务",
                 "schedule": {"kind": "cron", "expr": "0 7 * * *"}},
                _record(item, prompt="旧文案"),
            ])
        monkeypatch.setattr(cron_jobs, "update_job",
                           lambda jid, updates: calls.append(jid) or {"id": jid})
        monkeypatch.setattr(cron_jobs, "remove_job", lambda jid: calls.append(("remove", jid)))

        assert cc._sync_coco_jobs()["prompts"] == [item[2]]
        assert calls == [f"id-{item[2]}"]  # 经纪人自己的任务没被碰

    def test_absent_jobs_are_not_touched(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cc, "_install_cron_scripts", lambda: [])
        monkeypatch.setattr(cron_jobs, "list_jobs", lambda include_disabled=False: [])
        monkeypatch.setattr(cron_jobs, "update_job", lambda jid, updates: calls.append(jid))

        result = cc._sync_coco_jobs()
        assert result["prompts"] == [] and calls == []

    def test_deprecated_jobs_are_removed(self, monkeypatch):
        """老版本注册的 13:00 午间检查 / 每 30 分钟逾期检查必须清掉，否则新旧一起发"""
        removed = []
        monkeypatch.setattr(cc, "_install_cron_scripts", lambda: [])
        monkeypatch.setattr(
            cron_jobs, "list_jobs",
            lambda include_disabled=False: [
                {"id": "id-mid", "name": "coco_midday_check", "prompt": "午间检查",
                 "schedule": {"kind": "cron", "expr": "0 13 * * *"}},
                {"id": "id-30", "name": "coco_overdue_check", "prompt": "每 30 分钟",
                 "schedule": {"kind": "cron", "expr": "*/30 * * * *"}},
            ])
        monkeypatch.setattr(cron_jobs, "remove_job", lambda jid: removed.append(jid))
        monkeypatch.setattr(cron_jobs, "update_job", lambda jid, updates: {"id": jid})

        result = cc._sync_coco_jobs()
        assert result["removed"] == ["coco_midday_check", "coco_overdue_check"]
        assert removed == ["id-mid", "id-30"]

    def test_store_failure_does_not_raise(self, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("cron store 挂了")

        monkeypatch.setattr(cc, "_install_cron_scripts", lambda: [])
        monkeypatch.setattr(cron_jobs, "list_jobs", boom)
        assert cc._sync_coco_jobs()["prompts"] == []

    def test_vanished_job_is_reported_not_raised(self, monkeypatch):
        item = _job("coco_daily_report")
        monkeypatch.setattr(cc, "_install_cron_scripts", lambda: [])
        monkeypatch.setattr(cron_jobs, "list_jobs",
                           lambda include_disabled=False: [_record(item, prompt="旧文案")])
        monkeypatch.setattr(cron_jobs, "update_job", lambda jid, updates: None)

        assert cc._sync_coco_jobs()["prompts"] == []


class TestScriptInstall:
    """cron 脚本只能放在 HERMES_HOME/scripts/ 下执行（官方调度器护栏）"""

    def test_scripts_are_installed_as_forwarders(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cc, "_marker_path", lambda: str(tmp_path / ".coco_cron_registered"))
        installed = cc._install_cron_scripts()
        assert set(installed) == set(cc._SCRIPT_FILES)
        shim = tmp_path / "scripts" / "coco_cron_overdue.py"
        text = shim.read_text(encoding="utf-8")
        assert "runpy.run_path" in text
        assert str(cc.Path(cc.__file__).resolve().parents[1]) in text  # 指向仓库（源文件在仓库里）

    def test_install_is_idempotent(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cc, "_marker_path", lambda: str(tmp_path / ".coco_cron_registered"))
        cc._install_cron_scripts()
        assert cc._install_cron_scripts() == []

    def test_installed_shim_actually_runs(self, monkeypatch, tmp_path):
        """真正把转发入口跑一遍（干净库 → 空输出 = 静默，这是哨兵不刷屏的前提）"""
        import os
        import subprocess
        import sys

        monkeypatch.setattr(cc, "_marker_path", lambda: str(tmp_path / ".coco_cron_registered"))
        cc._install_cron_scripts()
        shim = tmp_path / "scripts" / "coco_cron_overdue.py"
        db_file = tmp_path / "shim.db"
        env = dict(os.environ, HERMES_HOME=str(tmp_path), DATABASE_URL=f"sqlite:///{db_file}")
        proc = subprocess.run([sys.executable, str(shim)], capture_output=True, text=True,
                              env=env, timeout=120)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "", proc.stdout


class TestJobTable:
    """任务表本身：5 条、时间与"高频检查脚本化"路线"""

    def test_five_jobs_with_expected_times(self):
        table = {item[2]: item[1] for item in cc._AVAILABLE_JOBS}
        assert table == {
            "coco_daily_report": "0 9 * * *",
            "coco_overdue_sentinel": "0 10,17 * * *",
            "coco_opportunity": "30 12 * * *",
            "coco_day_end": "30 20 * * *",
            "coco_weekly_report": "30 8 * * 1",
        }
        assert "coco_midday_check" not in table and "coco_overdue_check" not in table

    def test_overdue_sentinel_is_pure_script(self):
        spec = _job("coco_overdue_sentinel")[3]
        assert spec["no_agent"] is True and spec["script"] == "coco_cron_overdue.py"
        assert "prompt" not in spec  # 不叫模型 = 不烧 token

    def test_report_jobs_collect_data_with_a_script(self):
        for name in ("coco_daily_report", "coco_opportunity", "coco_day_end", "coco_weekly_report"):
            spec = _job(name)[3]
            if isinstance(spec, dict):
                assert spec.get("script") and spec.get("prompt")
                assert spec.get("no_agent") is not True


class TestRegistrationWiring:
    def test_register_path_syncs_and_reports(self, monkeypatch, tmp_path):
        monkeypatch.setenv("COCO_ENABLE_CRON", "1")
        marker = tmp_path / ".coco_cron_registered"
        marker.write_text("1", encoding="utf-8")
        monkeypatch.setattr(cc, "_marker_path", lambda: str(marker))
        monkeypatch.setattr(cc, "_cron_store_ready", lambda: True)
        monkeypatch.setattr(cc, "_job_exists", lambda name: True)
        monkeypatch.setattr(cc, "_install_cron_scripts", lambda: [])
        monkeypatch.setattr(
            cron_jobs, "list_jobs",
            lambda include_disabled=False: [_record(item, prompt="旧文案") for item in cc._AVAILABLE_JOBS])
        monkeypatch.setattr(cron_jobs, "update_job", lambda jid, updates: {"id": jid})

        result = cc.register_coco_cron_jobs("oc_test")

        assert set(result["synced"]["prompts"]) == {
            item[2] for item in cc._AVAILABLE_JOBS if _prompt_of(item) is not None}
        assert result["registered"] == []

    def test_sync_runs_even_when_registration_is_off(self, monkeypatch):
        """任务已存在（例如经纪人是靠工具开启的）时，环境开关没开也要对齐"""
        monkeypatch.delenv("COCO_ENABLE_CRON", raising=False)
        monkeypatch.setattr(cc, "_install_cron_scripts", lambda: [])
        monkeypatch.setattr(
            cron_jobs, "list_jobs",
            lambda include_disabled=False: [_record(item, prompt="旧文案") for item in cc._AVAILABLE_JOBS])
        monkeypatch.setattr(cron_jobs, "update_job", lambda jid, updates: {"id": jid})

        result = cc.register_coco_cron_jobs("oc_test")

        assert set(result["synced"]["prompts"]) == {
            item[2] for item in cc._AVAILABLE_JOBS if _prompt_of(item) is not None}
        assert result["registered"] == []

    def test_enable_path_syncs_too(self, monkeypatch):
        monkeypatch.setattr(cc, "_cron_store_ready", lambda: True)
        monkeypatch.setattr(cc, "_job_exists", lambda name: True)
        monkeypatch.setattr(cc, "_install_cron_scripts", lambda: [])
        monkeypatch.setattr(
            cron_jobs, "list_jobs",
            lambda include_disabled=False: [_record(item, prompt="旧文案") for item in cc._AVAILABLE_JOBS])
        monkeypatch.setattr(cron_jobs, "update_job", lambda jid, updates: {"id": jid})

        result = cc.enable_coco_cron_jobs("oc_test")

        assert set(result["synced"]["prompts"]) == {
            item[2] for item in cc._AVAILABLE_JOBS if _prompt_of(item) is not None}

    def test_disable_removes_current_and_deprecated(self, monkeypatch):
        removed = []
        monkeypatch.setattr(
            cron_jobs, "list_jobs",
            lambda include_disabled=False: [
                {"id": "id-daily", "name": "coco_daily_report"},
                {"id": "id-old", "name": "coco_overdue_check"},
                {"id": "id-other", "name": "my_own_job"},
            ])
        monkeypatch.setattr(cron_jobs, "remove_job", lambda jid: removed.append(jid))

        result = cc.disable_coco_cron_jobs()
        assert set(result["removed"]) == {"coco_daily_report", "coco_overdue_check"}
        assert removed == ["id-daily", "id-old"]  # 经纪人自己的任务留下
