"""migrate.py 迁移机制测试：幂等、失败回滚、安全检查、状态查询"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATE = REPO_ROOT / "scripts" / "migrate.py"


def run_migrate(db_url, *extra):
    return subprocess.run(
        [sys.executable, str(MIGRATE), "--database-url", db_url, *extra],
        capture_output=True, text=True, timeout=60,
    )


@pytest.fixture
def sqlite_db(tmp_path):
    """预建迁移会用到的表与列（003 需要 re_properties、011 需要 re_customer_changes、012 需要 re_customers.tags）"""
    db_path = tmp_path / "mig_test.db"
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE re_customers (id INTEGER PRIMARY KEY, name TEXT, tags TEXT)")
    conn.execute("CREATE TABLE re_properties (id INTEGER PRIMARY KEY, name TEXT)")   # 003/015 需要
    conn.execute("CREATE TABLE re_deals (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE re_customer_changes (id INTEGER PRIMARY KEY, customer_id INTEGER,"
                 " field TEXT, old_value TEXT, new_value TEXT)")
    conn.execute("CREATE TABLE re_followups (id INTEGER PRIMARY KEY, customer_id INTEGER,"
                 " type TEXT, content TEXT, next_date TIMESTAMP)")   # 014 需要
    conn.commit()
    conn.close()
    return f"sqlite:///{db_path}"


def write_migration(tmp_path, name, sql):
    """往临时 migrations 目录写迁移文件"""
    migrations_dir = REPO_ROOT / "migrations"
    f = migrations_dir / name
    f.write_text(sql, encoding="utf-8")
    return f


class TestMigrate:
    def test_executes_pending_migration(self, sqlite_db, tmp_path):
        f = write_migration(tmp_path, "901_test_add_col.sql",
                            "ALTER TABLE re_customers ADD COLUMN phone VARCHAR(20);")
        try:
            r = run_migrate(sqlite_db)
            assert r.returncode == 0
            assert "901_test_add_col.sql" in r.stdout
            # 列真的加上了
            import sqlite3
            db_path = sqlite_db.replace("sqlite:///", "")
            conn = sqlite3.connect(db_path)
            cols = [row[1] for row in conn.execute("PRAGMA table_info(re_customers)")]
            conn.close()
            assert "phone" in cols
        finally:
            f.unlink(missing_ok=True)

    def test_idempotent_second_run_skips(self, sqlite_db, tmp_path):
        """跑第二遍：已执行的跳过，不重复执行（幂等）"""
        f = write_migration(tmp_path, "902_test_skip.sql",
                            "ALTER TABLE re_customers ADD COLUMN tag VARCHAR(20);")
        try:
            assert run_migrate(sqlite_db).returncode == 0
            r2 = run_migrate(sqlite_db)
            assert r2.returncode == 0
            assert "数据库已是最新" in r2.stdout
        finally:
            f.unlink(missing_ok=True)

    def test_failure_stops_further_migrations(self, sqlite_db, tmp_path):
        """迁移失败：立即中止，后续迁移不再执行（sqlite 的 DDL 隐式提交
        无法回滚 ALTER；PostgreSQL 生产机上同一机制会整体回滚，事务语义不变）"""
        f_bad = write_migration(
            tmp_path, "903_test_bad.sql",
            "ALTER TABLE nonexistent_table ADD COLUMN x INT;")
        f_after = write_migration(
            tmp_path, "904_test_never_runs.sql",
            "ALTER TABLE re_customers ADD COLUMN never_col VARCHAR(10);")
        try:
            r = run_migrate(sqlite_db)
            assert r.returncode == 2
            assert "迁移中止" in r.stderr
            import sqlite3
            conn = sqlite3.connect(sqlite_db.replace("sqlite:///", ""))
            cols = [row[1] for row in conn.execute("PRAGMA table_info(re_customers)")]
            hist = conn.execute("SELECT COUNT(*) FROM migrations_history").fetchone()[0]
            conn.close()
            assert "never_col" not in cols   # 后续迁移没有跑
            # 002_price_history.sql（真实迁移）会先成功执行并记账，
            # 所以只断言"失败的 903 没有记账"（904 因中止也没跑）
            conn = sqlite3.connect(sqlite_db.replace("sqlite:///", ""))
            bad_recorded = conn.execute(
                "SELECT COUNT(*) FROM migrations_history WHERE seq >= 903"
            ).fetchone()[0]
            conn.close()
            assert bad_recorded == 0
        finally:
            f_bad.unlink(missing_ok=True)
            f_after.unlink(missing_ok=True)

    def test_forbidden_drop_table_rejected(self, sqlite_db, tmp_path):
        """DROP TABLE 被安全检查拒绝，不执行"""
        f = write_migration(tmp_path, "904_test_drop.sql",
                            "DROP TABLE re_customers;")
        try:
            r = run_migrate(sqlite_db)
            assert r.returncode == 2
            assert "DROP TABLE" in r.stderr
        finally:
            f.unlink(missing_ok=True)

    def test_drop_column_rejected_without_optin(self, sqlite_db, tmp_path):
        """未声明 -- migrate:allow-drop-column 的删列语句仍被拒绝（护栏不开口子）"""
        f = write_migration(tmp_path, "906_test_drop_col.sql",
                            "ALTER TABLE re_properties DROP COLUMN name;")
        try:
            r = run_migrate(sqlite_db)
            assert r.returncode == 2
            assert "DROP COLUMN" in r.stderr or "ALTER ... DROP" in r.stderr
            import sqlite3
            conn = sqlite3.connect(sqlite_db.replace("sqlite:///", ""))
            cols = [row[1] for row in conn.execute("PRAGMA table_info(re_properties)")]
            conn.close()
            assert "name" in cols  # 列还在，没被删
        finally:
            f.unlink(missing_ok=True)

    def test_drop_column_allowed_with_optin(self, sqlite_db, tmp_path):
        """显式声明后允许删列（清理用不上的遗留列）"""
        f = write_migration(tmp_path, "907_test_drop_col_ok.sql",
                            "-- migrate:allow-drop-column\n"
                            "ALTER TABLE re_properties DROP COLUMN name;")
        try:
            r = run_migrate(sqlite_db)
            assert r.returncode == 0
            import sqlite3
            conn = sqlite3.connect(sqlite_db.replace("sqlite:///", ""))
            cols = [row[1] for row in conn.execute("PRAGMA table_info(re_properties)")]
            conn.close()
            assert "name" not in cols  # 列真的删了
        finally:
            f.unlink(missing_ok=True)

    def test_drop_column_idempotent_when_missing(self, sqlite_db, tmp_path):
        """列已不存在时跳过（重跑安全；SQLite 没有 DROP COLUMN IF EXISTS）"""
        f = write_migration(tmp_path, "908_test_drop_col_missing.sql",
                            "-- migrate:allow-drop-column\n"
                            "ALTER TABLE re_properties DROP COLUMN never_existed;")
        try:
            r = run_migrate(sqlite_db)
            assert r.returncode == 0
            assert "不存在" in r.stdout
        finally:
            f.unlink(missing_ok=True)

    def test_status_only(self, sqlite_db, tmp_path):
        """--status 只看状态不执行"""
        f = write_migration(tmp_path, "905_test_status.sql",
                            "ALTER TABLE re_customers ADD COLUMN s VARCHAR(5);")
        try:
            r = run_migrate(sqlite_db, "--status")
            assert r.returncode == 0
            assert "待执行" in r.stdout
            # status 模式不实际执行
            import sqlite3
            conn = sqlite3.connect(sqlite_db.replace("sqlite:///", ""))
            # migrations_history 可能已建，但迁移未跑
            hist = conn.execute("SELECT COUNT(*) FROM migrations_history").fetchone()[0]
            conn.close()
            assert hist == 0
        finally:
            f.unlink(missing_ok=True)

    def test_011_masks_plaintext_contacts_in_change_history(self, sqlite_db):
        """011 迁移：变更历史里的明文手机号/微信换成掩码，非加密字段不动，重跑安全"""
        import sqlite3
        db_path = sqlite_db.replace("sqlite:///", "")
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO re_customer_changes (customer_id, field, old_value, new_value)"
                     " VALUES (1, 'phone', '13922220001', '13922220002')")
        conn.execute("INSERT INTO re_customer_changes (customer_id, field, old_value, new_value)"
                     " VALUES (1, 'wechat', 'mm_wx', 'mm_wx2')")
        conn.execute("INSERT INTO re_customer_changes (customer_id, field, old_value, new_value)"
                     " VALUES (1, 'budget_max', '3000000', '5000000')")
        conn.commit()
        conn.close()

        assert run_migrate(sqlite_db).returncode == 0
        assert run_migrate(sqlite_db).returncode == 0      # 再跑一遍：幂等

        conn = sqlite3.connect(db_path)
        rows = {f: (o, n) for f, o, n in conn.execute(
            "SELECT field, old_value, new_value FROM re_customer_changes")}
        conn.close()
        assert rows["phone"] == ("139****0001", "139****0002"), rows
        assert rows["wechat"] == ("mm****", "mm****"), rows
        assert rows["budget_max"] == ("3000000", "5000000"), rows   # 非加密字段不被改

    def test_012_normalizes_customer_tags(self, sqlite_db):
        """012 迁移：存量标签串规范化（分隔符统一/去空元素/去首尾逗号），幂等"""
        import sqlite3
        db_path = sqlite_db.replace("sqlite:///", "")
        conn = sqlite3.connect(db_path)
        rows = [("甲", "学区房, 地铁房"), ("乙", "学区房，近地铁"), ("丙", "a,,b"),
                ("丁", ",a"), ("戊", "a,"), ("己", "正常标签"), ("庚", ""), ("辛", None)]
        for name, tags in rows:
            conn.execute("INSERT INTO re_customers (name, tags) VALUES (?, ?)", (name, tags))
        conn.commit()
        conn.close()

        assert run_migrate(sqlite_db).returncode == 0
        assert run_migrate(sqlite_db).returncode == 0        # 再跑一遍：幂等

        conn = sqlite3.connect(db_path)
        got = dict(conn.execute("SELECT name, tags FROM re_customers"))
        conn.close()
        assert got["甲"] == "学区房,地铁房"
        assert got["乙"] == "学区房,近地铁"
        assert got["丙"] == "a,b"
        assert got["丁"] == "a" and got["戊"] == "a"
        assert got["己"] == "正常标签"                        # 干净的没被动
        assert got["庚"] == "" and got["辛"] is None          # 空值没被动

    def test_014_adds_followup_source_viewing(self, sqlite_db):
        """014 迁移：跟进表补 source_viewing_id（存量行为 None），重跑安全"""
        import sqlite3
        db_path = sqlite_db.replace("sqlite:///", "")
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO re_followups (customer_id, type, content)"
                     " VALUES (1, 'reminder', '存量提醒')")
        conn.commit()
        conn.close()

        assert run_migrate(sqlite_db).returncode == 0
        assert run_migrate(sqlite_db).returncode == 0        # 再跑一遍：ADD COLUMN 自动跳过

        conn = sqlite3.connect(db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(re_followups)")]
        got = conn.execute("SELECT type, content, source_viewing_id FROM re_followups").fetchall()
        conn.close()
        assert "source_viewing_id" in cols, cols
        assert got == [("reminder", "存量提醒", None)], got

    def test_015_adds_defect_baseline_column(self, sqlite_db):
        """015 迁移：房源补 defect_baseline_at（存量行为 NULL），重跑安全"""
        import sqlite3
        db_path = sqlite_db.replace("sqlite:///", "")
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO re_properties (id, name) VALUES (1, '存量房源')")
        conn.commit()
        conn.close()

        assert run_migrate(sqlite_db).returncode == 0
        assert run_migrate(sqlite_db).returncode == 0        # 再跑一遍：ADD COLUMN 自动跳过

        conn = sqlite3.connect(db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(re_properties)")]
        got = conn.execute("SELECT name, defect_baseline_at FROM re_properties").fetchall()
        conn.close()
        assert "defect_baseline_at" in cols, cols
        assert got == [("存量房源", None)], got

    def test_add_column_idempotent_despite_sql_like_comment(self, sqlite_db, tmp_path):
        """注释里出现 SQL 字样（如 ALTER TABLE ... ADD COLUMN）不该影响幂等判定

        2026-09-25 实测：判定是按正则 search 整段文本，注释里的假语句会被当真语句去判重，
        于是真语句的"列已存在则跳过"失效 → 全新库上报 duplicate column name。
        """
        import sqlite3
        db_path = sqlite_db.replace("sqlite:///", "")
        conn = sqlite3.connect(db_path)
        conn.execute("ALTER TABLE re_customers ADD COLUMN phone2 VARCHAR(20)")   # 模拟"结构已是最新"
        conn.commit()
        conn.close()
        f = write_migration(tmp_path, "909_test_comment_trap.sql",
                            "-- 写法说明：ALTER TABLE ... ADD COLUMN 由框架自动跳过已存在的列\n"
                            "ALTER TABLE re_customers ADD COLUMN phone2 VARCHAR(20);\n")
        try:
            r = run_migrate(sqlite_db)
            assert r.returncode == 0, r.stderr
            assert "跳过" in r.stdout, r.stdout
        finally:
            f.unlink(missing_ok=True)
