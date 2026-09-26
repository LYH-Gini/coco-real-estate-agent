# 迁移文件命名规范：001_name.sql（三位序号开头）

- 序号递增、只增不删；每个文件在事务里执行，失败回滚且不记账。
- 禁止：DROP TABLE / TRUNCATE / DELETE FROM / 给已有表加 NOT NULL 无默认值列。
- **删列（清理用不上的遗留列）**：文件里必须显式写一行 `-- migrate:allow-drop-column`，
  语句只允许 `ALTER TABLE <表> DROP COLUMN <列>;` 这一种规范形态（列已不存在会自动跳过，可重跑）。
- 幂等要求：`ALTER TABLE ... ADD COLUMN` 自动跳过已存在的列；其余语句请自行保证可重复执行。
- PG 兼容要求：禁用 AUTOINCREMENT/SQLite 专属写法（`INTEGER PRIMARY KEY AUTOINCREMENT` 会自动转成 `SERIAL PRIMARY KEY`）。
