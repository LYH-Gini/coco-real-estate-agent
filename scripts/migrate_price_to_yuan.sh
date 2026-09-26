#!/usr/bin/env bash
# ============================================================
# 价格单位迁移脚本：万元 → 元（仅历史旧库需要）
# 背景：2026-08 起系统所有价格字段以"元"为单位存储
#   - re_properties.price     数值(12,2)万元 → bigint 元
#   - re_customers.budget_*   万元 → 元
#   - re_deals.price / deposit_amount  万元 → 元
# 适用场景：旧服务器备份恢复（dump 里是万元数据）后执行一次。
# 全新安装（空库）不需要执行。
# 用法：bash scripts/migrate_price_to_yuan.sh
# ============================================================
set -euo pipefail

DB_URL="${DATABASE_URL:-postgresql://hermes:***@localhost:5432/hermes_agent}"

echo "== 1. re_properties.price: 万元 → 元（列类型 numeric(12,2) → bigint） =="
psql "$DB_URL" -c "ALTER TABLE re_properties ALTER COLUMN price TYPE bigint USING round(price * 10000);"

echo "== 2. re_customers 预算: 万元 → 元 =="
psql "$DB_URL" -c "UPDATE re_customers SET budget_min = budget_min * 10000 WHERE budget_min > 0;"
psql "$DB_URL" -c "UPDATE re_customers SET budget_max = budget_max * 10000 WHERE budget_max > 0;"

echo "== 3. re_deals 成交价/定金: 万元 → 元 =="
psql "$DB_URL" -c "UPDATE re_deals SET price = price * 10000 WHERE price > 0;"
psql "$DB_URL" -c "UPDATE re_deals SET deposit_amount = deposit_amount * 10000 WHERE deposit_amount > 0;"

echo "== 校验 =="
psql "$DB_URL" -t -c "SELECT property_type, min(price), max(price) FROM re_properties GROUP BY property_type;"
psql "$DB_URL" -t -c "SELECT count(*) FROM re_customers WHERE budget_max > 0 AND budget_max < 1000000;"
echo "== 完成 =="
