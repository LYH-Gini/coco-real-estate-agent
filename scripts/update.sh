#!/usr/bin/env bash
# =============================================================================
# Coco（可可）房产智能体 · 一键无损更新脚本
#
# 用法（在任何目录都能跑，脚本自己定位仓库根目录）：
#   bash <安装目录>/scripts/update.sh            # 默认：跟随当前通道（也可用 coco update）
#   bash scripts/update.sh --test               # 测试版：切到测试通道并按测试版更新（仅测试机）
#   bash scripts/update.sh --stable             # 稳定版：切回稳定通道并更新
#   bash scripts/update.sh --skip-backup        # 跳过备份（仅纯代码零风险场景）
#   bash scripts/update.sh --no-restart         # 更新后不自动重启（手动重启）
#
# 设计目标：后续"新增功能"无论纯代码还是动表结构，都用这一条命令无损更新。
# 数据库是用户重要数据，三重硬防护：
#   ① 更新前强制备份（可回滚）
#   ② git 只 pull，绝不跑 git clean（会删 .env.db / 加密密钥）
#   ③ 迁移只增不删、事务内执行失败回滚（由 scripts/migrate.py 校验器硬约束）
# 另有两个防混乱的措施（2026-09-21 加）：
#   · 并发保护：flock 独占锁，同一实例不会同时跑两次更新；
#   · 结尾打印「版本 + 提交号」，任何一台机器都能对上"到底是哪一次提交"。
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
VENV_PY="$REPO_ROOT/venv/bin/python"

# ---- 通用输出函数（必须在下面各处之前定义：曾经的坑是"先用了后定义"，一启动就 command not found）----
info(){ echo -e "\033[1;34m==>\033[0m $*"; }
ok(){   echo -e "\033[1;32m   OK\033[0m $*"; }
err(){  echo -e "\033[1;31m   FAIL\033[0m $*" >&2; }
fail(){ echo -e "\033[1;31m   FAIL\033[0m $*" >&2; exit 1; }

# 用户级 systemctl 的稳健调用（2026-09-29 加）：在 sudo -i / 剥净环境里
# `systemctl --user` 连不上用户总线（实测报 "Failed to connect to bus: No medium found"），
# 探测会假失败 → 更新后不重启、进程继续跑旧代码。先按当前环境试，失败则钉住
# XDG_RUNTIME_DIR 再试一次（同一 uid 下这样就能连上）。
sysd_user() {
  systemctl --user "$@" 2>/dev/null && return 0
  XDG_RUNTIME_DIR="/run/user/$(id -u)" systemctl --user "$@" 2>/dev/null
}

# 并发保护（2026-09-21 加）：同一实例同时跑两次更新会互相踩 —— git 索引锁冲突、
# 依赖安装与迁移交叠、服务被重复重启。这里用 flock 拿独占锁，拿不到就明确退出。
# 锁文件是未跟踪文件（不进 git），工作区检查会忽略它。
if command -v flock >/dev/null 2>&1; then
    LOCK_FILE="$REPO_ROOT/.coco-update.lock"
    if exec 9>"$LOCK_FILE"; then
        if ! flock -n 9; then
            err "另一个 Coco 更新正在进行中（锁文件 $LOCK_FILE）—— 等它跑完再执行；"
            echo "  若确认没有任何更新在跑（例如上次被强杀），删除该锁文件后重试即可。"
            exit 1
        fi
    else
        echo "提示: 无法创建更新锁 $LOCK_FILE，本次跳过并发保护。"
    fi
else
    echo "提示: 系统没有 flock，跳过并发保护（请勿同时跑两次更新）。"
fi

# ---- 参数 ----
SKIP_BACKUP=0
NO_RESTART=0
FORCE_CHANNEL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-backup) SKIP_BACKUP=1 ;;
    --no-restart)  NO_RESTART=1 ;;
    --test)        FORCE_CHANNEL="next" ;;      # 测试版：最新待实测的版本
    --stable)      FORCE_CHANNEL="master" ;;    # 稳定版：已实测通过的版本
    --channel)     FORCE_CHANNEL="${2:-}"; shift ;;
    *) echo "未知参数: $1（支持 --test / --stable / --channel <分支> / --skip-backup / --no-restart）" >&2; exit 1 ;;
  esac
  shift
done

# 通道（稳定版 master / 测试版 next，2026-09-21 加）
#   不传通道参数：跟随当前分支 —— 别人的机器在 master 上，永远是稳定版，零迁移（行为与以前完全一致）。
#   测试机：`update.sh --test` 一条命令切到测试版并按测试版更新；`--stable` 一条命令切回稳定版。
#   也可用环境变量 COCO_CHANNEL=next，或在仓库根放 .coco-channel 文件。详见 scripts/coco_channel.sh。
CHANNEL_SCRIPT="$REPO_ROOT/scripts/coco_channel.sh"
if [[ -n "$FORCE_CHANNEL" ]]; then
    export COCO_CHANNEL="$FORCE_CHANNEL"
fi
CUR_BRANCH="$(git -C "$REPO_ROOT" branch --show-current 2>/dev/null || echo '')"
if [[ -f "$CHANNEL_SCRIPT" ]]; then
    WANT_BRANCH="$(bash "$CHANNEL_SCRIPT" want 2>/dev/null || echo "$CUR_BRANCH")"
    if [[ -n "$WANT_BRANCH" && "$WANT_BRANCH" != "$CUR_BRANCH" ]]; then
        info "切换通道：${CUR_BRANCH:-游离} → $WANT_BRANCH"
        bash "$CHANNEL_SCRIPT" switch "$WANT_BRANCH" >/dev/null \
            || fail "切换通道失败（工作区有未提交改动，或网络不通）—— 你的改动没有被覆盖"
        CUR_BRANCH="$(git -C "$REPO_ROOT" branch --show-current 2>/dev/null || echo '')"
    fi
    CHANNEL_LABEL="$(bash "$CHANNEL_SCRIPT" label "$CUR_BRANCH" 2>/dev/null || echo '自定义通道')"
else
    CHANNEL_LABEL="稳定通道"
fi
TEST_TAG=""
if [[ "$CUR_BRANCH" == "next" && -f "$CHANNEL_SCRIPT" ]]; then
    TEST_TAG="$(bash "$CHANNEL_SCRIPT" test-tag 2>/dev/null || echo '')"
fi
info "当前通道：$CHANNEL_LABEL（分支 ${CUR_BRANCH:-游离}${TEST_TAG:+，测试号 $TEST_TAG}）"

# 通道相对稳定线的位置提示：避免"以为在测、其实没有待测内容"（只读，失败不影响更新）
if [[ "$CUR_BRANCH" != "master" && -n "$CUR_BRANCH" ]] && git rev-parse -q --verify origin/master >/dev/null 2>&1; then
    AHEAD_N="$(git rev-list --count origin/master..HEAD 2>/dev/null || echo 0)"
    BEHIND_N="$(git rev-list --count HEAD..origin/master 2>/dev/null || echo 0)"
    [[ "${AHEAD_N:-0}" != "0" ]] && echo "  本机领先稳定版 ${AHEAD_N} 个提交（待实测内容）"
    [[ "${BEHIND_N:-0}" != "0" ]] && echo "  本机落后稳定版 ${BEHIND_N} 个提交（当前没有待实测内容）"
fi

# ---- 前提检查：venv python 必须存在（说明已跑过 install.sh）----
if [[ ! -x "$VENV_PY" ]]; then
  err "未找到虚拟环境 Python: $VENV_PY"
  echo "  请确认已在仓库目录运行过 install.sh（会创建 venv/）。"
  exit 1
fi

info "[1/8] 前置检查：git 工作区"
# 经纪人自己改过代码（已跟踪文件）时的友好模式（2026-09-19 拍板）：
#   ① 先把改动导出成 patch 备份  ② git stash 暂存（只暂存已跟踪文件，绝不动未跟踪的 .env.db/密钥）
#   ③ 正常更新  ④ 更新后自动尝试恢复；有冲突则保留 stash+patch 并保持代码干净
STASHED=0
PATCH_FILE=""
if [[ -n "$(git status --porcelain | grep -vE '^\?\?' || true)" ]]; then
  TS="$(date +%Y%m%d_%H%M%S)"
  BK_DIR="$HOME/backups/real_estate"
  mkdir -p "$BK_DIR"
  PATCH_FILE="$BK_DIR/code_changes_${TS}.patch"
  { git diff; git diff --cached; } > "$PATCH_FILE" 2>/dev/null || true
  echo "  检测到你对代码的本地改动（已跟踪文件），先备份再更新："
  git status --short | head -20
  echo "  · 改动已备份：$PATCH_FILE（$(wc -l < "$PATCH_FILE" 2>/dev/null | tr -d ' ') 行）"
  if git stash push -m "coco-auto-${TS}" >/dev/null 2>&1; then
    STASHED=1
    ok "已暂存你的改动（不含未跟踪文件），继续更新，更新后会自动恢复"
  else
    err "暂存失败，已中止更新（你的改动没有被改动过）"
    exit 1
  fi
else
  ok "工作区干净；本脚本绝不运行 git clean（不删 .env.db / 加密密钥等未跟踪文件）"
fi

info "[2/8] 备份数据库（安全网，可回滚到更新前）"
if [[ "$SKIP_BACKUP" == "1" ]]; then
  echo "  已跳过备份（--skip-backup）"
else
  "$VENV_PY" scripts/backup_db.py backup
  ok "已生成恢复点（~/backups/real_estate/）"
fi

info "[3/8] 拉取最新代码（git pull --ff-only）"
if ! git pull --ff-only; then
  err "拉取失败（网络或历史分叉）"
  if [[ "$STASHED" == "1" ]]; then
    git stash pop >/dev/null 2>&1 && echo "  你的本地改动已恢复（备份：$PATCH_FILE）"
  fi
  exit 1
fi
ok "代码已更新"

# 恢复经纪人自己的改动（有冲突就保留现场：patch + git stash，代码保持干净可运行）
if [[ "$STASHED" == "1" ]]; then
  if git stash pop >/dev/null 2>&1; then
    ok "你的本地改动已恢复（备份也在 $PATCH_FILE）"
  else
    git checkout -- . >/dev/null 2>&1 || true
    git reset --hard HEAD >/dev/null 2>&1 || true
    echo "  提醒：你的改动与新版本有冲突，未能自动合并，但已完整保留："
    echo "    · patch 备份：$PATCH_FILE"
    echo "    · git stash：git -C $REPO_ROOT stash list 查看，用 git stash show -p 看内容"
    ok "已跳过冲突部分，代码保持为新版本（功能正常）"
  fi
fi

info "[4/8] 安装 / 更新 Python 依赖（pip install -e .）"
"$VENV_PY" -m pip install -e . -q
# sqlalchemy 必须显式锁版本：它不在 pyproject 的直接依赖里（只由 mem0 附加项间接引入），
# 所以 pip install -e . 不会校正它。2026-09-27 实测事故：SQLAlchemy 2.1 把
# postgresql:// 的默认驱动从 psycopg2 换成 psycopg3，与 psycopg2-binary 不匹配，
# 建表与迁移全部失败。设备上已装错的版本会被这一行纠正回来。
"$VENV_PY" -m pip install -q "sqlalchemy==2.0.51" "aiohttp==3.14.3" "cryptography==50.0.0" psycopg2-binary
# aiohttp/cryptography 同上：平台适配器（微信、webhook/HTTP 接口等）加载需要，基础依赖不带 aiohttp，
# 装过一次之后老实例更新时也会被这一行补齐。版本与 pyproject 一致。
ok "依赖已就绪"

info "[5/8] 应用数据库迁移（只增不删、事务、失败回滚）"
"$VENV_PY" scripts/migrate.py
ok "数据库迁移检查完成（若提示'数据库已是最新'即无表结构变更）"

info "[6/8] 迁移配置文件（跟随官方版本升级时需要，非交互式）"
# 官方配置带版本号（_config_version）；底座升级后旧配置会落后（实测 33 → 44）。
# 官方命令 hermes config migrate 是交互式的，在无人值守更新里会卡住，故走本脚本。
"$VENV_PY" scripts/migrate_config.py || echo "  警告：配置迁移步骤异常，不阻断更新"
ok "配置迁移步骤完成"

info "补齐海报字体与渲染器（幂等，已装则跳过）"
bash "$REPO_ROOT/scripts/install_fonts.sh" --quiet || echo "  提示: 字体安装未完成，可重跑 scripts/install_fonts.sh（海报会回落系统字体，不阻塞）"

info "统一时区（北京时间；如需保留原时区可设 COCO_SKIP_TZ=1）"
TARGET_TZ="Asia/Shanghai"
CUR_TZ=""
if command -v timedatectl >/dev/null 2>&1; then
  CUR_TZ="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
fi
if [[ -z "$CUR_TZ" && -f /etc/timezone ]]; then
  CUR_TZ="$(tr -d '[:space:]' < /etc/timezone 2>/dev/null || true)"
fi
if [[ "${COCO_SKIP_TZ:-0}" == "1" ]]; then
  echo "  已跳过（COCO_SKIP_TZ=1）"
elif [[ "$CUR_TZ" == "$TARGET_TZ" ]]; then
  ok "服务器时区已是 $TARGET_TZ"
elif command -v timedatectl >/dev/null 2>&1 && sudo timedatectl set-timezone "$TARGET_TZ" 2>/dev/null; then
  ok "服务器时区已统一为 $TARGET_TZ（当前 $(date '+%Y-%m-%d %H:%M %Z')）"
elif sudo ln -sf "/usr/share/zoneinfo/$TARGET_TZ" /etc/localtime 2>/dev/null; then
  echo "$TARGET_TZ" | sudo tee /etc/timezone >/dev/null 2>&1 || true
  ok "服务器时区已统一为 $TARGET_TZ"
else
  echo "  提示: 未能自动设置时区（可能需要 sudo）。手动: sudo timedatectl set-timezone $TARGET_TZ"
fi
# Coco 标准运行时配置（幂等）：时区 + 轮次上限 + 压缩阈值等，避免被向导/重装冲掉
if "$REPO_ROOT/venv/bin/python" "$REPO_ROOT/scripts/coco_config_align.py"; then
  ok "Coco 标准运行时配置已对齐（时区 / 轮次 500 / 压缩阈值 0.8 / 保留最近 40 条）"
else
  echo "  提示: 运行时配置对齐未完成，可执行 coco config set 手动设置，或用 coco config 查看当前值"
fi

info "[7/8] 重启服务（优先走官方重启入口，兼容用户服务 / 旧版系统服务）"
if [[ "$NO_RESTART" == "1" ]]; then
  echo "  已跳过重启（--no-restart），请稍后手动重启。"
else
  RESTARTED=0
  RESTART_HOW=""
  # 首选官方入口（与 coco restart 同一条路）：它自己解析单元名与作用域，
  # 在 sudo -i 这类"连不上用户总线"的环境里同样可用（2026-09-29 实测）。
  if [[ -x "$REPO_ROOT/venv/bin/hermes" ]]; then
    "$REPO_ROOT/venv/bin/hermes" gateway restart >/dev/null 2>&1 && { RESTARTED=1; RESTART_HOW="官方重启入口"; }
  else
    "$VENV_PY" -m hermes_cli.main gateway restart >/dev/null 2>&1 && { RESTARTED=1; RESTART_HOW="官方重启入口"; }
  fi
  # 回退：候选单元名 × 用户/系统两级（用户级带钉住 XDG_RUNTIME_DIR 的重试）
  if [[ "$RESTARTED" == "0" ]] && command -v systemctl >/dev/null 2>&1; then
    for _unit in hermes-gateway hermes-agent coco; do
      if sysd_user is-active --quiet "$_unit"; then
        if sysd_user restart "$_unit"; then RESTARTED=1; RESTART_HOW="用户服务 $_unit"; break; fi
      elif systemctl is-active --quiet "$_unit" 2>/dev/null; then
        if sudo systemctl restart "$_unit" 2>/dev/null; then RESTARTED=1; RESTART_HOW="系统服务 $_unit"; break; fi
      fi
    done
  fi
  if [[ "$RESTARTED" == "1" ]]; then
    ok "服务已重启（$RESTART_HOW）"
  else
    err "服务没能重启：新代码已下载到本地，但正在运行的进程还是旧代码（Coco 会继续按旧行为回答）。"
    echo "  手动重启：coco restart"
    echo "  若上面这条也报错，用：XDG_RUNTIME_DIR=/run/user/\$(id -u) systemctl --user restart hermes-gateway"
    echo "  注意：这次更新若是在 sudo -i / 另一个用户下执行的，请回到安装该实例的用户下执行 coco restart。"
  fi
  # 等新进程真正起来（服务是 Type=simple，重启会在进程刚起来时就返回）
  if [[ "$RESTARTED" == "1" ]] && command -v systemctl >/dev/null 2>&1; then
    for _ in $(seq 1 30); do
      sysd_user is-active --quiet hermes-gateway.service && break
      sleep 2
    done
    sleep 5
  fi
fi

# 复核本次更新是否真的生效：进程启动时会把代码指纹写进 gateway_state.json，
# 与本地 HEAD 不一致 = 跑的还是旧代码（2026-09-29 加，来源是一次实测事故）。
LOCAL_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo '')"
RUN_SHA="$(HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}" "$VENV_PY" - <<'PY' 2>/dev/null || true
import json, os, pathlib
home = pathlib.Path(os.environ.get("HERMES_HOME") or (pathlib.Path.home() / ".hermes"))
try:
    print(json.loads((home / "gateway_state.json").read_text()).get("code_sha") or "")
except Exception:
    print("")
PY
)"
if [[ -n "$LOCAL_SHA" && -n "$RUN_SHA" && "$RUN_SHA" != "$LOCAL_SHA" ]]; then
  err "本次更新尚未生效：正在运行的网关仍是旧代码（进程指纹 ${RUN_SHA:0:7} ≠ 本地 ${LOCAL_SHA:0:7}）。"
  echo "  处理：coco restart（在安装该实例的用户下执行）"
elif [[ -n "$RUN_SHA" ]]; then
  ok "新代码已生效（进程指纹 ${RUN_SHA:0:7}）"
fi

info "[8/8] 部署健康自检"
"$VENV_PY" scripts/healthcheck.py || echo "  警告：健康自检存在 FAIL 项，请查看上方提示"

echo ""
# 清理 hermes 命令入口（2026-09-21 拍板：命令统一用 coco，不再对外暴露 hermes）
# 幂等：只删指向本安装目录的软链，别家的同名命令不动
for _hlink in /usr/local/bin/hermes "$HOME/.local/bin/hermes"; do
  [[ -L "$_hlink" ]] || continue
  _htarget="$(readlink -f "$_hlink" 2>/dev/null || echo '')"
  if [[ "$_htarget" == "$REPO_ROOT/"* ]]; then
    rm -f "$_hlink" 2>/dev/null || sudo rm -f "$_hlink" 2>/dev/null || true
    echo "  已移除 hermes 命令入口（统一用 coco）"
  fi
done

# 确保 coco 命令入口存在（新命令靠这条通道分发给已安装实例；幂等，失败不阻断）
COCO_BIN="$REPO_ROOT/scripts/coco.sh"
if [[ -x "$COCO_BIN" ]] && ! command -v coco >/dev/null 2>&1; then
  COCO_LINKED=0
  if [[ -w /usr/local/bin ]] && ln -sf "$COCO_BIN" /usr/local/bin/coco 2>/dev/null; then
    COCO_LINKED=1
  elif command -v sudo >/dev/null 2>&1 && sudo ln -sf "$COCO_BIN" /usr/local/bin/coco 2>/dev/null; then
    COCO_LINKED=1
  else
    mkdir -p "$HOME/.local/bin"
    if ln -sf "$COCO_BIN" "$HOME/.local/bin/coco" 2>/dev/null; then
      COCO_LINKED=1
      # ~/.local/bin 常常不在 PATH：照官方那套补一行，否则用户敲 coco 会找不到命令
      if [[ -f "$REPO_ROOT/scripts/path_guard.sh" ]]; then
        bash "$REPO_ROOT/scripts/path_guard.sh" ensure || true
      fi
    fi
  fi
  if [[ "$COCO_LINKED" == "1" ]]; then
    ok "coco 命令已就绪（coco version 查版本号 / coco check 体检）"
  else
    echo "  提示: coco 命令未创建，可手动执行 sudo ln -sf $COCO_BIN /usr/local/bin/coco"
  fi
fi

# 版本号直接读仓库根 VERSION 文件（2026-08-29 加，与 install.sh 保持一致）：以后只改 VERSION，更新终端自动同步
COCO_VER=$(cat "$REPO_ROOT/VERSION" 2>/dev/null | tr -d '[:space:]' || echo "未知")
# 版本号形如 0.21.3-1：前半段是官方底座，后半段是 Coco 自己的第 N 次发行
COCO_BASE="${COCO_VER%%-*}"
echo -e "版本: \033[1;34mv${COCO_VER}\033[0m"
# 提交号：与安装提示一致，便于任何一台机器对齐"哪一次提交"
COCO_COMMIT=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo "未知")
echo -e "提交: \033[1;34m${COCO_COMMIT}\033[0m"
echo -e "通道: \033[1;34m${CHANNEL_LABEL}${TEST_TAG:+（测试号 $TEST_TAG）}\033[0m"
ok "无损更新完成。若本次更新涉及表结构，数据库已通过迁移升级，旧数据全部保留。"
