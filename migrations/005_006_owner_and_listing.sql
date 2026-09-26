-- 005: 房东（业主）表（功能7：委托管理）
CREATE TABLE IF NOT EXISTS re_owners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(100) NOT NULL,
    phone TEXT,
    wechat TEXT,
    id_masked VARCHAR(30),
    trust_note VARCHAR(200),
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS re_idx_owner_name ON re_owners(name);

-- 006: 房源关联房东 + 佣金率 + 独家委托到期
ALTER TABLE re_properties ADD COLUMN owner_id INTEGER REFERENCES re_owners(id);
ALTER TABLE re_properties ADD COLUMN commission_rate REAL;
ALTER TABLE re_properties ADD COLUMN exclusive_until DATE;
ALTER TABLE re_properties ADD COLUMN viewing_note VARCHAR(200);
CREATE INDEX IF NOT EXISTS re_idx_prop_owner ON re_properties(owner_id);
