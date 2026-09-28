-- 016: 房东与客户的身份证号改成加密存储（2026-09-29）
-- 背景：原先身份证号只存脱敏串（re_owners.id_masked）、原号不落库，出发点是不让备份/导出读到全号；
-- 但经纪人的真实用途是网签、贷款、备案 —— 下次要用时库里没有全号，等于白记。
-- 现在与手机号同一套口径：加密列存全号（COCO_ENC_KEY），读取时自动解密；列表与详情都给完整号；
-- 变更留痕（re_customer_changes / re_owner_changes）仍然只留脱敏串，备份与看库的人读不到完整号。
-- 历史数据：本迁移只加列、不猜测、不回填 —— 改动前登记的记录只有脱敏号，完整号无法还原，需要时补录一次。
-- 幂等：ADD COLUMN 由 migrate.py 判定列是否已存在（PG / SQLite 通用）；全新库由建表语句直接带上这两列。
ALTER TABLE re_owners ADD COLUMN id_number TEXT;
ALTER TABLE re_customers ADD COLUMN id_number TEXT;
