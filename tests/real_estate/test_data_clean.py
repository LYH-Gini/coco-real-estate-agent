"""coco data-clean（数据清理向导）行为测试（2026-09-23）

隔离：每个用例一个 sqlite 临时库（DATABASE_URL 指向它），不碰真实 PostgreSQL。
备份链路用 pg_dump，本机/sqlite 下必然失败 —— 正好验证"备份失败即中止"。
"""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "data_clean.py"


@pytest.fixture
def dc(tmp_path, monkeypatch):
    """载入 data_clean 模块，并把库指到临时 sqlite 文件"""
    db_file = tmp_path / "dc.db"
    spec = importlib.util.spec_from_file_location("data_clean_mod", SCRIPT)  # 直接载原脚本，_load_db 才能找到同目录的 backup_db
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    mod._db_path = db_file
    return mod


def _seed(mod):
    """造：1 个干净关闭客户、1 个有跟进的关闭客户、1 个无牵挂已售房源"""
    db = mod._load_db()
    clean = db.add_customer(name="干净关闭客户", status="closed")
    protected = db.add_customer(name="有跟进的关闭客户", status="closed")
    db.add_followup(customer_id=protected["id"], type="phone", content="回访")
    prop = db.add_property(title="无牵挂已售房", price=900000, area=60.0, status="sold")
    return db, clean, protected, prop


def _counts(mod):
    conn = sqlite3.connect(mod._db_path)
    customers = conn.execute("select count(*) from re_customers").fetchone()[0]
    closed = conn.execute("select count(*) from re_customers where status='closed'").fetchone()[0]
    props = conn.execute("select count(*) from re_properties").fetchone()[0]
    conn.close()
    return {"customers": customers, "closed": closed, "properties": props}


class TestDryRun:
    def test_dry_run_changes_nothing(self, dc, capsys):
        _seed(dc)
        before = _counts(dc)
        assert dc.main(["--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "预演" in out and "未做任何修改" in out
        assert _counts(dc) == before

    def test_dry_run_lists_protected_records(self, dc, capsys):
        _seed(dc)
        dc.main(["--dry-run"])
        out = capsys.readouterr().out
        assert "有跟进的关闭客户" in out
        assert "会被跳过" in out


class TestNonInteractiveGuard:
    def test_no_action_without_tty_is_refused(self, dc, monkeypatch, capsys):
        monkeypatch.setattr(dc.sys.stdin, "isatty", lambda: False)
        _seed(dc)
        before = _counts(dc)
        assert dc.main([]) == 2
        assert "需要交互终端" in capsys.readouterr().err
        assert _counts(dc) == before


class TestDelete:
    def test_clean_only_and_keeps_history(self, dc, capsys):
        db, clean, protected, prop = _seed(dc)
        assert dc.main(["--kind", "all", "--yes", "--no-backup"]) == 0
        out = capsys.readouterr().out
        assert "清理前" in out and "清理后" in out
        counts = _counts(dc)
        assert counts["properties"] == 0          # 无牵挂房源已删
        assert counts["customers"] == 1           # 只有那个干净的被删
        assert db.get_customer(protected["id"]) is not None

    def test_force_also_removes_history(self, dc):
        db, clean, protected, prop = _seed(dc)
        assert dc.main(["--kind", "all", "--yes", "--no-backup", "--force"]) == 0
        assert db.get_customer(protected["id"]) is None
        assert _counts(dc)["customers"] == 0

    def test_before_filter_limits_scope(self, dc, capsys):
        db = dc._load_db()
        old = db.add_customer(name="很久以前的关闭客户", status="closed")
        newer = db.add_customer(name="昨天的关闭客户", status="closed")
        from datetime import datetime, timedelta
        with db.get_session() as s:
            from agent.real_estate_db import Customer
            s.query(Customer).get(old["id"]).created_at = datetime.now() - timedelta(days=30)
            s.commit()
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        assert dc.main(["--kind", "customer", "--before", cutoff, "--yes", "--no-backup"]) == 0
        assert db.get_customer(old["id"]) is None
        assert db.get_customer(newer["id"]) is not None

    def test_backup_failure_aborts_and_keeps_data(self, dc, capsys):
        """没带 --no-backup 时先备份；备份失败必须中止（sqlite 环境必然失败）"""
        db, clean, protected, prop = _seed(dc)
        before = _counts(dc)
        with pytest.raises(SystemExit) as exc:
            dc.main(["--kind", "all", "--yes"])
        assert exc.value.code == 1
        assert "备份失败" in capsys.readouterr().err
        assert _counts(dc) == before

    def test_confirmation_declined_keeps_data(self, dc, monkeypatch, capsys):
        _seed(dc)
        monkeypatch.setattr("builtins.input", lambda *a: "no")
        before = _counts(dc)
        assert dc.main(["--kind", "all"]) == 0
        assert "已取消" in capsys.readouterr().out
        assert _counts(dc) == before


class TestArchiveAndRestore:
    def test_archive_marks_without_deleting(self, dc):
        db = dc._load_db()
        c = db.add_customer(name="在跟客户", status="active")
        assert dc.main(["--mode", "archive", "--kind", "customer", "--statuses", "active",
                        "--yes", "--no-backup"]) == 0
        assert db.get_customer(c["id"])["status"] == "closed"
        assert _counts(dc)["customers"] == 1

    def test_restore_brings_records_back(self, dc):
        db, clean, protected, prop = _seed(dc)
        assert dc.main(["--restore", "--yes", "--no-backup"]) == 0
        assert db.get_customer(clean["id"])["status"] == "active"
        assert db.get_property(prop["id"])["status"] == "available"


def _run_menu(mod, answers, extra=("--no-backup",)):
    """用 pty 跑一次向导（假装真人在终端敲键）→ (退出码, 输出)"""
    import os
    import pty
    import subprocess
    import sys

    mod._load_db().engine.dispose()  # 放开 sqlite 文件句柄，免得子进程写不进去
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{mod._db_path}"}
    master, slave = pty.openpty()
    proc = subprocess.Popen([sys.executable, str(SCRIPT), *extra], cwd=REPO_ROOT,
                            stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=env, text=True)
    os.close(slave)
    try:
        os.write(master, ("\n".join(answers) + "\n").encode())
        out = proc.communicate(timeout=90)[0]
    finally:
        os.close(master)
    return proc.returncode, out


class TestWizardMenu:
    """向导菜单（真终端）：预演 / 清理 / 取消 三条路都要走得通"""

    def test_menu_preview_changes_nothing(self, dc):
        _seed(dc)
        before = _counts(dc)
        code, out = _run_menu(dc, ["1"])
        assert code == 0, out
        assert "数据清理向导" in out and "预演结束" in out
        assert _counts(dc) == before

    def test_menu_clean_with_confirmation(self, dc):
        db, clean, protected, prop = _seed(dc)
        code, out = _run_menu(dc, ["4", "yes"])
        assert code == 0, out
        assert "清理前" in out and "清理后" in out
        assert db.get_property(prop["id"]) is None

    def test_menu_cancel_changes_nothing(self, dc):
        _seed(dc)
        before = _counts(dc)
        code, out = _run_menu(dc, ["6"])
        assert code == 0, out
        assert "已取消" in out
        assert _counts(dc) == before
