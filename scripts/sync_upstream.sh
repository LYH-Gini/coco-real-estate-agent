#!/usr/bin/env bash
# =============================================================================
# Coco 同步官方上游 —— 把 Coco 的底座抬到官方新版本
#
# 做什么：
#   1. 下载官方指定版本的完整快照
#   2. 用官方新文件替换本仓库的「官方层」文件
#   3. 【保留】Coco 自有文件（官方快照里没有的，一律不动）
#   4. 【备份】挂钩点文件，并列出需要重新应用改动的位置
#
# 不做什么（安全边界）：
#   · 不动未跟踪的运行时文件（.env.db / 加密密钥 / 缓存）
#   · 默认不自动删除文件（官方删掉的文件只列出来，由人确认）；
#     如需顺带清理，加 --prune-official-deleted（清理有保护规则：只删「官方基线里登记过」的
#     文件，Coco 自有文件与挂钩点永不动；见 scripts/prune_official_deleted.sh）
#     实现方式：每次同步把「官方文件清单」存进 .sync-baseline/upstream_files.txt；
#     下次同步与它对比，即可精确算出「官方这次删了/改名了哪些文件」、「其中本地还留着几个」，
#     避免官方已删的旧文件在本地越堆越多（堆积会让构建/导入读到过时版本）。
#       · 只增不减的 rsync 无法发现这类残留，靠这份基线才能发现
#   · 不自动 git commit / push（验收通过后由人/上级流程提交）
#
# 用法：
#   bash scripts/sync_upstream.sh v2026.9.14 --dry-run   # 只演练，不改任何文件
#   bash scripts/sync_upstream.sh v2026.9.14             # 真实执行
# =============================================================================
set -uo pipefail

TAG="${1:-}"
DRY_RUN=0
# 可选：同步时顺带清理「官方已删/改名」的历史残留文件（有保护规则，见 prune_official_deleted.sh）
PRUNE_OFFICIAL_DELETED=0
for arg in "${@:2}"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --prune-official-deleted) PRUNE_OFFICIAL_DELETED=1 ;;
    *) echo "未知参数: $arg（支持 --dry-run / --prune-official-deleted）" >&2; exit 1 ;;
  esac
done

if [[ -z "$TAG" ]]; then
  echo "用法: bash scripts/sync_upstream.sh <官方版本tag> [--dry-run] [--prune-official-deleted]"
  echo "例:   bash scripts/sync_upstream.sh v2026.9.14 --dry-run"
  echo "      bash scripts/sync_upstream.sh v2026.9.14 --prune-official-deleted   # 顺带清理官方已删的历史残留"
  echo "官方版本列表: gh release list --repo NousResearch/hermes-agent --limit 10"
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
UPSTREAM_URL="${COCO_UPSTREAM_URL:-https://github.com/NousResearch/hermes-agent.git}"

# 挂钩点文件：同步后会变成官方版本，需要重新应用 Coco 的改动（完整清单见 patches/README.md）
HOOK_FILES=(
  "toolsets.py"
  "agent/prompt_builder.py"
  "agent/system_prompt.py"
  "agent/agent_init.py"
  "hermes_cli/config_defaults.py"
  "plugins/platforms/feishu/adapter.py"
  "gateway/run.py"
  # 第 8 处：CI 挑标签的正则（2026-09-15 同步时被官方版冲掉，实测教训）
  "scripts/sandbox/pick-release-tags.sh"
  # 第 9、10 处：设置向导的默认值（2026-09-19 加；官方写 max_turns=150，会冲掉 Coco 的 500）
  "hermes_cli/setup.py"
  "hermes_cli/setup_quick.py"
  # 第 19 处：示例配置的种子值（2026-09-27 加；install.sh 会把它复制成新实例的 config.yaml，
  # 官方阈值 0.50 / 保留 20 会让新装实例在对齐脚本跑之前就是错的）
  "cli-config.yaml.example"
  # 第 20 处：压缩兜底值（2026-09-27 加；官方 0.50 / 20 与出厂默认 0.8 / 40 不一致，
  # 配置缺键或读取失败时会退回官方口径。agent/agent_init.py 上面已列出，它同时背着第 04 处）
  "tui_gateway/session_compression.py"
  "hermes_cli/context_switch_guard.py"
)

# Coco 刻意不跟踪的官方路径（删过就不许同步再带回来；2026-09-27 定）
COCO_DONT_SYNC=(
  # 上游把「提交邮箱 → 贡献者名」的 1161 个映射文件放在这里，属于他们的贡献者台账；
  # Coco 仓库只署一个名字，这份名单不放进我们的公开仓库。
  "contributors"
  # 2026-09-27 决定删掉的官方目录（用户装机/运行都用不到，删了少下载、少占磁盘）。
  # 登记在这里 = 同步官方新版时不会被 rsync 带回本地（删过就不许再回来）；
  # 配套的守护是 check_coco_hooks 的 A30–A35（应不存在，被带回来就报警）。
  # 例外：evals/ 保留「最小必要集」13 个文件（tests/gateway / tests/agent 里共 10 个用例
  # import evals.heartbeat_idle_wire 等模块，整个删掉会让这两个区在收集阶段中断）。
  # evals 登记在此，rsync 不碰它 → 那 13 个文件原样保留；A33 守它们在位。
  "apps"
  "website"
  "optional-skills"
  "evals"
  "tests-js"
)

# Coco 重写过、但官方也有同名文件：**跳过替换**，保住我们自己的版本。
# （2026-09-15 实测教训：第一次同步时没排除，README 被官方版覆盖，Coco 中文介绍丢失，
#   只能用 pre-sync tag 找回。）
COCO_OWNED_KEEP=(
  "README.md"
  "README.zh-CN.md"
  "SOUL.md"
  "VERSION"
  "UPSTREAM_VERSION"
)

info(){ echo -e "\033[1;34m==>\033[0m $*"; }
ok(){   echo -e "\033[1;32m  OK\033[0m $*"; }
warn(){ echo -e "\033[1;33m  !!\033[0m $*"; }
err(){  echo -e "\033[1;31mFAIL\033[0m $*" >&2; }

echo "=========================================================="
echo " Coco 同步官方上游"
echo " 目标版本 : $TAG"
echo " 仓库     : $REPO_ROOT"
echo " 模式     : $([[ $DRY_RUN == 1 ]] && echo '演练（不改文件）' || echo '真实执行')"
echo "=========================================================="

# ---- 0. 前置检查 -----------------------------------------------------------
info "前置检查"
if [[ ! -d .git ]]; then err "当前目录不是 git 仓库"; exit 1; fi
if [[ -n "$(git status --porcelain | grep -vE '^\?\?' || true)" ]]; then
  err "检测到已跟踪文件的本地改动，请先提交或 stash 后再同步。"
  git status --short | head -20
  exit 1
fi
ok "工作区干净（未跟踪的运行时文件不受影响）"

CUR_VER=$(cat VERSION 2>/dev/null | tr -d '[:space:]' || echo "?")
CUR_SIZE=$(du -sm .git 2>/dev/null | cut -f1)
echo "  当前 Coco 版本: v${CUR_VER}  仓库体积: ${CUR_SIZE}MB"

# ---- 1. 下载官方快照 -------------------------------------------------------
SNAP=$(mktemp -d /tmp/coco-upstream-XXXXXX)
info "下载官方 $TAG 快照（浅克隆，只取该版本，不带历史）"
if ! git clone --quiet --depth=1 --branch "$TAG" --single-branch "$UPSTREAM_URL" "$SNAP/up" 2>/dev/null; then
  err "下载失败：检查版本 tag 是否存在、网络是否可达"
  echo "  可用版本: gh release list --repo NousResearch/hermes-agent --limit 10"
  rm -rf "$SNAP"; exit 1
fi
rm -rf "$SNAP/up/.git"
UP_FILES=$(find "$SNAP/up" -type f | wc -l)
ok "官方快照就绪：$UP_FILES 个文件"

# ---- 2. 计算差异 -----------------------------------------------------------
info "计算差异（官方快照 vs 本仓库）"
CHANGED=0; NEW=0; LOCAL_ONLY=0
: > "$SNAP/changed.txt"; : > "$SNAP/new.txt"
while IFS= read -r rel; do
  src="$SNAP/up/$rel"; dst="$REPO_ROOT/$rel"
  if [[ ! -e "$dst" ]]; then
    NEW=$((NEW+1)); echo "$rel" >> "$SNAP/new.txt"
  elif ! cmp -s "$src" "$dst"; then
    CHANGED=$((CHANGED+1)); echo "$rel" >> "$SNAP/changed.txt"
  fi
done < <(cd "$SNAP/up" && find . -type f | sed 's|^\./||')

# 本地独有（官方没有的）= Coco 自有文件 + 需人工确认的残留
(cd "$REPO_ROOT" && find . -type f \
   -not -path './.git/*' -not -path './venv/*' -not -path './node_modules/*' \
   -not -path './.venv*/*' -not -path '*/__pycache__/*' \
   | sed 's|^\./||' | sort) > "$SNAP/local_all.txt"
(cd "$SNAP/up" && find . -type f | sed 's|^\./||' | sort) > "$SNAP/up_all.txt"
comm -23 "$SNAP/local_all.txt" "$SNAP/up_all.txt" > "$SNAP/local_only.txt"
LOCAL_ONLY=$(wc -l < "$SNAP/local_only.txt")

echo "  内容有变化 : $CHANGED 个文件"
echo "  官方新增   : $NEW 个文件"
echo "  本地独有   : $LOCAL_ONLY 个（Coco 自有文件 + 官方已删的历史残留，下面会分开报告）"

# ---- 2b. 官方删除清单（与上次同步记录的官方文件清单对比）--------------------
BASELINE_FILE="$REPO_ROOT/.sync-baseline/upstream_files.txt"
UP_DELETED=0; STALE_LOCAL=0
if [[ -f "$BASELINE_FILE" ]]; then
  comm -23 <(sort "$BASELINE_FILE") "$SNAP/up_all.txt" > "$SNAP/upstream_deleted.txt"
  UP_DELETED=$(wc -l < "$SNAP/upstream_deleted.txt")
  comm -12 <(sort "$SNAP/upstream_deleted.txt") <(cd "$REPO_ROOT" && git ls-files | sort) \
    > "$SNAP/stale_in_local.txt"
  STALE_LOCAL=$(wc -l < "$SNAP/stale_in_local.txt")
  echo "  官方删/改名: $UP_DELETED 个（其中本地仍留着 $STALE_LOCAL 个旧文件）"
else
  echo "  官方删/改名: 未计算（尚无 .sync-baseline/upstream_files.txt，本次同步会建立基线）"
fi

echo
echo "  --- 需要重新应用 Coco 改动的挂钩点（本次会被换成官方新版）---"
for f in "${HOOK_FILES[@]}"; do
  if [[ -e "$SNAP/up/$f" ]]; then
    printf "    · %-45s 官方新版 %s bytes\n" "$f" "$(stat -c%s "$SNAP/up/$f")"
  else
    warn "$f 在官方新版中不存在（可能被官方改路径或删除，需人工确认）"
  fi
done

if [[ $DRY_RUN == 1 ]]; then
  echo
  info "演练结束 —— 未改动任何文件"
  echo "  变化文件清单: $SNAP/changed.txt"
  echo "  新增文件清单: $SNAP/new.txt"
  echo "  自有文件清单: $SNAP/local_only.txt"
  if [[ $UP_DELETED -gt 0 ]]; then
    echo "  官方删除清单: $SNAP/upstream_deleted.txt"
    echo "  本地残留清单: $SNAP/stale_in_local.txt（$STALE_LOCAL 个）"
    if [[ $PRUNE_OFFICIAL_DELETED == 1 ]]; then
      echo "  本次带了 --prune-official-deleted：真实执行时会自动清理这 $STALE_LOCAL 个（按保护规则）"
    else
      echo "  需人工确认后删；如要顺带清理，执行时加上 --prune-official-deleted"
    fi
  fi
  echo
  echo "确认无误后去掉 --dry-run 执行真实同步。"
  exit 0
fi

# ---- 3. 备份挂钩点 + 打 tag -----------------------------------------------
STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP="$REPO_ROOT/.sync-backup/$STAMP"
mkdir -p "$BACKUP"
for f in "${HOOK_FILES[@]}"; do
  [[ -f "$f" ]] && { mkdir -p "$BACKUP/$(dirname "$f")"; cp -a "$f" "$BACKUP/$f"; }
done
cp -a "$SNAP/local_only.txt" "$BACKUP/local_only_files.txt" 2>/dev/null || true
cp -a "$SNAP/stale_in_local.txt" "$BACKUP/stale_in_local.txt" 2>/dev/null || true
ok "挂钩点文件已备份到 .sync-backup/$STAMP/"

if git rev-parse -q --verify "refs/tags/pre-sync-$STAMP" >/dev/null 2>&1; then
  warn "备份 tag 已存在，跳过"
else
  git tag "pre-sync-$STAMP" 2>/dev/null && ok "已打备份 tag: pre-sync-$STAMP（回滚用 git reset --hard pre-sync-$STAMP）"
fi

# ---- 4. 替换官方层文件（不动本地独有文件、不删除）-------------------------
info "替换官方层文件"
# Coco 自己重写过的同名文件不替换（见上方 COCO_OWNED_KEEP 说明）
KEEP_ARGS=()
for keep in "${COCO_OWNED_KEEP[@]}"; do
  KEEP_ARGS+=("--exclude=/$keep")
  [[ "$keep" == */* ]] && KEEP_ARGS+=("--exclude=/$keep")
  echo "  跳过（保留 Coco 自有版本）: $keep"
done
if command -v rsync >/dev/null 2>&1; then
  DONT_ARGS=()
  for skip in "${COCO_DONT_SYNC[@]}"; do DONT_ARGS+=("--exclude=/$skip"); done
  rsync -a --exclude='.git/' "${KEEP_ARGS[@]}" "${DONT_ARGS[@]}" "$SNAP/up/" "$REPO_ROOT/"
else
  (cd "$SNAP/up" && find . -type f -exec sh -c '
      for src; do
        rel="${src#./}"
        case "$rel" in
          README.md|README.zh-CN.md|SOUL.md|VERSION|UPSTREAM_VERSION) continue ;;
          contributors/*|contributors) continue ;;
        esac
        mkdir -p "'"$REPO_ROOT"'/$(dirname "$rel")"
        cp -a "$src" "'"$REPO_ROOT"'/$rel"
      done' _ {} +)
fi
ok "官方层已替换（Coco 自有文件与自有文档均未受影响）"

# ---- 4b. 可选：清理官方已删/改名的历史残留（必须排在「更新基线」之前 ——
#            基线一更新，被删的老文件就不在基线里了，prune 的保护规则会全部跳过）
PRUNED=0
if [[ $PRUNE_OFFICIAL_DELETED == 1 ]]; then
  if [[ ${STALE_LOCAL:-0} -gt 0 ]]; then
    info "清理官方已删的历史残留（保护规则：只删官方基线里登记过的文件）"
    if COCO_PRUNE_BASELINE="$BASELINE_FILE" \
      bash "$REPO_ROOT/scripts/prune_official_deleted.sh" "$SNAP/stale_in_local.txt"; then
      PRUNED=$STALE_LOCAL
    else
      warn "清理未完成（见上面的输出），同步的其它步骤已完成"
    fi
  else
    ok "没有需要清理的官方历史残留"
  fi
fi

# ---- 4c. 更新官方文件清单基线（供下次同步做删除对比）------------------------
mkdir -p "$REPO_ROOT/.sync-baseline"
cp -a "$SNAP/up_all.txt" "$BASELINE_FILE"
ok "官方文件清单基线已更新（.sync-baseline/upstream_files.txt：$UP_FILES 条）"

# ---- 5. 报告 ---------------------------------------------------------------
git add -A >/dev/null 2>&1 || true
ADDED=$(git diff --cached --numstat --diff-filter=A | wc -l)
MODIFIED=$(git diff --cached --numstat --diff-filter=M | wc -l)
DELETED=$(git diff --cached --numstat --diff-filter=D | wc -l)
NEW_SIZE=$(du -sm .git 2>/dev/null | cut -f1)

# ── 自动重打中文文案（官方层文件被整批替换后会回到英文，这张表是我们自己的） ──
CN_ANCHOR=0
if [[ -f scripts/coco_cn_strings.py ]]; then
  echo
  echo "── 自动重打中文文案（scripts/coco_cn_strings.py --apply）"
  python3 scripts/coco_cn_strings.py --apply 2>&1 | tail -3 | sed 's/^/   /' || true
  CN_ANCHOR=$(python3 scripts/coco_cn_strings.py 2>&1 | grep -c "ANCHOR" || true)
fi

echo
echo "=========================================================="
echo " 同步完成 —— 但还没结束，按下面顺序继续"
echo "=========================================================="
echo "  变更统计: 新增 $ADDED / 修改 $MODIFIED / 删除 $DELETED"
echo "  仓库体积: ${CUR_SIZE}MB → ${NEW_SIZE}MB（增量 $((NEW_SIZE-CUR_SIZE))MB；Gitee 上限 500MB）"
echo
echo "  ① 重新应用 Coco 对官方文件的改动（按语义，别机械 apply；清单见 patches/README.md）："
echo "       cat patches/README.md"
echo "     中文文案那部分已自动跑过 --apply（见上面「自动重打中文文案」）"
if [[ "${CN_ANCHOR:-0}" -gt 0 ]]; then
  echo "     ⚠ 有 $CN_ANCHOR 条锚点失效（ANCHOR）：官方改过这些句子，要按 patches/README.md 人工重译"
  echo "       python3 scripts/coco_cn_strings.py            # 看具体是哪些"
else
  echo "     （没有锚点失效：被换回英文的都自动改回中文了）"
fi
echo "     挂钩点备份在 .sync-backup/$STAMP/"
echo
echo "  ② 自检（应全 PASS）："
echo "       python3 scripts/check_coco_hooks.py"
echo
echo "  ③ 三层验收 + 灰度："
echo "       python3 scripts/healthcheck.py"
echo "       python3 scripts/smoke_test_real_estate.py"
echo "       （详见 docs/UPSTREAM_SYNC.md 第 5、6 步）"
echo
echo "  ④ 验收通过后再提交推送（脚本不替你决定）："
echo "       git commit -m 'chore: 同步官方 $TAG'"
echo
if [[ $UP_DELETED -gt 0 ]]; then
  echo "  官方这次删掉/改名、本地还留着的旧文件：$STALE_LOCAL 个"
  if [[ $STALE_LOCAL -gt 0 ]]; then
    if [[ $PRUNED -gt 0 ]]; then
      echo "      —— 已按 --prune-official-deleted 清理（清单存档在上面的 prune-* 目录）"
    else
      head -8 "$SNAP/stale_in_local.txt" | sed 's|^|      · |'
      [[ $STALE_LOCAL -gt 8 ]] && echo "      …（完整清单：$BACKUP/stale_in_local.txt）"
      echo "      需人工确认后删除；或下次同步加 --prune-official-deleted 自动清理"
    fi
  fi
else
  echo "  官方这次没有删除/改名文件（或基线文件尚未建立）"
fi
echo
warn "回滚方式：git reset --hard pre-sync-$STAMP"
