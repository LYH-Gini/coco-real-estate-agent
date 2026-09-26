-- 007: 转介绍表（功能6：老客户转介绍经营）
CREATE TABLE IF NOT EXISTS re_referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_customer_id INTEGER NOT NULL REFERENCES re_customers(id),
    referred_customer_id INTEGER REFERENCES re_customers(id),
    referred_name VARCHAR(100) NOT NULL,
    referred_phone TEXT,
    status VARCHAR(20) DEFAULT 'registered',
    reward_note VARCHAR(200),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS re_idx_referral_referrer ON re_referrals(referrer_customer_id);
ALTER TABLE re_deals ADD COLUMN referral_id INTEGER REFERENCES re_referrals(id);
