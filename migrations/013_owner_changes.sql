-- 013: 房东资料变更留痕表（2026-09-25，配合新工具 update_owner）
-- 背景：客户侧早有 re_customer_changes，房东侧一直没有任何留痕 —— "谁什么时候把房东电话改了"
-- 查不到；而房东录错信息以前只能删了重建（会把名下房源的关联断掉），现在有 update_owner 可改，
-- 就更需要留痕。
-- 幂等：CREATE TABLE / CREATE INDEX 都是 IF NOT EXISTS；PG 上 AUTOINCREMENT 由 migrate.py 自动换成 SERIAL。
-- 说明：全新库由 Base.metadata.create_all() 直接建表，这条迁移是给已存在的库补齐。
CREATE TABLE IF NOT EXISTS re_owner_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER REFERENCES re_owners(id),
    field VARCHAR(50) NOT NULL,
    old_value TEXT,
    new_value TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS re_idx_owner_change_owner ON re_owner_changes(owner_id);
