#!/usr/bin/env bash
# ============================================================
# 修复历史脏数据（2026-08-10 首次Excel导入产生）
# 问题：
#   1. district 被填成景观（园区/海景/园林），真实区域信息缺失
#   2. 部分房源 price=0（无价格数据，保留0待补充）
# 注意：2026-08 起系统价格单位已改为"元"（BigInteger），
#       不再需要"出租价格 元→万元"转换步骤（已删除）。
#       若恢复的是旧"万元"库，请先执行 scripts/migrate_price_to_yuan.sh。
# 用法：在服务器上执行  bash scripts/fix_import_data.sh
# ============================================================
set -euo pipefail

DB_URL="${DATABASE_URL:-postgresql://hermes:***@localhost:5432/hermes_agent}"

echo "== 修复前 =="
psql "$DB_URL" -t -c "SELECT property_type, count(*) FROM re_properties WHERE district IN ('园区','海景','园林') GROUP BY property_type;"

echo "== 1. 景观从 district 移到 tags =="
psql "$DB_URL" -c "UPDATE re_properties SET tags = CASE WHEN tags IS NULL OR tags='' THEN '景观:'||district ELSE tags||',景观:'||district END WHERE district IN ('园区','海景','园林');"

echo "== 2. district 置空（无真实区域数据，宁可空不能错） =="
psql "$DB_URL" -c "UPDATE re_properties SET district = '' WHERE district IN ('园区','海景','园林');"

echo "== 修复后校验 =="
psql "$DB_URL" -t -c "SELECT count(*) AS remaining_wrong_district FROM re_properties WHERE district IN ('园区','海景','园林');"
psql "$DB_URL" -t -c "SELECT count(*) AS total_with_tags FROM re_properties WHERE tags IS NOT NULL AND tags != '';"
echo "== 完成 =="
