"""`coco` 命令入口的回归测试（2026-09-21 命令统一）。

覆盖：
  · 转发组（model / setup / gateway / pairing）用 exec 原样转发给官方 hermes 程序；
  · 运维组（check / backup / backups / restore / data-clean / images-recover / update / uninstall）行为正确；
  · 危险操作要输 yes 才执行；缺 Python 环境时报错清楚（不抛裸 shell 错）；
  · help 分组列出命令集合 —— 与文档口径一致。

全部在临时假仓库里跑（假 VERSION、假 venv/bin/hermes），**不会动真机器**。
"""
import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _fake_install(tmp_path, hermes_exit=0):
    """搭一个假安装目录：scripts/coco.sh + VERSION + venv/bin/{python,hermes}"""
    root = tmp_path / "fake-coco"
    (root / "scripts").mkdir(parents=True)
    (root / "venv" / "bin").mkdir(parents=True)
    for name in ("coco.sh", "coco_channel.sh"):
        src = SCRIPTS / name
        if src.exists():
            (root / "scripts" / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    (root / "scripts" / "coco.sh").chmod(0o755)
    (root / "VERSION").write_text("9.9.9-1\n", encoding="utf-8")

    # 假官方程序：打印收到的参数与标记，按需返回退出码
    hermes = root / "venv" / "bin" / "hermes"
    hermes.write_text(
        "#!/usr/bin/env bash\n"
        'echo "FAKE-HERMES args: $*"\n'
        f"exit {hermes_exit}\n", encoding="utf-8")
    hermes.chmod(hermes.stat().st_mode | stat.S_IEXEC)

    # 假 python：打印收到的参数（验证 backup / restore 的参数映射）
    py = root / "venv" / "bin" / "python"
    py.write_text(
        "#!/usr/bin/env bash\n"
        'echo "FAKE-PY args: $*"\n', encoding="utf-8")
    py.chmod(py.stat().st_mode | stat.S_IEXEC)
    return root


def _run(root, args, stdin_text=None, env=None):
    e = dict(os.environ); e.update(env or {})
    return subprocess.run(["bash", str(root / "scripts" / "coco.sh"), *args],
                          capture_output=True, text=True, input=stdin_text, env=e, timeout=60)


class TestForwarding:
    """安装配置类命令转发给官方程序（用 exec，退出码/参数都要原样）"""

    def test_model_and_setup_forward(self, tmp_path):
        root = _fake_install(tmp_path)
        for cmd in ("model", "setup"):
            r = _run(root, [cmd])
            assert r.returncode == 0, r.stderr
            assert f"FAKE-HERMES args: {cmd}" in r.stdout, r.stdout

    def test_gateway_and_pairing_forward(self, tmp_path):
        root = _fake_install(tmp_path)
        r = _run(root, ["gateway", "install"])
        assert "FAKE-HERMES args: gateway install" in r.stdout, r.stdout
        r = _run(root, ["pairing", "approve", "feishu", "1234"])
        assert "FAKE-HERMES args: pairing approve feishu 1234" in r.stdout, r.stdout

    def test_service_lifecycle_forward(self, tmp_path):
        root = _fake_install(tmp_path)
        for short in ("start", "stop", "restart"):
            r = _run(root, [short])
            assert f"FAKE-HERMES args: gateway {short}" in r.stdout, r.stdout

    def test_exit_code_is_forwarded(self, tmp_path):
        root = _fake_install(tmp_path, hermes_exit=7)
        r = _run(root, ["setup"])
        assert r.returncode == 7, "官方程序的退出码必须原样透出"

    def test_missing_hermes_reports_clearly(self, tmp_path):
        """假程序删掉、且 PATH 里没有别的 hermes 时，必须明确报错（别假装成功）"""
        import shutil as _shutil

        root = _fake_install(tmp_path)
        (root / "venv" / "bin" / "hermes").unlink()
        bindir = tmp_path / "bin"
        bindir.mkdir()
        for tool in ("bash", "readlink", "tr", "git", "journalctl"):
            src = _shutil.which(tool)
            if src:
                (bindir / tool).symlink_to(src)
        r = _run(root, ["model"], env={"PATH": str(bindir)})
        assert r.returncode != 0
        assert "找不到官方 hermes 程序" in (r.stdout + r.stderr), r.stdout + r.stderr


class TestLocalCommands:
    """我们自己的命令：参数要正确映射到脚本"""

    def test_backup_and_check_map_to_scripts(self, tmp_path):
        root = _fake_install(tmp_path)
        r = _run(root, ["backup", "--force"])
        assert "backup_db.py backup --force" in r.stdout, r.stdout
        r = _run(root, ["check"])
        assert "healthcheck.py" in r.stdout, r.stdout
        r = _run(root, ["backups"])
        assert "backup_db.py list" in r.stdout, r.stdout

    def test_update_maps_to_update_script(self, tmp_path):
        root = _fake_install(tmp_path)
        (root / "scripts" / "update.sh").write_text("#!/usr/bin/env bash\necho FAKE-UPDATE ran\n", encoding="utf-8")
        r = _run(root, ["update"])
        assert "FAKE-UPDATE ran" in r.stdout, r.stdout

    def test_restore_requires_confirmation(self, tmp_path):
        root = _fake_install(tmp_path)
        r = _run(root, ["restore", "--file", "x.dump"], stdin_text="no\n")
        assert "已取消" in r.stdout and "FAKE-PY" not in r.stdout, r.stdout
        r = _run(root, ["restore", "--file", "x.dump"], stdin_text="yes\n")
        assert "backup_db.py restore --restore-file x.dump" in r.stdout, r.stdout

    def test_restore_migration_maps_correctly(self, tmp_path):
        root = _fake_install(tmp_path)
        r = _run(root, ["restore", "--migration", "/root/m.tar.gz", "--yes"])
        assert "backup_db.py restore_migration --migration-tar /root/m.tar.gz" in r.stdout, r.stdout

    def test_data_clean_maps_to_script(self, tmp_path):
        """data-clean 把参数原样交给 data_clean.py（本机不碰真数据，只验映射）"""
        root = _fake_install(tmp_path)
        r = _run(root, ["data-clean", "--dry-run"])
        assert "data_clean.py --dry-run" in r.stdout, r.stdout
        r = _run(root, ["data-clean", "--kind", "all", "--yes"])
        assert "data_clean.py --kind all --yes" in r.stdout, r.stdout

    def test_images_recover_maps_to_script(self, tmp_path):
        """images-recover 把参数原样交给 recover_property_images.py（本机不碰真数据，只验映射）"""
        root = _fake_install(tmp_path)
        r = _run(root, ["images-recover"])
        assert "recover_property_images.py" in r.stdout, r.stdout
        r = _run(root, ["images-recover", "--apply"])
        assert "recover_property_images.py --apply" in r.stdout, r.stdout

    def test_restore_without_target_shows_usage(self, tmp_path):
        root = _fake_install(tmp_path)
        r = _run(root, ["restore"])
        assert r.returncode != 0 and "--file" in (r.stdout + r.stderr)

    def test_missing_venv_reports_clearly(self, tmp_path):
        root = _fake_install(tmp_path)
        (root / "venv" / "bin" / "python").unlink()
        r = _run(root, ["check"])
        assert r.returncode != 0
        assert "找不到 Coco 的 Python 环境" in (r.stdout + r.stderr), r.stdout + r.stderr


class TestHelpAndNaming:
    def test_help_lists_all_groups(self, tmp_path):
        root = _fake_install(tmp_path)
        r = _run(root, ["help"])
        out = r.stdout
        for token in ("日常运维", "服务与诊断", "安装配置"):
            assert token in out, out
        for cmd in ("version", "check", "backup", "backups", "restore", "update", "uninstall",
                    "status", "logs", "start", "restart", "stop", "model", "setup", "gateway", "pairing"):
            assert cmd in out, f"help 缺少 {cmd}"

    def test_help_speaks_coco_only(self, tmp_path):
        """命令统一口径（2026-09-22 定）：帮助里只给 coco 命令，不再写"等价于 hermes xxx"注记；
        底层命令仍需说明"不再对外暴露"（排障用 coco cli）。"""
        root = _fake_install(tmp_path)
        out = _run(root, ["help"]).stdout
        assert "等价于" not in out, out
        for cmd in ("status", "logs", "restart", "model", "setup", "config", "doctor", "tools", "pairing"):
            assert cmd in out, f"帮助里应列出 coco {cmd}：{out}"
        assert "不再对外暴露" in out and "coco cli" in out, out

    def test_unknown_command_points_to_help(self, tmp_path):
        root = _fake_install(tmp_path)
        r = _run(root, ["nonsense"])
        assert r.returncode != 0 and "coco help" in (r.stdout + r.stderr)


class TestDocsUseCocoPrefix:
    """文档口径：我们的操作一律 coco 前缀；不再出现长路径调用"""

    def test_readmes_have_no_long_path_invocations(self):
        for rel in ("README.md", "README.zh-CN.md", "docs/BACKUP_MIGRATION.md"):
            t = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert "venv/bin/python ~/hermes-agent/scripts/backup_db.py" not in t, rel
            assert "venv/bin/python ~/hermes-agent/scripts/healthcheck.py" not in t, rel

    def test_readmes_document_coco_commands(self):
        for rel in ("README.md", "README.zh-CN.md"):
            t = (REPO_ROOT / rel).read_text(encoding="utf-8")
            for cmd in ("coco update", "coco check", "coco backup", "coco restore", "coco data-clean", "coco uninstall"):
                assert cmd in t, f"{rel} 未统一到 {cmd}"
