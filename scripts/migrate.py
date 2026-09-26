#!/usr/bin/env python3
"""
Coco 数据库迁移脚本：带版本号的表结构变更管理

用法:
    python3 scripts/migrate.py                 # 执行 migrations/ 下未跑过的迁移
    python3 scripts/migrate.py --status        # 只查看迁移状态，不执行
    python3 scripts/migrate.py --database-url postgresql://...  # 显式指定库

机制:
- migrations/ 目录下按 001_xxx.sql、002_xxx.sql 序号命名
- migrations_history 表记录已执行的迁移（序号、文件名、时间、耗时）
- 只执行序号大于已记录最大序号的迁移；执行过的跳过（幂等）
- 每个迁移在事务里执行，失败立即中止（生产库不会被半途而废的迁移污染）
- 安全约束：迁移 SQL 禁止 DROP TABLE / TRUNCATE / DELETE（只增不删）
- 删列（清理用不上的遗留列）需要显式声明：迁移文件里写 `-- migrate:allow-drop-column`
  才允许 `ALTER TABLE <表> DROP COLUMN <列>`，且该语句按列存在性幂等（列已不存在则跳过）

首次执行前建议先跑 backup_db.py backup。
"""
import argparse
import re
import sys
import time
from pathlib import Path

# 控制台输出抗编码（Windows 上 stdout 跟随控制台代码页 cp1252/GBK，中文 print 会抛
# UnicodeEncodeError 把迁移带崩；同款处理见 scripts/backup_db.py 与 scripts/healthcheck.py）
for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

MIGRATIONS_DIR = REPO_ROOT / "migrations"
# 安全约束：迁移里禁止出现的破坏性语句（只增不删）。
# 例外：删列（DROP COLUMN）在迁移文件显式写 -- migrate:allow-drop-column 时放行，
# 且只放行 ALTER TABLE <表> DROP COLUMN <列> 这一种规范形态（见 _DROP_COL_RE）。
FORBIDDEN_PATTERNS = [
    (re.compile(r"\bDROP\s+TABLE\b", re.I), "DROP TABLE"),
    (re.compile(r"\bDROP\s+COLUMN\b", re.I), "DROP COLUMN"),
    (re.compile(r"\bDROP\s+VIEW\b", re.I), "DROP VIEW"),
    (re.compile(r"\bDROP\s+SCHEMA\b", re.I), "DROP SCHEMA"),
    (re.compile(r"\bDROP\s+DATABASE\b", re.I), "DROP DATABASE"),
    (re.compile(r"\bDROP\s+FUNCTION\b", re.I), "DROP FUNCTION"),
    (re.compile(r"\bDROP\s+MATERIALIZED\s+VIEW\b", re.I), "DROP MATERIALIZED VIEW"),
    (re.compile(r"\bDROP\s+TRIGGER\b", re.I), "DROP TRIGGER"),
    (re.compile(r"\bDROP\s+SEQUENCE\b", re.I), "DROP SEQUENCE"),
    (re.compile(r"\bALTER\s+TABLE\b[^;]*\bDROP\b", re.I), "ALTER ... DROP"),
    (re.compile(r"\bTRUNCATE\b", re.I), "TRUNCATE"),
    (re.compile(r"\bDELETE\s+FROM\b", re.I), "DELETE FROM"),
]

# 无损更新红线：给已有表 ADD COLUMN 且带 NOT NULL 但无 DEFAULT，会破坏已有数据行
_ADD_COL_NOT_NULL_RE = re.compile(r"\bADD\s+(COLUMN\s+)?\w+\b.*?\bNOT\s+NULL\b", re.I)
_DEFAULT_RE = re.compile(r"\bDEFAULT\b", re.I)
_ALTER_RE = re.compile(r"\bALTER\s+TABLE\b", re.I)

MIG_FILE_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")

# 删列放行开关：迁移文件里写 -- migrate:allow-drop-column 才允许 DROP COLUMN
_DROP_COLUMN_OPT_IN_RE = re.compile(r"--\s*migrate:allow-drop-column\b", re.I)
_DROP_COLUMN_LABELS = {"DROP COLUMN", "ALTER ... DROP"}
# CREATE INDEX ... ON <表> (列, 列)：用于"部分结构库"安全检查（缺列则跳过）
_INDEX_RE = re.compile(r"CREATE\s+(?:UNIQUE\s+)?INDEX[^;]*?\bON\s+([\"\w.]+)\s*\(([^)]*)\)", re.I)
# 规范删列形态：ALTER TABLE <表> DROP COLUMN <列>（COLUMN 关键字可省）
_DROP_COL_RE = re.compile(r"ALTER\s+TABLE\s+([\"\w.]+)\s+DROP\s+(?:COLUMN\s+)?(\w+)", re.I)

# 解析 ALTER TABLE ... ADD COLUMN（用于幂等：列已存在则跳过）
_ALTER_ADD_COL_RE = re.compile(r"ALTER\s+TABLE\s+([\"\w.]+)\s+ADD\s+COLUMN\s+(\w+)", re.I)


def _column_exists(conn, table, column):
    """检查某表某列是否存在（兼容 SQLite / PostgreSQL）"""
    from sqlalchemy import text
    dialect = conn.dialect.name
    table_clean = table.strip('"').split('.')[-1]
    try:
        if dialect == "sqlite":
            rows = conn.execute(text(f'PRAGMA table_info("{table_clean}")')).fetchall()
            return any((r[1] if len(r) > 1 else None) == column for r in rows)
        r = conn.execute(text(
            "SELECT 1 FROM information_schema.columns WHERE table_name=:t AND column_name=:c"
        ), {"t": table_clean, "c": column}).fetchone()
        return r is not None
    except Exception:
        return False


def get_database_url(cli_url=None):
    if cli_url:
        return cli_url
    url = None
    # 1) 环境变量
    url = sys.modules["os"].environ.get("DATABASE_URL")
    if url:
        return url
    # 2) 项目 .env.db（生产机由 install.sh 配置）
    env_file = REPO_ROOT / ".env.db"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^DATABASE_URL=(.+)$", line.strip())
            if m:
                return m.group(1).strip().strip('"').strip("'")
    print("错误：未找到 DATABASE_URL（环境变量或 .env.db）。", file=sys.stderr)
    print("测试场景可显式传 --database-url sqlite:///path.db", file=sys.stderr)
    sys.exit(1)


def load_history(conn):
    """读取已执行迁移记录；表不存在则建表（按方言兼容 SQLite/PostgreSQL）"""
    from sqlalchemy import text
    dialect = conn.dialect.name
    id_def = "INTEGER PRIMARY KEY AUTOINCREMENT" if dialect == "sqlite" else "SERIAL PRIMARY KEY"
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS migrations_history (
            id {id_def},
            seq INTEGER NOT NULL,
            filename VARCHAR(200) NOT NULL,
            executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            duration_ms INTEGER
        )
    """))
    conn.commit()
    rows = conn.execute(text(
        "SELECT seq, filename FROM migrations_history ORDER BY seq"
    )).fetchall()
    return {r[0]: r[1] for r in rows}


def scan_migrations():
    """扫描 migrations/ 目录，返回 [(seq, path), ...] 按序号排序"""
    if not MIGRATIONS_DIR.exists():
        return []
    result = []
    for f in sorted(MIGRATIONS_DIR.iterdir()):
        m = MIG_FILE_RE.match(f.name)
        if m:
            result.append((int(m.group(1)), f))
        elif f.suffix == ".sql":
            print(f"警告：{f.name} 不符合 001_name.sql 命名规范，已跳过", file=sys.stderr)
    return result


def _split_statements(sql_text):
    """按分号切分 SQL 语句，忽略空串与纯注释"""
    stmts = []
    for raw in sql_text.split(";"):
        s = raw.strip()
        if not s:
            continue
        body = "\n".join(line for line in s.splitlines() if not line.strip().startswith("--"))
        if not body.strip():
            continue
        stmts.append(s)
    return stmts


def _strip_comment_lines(sql_text):
    """去掉"整行都是注释"的行 —— 幂等/形态判定只看真 SQL

    背景（2026-09-25 实测）：判定用的是正则 search **整段文本**，注释里出现的 SQL 字样
    会被当成真语句 —— 某迁移在注释里写了 `ALTER TABLE ... ADD COLUMN` 当说明，
    于是真语句的"列已存在则跳过"判定失效，全新库上报 duplicate column name
    （存量库反而正常，最容易被漏掉）。
    """
    return "\n".join(line for line in sql_text.splitlines()
                     if not line.strip().startswith("--"))


def validate_sql(sql_text, filename):
    """安全检查：禁止破坏性语句 + 禁止'给已有表加 NOT NULL 无默认值列'（无损更新红线）

    删列例外：文件里显式写了 -- migrate:allow-drop-column 时，放行规范形态的
    ALTER TABLE <表> DROP COLUMN <列>（用于清理用不上的遗留列）；其余 DROP 语句照旧禁止。
    """
    allow_drop_column = bool(_DROP_COLUMN_OPT_IN_RE.search(sql_text))
    for pattern, label in FORBIDDEN_PATTERNS:
        if allow_drop_column and label in _DROP_COLUMN_LABELS:
            continue
        if pattern.search(sql_text):
            raise ValueError(f"{filename} 含禁止语句 {label}（迁移只增不删，不允许删改数据）")
    if allow_drop_column:
        for stmt in _split_statements(sql_text):
            if re.search(r"\bDROP\b", stmt, re.I) and not _DROP_COL_RE.search(stmt):
                raise ValueError(
                    f"{filename} 含非规范删列语句（只允许 ALTER TABLE <表> DROP COLUMN <列>）: {stmt[:80]}"
                )
    # 逐句检查：对已有表 ALTER ADD COLUMN 且带 NOT NULL 但无 DEFAULT，会把已有数据行写坏
    for stmt in _split_statements(sql_text):
        if _ALTER_RE.search(stmt) and _ADD_COL_NOT_NULL_RE.search(stmt):
            if not _DEFAULT_RE.search(stmt):
                raise ValueError(
                    f"{filename} 含危险加列：给已有表加 NOT NULL 列却无 DEFAULT，"
                    f"会导致已有数据行写入失败/损坏（无损更新红线）。"
                    f"请改为可空列，或加 NOT NULL 同时提供 DEFAULT。语句: {stmt[:80]}"
                )


def apply_migration(conn, seq, path):
    from sqlalchemy import text
    sql_text = path.read_text(encoding="utf-8")
    validate_sql(sql_text, path.name)
    start = time.time()
    conn.rollback()  # 结束 load_history 留下的 autobegin 事务，保证从这里开始的事务边界干净
    trans = conn.begin()
    try:
        dialect = conn.dialect.name
        for statement in _split_statements(sql_text):
            stmt = statement.strip()
            if not stmt:
                continue
            # 幂等/形态判定只看真 SQL（注释里的 SQL 字样不当真，见 _strip_comment_lines）
            probe = _strip_comment_lines(stmt).strip()
            if not probe:
                continue
            # PostgreSQL 兼容：把 SQLite 的 AUTOINCREMENT 换成 SERIAL
            if dialect == "postgresql":
                stmt = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "SERIAL PRIMARY KEY", stmt, flags=re.I)
                stmt = re.sub(r"\bAUTOINCREMENT\b", "", stmt, flags=re.I)
            # ALTER ADD COLUMN 幂等：列已存在则跳过（对已是最新结构的库安全，不报错）
            m = _ALTER_ADD_COL_RE.search(probe)
            if m:
                table_name, col_name = m.group(1), m.group(2)
                if _column_exists(conn, table_name, col_name):
                    print(f"    跳过（列 {table_name}.{col_name} 已存在）")
                    continue
            # DROP COLUMN 幂等：列已不存在则跳过（重跑/已删过的库安全，SQLite 不支持 DROP COLUMN IF EXISTS）
            m = _DROP_COL_RE.search(probe)
            if m:
                table_name, col_name = m.group(1), m.group(2)
                if not _column_exists(conn, table_name, col_name):
                    print(f"    跳过（列 {table_name}.{col_name} 不存在）")
                    continue
            # CREATE INDEX 幂等：索引引用的列不存在则跳过（对"只有部分结构"的库安全）
            _body = probe.upper()

            if _body.startswith("CREATE INDEX") or _body.startswith("CREATE UNIQUE INDEX"):
                mi = _INDEX_RE.search(probe)
                if mi:
                    tbl, cols_raw = mi.group(1), mi.group(2)
                    cols = [c.strip().split()[0].strip('"') for c in cols_raw.split(",") if c.strip()]
                    missing = [c for c in cols if not _column_exists(conn, tbl, c)]
                    if missing:
                        print(f"    跳过（表 {tbl} 缺列 {missing}，该索引暂不适用）")
                        continue
            conn.execute(text(stmt))
        conn.execute(text(
            "INSERT INTO migrations_history (seq, filename, duration_ms) "
            "VALUES (:seq, :fname, :ms)"
        ), {"seq": seq, "fname": path.name, "ms": int((time.time() - start) * 1000)})
        trans.commit()
        return int((time.time() - start) * 1000)
    except Exception:
        trans.rollback()
        raise


def run_check_only():
    """静态校验所有迁移文件合法性（不连库、不执行）。供 update.sh / CI 使用。"""
    migrations = scan_migrations()
    if not migrations:
        print("迁移目录为空或无可识别迁移文件。")
        return True
    bad = 0
    for seq, p in migrations:
        try:
            content = p.read_text(encoding="utf-8")
            validate_sql(content, p.name)
        except ValueError as e:
            bad += 1
            print(f"  FAIL {p.name}: {e}", file=sys.stderr)
    if bad:
        print(f"迁移安全校验失败：{bad} 个文件含不安全操作。", file=sys.stderr)
        return False
    print(f"迁移安全校验通过：{len(migrations)} 个文件均为只增不删。")
    return True


def main():
    parser = argparse.ArgumentParser(description="Coco 数据库迁移")
    parser.add_argument("--database-url", help="显式指定数据库连接")
    parser.add_argument("--status", action="store_true", help="只查看状态")
    parser.add_argument("--check", action="store_true", help="只静态校验迁移文件合法性，不执行")
    args = parser.parse_args()

    if args.check:
        sys.exit(0 if run_check_only() else 1)

    from sqlalchemy import create_engine, text

    url = get_database_url(args.database_url)
    # 兼容 postgres:// 前缀（部分平台旧写法）
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    engine = create_engine(url)
    migrations = scan_migrations()

    with engine.connect() as conn:
        history = load_history(conn)
        pending = [(seq, p) for seq, p in migrations if seq not in history]

        # 正常情况下（无待执行迁移）只回一行结论，不逐条列文件 —— 每次更新都刷 7 行"已执行"
        # 属过程噪音；要看逐条明细用 `--status`。
        if not pending and not args.status:
            seqs = sorted(history)
            span = f"{seqs[0]:03d}~{seqs[-1]:03d}" if seqs else "无"
            print(f"数据库已是最新（已执行 {len(history)} 个迁移：{span}），无需迁移。")
            return

        print(f"迁移目录: {MIGRATIONS_DIR}")
        print(f"已执行: {len(history)} 个 | 待执行: {len(pending)} 个")
        print("----------------------------------------")
        for seq, p in migrations:
            status = "已执行 " + str(history[seq]) if seq in history else "待执行"
            mark = "✓" if seq in history else "→"
            print(f"  {mark} {p.name}  [{status}]")

        if args.status:
            return

        for seq, p in pending:
            print(f"执行 {p.name} ...", flush=True)
            try:
                ms = apply_migration(conn, seq, p)
                print(f"  完成 {p.name}（{ms}ms）")
            except Exception as e:
                print(f"  失败 {p.name}: {e}", file=sys.stderr)
                print("迁移中止，数据库未受污染。修复后重跑。", file=sys.stderr)
                sys.exit(2)

    print("全部迁移执行完成。")


if __name__ == "__main__":
    main()
