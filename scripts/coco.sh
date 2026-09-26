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
    echo "可重装（会保留数据库与密钥）：curl -fsSL https://gitee.com/lyh-gini/coco-real-estate-agent/raw/master/install.sh -o install.sh && bash install.sh" >&2
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
  start|stop|restart)
    run_hermes gateway "$1"
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
