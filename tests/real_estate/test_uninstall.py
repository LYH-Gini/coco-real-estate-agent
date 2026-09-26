"""卸载（`coco uninstall`）—— 2026-09-21 拍板的形态。

约定：一条命令 → 三档菜单（1 保留数据 / 2 卸载+清状态 / 3 彻底清理含数据库 / 4 取消）
→ 输 `yes` 确认（与官方 hermes uninstall 同款，不再手输特殊词）。
官方卸载只管 Hermes 本体；Coco 专有的 crontab 备份行、coco 软链、字体、数据库由本脚本补。

这里全部用「假环境」验证：假 crontab、临时 HOME、假仓库（带 .env.db）、跳过官方卸载与删库，
所以测试**不会**动到真机器上的任何东西。
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "uninstall.sh"


def _fake_crontab(tmp_path):
    """假 crontab：-l 读文件，其它参数把 stdin 写进文件"""
    fn = tmp_path / "fake_crontab.sh"
    store = tmp_path / "crontab.txt"
    fn.write_text(
        '#!/usr/bin/env bash\n'
        'FILE="' + str(store) + '"\n'
        'if [ "${1:-}" = "-l" ]; then [ -f "$FILE" ] && cat "$FILE"; exit 0; fi\n'
        'if [ "${1:-}" = "-" ]; then cat > "$FILE"; exit 0; fi\n'
        'exit 0\n', encoding="utf-8")
    fn.chmod(fn.stat().st_mode | stat.S_IEXEC)
    return fn, store


def _fake_repo(tmp_path, db_url="postgresql://cocouser:secret@localhost:5432/real_estate"):
    repo = tmp_path / "fake-repo"
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    (repo / ".env.db").write_text(f"DATABASE_URL={db_url}\n", encoding="utf-8")
    return repo


def _env(tmp_path, repo, crontab_bin, home, extra=None):
    env = dict(os.environ)
    env.update({
        "COCO_UNINSTALL_REPO_ROOT": str(repo),
        "COCO_UNINSTALL_HOME": str(home),
        "COCO_UNINSTALL_CRONTAB": str(crontab_bin),
        "COCO_UNINSTALL_SKIP_OFFICIAL": "1",
        "COCO_UNINSTALL_SKIP_DB": "1",
        "COCO_UNINSTALL_BACKUP_CMD": "true",     # 备份成功但什么都不做
        "COCO_UNINSTALL_ALLOW_MENU": "1",
    })
    env.update(extra or {})
    return env


def _run(args, env, stdin_text=None):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True,
                          env=env, input=stdin_text, timeout=120)


class TestDryRunAndModes:
    def test_dry_run_changes_nothing(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, store = _fake_crontab(tmp_path)
        home = tmp_path / "home"; home.mkdir()
        store.write_text("0 2 * * * cd /x && python3 scripts/backup_db.py backup\n# 我的其它任务\n")
        link = home / ".local" / "bin"; link.mkdir(parents=True)
        (link / "coco").symlink_to(repo / "scripts" / "coco.sh")

        r = _run(["--mode", "3", "--dry-run"], _env(tmp_path, repo, crontab_bin, home))
        out = r.stdout
        assert r.returncode == 0, out + r.stderr
        assert "干跑" in out and "DROP DATABASE real_estate" in out, out
        assert "hermes uninstall --dry-run" in out, out
        assert (link / "coco").is_symlink(), "干跑不应删除软链"
        assert "backup_db.py" in store.read_text(), "干跑不应改写定时任务"

    def test_mode_1_keeps_database_and_state(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, _ = _fake_crontab(tmp_path)
        home = tmp_path / "home"; home.mkdir()
        r = _run(["--mode", "1", "--yes"], _env(tmp_path, repo, crontab_bin, home))
        out = r.stdout
        assert r.returncode == 0, out + r.stderr
        assert "保留数据" in out and "数据库保留：real_estate" in out, out
        assert "[6/6]" in out

    def test_header_shows_database_from_env(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, _ = _fake_crontab(tmp_path)
        home = tmp_path / "home"; home.mkdir()
        r = _run(["--mode", "1", "--dry-run"], _env(tmp_path, repo, crontab_bin, home))
        assert "real_estate" in r.stdout and "cocouser" in r.stdout, r.stdout


class TestMenu:
    def test_menu_lists_three_levels_and_cancel(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, _ = _fake_crontab(tmp_path)
        home = tmp_path / "home"; home.mkdir()
        r = _run(["--dry-run"], _env(tmp_path, repo, crontab_bin, home), stdin_text="4\n")
        out = r.stdout
        assert "1)" in out and "2)" in out and "3)" in out and "4)" in out, out

    def test_cancel_does_nothing(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, _ = _fake_crontab(tmp_path)
        home = tmp_path / "home"; home.mkdir()
        r = _run(["--mode", "1"], _env(tmp_path, repo, crontab_bin, home), stdin_text="4\n")
        assert r.returncode == 0
        assert "已取消" in r.stdout and "[1/6]" not in r.stdout, r.stdout

    def test_menu_choice_runs_after_yes(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, _ = _fake_crontab(tmp_path)
        home = tmp_path / "home"; home.mkdir()
        env = _env(tmp_path, repo, crontab_bin, home)
        r = _run([], env, stdin_text="1\nyes\n")
        out = r.stdout
        assert r.returncode == 0, out + r.stderr
        assert "[6/6]" in out, out

    def test_requires_typing_yes(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, _ = _fake_crontab(tmp_path)
        home = tmp_path / "home"; home.mkdir()
        r = _run([], _env(tmp_path, repo, crontab_bin, home), stdin_text="1\nno\n")
        assert "已取消" in r.stdout and "[1/6]" not in r.stdout, r.stdout

    def test_non_tty_without_mode_is_rejected(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, _ = _fake_crontab(tmp_path)
        home = tmp_path / "home"; home.mkdir()
        env = _env(tmp_path, repo, crontab_bin, home, extra={"COCO_UNINSTALL_ALLOW_MENU": "0"})
        r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env,
                           stdin=subprocess.DEVNULL, timeout=60)
        assert r.returncode != 0
        assert "不是交互终端" in (r.stdout + r.stderr)


class TestSafety:
    def test_backup_failure_aborts_before_touching_anything(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, store = _fake_crontab(tmp_path)
        store.write_text("0 2 * * * python3 scripts/backup_db.py backup\n")
        home = tmp_path / "home"; home.mkdir()
        env = _env(tmp_path, repo, crontab_bin, home, extra={"COCO_UNINSTALL_BACKUP_CMD": "false"})
        r = _run(["--mode", "1", "--yes"], env)
        assert r.returncode != 0
        assert "备份失败" in (r.stdout + r.stderr), r.stdout
        assert "[2/6]" not in r.stdout, "备份失败后不应继续卸载"
        assert "backup_db.py" in store.read_text(), "中止时不应改动定时任务"

    def test_invalid_mode_rejected(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, _ = _fake_crontab(tmp_path)
        home = tmp_path / "home"; home.mkdir()
        for bad in ("9", "abc"):
            r = _run(["--mode", bad, "--yes"], _env(tmp_path, repo, crontab_bin, home))
            assert r.returncode != 0 and "--mode 只能是" in (r.stdout + r.stderr)

    def test_help_lists_options(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, _ = _fake_crontab(tmp_path)
        home = tmp_path / "home"; home.mkdir()
        r = _run(["--help"], _env(tmp_path, repo, crontab_bin, home))
        assert r.returncode == 0, r.stderr
        out = r.stdout
        assert "--mode" in out and "--no-backup" in out and "--dry-run" in out, out


class TestCocoSpecificCleanup:
    def test_backup_cron_line_removed_others_kept(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, store = _fake_crontab(tmp_path)
        store.write_text("0 2 * * * cd /home/x && python3 scripts/backup_db.py backup >> log 2>&1\n"
                         "30 3 * * * /usr/local/bin/my-task\n")
        home = tmp_path / "home"; home.mkdir()
        r = _run(["--mode", "1", "--yes"], _env(tmp_path, repo, crontab_bin, home))
        assert r.returncode == 0, r.stdout + r.stderr
        after = store.read_text()
        assert "backup_db.py" not in after, after
        assert "my-task" in after, "不应删掉用户其它定时任务"
        assert "定时任务已清理" in r.stdout

    def test_coco_symlink_removed_only_when_ours(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, _ = _fake_crontab(tmp_path)
        home = tmp_path / "home"; (home / ".local" / "bin").mkdir(parents=True)
        ours = home / ".local" / "bin" / "coco"
        ours.symlink_to(repo / "scripts" / "coco.sh")
        r = _run(["--mode", "1", "--yes"], _env(tmp_path, repo, crontab_bin, home))
        assert r.returncode == 0, r.stdout + r.stderr
        assert not ours.exists(), "指向本仓库的 coco 软链应被删除"

        # 不是我们的同名命令必须留着
        home2 = tmp_path / "home2"; (home2 / ".local" / "bin").mkdir(parents=True)
        foreign = home2 / ".local" / "bin" / "coco"
        foreign.symlink_to("/bin/true")
        r2 = _run(["--mode", "1", "--yes"], _env(tmp_path, repo, crontab_bin, home2))
        assert foreign.is_symlink(), "指向别处的软链不应被删"
        assert "指向别处" in r2.stdout, r2.stdout


class TestCocoCli:
    def test_coco_help_lists_uninstall(self):
        r = subprocess.run(["bash", "scripts/coco.sh", "help"], capture_output=True, text=True, cwd=REPO_ROOT)
        assert r.returncode == 0
        assert "uninstall" in r.stdout

    def test_coco_uninstall_passthrough(self):
        r = subprocess.run(["bash", "scripts/coco.sh", "uninstall", "--help"],
                           capture_output=True, text=True, cwd=REPO_ROOT)
        assert r.returncode == 0, r.stderr
        assert "Coco 卸载" in r.stdout


class TestBackupPolicy:
    """备份策略（2026-09-21 定）：

    · 1/2 档（数据要留）→ **自动备份**，并把备份包路径 + 下载到本地电脑的命令打印出来；
    · 3 档（彻底清理，啥都不要）→ **不备份**，只提示"如需备份先执行 coco backup"；
    · --no-backup 跳过（1/2 档）；--backup 强制在 3 档也备份。
    """

    def _fake_backup(self, tmp_path):
        """假备份命令：跑成功并在 tmp 下留个标记文件"""
        marker = tmp_path / "backup-ran.marker"
        fn = tmp_path / "fake_backup.sh"
        fn.write_text("#!/usr/bin/env bash\ntouch " + str(marker) + "\n", encoding="utf-8")
        fn.chmod(fn.stat().st_mode | stat.S_IEXEC)
        return fn, marker

    def test_mode_3_does_not_backup(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, _ = _fake_crontab(tmp_path)
        home = tmp_path / "home"; home.mkdir()
        backup_cmd, marker = self._fake_backup(tmp_path)
        env = _env(tmp_path, repo, crontab_bin, home, extra={"COCO_UNINSTALL_BACKUP_CMD": str(backup_cmd)})
        r = _run(["--mode", "3", "--yes"], env)
        assert r.returncode == 0, r.stdout + r.stderr
        assert not marker.exists(), "选彻底清理时不应备份"
        assert "按所选档位不备份" in r.stdout, r.stdout
        assert "coco backup" in r.stdout, "应提示可手动备份"

    def test_mode_3_backup_flag_forces_backup(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, _ = _fake_crontab(tmp_path)
        home = tmp_path / "home"; home.mkdir()
        backup_cmd, marker = self._fake_backup(tmp_path)
        env = _env(tmp_path, repo, crontab_bin, home, extra={"COCO_UNINSTALL_BACKUP_CMD": str(backup_cmd)})
        r = _run(["--mode", "3", "--backup", "--yes"], env)
        assert r.returncode == 0, r.stdout + r.stderr
        assert marker.exists(), "--backup 应在 3 档也先备份"

    def test_mode_1_backs_up_and_prints_download_command(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, _ = _fake_crontab(tmp_path)
        home = tmp_path / "home"; home.mkdir()
        backup_cmd, marker = self._fake_backup(tmp_path)
        env = _env(tmp_path, repo, crontab_bin, home, extra={"COCO_UNINSTALL_BACKUP_CMD": str(backup_cmd)})
        r = _run(["--mode", "1", "--yes"], env)
        out = r.stdout
        assert r.returncode == 0, out + r.stderr
        assert marker.exists(), "1 档应自动备份"
        assert "coco_uninstall_backup_" in out, out
        assert "下载到本地电脑" in out and "scp " in out, out
        assert "请用上面的 scp 命令下载到电脑" in out, out

    def test_mode_2_backs_up(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, _ = _fake_crontab(tmp_path)
        home = tmp_path / "home"; home.mkdir()
        backup_cmd, marker = self._fake_backup(tmp_path)
        env = _env(tmp_path, repo, crontab_bin, home, extra={"COCO_UNINSTALL_BACKUP_CMD": str(backup_cmd)})
        r = _run(["--mode", "2", "--yes"], env)
        assert r.returncode == 0, r.stdout + r.stderr
        assert marker.exists(), "2 档应自动备份"

    def test_no_backup_flag_skips_in_mode_1(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, _ = _fake_crontab(tmp_path)
        home = tmp_path / "home"; home.mkdir()
        backup_cmd, marker = self._fake_backup(tmp_path)
        env = _env(tmp_path, repo, crontab_bin, home, extra={"COCO_UNINSTALL_BACKUP_CMD": str(backup_cmd)})
        r = _run(["--mode", "1", "--no-backup", "--yes"], env)
        assert r.returncode == 0, r.stdout + r.stderr
        assert not marker.exists(), "--no-backup 应跳过备份"
        assert "已跳过备份" in r.stdout

    def test_menu_text_states_backup_policy(self, tmp_path):
        repo = _fake_repo(tmp_path)
        crontab_bin, _ = _fake_crontab(tmp_path)
        home = tmp_path / "home"; home.mkdir()
        r = _run(["--dry-run"], _env(tmp_path, repo, crontab_bin, home), stdin_text="4\n")
        out = r.stdout
        assert "会自动备份" in out and "【不备份】" in out, out


class TestDocsHaveBackupRestoreUninstall:
    """README 必须有「备份 / 恢复 / 卸载」三小节且命令可用（2026-09-21 要求），
    同时不得再用会污染终端的老写法（`cd ... && ls` / `cd ... && tar`）。"""

    def test_readmes_have_three_sections(self):
        for rel in ("README.md", "README.zh-CN.md"):
            t = (REPO_ROOT / rel).read_text(encoding="utf-8")
            for sec in ("### 数据库备份", "### 恢复", "### 卸载"):
                assert sec in t, f"{rel} 缺少小节：{sec}"
            assert "coco uninstall" in t, f"{rel} 未给出卸载命令"
            assert "coco backup" in t, f"{rel} 未给出手动备份命令"

    def test_readmes_use_terminal_safe_commands(self):
        """规则：不要用裸 `cd`（会改变用户终端提示符），必须包进括号子 shell；
        查看备份用 `ls -la ~/backups/real_estate/`（不需要进目录）。"""
        for rel in ("README.md", "README.zh-CN.md"):
            t = (REPO_ROOT / rel).read_text(encoding="utf-8")
            for line in t.split("\n"):
                if "cd ~/backups/real_estate" not in line:
                    continue
                stripped = line.strip()
                assert stripped.startswith("("), f"{rel} 的 cd 必须包进括号子 shell：{stripped}"
                assert "ls -la" not in stripped, f"{rel} 查看备份不需要进目录：{stripped}"

    def test_coco_help_lists_command_set(self):
        r = subprocess.run(["bash", "scripts/coco.sh", "help"], capture_output=True, text=True, cwd=REPO_ROOT)
        out = r.stdout
        for cmd in ("version", "check", "backup", "uninstall"):
            assert cmd in out, out
        # 帮助文案要与实际行为一致：只有 1/2 档备份
        assert "1/2 档" in out, out
