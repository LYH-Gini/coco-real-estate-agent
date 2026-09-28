#!/usr/bin/env bash
# =============================================================================
# Coco 命令入口（一个前缀管到底）
#
# 命令分三组：
#   日常运维（我们自己的工具）：version / check / backup / backups / restore / data-clean / images-recover / update / uninstall
#   服务与诊断：                status / logs / start / stop / restart
#   安装配置（转发官方命令）：  model / setup / gateway / pairing
#
# 为什么配置文件里既有 coco 又有 hermes：
#   `hermes` 是底层框架（Hermes Agent）的官方程序；`coco` 是本项目的封装。
#   安装配置类动作本质是官方程序的命令，这里用 exec 原样转发 ——
#   等于"换了个名字的同一个程序"：交互输入、TTY、Ctrl-C、退出码完全一致。
#   官方程序本体仍在安装目录里（venv/bin/hermes），但 2026-09-21 起不再对外
#   暴露 `hermes` 命令入口（install/update 会移除指向本安装目录的 hermes 软链，
#   只保留 coco）—— 要用官方子命令就走 `coco cli <参数>`。
#
# 用法: coco [命令]        （不给参数 = version）
# =============================================================================
set -euo pipefail

# 本脚本会被软链到 /usr/local/bin/coco（或 ~/.local/bin/coco）调用，
# 那时 BASH_SOURCE 是软链路径而不是真实脚本路径 —— 必须先解析软链，否则读不到 VERSION。
SELF="${BASH_SOURCE[0]}"
if command -v readlink >/dev/null 2>&1 && readlink -f "$SELF" >/dev/null 2>&1; then
  SELF="$(readlink -f "$SELF")"
fi
REPO_ROOT="$(cd "$(dirname "$SELF")/.." && pwd)"
VENV_PY="$REPO_ROOT/venv/bin/python"
HERMES_BIN="$REPO_ROOT/venv/bin/hermes"      # 官方程序：用绝对路径，不依赖 PATH
COCO_VER="$(tr -d '[:space:]' < "$REPO_ROOT/VERSION" 2>/dev/null || echo "未知")"
COCO_BASE="${COCO_VER%%-*}"
COCO_COMMIT="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo "未知")"
COCO_BRANCH="$(git -C "$REPO_ROOT" branch --show-current 2>/dev/null || echo "")"
COCO_CHANNEL_LABEL="$(bash "$REPO_ROOT/scripts/coco_channel.sh" label "$COCO_BRANCH" 2>/dev/null || echo "")"
COCO_TEST_TAG="$(bash "$REPO_ROOT/scripts/coco_channel.sh" test-tag 2>/dev/null || echo "")"

# 转发给官方程序：找不到就明确报错（绝不假装成功）
run_hermes() {
  if [[ -x "$HERMES_BIN" ]]; then
    exec "$HERMES_BIN" "$@"
  elif command -v hermes >/dev/null 2>&1; then
    exec hermes "$@"
  else
    echo "找不到官方 hermes 程序（期望位置：$HERMES_BIN）" >&2
    echo "请确认 Coco 已安装：ls $REPO_ROOT/venv/bin/hermes" >&2
    exit 1
  fi
}

# 依赖本地 Python 环境的子命令：缺了就明确报错（不要抛裸的 shell 错误）
need_venv() {
  if [[ ! -x "$VENV_PY" ]]; then
    echo "找不到 Coco 的 Python 环境（$VENV_PY）" >&2
    echo "请确认已安装：ls $VENV_PY" >&2
    exit 1
  fi
}

# 危险操作先确认（输 yes）
confirm_yes() {   # $1=提示
  printf "%s\nType 'yes' to confirm: " "$1"
  local answer=""
  read -r answer || answer=""
  if [[ "$(echo "${answer:-}" | tr -d '[:space:]' | tr 'A-Z' 'a-z')" != "yes" ]]; then
    echo "已取消，未做任何修改。"
    exit 0
  fi
}

# 非 exec 的转发：调用方要拿退出码做后处理（exec 会连进程一起替掉）
coco_call_hermes() {
  if [[ -x "$HERMES_BIN" ]]; then
    "$HERMES_BIN" "$@"
  elif command -v hermes >/dev/null 2>&1; then
    hermes "$@"
  else
    echo "找不到官方 hermes 程序（期望位置：$HERMES_BIN）" >&2
    echo "请确认 Coco 已安装：ls $REPO_ROOT/venv/bin/hermes" >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# 重启后的就绪复查（coco restart 用）
#
# 官方重启流程在「等新进程把运行状态写成 running/degraded」这一步只等 155 秒
# （60 秒 + systemd 的 RestartSec + TimeoutStartSec）；冷启动比这更慢的机器上，
# 它会先打一句「did not become active within …」就返回 —— 那句话只说明"没等到
# 确认"，不代表服务没起来。官方命令返回后这里自己再确认一次，把结论说清楚：
#   已就绪   → 不额外打印（官方那行已经说过了）
#   还没就绪 → 等到就绪；到点了如实说不确定，并给日志入口
#   没起来   → 明确报出来并返回非零（不让人猜）
# 等待上限可用 COCO_RESTART_RECHECK_SECS 调整（默认 180 秒），
# 轮询间隔可用 COCO_RESTART_RECHECK_INTERVAL（默认 3 秒；单测里调小以免等太久）。
# ---------------------------------------------------------------------------
COCO_RESTART_RECHECK_SECS="${COCO_RESTART_RECHECK_SECS:-180}"

# 输出 "<服务名> <状态>"：用户服务 hermes-gateway 优先，兼容老实例的系统服务 hermes-agent
coco_service_state() {
  local u s
  u="$(systemctl --user is-active hermes-gateway 2>/dev/null || true)"
  case "$u" in
    active|activating|reloading|deactivating) echo "hermes-gateway $u"; return 0 ;;
  esac
  s="$(systemctl is-active hermes-agent 2>/dev/null || true)"
  case "$s" in
    active|activating|reloading|deactivating) echo "hermes-agent $s"; return 0 ;;
  esac
  echo "hermes-gateway ${u:-unknown}"
}

# 主进程号（$1=服务名）
coco_main_pid() {
  if [[ "${1:-hermes-gateway}" == "hermes-agent" ]]; then
    systemctl show hermes-agent -p MainPID 2>/dev/null | sed -n 's/^MainPID=\([0-9]\{1,\}\).*/\1/p'
  else
    systemctl --user show hermes-gateway -p MainPID 2>/dev/null | sed -n 's/^MainPID=\([0-9]\{1,\}\).*/\1/p'
  fi
}

# 输出运行状态（running / degraded / starting / stopped / startup_failed …）
# 状态文件里记的 pid 与当前主进程不一致时视为"未就绪" —— 那是上一代进程写的记录
coco_gateway_state() {
  local unit="${1:-hermes-gateway}" home f state pid main_pid
  home="${HERMES_HOME:-$HOME/.hermes}"
  f="$home/gateway_state.json"
  [[ -f "$f" ]] || return 1
  state="$(sed -n 's/.*"gateway_state"[[:space:]]*:[[:space:]]*"\([a-z_]*\)".*/\1/p' "$f" | head -n 1)"
  [[ -n "$state" ]] || return 1
  pid="$(sed -n 's/.*"pid"[[:space:]]*:[[:space:]]*\([0-9]\{1,\}\).*/\1/p' "$f" | head -n 1)"
  main_pid="$(coco_main_pid "$unit")"
  if [[ -n "$main_pid" && "$main_pid" != "0" && -n "$pid" && "$pid" != "$main_pid" ]]; then
    return 1
  fi
  echo "$state"
}

# 等到就绪：0=已就绪；1=进程在跑但没报就绪；2=服务没起来
coco_wait_gateway_ready() {
  local budget="${1:-180}" start=$SECONDS unit="" us="" state dead=0 waited=0
  while :; do
    read -r unit us <<< "$(coco_service_state)"
    if [[ "$us" == "active" ]]; then
      dead=0
      state="$(coco_gateway_state "$unit" || echo "unknown")"
      case "$state" in running|degraded) return 0 ;; esac
    else
      case "$us" in
        activating|reloading|deactivating) dead=0 ;;
        *) dead=$((dead + 1)); [[ $dead -lt 3 ]] || return 2 ;;
      esac
    fi
    waited=$((SECONDS - start))
    [[ $waited -lt $budget ]] || return 1
    if [[ $waited -gt 0 && $((waited % 30)) -eq 0 ]]; then
      echo "⏳ 还在等就绪（已等 ${waited} 秒 / 最多 ${budget} 秒）..."
    fi
    sleep "${COCO_RESTART_RECHECK_INTERVAL:-3}"
  done
}

case "${1:-version}" in
  # ---------- 日常运维 ----------
  version|--version|-v|"")
    echo "Coco v${COCO_VER} · 提交 ${COCO_COMMIT}${COCO_CHANNEL_LABEL:+ · ${COCO_CHANNEL_LABEL}}${COCO_TEST_TAG:+ · 测试号 ${COCO_TEST_TAG}}"
    ;;
  check)
    shift
    need_venv
    exec "$VENV_PY" "$REPO_ROOT/scripts/healthcheck.py" "$@"
    ;;
  backup)
    shift
    need_venv
    exec "$VENV_PY" "$REPO_ROOT/scripts/backup_db.py" backup "$@"
    ;;
  backups)
    need_venv
    exec "$VENV_PY" "$REPO_ROOT/scripts/backup_db.py" list
    ;;
  restore)
    shift
    R_FILE=""; R_MIGRATION=""; R_YES=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --file|-f) R_FILE="${2:-}"; shift 2 ;;
        --migration|-m) R_MIGRATION="${2:-}"; shift 2 ;;
        --yes|-y) R_YES=1; shift ;;
        *) echo "未知参数: $1" >&2
           echo "用法: coco restore --file <数据库备份.dump>   或   coco restore --migration <迁移包.tar.gz>" >&2
           exit 1 ;;
      esac
    done
    if [[ -z "$R_FILE" && -z "$R_MIGRATION" ]]; then
      echo "用法: coco restore --file <数据库备份.dump>   或   coco restore --migration <迁移包.tar.gz>" >&2
      echo "备份文件名可用 coco backups 查看。" >&2
      exit 1
    fi
    if [[ "$R_YES" != "1" ]]; then
      echo "恢复会覆盖当前数据库，建议先执行 coco backup 留一份还原点。"
      confirm_yes "确认恢复？"
    fi
    need_venv
    if [[ -n "$R_MIGRATION" ]]; then
      exec "$VENV_PY" "$REPO_ROOT/scripts/backup_db.py" restore_migration --migration-tar "$R_MIGRATION"
    else
      exec "$VENV_PY" "$REPO_ROOT/scripts/backup_db.py" restore --restore-file "$R_FILE"
    fi
    ;;
  data-clean)
    shift
    need_venv
    exec "$VENV_PY" "$REPO_ROOT/scripts/data_clean.py" "$@"
    ;;
  images-recover)
    shift
    need_venv
    exec "$VENV_PY" "$REPO_ROOT/scripts/recover_property_images.py" "$@"
    ;;
  update)
    shift
    if [[ -f "$REPO_ROOT/scripts/update.sh" ]]; then
      exec bash "$REPO_ROOT/scripts/update.sh" "$@"
    fi
    echo "找不到更新脚本（$REPO_ROOT/scripts/update.sh）—— 安装可能不完整" >&2
    echo "可重装（会保留数据库与密钥）：curl -fsSL https://gitee.com/LYH-Gini/coco-real-estate-agent/raw/master/install.sh -o install.sh && bash install.sh" >&2
    exit 1
    ;;
  uninstall)
    shift
    exec bash "$REPO_ROOT/scripts/uninstall.sh" "$@"
    ;;

  # ---------- 服务与诊断 ----------
  status)
    if [[ -x "$HERMES_BIN" ]] || command -v hermes >/dev/null 2>&1; then
      run_hermes gateway status
    fi
    if command -v systemctl >/dev/null 2>&1; then
      exec systemctl --user status hermes-gateway --no-pager
    fi
    echo "本机既没有 hermes 命令也没有 systemctl，无法查看服务状态。" >&2
    exit 1
    ;;
  logs)
    shift
    LINES="${1:-50}"
    if command -v journalctl >/dev/null 2>&1; then
      exec journalctl --user -u hermes-gateway -n "$LINES" --no-pager
    fi
    echo "本机没有 journalctl，无法读取日志。" >&2
    exit 1
    ;;
  start|stop)
    run_hermes gateway "$1"
    ;;
  restart)
    # 官方重启流程走完后自己再确认一次就绪（原因见上方「重启后的就绪复查」）
    shift
    RC=0
    coco_call_hermes gateway restart "$@" || RC=$?
    if [[ $RC -ne 0 ]]; then
      exit $RC                      # 官方已经给出失败原因，不追加
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
      exit $RC                      # 没有 systemd 就不做复查（不给没有依据的结论）
    fi
    if coco_wait_gateway_ready 0; then
      exit 0                        # 官方那行已经确认过就绪，不重复打印
    fi
    # 等就绪期间不打提示行（保持静默等待，设定仍是 COCO_RESTART_RECHECK_SECS 秒）
    RCK=0
    coco_wait_gateway_ready "$COCO_RESTART_RECHECK_SECS" || RCK=$?
    read -r _ _UNIT <<< "$(coco_service_state)"
    PID="$(coco_main_pid "$_UNIT")"
    case "$RCK" in
      0)
        if [[ "$(coco_gateway_state "$_UNIT" || echo "")" == "degraded" ]]; then
          echo "✓ Coco 服务已重启（PID ${PID:-未知}）—— 状态 degraded：有平台还没连上，详情看 coco status"
        else
          echo "✓ Coco 服务已重启并在运行（PID ${PID:-未知}）"
        fi
        exit 0
        ;;
      1)
        FINAL_STATE="$(coco_gateway_state "$_UNIT" || echo "未就绪")"
        echo "⚠ 服务进程在运行（PID ${PID:-未知}），但 ${COCO_RESTART_RECHECK_SECS} 秒内状态还是 ${FINAL_STATE}"
        echo "  稍后用 coco status 复查；日志：coco logs 50"
        exit 0
        ;;
      *)
        echo "⚠ Coco 服务当前不在运行（机器人不会回话；客户数据不受影响）"
        echo "  日志：coco logs 50"
        exit 1
        ;;
    esac
    ;;

  # ---------- 安装配置（转发官方命令） ----------
  model|setup)
    # 必须把后面的参数一起转发：coco setup model / coco setup tools 否则会丢参数
    CMD="$1"; shift
    run_hermes "$CMD" "$@"
    ;;
  config|doctor|tools)
    CMD="$1"; shift
    run_hermes "$CMD" "$@"
    ;;
  gateway)
    shift
    run_hermes gateway "$@"
    ;;
  pairing)
    shift
    run_hermes pairing "$@"
    ;;
  cli)
    # 逃生口：临时用官方程序的任意子命令（排障/支持用）
    shift
    run_hermes "$@"
    ;;

  # ---------- 帮助 ----------
  help|--help|-h)
    cat <<EOF
Coco v${COCO_VER}

用法: coco <命令>

日常运维:
  version    查看版本号（默认）
  check      部署体检（服务/依赖/数据库/密钥/备份/日志等）
  backup     手动备份数据库（coco backup --force 强制备份）
  backups    查看备份列表
  restore    恢复数据：coco restore --file <备份.dump> / --migration <迁移包.tar.gz>
  data-clean 数据清理：预演 / 清理已关闭客户与已成交房源（向导式，动手前自动备份）
  images-recover  房源照片修复：把还活着的照片补进归档、从备份图片包捞回已丢的（默认只演练）
  update     更新到最新版（内部即完整更新流程：备份 → 拉代码 → 依赖 → 迁移 → 重启 → 体检）
  uninstall  卸载 Coco（三档菜单 + 输 yes 确认；1/2 档会先自动备份，3 档不备份）

服务与诊断:
  status     查看服务状态
  logs       查看日志，默认最近 50 行（coco logs 200）
  start      启动服务
  restart    重启服务（改完配置后要执行：服务只在启动时读配置）
  stop       停止服务

安装配置:
  model      选择模型 / 填 API Key
  setup      配置向导（飞书等）
  config     查看或修改配置：config edit / config set <键> <值>
             （config set model.context_length <数值>：模型上下文窗口大小；填错不会报错，但会影响何时开始压缩对话）
  doctor     环境自检
  tools      配置工具与技能
  gateway    服务安装等：coco gateway install
  pairing    飞书配对批准：coco pairing approve feishu <配对码>

高级（排障用）:
  cli        直接用底层程序的子命令：coco cli doctor（日常不需要）

  help       显示本帮助

说明:
  · 日常统一用 coco；底层程序命令不再对外暴露（需要时用 coco cli <子命令>）。
  · 版本号存在 $REPO_ROOT/VERSION；官方 hermes --version 显示的是底层框架版本。
  · 更新也可以用（老实例/排障）：git -C <安装目录> pull && bash <安装目录>/scripts/update.sh
    （不要用 install.sh 更新：它会重建安装目录，清掉数据库密钥与图片缓存。）
EOF
    ;;
  *)
    echo "未知命令: $1" >&2
    echo "用法: coco help" >&2
    exit 1
    ;;
esac
