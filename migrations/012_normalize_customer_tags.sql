-- 存量客户标签规范化（2026-09-24）：标签是逗号串存储，历史上没做归一，库里积了这些脏形态 ——
--   "学区房, 地铁房"（分隔符带空格）、"学区房，"（全角逗号）、"a,,b"（空元素）、",a" / "a,"（首尾多余逗号）
-- 不清理的话，按标签筛客户会漏人（同一标签因写法不同算两个）。
-- 幂等：跑第二遍时上面的形态都已不存在，WHERE 不成立。
-- 兼容：replace / length / substr / || 在 SQLite 与 PostgreSQL 上语义一致。
-- 说明：同一客户内部的"完全重复标签"无法用可移植 SQL 去重，运行时已按归一去重；存量重复项不清理（只影响显示）。
UPDATE re_customers SET tags = replace(tags, '，', ',') WHERE tags LIKE '%%，%%';
UPDATE re_customers SET tags = replace(tags, '、', ',') WHERE tags LIKE '%%、%%';
UPDATE re_customers SET tags = replace(tags, '；', ',') WHERE tags LIKE '%；%%';

-- 分隔符两侧空格
UPDATE re_customers SET tags = replace(tags, ', ', ',') WHERE tags LIKE '%, %';
UPDATE re_customers SET tags = replace(tags, ' ,', ',') WHERE tags LIKE '% ,%';

-- 连续逗号（空元素）：反复折叠到只剩一个
UPDATE re_customers SET tags = replace(tags, ',,', ',') WHERE tags LIKE '%,,%';
UPDATE re_customers SET tags = replace(tags, ',,', ',') WHERE tags LIKE '%,,%';

-- 首尾多余逗号：先折掉连续逗号，再各切一次首/尾的逗号（substr/length 两个库都支持）
UPDATE re_customers SET tags = substr(tags, 2, length(tags) - 1) WHERE tags LIKE ',%';
UPDATE re_customers SET tags = substr(tags, 1, length(tags) - 1) WHERE tags LIKE '%,';
