-- 008: 出租房源"租客要求"字段（2026-08-29 加：租客要求正式入库，匹配租客时用于过滤）
ALTER TABLE re_properties ADD COLUMN tenant_requirements TEXT;
