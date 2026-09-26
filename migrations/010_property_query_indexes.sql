-- 房源查询/匹配常用列加索引（大库 6 万+ 时按类型/价格/户型/区域检索更快）
-- 幂等：CREATE INDEX IF NOT EXISTS 在 PostgreSQL 与 SQLite 上均可用
CREATE INDEX IF NOT EXISTS re_idx_prop_status_type ON re_properties (status, property_type);
CREATE INDEX IF NOT EXISTS re_idx_prop_price_status ON re_properties (price, status);
CREATE INDEX IF NOT EXISTS re_idx_prop_rooms_status ON re_properties (rooms, status);
CREATE INDEX IF NOT EXISTS re_idx_prop_district_status ON re_properties (district, status);
