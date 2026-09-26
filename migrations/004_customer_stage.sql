-- 004: 客户生命周期阶段（功能5）
ALTER TABLE re_customers ADD COLUMN stage VARCHAR(20) DEFAULT 'lead';
CREATE INDEX IF NOT EXISTS re_idx_customer_stage ON re_customers(stage);
