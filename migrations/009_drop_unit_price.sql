-- 删除历史遗留列 re_properties.unit_price：单价不再单独存列，改为读取时按 总价÷面积 现算（保留两位小数）
-- migrate:allow-drop-column
ALTER TABLE re_properties DROP COLUMN unit_price;
