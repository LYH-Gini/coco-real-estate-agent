#!/usr/bin/env python3
"""Coco 备份/密钥 watchdog 脚本

由 cron 任务 coco_watchdog 每 6 小时调用（no_agent 模式）：
- 检测数据库备份新鲜度（最新 dump <48h 否则告警）
- 检测加密密钥存在性与备份
- 检测磁盘空间（<2GB 告警）
- 检测 gateway 服务存活

正常时无输出（cron no_agent 模式静默）；异常时输出告警文本，
由 cron 原样推送到飞书。输出必须是稳定格式（无随机内容）。
"""
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HOME = Path.home()
BACKUP_DIR = HOME / "backups" / "real_estate"
HERMES_HOME = os.environ.get("HERMES_HOME", str(HOME / ".hermes"))

# 告警阈值
BACKUP_MAX_AGE_HOURS = 48
DISK_MIN_MB = 2048


def check_backup():
    """最新 dump 是否在 48h 内"""
    if not BACKUP_DIR.is_dir():
        return f"❌ 备份目录不存在: {BACKUP_DIR}，请立即执行 python3 scripts/backup_db.py backup --force"
    dumps = [f for f in BACKUP_DIR.iterdir() if f.name.endswith(".dump")]
    if not dumps:
        return "❌ 无任何数据库备份！请立即执行 python3 scripts/backup_db.py backup --force"
    newest = max(dumps, key=lambda f: f.stat().st_mtime)
    age_h = (time.time() - newest.stat().st_mtime) / 3600
    if age_h > BACKUP_MAX_AGE_HOURS:
        return f"❌ 备份过期 {age_h:.0f}h（最新: {newest.name}，> {BACKUP_MAX_AGE_HOURS}h），请立即备份并检查自动备份任务"
    return None  # 正常，静默


def check_key():
    """加密密钥配置 + 备份"""
    env_file = HOME / "hermes-agent" / ".env.db"
    enc_key = None
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("COCO_ENC_KEY="):
                enc_key = line.split("=", 1)[1].strip()
    if not enc_key:
        return "❌ COCO_ENC_KEY 未配置！客户手机号/微信将无法加密，请检查 .env.db"
    key_backup = BACKUP_DIR / "enc_key.txt"
    if not key_backup.is_file():
        return "⚠️ 加密密钥备份缺失！密钥丢失将永久无法解密客户数据，请立即备份到 ~/backups/real_estate/enc_key.txt"
    return None


def check_disk():
    """磁盘空间"""
    st = os.statvfs("/")
    free_mb = st.f_bavail * st.f_frsize / 1024 / 1024
    if free_mb < DISK_MIN_MB:
        return f"❌ 磁盘可用仅 {free_mb:.0f}MB，请立即清理空间"
    return None


def check_service():
    """gateway 服务存活（用户服务 + 备选系统服务）"""
    import subprocess
    for svc, user in (("hermes-gateway", True), ("hermes-agent", False)):
        cmd = ["systemctl", "--user", "is-active", "--quiet", svc] if user else ["systemctl", "is-active", "--quiet", svc]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=10)
            if r.returncode == 0:
                return None  # 任一服务活着就 OK
        except Exception:
            continue
    return "❌ gateway 服务未运行（hermes-gateway / hermes-agent 均 inactive），飞书消息可能无人响应！"


def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    problems = []
    for name, fn in (("备份", check_backup), ("密钥", check_key),
                     ("磁盘", check_disk), ("服务", check_service)):
        try:
            msg = fn()
            if msg:
                problems.append(f"[{name}] {msg}")
        except Exception as e:
            problems.append(f"[{name}] 检查异常: {e}")

    if not problems:
        return  # 一切正常，静默（cron no_agent 模式空输出=不打扰）

    # 有异常才输出（cron 会原样推送到飞书）
    print(f"【Coco 系统告警】{now}")
    for p in problems:
        print(p)


if __name__ == "__main__":
    main()
