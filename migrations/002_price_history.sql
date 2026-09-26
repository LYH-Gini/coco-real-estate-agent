-- 002: 房源调价历史表（功能1：调价记录与反匹配提醒）
CREATE TABLE IF NOT EXISTS re_price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER NOT NULL REFERENCES re_properties(id),
    old_price INTEGER,
    new_price INTEGER NOT NULL,
    change_reason VARCHAR(200),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS re_idx_price_history_prop ON re_price_history(property_id);
CREATE INDEX IF NOT EXISTS re_idx_price_history_created ON re_price_history(created_at);
