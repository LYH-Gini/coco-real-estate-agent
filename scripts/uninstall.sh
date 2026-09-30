#!/usr/bin/env bash
# =============================================================================
# Coco 卸载（对外入口：`coco uninstall`）
#
# 定位：**给正式版实例用**（别人装的那台，或你想临时清掉又不想重装系统的情况）。
# 测试机不需要它 —— 直接重装系统更彻底。
#
# 为什么要有这一层：官方 `hermes uninstall` 只管 Hermes 本体 —— 停/卸 gateway 服务、
# 清 PATH 与 hermes 软链、删代码目录、按需删 ~/.hermes。它不知道 Coco 专有的四样：
#   ① crontab 里的夜间备份行   ② `coco` 软链（官方只认 hermes/hermes-acp/hermes-agent）
#   ③ 字体与渲染依赖           ④ PostgreSQL 里的房产数据库与账号
# 而且它删代码目录时会连 **.env.db（加密密钥）** 一起删 —— 密钥丢了，
# 客户手机号/微信就永远解不开，所以动手前必须先备份。
#
# 交互（与官方同构）：一条命令 → 三档菜单 → 输 yes 确认
#   1) 保留数据（推荐）  2) 卸载 + 清状态  3) 彻底清理（含数据库）  4) 取消
#
# 备份策略（2026-09-21 定）：
#   · 1/2 档**自动备份**（数据要留，代码目录里的 .env.db 密钥会被删，必须留还原点），
#     并把「备份包路径 + 下载到本地电脑的命令」打印出来，由使用者自己下载。
#   · 3 档**不备份**（都选彻底删除了就是不想要了），只提示：如需备份请先手动执行 coco backup。
#   · 任何时候都可手动备份：coco backup（或 --backup 强制在 3 档也备份）。
#
# 非交互入口：
#   --mode 1|2|3   直接指定档位（免菜单）
#   --yes          跳过最后的 yes 确认
#   --dry-run      只显示会做什么，不改任何东西
#   --no-backup    跳过 1/2 档的自动备份
#   --backup       强制在 3 档也先备份（默认 3 档不备份）
#   --remove-fonts 连字体与渲染依赖一起清（默认保留）
#
# 测试/特殊场景覆盖入口：
#   COCO_UNINSTALL_SKIP_OFFICIAL=1   不调官方卸载
#   COCO_UNINSTALL_SKIP_DB=1         不碰数据库
#   COCO_UNINSTALL_BACKUP_CMD="cmd"  覆盖备份命令
#   COCO_UNINSTALL_HOME=<目录>       指定 HOME（软链/备份目录用）
#   COCO_UNINSTALL_CRONTAB=<cmd>     指定 crontab 命令（测试用假命令）
#   COCO_UNINSTALL_ALLOW_MENU=1      非终端环境也显示菜单（测试用；输入空行即取消）
# =============================================================================

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[1;34m'; NC='\033[0m'
info() { echo -e "${YELLOW}==>${NC} $*"; }
ok()   { echo -e "${GREEN}  OK${NC} $*"; }
warn() { echo -e "${YELLOW}  WARN${NC} $*"; }
fail() { echo -e "${RED}  FAIL${NC} $*" >&2; exit 1; }

# ---- 自保护：官方卸载会删掉仓库目录，脚本本体在里面会被连带删掉 → 先复制到临时目录再执行 ----
if [[ "${COCO_UNINSTALL_SELFCOPY:-0}" != "1" ]]; then
    _REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    _TMP_SELF="$(mktemp "${TMPDIR:-/tmp}/coco-uninstall-XXXXXX.sh")"
    cp "${BASH_SOURCE[0]}" "$_TMP_SELF" || fail "无法复制自身到临时目录"
    chmod +x "$_TMP_SELF"
    case "$_TMP_SELF" in
        */coco-uninstall-*.sh) export COCO_UNINSTALL_TMP_SELF="$_TMP_SELF" ;;
    esac
    COCO_UNINSTALL_SELFCOPY=1 COCO_UNINSTALL_REPO_ROOT="${COCO_UNINSTALL_REPO_ROOT:-$_REPO}" exec bash "$_TMP_SELF" "$@"
fi

REPO_DIR="${COCO_UNINSTALL_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
TARGET_HOME="${COCO_UNINSTALL_HOME:-$HOME}"
CRONTAB_BIN="${COCO_UNINSTALL_CRONTAB:-crontab}"
VENV_PY="$REPO_DIR/venv/bin/python"
# 官方程序：优先用安装目录里的那一个 —— 客户机上没有 `hermes` 命令入口（install/update 会移除指向
# 本安装目录的软链），只按 PATH 找会漏掉「停服务」与「官方卸载」两步（2026-09-27 修）。
HERMES_CLI=""
if [[ -x "$REPO_DIR/venv/bin/hermes" ]]; then
    HERMES_CLI="$REPO_DIR/venv/bin/hermes"
elif command -v hermes >/dev/null 2>&1; then
    HERMES_CLI="$(command -v hermes)"
fi
BACKUP_DIR="$TARGET_HOME/backups/real_estate"

DRY_RUN=0
ASSUME_YES=0
BACKUP_FLAG=""      # ""=按档位默认；"yes"=强制备份；"no"=强制不备份
REMOVE_FONTS=0
MODE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode) MODE="${2:-}"; shift 2 ;;
        --yes) ASSUME_YES=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --no-backup) BACKUP_FLAG="no"; shift ;;
        --backup) BACKUP_FLAG="yes"; shift ;;
        --remove-fonts) REMOVE_FONTS=1; shift ;;
        -h|--help) sed -n '3,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) fail "未知参数: $1（用 --help 看用法）" ;;
    esac
done
[[ -n "$MODE" && ! "$MODE" =~ ^[123]$ ]] && fail "--mode 只能是 1、2 或 3"

# 退出时清理自己的临时副本（只清我们约定的文件名）
if [[ -n "${COCO_UNINSTALL_TMP_SELF:-}" ]]; then
    trap 'rm -f "$COCO_UNINSTALL_TMP_SELF" 2>/dev/null || true' EXIT
fi

# 数据库连接串（决定保留还是删除哪个库）
DB_NAME=""; DB_USER=""
if [[ -f "$REPO_DIR/.env.db" ]]; then
    DB_URL="$(grep -E '^DATABASE_URL=' "$REPO_DIR/.env.db" 2>/dev/null | head -1 | cut -d= -f2- || true)"
    if [[ -n "${DB_URL:-}" ]]; then
        DB_NAME="$(echo "$DB_URL" | sed -E 's#.*/([^/?]+).*#\1#')"
        DB_USER="$(echo "$DB_URL" | sed -E 's#^[a-z]+://([^:@/]+).*#\1#')"
    fi
fi

mode_label() { case "$1" in 1) echo "保留数据（数据库与状态都保留）";; 2) echo "卸载程序 + 清理状态（数据库保留）";; 3) echo "彻底清理（程序 + 状态 + 数据库）";; esac; }

echo "========================================"
echo " Coco 卸载"
echo " 安装目录: $REPO_DIR"
echo " 数据库:   ${DB_NAME:-（未检测到）}${DB_USER:+（账号 $DB_USER）}"
[[ "$DRY_RUN" == "1" ]] && echo " 模式:     干跑（不修改任何东西）"
echo "========================================"

# ---- 选择卸载程度 ----
if [[ -z "$MODE" ]]; then
    if [[ ! -t 0 && "${COCO_UNINSTALL_ALLOW_MENU:-0}" != "1" ]]; then
        fail "当前环境不是交互终端，请用 --mode 1|2|3 指定卸载程度（可加 --yes 免确认）"
    fi
    echo ""
    echo "卸载程度："
    echo -e "  ${GREEN}1)${NC} 保留数据（推荐）—— 只卸载程序与服务；数据库、状态目录都保留【会自动备份】"
    echo -e "  ${GREEN}2)${NC} 卸载程序 + 清理状态 —— 额外删除会话/配置/日志/缓存；数据库保留【会自动备份】"
    echo -e "  ${RED}3)${NC} 彻底清理 —— 再删除数据库（房源/客户数据一并删除，不可恢复）【不备份】"
    echo -e "  ${BLUE}4)${NC} 取消 —— 什么都不做"
    echo ""
    printf "请选择 [1/2/3/4]: "
    read -r choice || choice=""
    choice="$(echo "${choice:-}" | tr -d '[:space:]' | tr 'A-Z' 'a-z')"
    case "$choice" in
        1|2|3) MODE="$choice" ;;
        4|c|cancel|q|quit|n|no|"") echo "已取消，未做任何修改。"; exit 0 ;;
        *) fail "无法识别的选项：$choice" ;;
    esac
fi

# 备份策略：1/2 档默认备份（数据要留）；3 档默认不备份（都选彻底删除了就是不想要了）
if [[ "$BACKUP_FLAG" == "yes" ]]; then DO_BACKUP=1
elif [[ "$BACKUP_FLAG" == "no" ]]; then DO_BACKUP=0
elif [[ "$MODE" == "3" ]]; then DO_BACKUP=0
else DO_BACKUP=1
fi

echo ""
info "本次将执行：$(mode_label "$MODE")"
if [[ "$DO_BACKUP" == "1" ]]; then
    echo "       动手前会先自动备份（数据库 + 加密密钥 + 图片），备份包路径与下载命令结束时打印"
else
    echo "       不备份（如需先备份，执行 coco backup，或本命令加 --backup）"
fi
[[ "$MODE" == "3" ]] && echo -e "       ${RED}注意：数据库会一起删除，不可恢复${NC}"

# ---- 最后的 yes 确认（与官方同款：输 yes 才执行）----
if [[ "$DRY_RUN" != "1" && "$ASSUME_YES" != "1" ]]; then
    echo ""
    printf "如果这台机器上有要保留的数据，请先确认备份包已拷到本机之外。\n"
    printf "输入 yes 确认："
    read -r answer || answer=""
    if [[ "$(echo "${answer:-}" | tr -d '[:space:]' | tr 'A-Z' 'a-z')" != "yes" ]]; then
        echo "已取消，未做任何修改。"; exit 0
    fi
fi

# ---- ① 自动备份（动手前先留还原点）----
info "[1/6] 卸载前备份"
if [[ "$DO_BACKUP" != "1" ]]; then
    if [[ "$MODE" == "3" ]]; then
        warn "按所选档位不备份（彻底清理 = 数据一起删除）"
        echo "    如你其实想留一份备份：先执行  coco backup  ，或给本命令加 --backup 重跑"
    else
        warn "已跳过备份（--no-backup）：本次卸载后没有还原点"
    fi
elif [[ "$DRY_RUN" == "1" ]]; then
    echo "    [干跑] 会先备份：数据库导出 + 加密密钥 + 房源图片 → $TARGET_HOME/coco_uninstall_backup_<时间>.tar.gz"
else
    mkdir -p "$BACKUP_DIR"
    BACKUP_CMD="${COCO_UNINSTALL_BACKUP_CMD:-}"
    if [[ -n "$BACKUP_CMD" ]]; then
        $BACKUP_CMD || fail "备份失败 —— 已中止卸载（确认不要数据可加 --no-backup 重跑）"
    elif [[ -x "$VENV_PY" ]]; then
        "$VENV_PY" "$REPO_DIR/scripts/backup_db.py" backup --force \
            || fail "备份失败 —— 已中止卸载（确认不要数据可加 --no-backup 重跑）"
    else
        fail "找不到备份工具（$VENV_PY），无法备份 —— 已中止卸载（确认不要数据可加 --no-backup 重跑）"
    fi
    TS="$(date +%Y%m%d_%H%M%S)"
    BUNDLE="$TARGET_HOME/coco_uninstall_backup_${TS}.tar.gz"
    # 图片包每份都是**全量**（照片归档的完整副本），只打最新那份，避免里外两份全量副本白占一倍体积
    ( cd "$BACKUP_DIR" && {
        TAR_ITEMS=( ./*.dump ./enc_key.txt )
        NEWEST_IMG="$(ls -1t real_estate_images_*.tar.gz 2>/dev/null | head -1)"
        [[ -n "$NEWEST_IMG" ]] && TAR_ITEMS+=( "$NEWEST_IMG" )
        tar czf "$BUNDLE" "${TAR_ITEMS[@]}"
    } ) || warn "备份包打包不完整（可能缺图片包或密钥），请检查 $BACKUP_DIR"
    ok "已生成备份包：$BUNDLE"
    echo "    ⚠️  请把它下载到你的电脑：删库或重装系统后，服务器上这份也会一起没。"
    # 下载命令（由使用者自己在电脑上执行）：给出实际用户名与服务器地址
    DL_USER="$(whoami 2>/dev/null || echo '<服务器用户名>')"
    DL_HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
    [[ -z "$DL_HOST" ]] && DL_HOST="<服务器IP>"
    echo ""
    echo "    下载到本地电脑（在你自己的电脑终端执行）："
    echo "      scp ${DL_USER}@${DL_HOST}:${BUNDLE} ~/Desktop/"
    echo "    或用 WinSCP / FileZilla 连 ${DL_HOST}，进到 $(dirname "$BUNDLE") 下载"
    echo "    恢复用（在新机器上）：python3 scripts/backup_db.py restore_migration --migration-tar <备份包>"
fi

# ---- ② 停服务 ----
info "[2/6] 停止服务"
if [[ "$DRY_RUN" == "1" ]]; then
    echo "    [干跑] 会停止 gateway 服务（并停用老部署残留的 hermes-agent 系统服务）"
else
    if [[ -n "$HERMES_CLI" ]]; then
        "$HERMES_CLI" gateway stop >/dev/null 2>&1 || true
        ok "已停止 gateway 服务"
    else
        warn "找不到官方程序（$REPO_DIR/venv/bin/hermes），跳过停服务"
    fi
    if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q '^hermes-agent\.service'; then
        sudo systemctl stop hermes-agent 2>/dev/null || true
        sudo systemctl disable hermes-agent 2>/dev/null || true
        ok "已停用老服务 hermes-agent.service"
    fi
fi

# ---- ③ Coco 专有项（官方不管的部分）----
info "[3/6] 清理 Coco 专有项（官方卸载不管的部分）"

# ③-a crontab 夜间备份行（保留其它任务）
if command -v "$CRONTAB_BIN" >/dev/null 2>&1; then
    CURRENT_CRON="$("$CRONTAB_BIN" -l 2>/dev/null || true)"
    if [[ -n "$CURRENT_CRON" ]] && printf '%s\n' "$CURRENT_CRON" | grep -q 'backup_db\.py'; then
        KEPT_CRON="$(printf '%s\n' "$CURRENT_CRON" | grep -v 'backup_db\.py' || true)"
        N_REMOVED="$(printf '%s\n' "$CURRENT_CRON" | grep -c 'backup_db\.py' || true)"
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "    [干跑] 会从定时任务里删掉 $N_REMOVED 行 Coco 备份任务（其它任务保留）"
        else
            printf '%s\n' "$KEPT_CRON" | "$CRONTAB_BIN" - 2>/dev/null && ok "定时任务已清理（删 $N_REMOVED 行，其它保留）" || warn "写入 crontab 失败"
        fi
    else
        ok "定时任务里没有 Coco 备份行"
    fi
else
    warn "没有 $CRONTAB_BIN 命令，跳过定时任务清理"
fi

# ③-b coco / hermes 软链（只删指向本仓库的；hermes 在 2026-09-21 后不再对外暴露）
for link in /usr/local/bin/coco "$TARGET_HOME/.local/bin/coco" /usr/local/bin/hermes "$TARGET_HOME/.local/bin/hermes"; do
    [[ -L "$link" ]] || continue
    TARGET="$(readlink -f "$link" 2>/dev/null || echo '')"
    if [[ "$TARGET" == "$REPO_DIR/"* ]]; then
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "    [干跑] 会删除软链 $link"
        else
            rm -f "$link" && ok "已删除软链 $link"
        fi
    else
        warn "$link 指向别处（$TARGET），不是我们的，跳过"
    fi
done

# ③-b2 shell 配置里的 Coco PATH 兜底行（path_guard.sh 写入；官方卸载只认 hermes 标记，这里自己清）
COCO_PATH_MARK='# Coco：确保 ~/.local/bin 在 PATH（coco 命令入口在此）'
for _rc in "$TARGET_HOME/.bashrc" "$TARGET_HOME/.bash_profile" "$TARGET_HOME/.profile" \
           "$TARGET_HOME/.zshrc" "$TARGET_HOME/.zprofile" "$TARGET_HOME/.config/fish/config.fish"; do
    [[ -f "$_rc" ]] || continue
    grep -qF "$COCO_PATH_MARK" "$_rc" 2>/dev/null || continue
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "    [干跑] 会清除 PATH 兜底行 $_rc"
        continue
    fi
    _before=$(wc -l < "$_rc")
    awk -v mark="$COCO_PATH_MARK" '
        $0 == mark { skip = 1; next }
        skip && $0 ~ /\.local\/bin/ { skip = 0; next }
        { skip = 0; print }
    ' "$_rc" > "$_rc.coco-tmp" && _after=$(wc -l < "$_rc.coco-tmp")
    if [[ -z "${_after:-}" || "$_after" -lt "$((_before - 4))" ]]; then
        warn "shell 配置行数变化异常，未改动：$_rc"
        rm -f "$_rc.coco-tmp"
        continue
    fi
    mv "$_rc.coco-tmp" "$_rc" && ok "已清除 PATH 兜底行：$_rc"
done

# ③-c 字体与渲染依赖（默认保留）
if [[ "$REMOVE_FONTS" == "1" ]]; then
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "    [干跑] 会删除字体目录与渲染依赖 librsvg2-bin"
    else
        [[ -d /usr/local/share/fonts/coco ]] && sudo rm -rf /usr/local/share/fonts/coco && ok "已删除字体目录"
        if command -v dpkg >/dev/null 2>&1 && dpkg -l librsvg2-bin >/dev/null 2>&1; then
            sudo apt-get remove -y -qq librsvg2-bin >/dev/null 2>&1 && ok "已卸载渲染依赖 librsvg2-bin"
        fi
    fi
else
    ok "字体与渲染依赖保留（需要清理时加 --remove-fonts）"
fi

# ---- ④ 官方卸载（本体部分交给官方卸载程序）----
info "[4/6] 卸载程序本体（官方卸载程序）"
if [[ "$DRY_RUN" == "1" ]]; then
    echo "    [干跑] 会执行: coco cli uninstall --dry-run"
    if [[ "$MODE" == "1" ]]; then
        echo "           （正式执行时为 coco cli uninstall --yes，保留 ~/.hermes 状态）"
    else
        echo "           （正式执行时为 coco cli uninstall --yes --full，连状态一起删）"
    fi
elif [[ "${COCO_UNINSTALL_SKIP_OFFICIAL:-0}" == "1" ]]; then
    warn "已按环境变量跳过官方卸载（COCO_UNINSTALL_SKIP_OFFICIAL=1）"
elif [[ -n "$HERMES_CLI" ]]; then
    if [[ "$MODE" == "1" ]]; then
        "$HERMES_CLI" uninstall --yes || fail "官方卸载失败 —— 请手动执行 coco cli uninstall --yes 后重跑"
    else
        "$HERMES_CLI" uninstall --yes --full || fail "官方卸载失败 —— 请手动执行 coco cli uninstall --yes --full 后重跑"
    fi
    ok "官方卸载完成（程序目录、服务、命令行入口已清理）"
else
    warn "找不到官方程序（$REPO_DIR/venv/bin/hermes），跳过官方卸载；如仍残留请手动删除安装目录 $REPO_DIR"
fi

# ---- ⑤ 数据库 ----
info "[5/6] 数据库处理"
if [[ "$MODE" != "3" ]]; then
    ok "数据库保留：${DB_NAME:-（未检测到）}"
    echo "    需要连数据库一起彻底清理时，重跑并选择 3（或 --mode 3）。"
elif [[ "$DRY_RUN" == "1" ]]; then
    # 干跑优先展示计划（即使环境变量要求跳过，也先展示会动哪个库）
    echo "    [干跑] 会执行: DROP DATABASE ${DB_NAME:-<库名>};  DROP USER ${DB_USER:-<账号>};"
elif [[ "${COCO_UNINSTALL_SKIP_DB:-0}" == "1" ]]; then
    warn "已按环境变量跳过数据库删除（COCO_UNINSTALL_SKIP_DB=1）"
elif [[ -z "$DB_NAME" || -z "$DB_USER" ]]; then
    warn "没检测到数据库连接串（缺 .env.db），无法自动删库 —— 请手动处理"
else
    sudo -u postgres psql -c "DROP DATABASE IF EXISTS $DB_NAME;" >/dev/null 2>&1 \
        && ok "已删除数据库 $DB_NAME" || warn "删库未成功（可手动：sudo -u postgres dropdb $DB_NAME）"
    sudo -u postgres psql -c "DROP USER IF EXISTS $DB_USER;" >/dev/null 2>&1 \
        && ok "已删除数据库账号 $DB_USER" || warn "删账号未成功（可手动：sudo -u postgres dropuser $DB_USER）"
fi

# ---- ⑥ 自检 ----
info "[6/6] 卸载后自检"
if [[ "$DRY_RUN" == "1" ]]; then
    echo "    [干跑] 未做任何改动，跳过自检（正式执行后这里会逐项核对服务/目录/软链是否清干净）"
else
    if command -v systemctl >/dev/null 2>&1 && systemctl --user list-units 2>/dev/null | grep -q 'hermes-gateway'; then
        warn "仍能看到 hermes-gateway 服务（可 systemctl --user status hermes-gateway 查看）"
    else
        ok "gateway 用户服务已不存在"
    fi
    for p in "$REPO_DIR" "$TARGET_HOME/.hermes"; do
        [[ -e "$p" ]] || continue
        if [[ "$p" == "$TARGET_HOME/.hermes" && "$MODE" == "1" ]]; then
            ok "状态目录按所选档位保留：$p"
        else
            warn "仍存在：$p（官方卸载未完成时可见，可手动删除）"
        fi
    done
    for b in hermes coco; do command -v "$b" >/dev/null 2>&1 && warn "命令仍在 PATH 里：$b（可手动删除软链）"; done
fi
echo ""
if [[ "$DO_BACKUP" == "1" && "$DRY_RUN" != "1" ]]; then
    ok "卸载流程结束。备份包在 $TARGET_HOME/，请用上面的 scp 命令下载到电脑后再删服务器。"
else
    ok "卸载流程结束。" 
fi
