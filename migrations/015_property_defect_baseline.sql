-- 015: 房源整改时间（缺陷标签的手动清除基线）
-- 背景：经纪人手动清除缺陷标签后，只要再有人录一条带看反馈，"重扫"就会把整改前的历史差评
-- 重新算进去、标签被打回来 —— 房东整改等于白做。记下整改时间之后，重扫只统计这段时间之后的新反馈。
-- 幂等：加列语句由 migrate.py 自动跳过已存在的列；全新库由 Base.metadata.create_all() 直接建带这一列的表。
ALTER TABLE re_properties ADD COLUMN defect_baseline_at TIMESTAMP;
