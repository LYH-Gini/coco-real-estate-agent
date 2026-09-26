-- 客户变更历史里的联系方式是明文（加密字段在主表里是密文，但被解密后写进了留痕表），
-- 统一替换成掩码：139****0002 / wx****。这样备份、导出、看库的人都读不到完整号码。
-- 幂等：掩码值里带 *，重跑时 WHERE 不成立；密文形态与空值不动。
-- 兼容：substr/length/|| 在 SQLite 与 PostgreSQL 上语义一致。
UPDATE re_customer_changes
SET old_value = substr(old_value, 1, 3) || '****' || substr(old_value, length(old_value) - 3, 4)
WHERE field = 'phone' AND old_value IS NOT NULL
  AND old_value NOT LIKE '%*%' AND length(old_value) BETWEEN 8 AND 39;

UPDATE re_customer_changes
SET new_value = substr(new_value, 1, 3) || '****' || substr(new_value, length(new_value) - 3, 4)
WHERE field = 'phone' AND new_value IS NOT NULL
  AND new_value NOT LIKE '%*%' AND length(new_value) BETWEEN 8 AND 39;

UPDATE re_customer_changes
SET old_value = substr(old_value, 1, 2) || '****'
WHERE field = 'wechat' AND old_value IS NOT NULL
  AND old_value NOT LIKE '%*%' AND length(old_value) < 40;

UPDATE re_customer_changes
SET new_value = substr(new_value, 1, 2) || '****'
WHERE field = 'wechat' AND new_value IS NOT NULL
  AND new_value NOT LIKE '%*%' AND length(new_value) < 40;
