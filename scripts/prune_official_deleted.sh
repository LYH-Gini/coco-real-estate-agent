#!/usr/bin/env bash
# =============================================================================
# 清理「官方已删/改名」的历史残留文件
#
# 背景：同步官方上游是快照式替换，只写入官方新版有的文件 —— 官方删掉的旧文件会
# 一直留在本地（越堆越多会让构建/导入读到过时版本）。同步脚本每次会把这类文件
# 列进 stale_in_local.txt，本脚本负责按确认后的清单把它们删掉。
#
# 用法：
#   bash scripts/prune_official_deleted.sh <清单文件> [--dry-run]
#     COCO_PRUNE_BASELINE=<旧基线文件> 可指定基线（同步脚本在更新基线之前调用它，见下）
#     清单文件 = 同步脚本产出的 /tmp/coco-upstream-*/stale_in_local.txt（一行一个相对路径）
#   bash scripts/prune_official_deleted.sh --selftest
#     自检：在临时仓库里验证保护规则（不动本仓库）
#
# 安全边界（删之前逐条查，任何一条不过就不删）：
#   ① 必须登记在 .sync-baseline/upstream_files.txt 里 —— 也就是「官方基线上确实有过这个文件」；
#      我们自己的文件从不在基线里，所以永远删不到自有代码（即使清单被写错）。
#   ② 必须是被 git 跟踪的文件（未跟踪的运行时文件如 .env.db 不在此范围）。
#   ③ 不在保护名单里（Coco 自有文档、挂钩点文件、我们的业务目录）。
#   ④ 没有未提交的本地改动（有人在改的文件不碰）。
#   删掉的文件清单会写进 .sync-backup/，并打印回滚命令。
# =============================================================================
set -uo pipefail

# 仓库根默认按脚本位置定位（从任何目录调用都能用）；自检 / 测试可用 COCO_PRUNE_REPO_ROOT 覆盖
REPO_ROOT="${COCO_PRUNE_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# 基线：默认用仓库当前的官方文件清单；同步流程里要清理「官方本次删掉」的文件时，
# 必须指向**同步之前**那份基线（同步会把基线更新成新版，删掉的文件就不在里面了）。
BASELINE="${COCO_PRUNE_BASELINE:-$REPO_ROOT/.sync-baseline/upstream_files.txt}"

# Coco 自己重写过、或完全属于我们的路径：永不清
COCO_OWNED_KEEP=("README.md" "README.zh-CN.md" "SOUL.md" "VERSION" "UPSTREAM_VERSION")
COCO_OWNED_PREFIX=("patches/" "migrations/" "docs/" "tools/real_estate_" "agent/real_estate_" \
                   "tests/real_estate/" "skills/real_estate/" ".github/workflows/real-estate-" \
                   ".sync-baseline/" ".sync-backup/")

info(){ echo -e "\033[1;34m==>\033[0m $*"; }
ok(){   echo -e "\033[1;32m  OK\033[0m $*"; }
warn(){ echo -e "\033[1;33m  !!\033[0m $*"; }
err(){  echo -e "\033[1;31mFAIL\033[0m $*" >&2; }

is_protected(){
  local rel="$1" k
  for k in "${COCO_OWNED_KEEP[@]}"; do [[ "$rel" == "$k" ]] && return 0; done
  for k in "${COCO_OWNED_PREFIX[@]}"; do [[ "$rel" == "$k"* ]] && return 0; done
  return 1
}

# 判定单个路径能不能删；打印理由
judge(){
  local rel="$1"
  if is_protected "$rel"; then echo "protected"; return; fi
  if ! grep -qxF "$rel" "$BASELINE" 2>/dev/null; then echo "not-in-baseline"; return; fi
  if [[ ! -e "$REPO_ROOT/$rel" ]]; then echo "absent"; return; fi
  if ! git -C "$REPO_ROOT" ls-files --error-unmatch -- "$rel" >/dev/null 2>&1; then echo "untracked"; return; fi
  if ! git -C "$REPO_ROOT" diff --quiet -- "$rel" 2>/dev/null; then echo "locally-modified"; return; fi
  if ! git -C "$REPO_ROOT" diff --cached --quiet -- "$rel" 2>/dev/null; then echo "staged-changes"; return; fi
  echo "ok"
}

prune(){
  local list="$1" dry="$2"
  [[ -f "$list" ]] || { err "清单文件不存在: $list"; exit 1; }
  [[ -f "$BASELINE" ]] || { err "缺少官方基线 $BASELINE，无法判断哪些是官方文件，拒绝执行"; exit 1; }

  local accepted=() skipped=() rel why
  while IFS= read -r rel; do
    [[ -z "$rel" ]] && continue
    why=$(judge "$rel")
    if [[ "$why" == "ok" ]]; then accepted+=("$rel"); else skipped+=("$rel [$why]"); fi
  done < "$list"

  echo "  清单条目: $(grep -cve '^$' "$list" || true)   可删: ${#accepted[@]}   跳过: ${#skipped[@]}"
  local s
  for s in "${skipped[@]}"; do warn "跳过 $s"; done

  if [[ ${#accepted[@]} -eq 0 ]]; then
    ok "没有需要删除的文件"
    return 0
  fi
  printf '  · %s\n' "${accepted[@]}"

  if [[ "$dry" == 1 ]]; then
    echo "  （演练模式，未删除任何文件）"
    return 0
  fi

  local stamp dir
  stamp=$(date +%Y%m%d_%H%M%S)
  dir="${COCO_PRUNE_BACKUP_DIR:-$REPO_ROOT/.sync-backup/prune-$stamp}"
  mkdir -p "$dir"
  printf '%s\n' "${accepted[@]}" > "$dir/pruned.txt"
  printf '%s\n' "${skipped[@]}"  > "$dir/skipped.txt"

  git -C "$REPO_ROOT" rm -q -f -- "${accepted[@]}" || { err "git rm 失败（清单已存档，可人工处理）"; exit 1; }
  ok "已删除 ${#accepted[@]} 个官方历史残留文件（删除前的内容仍在 git 历史里）"
  echo "  清单存档: $dir/"
  echo "  误删回滚: git -C $REPO_ROOT checkout HEAD -- ${accepted[*]}"
}

selftest(){
  local t; t=$(mktemp -d /tmp/coco-prune-selftest-XXXXXX)
  echo "==> 自检（临时仓库 $t，不动本仓库）"
  (
    cd "$t" || exit 1
    git init -q . && git config user.email t@t && git config user.name t
    mkdir -p .sync-baseline apps/desktop/src tests/run_agent
    printf 'apps/desktop/src/old-util.ts\ntests/run_agent/test_old.py\nREADME.md\n' > .sync-baseline/upstream_files.txt
    printf 'x\n' > apps/desktop/src/old-util.ts
    printf 'y\n' > tests/run_agent/test_old.py
    printf 'z\n' > README.md
    printf 'w\n' > apps/desktop/src/our-file.ts          # 官方从未有过 → 不在基线
    git add -A >/dev/null && git commit -qm init
    printf 'apps/desktop/src/old-util.ts\ntests/run_agent/test_old.py\nREADME.md\napps/desktop/src/our-file.ts\nghost.ts\n' > list.txt
  ) || { err "自检环境搭建失败"; rm -rf "$t"; return 1; }

  local fail=0
  COCO_PRUNE_REPO_ROOT="$t" bash "$0" "$t/list.txt" --dry-run >/dev/null 2>&1
  [[ -f "$t/apps/desktop/src/old-util.ts" ]] || { err "演练模式不应删除文件"; fail=1; }

  COCO_PRUNE_REPO_ROOT="$t" bash "$0" "$t/list.txt" >/dev/null 2>&1
  [[ ! -e "$t/apps/desktop/src/old-util.ts" ]] && echo "  OK 官方基线内的旧文件被删除" || { err "应删而未删: old-util.ts"; fail=1; }
  [[ ! -e "$t/tests/run_agent/test_old.py" ]] && echo "  OK 官方基线内的旧测试被删除" || { err "应删而未删: test_old.py"; fail=1; }
  # 有暂存改动的文件不碰（真实缺陷：文件处于暂存态会让 git rm 直接失败）
  mkdir -p "$t/tests/run_agent"
  printf 'staged\n' > "$t/tests/run_agent/test_old.py"
  printf 'tests/run_agent/test_old.py\n' > "$t/list2.txt"
  ( cd "$t" && git add tests/run_agent/test_old.py >/dev/null 2>&1 )
  COCO_PRUNE_REPO_ROOT="$t" bash "$0" "$t/list2.txt" >/dev/null 2>&1
  [[ -e "$t/tests/run_agent/test_old.py" ]] && echo "  OK 有暂存改动的文件被跳过" || { err "误删有暂存改动的文件"; fail=1; }
  [[ -e "$t/README.md" ]] && echo "  OK Coco 自有文档被保护（README.md 仍在）" || { err "误删 README.md"; fail=1; }
  [[ -e "$t/apps/desktop/src/our-file.ts" ]] && echo "  OK 不在官方基线的自有文件被保护" || { err "误删自有文件"; fail=1; }
  [[ -e "$t/ghost.ts" ]] || true

  rm -rf "$t"
  [[ $fail == 0 ]] && { ok "自检全部通过"; return 0; } || { err "自检失败"; return 1; }
}

case "${1:-}" in
  --selftest) selftest; exit $? ;;
  ""|-h|--help)
    echo "用法: bash scripts/prune_official_deleted.sh <清单文件> [--dry-run]"
    echo "      bash scripts/prune_official_deleted.sh --selftest"
    exit 1 ;;
esac

LIST="$1"; DRY=0
for arg in "${@:2}"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    *) err "未知参数: $arg（支持 --dry-run）"; exit 1 ;;
  esac
done
info "清理官方已删的历史残留文件"
prune "$LIST" "$DRY"
