#!/usr/bin/env bash
# =============================================================================
# Coco 测试版标签脚本（2026-09-21 加）：给测试通道的提交打"测试号"。
#
# 背景：测试版与正式版都要有可区分的版本号，测试版另打一个小标签。
# 所以约定：
#   · 正式版标签：v0.21.3-67          —— 只在晋升时打（对外发布）
#   · 测试版标签：v0.21.3-67-test1、-test2 … —— 每交付一批待测内容打一个，**不发 Release**
# 两者一眼可分；安装/更新都走分支，不看标签，所以别人不会因为测试标签而装到测试版。
#
# 用法：
#   bash scripts/tag_test_version.sh --note "本批包含：XX 功能"   # 打测试标签并推送
#   bash scripts/tag_test_version.sh --list                       # 看已有测试标签
#   bash scripts/tag_test_version.sh --note "..." --no-push       # 只打本地标签
# =============================================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[1;34m'; NC='\033[0m'
info() { echo -e "${YELLOW}[INFO]${NC} $*"; }
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*" >&2; exit 1; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

NOTE=""
DO_PUSH=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --note) NOTE="${2:-}"; shift 2 ;;
        --no-push) DO_PUSH=0; shift ;;
        --list) LIST=1; shift ;;
        *) fail "未知参数: $1（支持 --note \"说明\" / --list / --no-push）" ;;
    esac
done

if [[ -n "${LIST:-}" ]]; then
    echo "已有测试标签（越新越靠下）："
    git tag -l 'v*-test*' --sort=creatordate --format='  %(refname:short)  →  %(objectname:short)  %(contents:subject)'
    exit 0
fi

[[ -n "$NOTE" ]] || fail "请用 --note \"说明\" 写清这批测试版包含什么（例如：本批包含：海报参考图风格）"

BRANCH="$(git branch --show-current 2>/dev/null || echo '')"
[[ "$BRANCH" == "next" ]] || fail "只能在测试通道（next）上打测试标签，当前分支：${BRANCH:-游离}"

if [[ -n "$(git status --porcelain | grep -vE '^\?\?' || true)" ]]; then
    fail "工作区有未提交的代码改动，先 commit 再打标签（标签必须指向仓库里的内容）"
fi

SHA="$(git rev-parse HEAD)"
VER="$(tr -d '[:space:]' < VERSION)"

# 同一版本号下测试号递增：找已有的 v<VER>-test<N>，取最大 N + 1
MAX_N=0
while read -r t; do
    [[ -z "$t" ]] && continue
    n="${t##*-test}"
    [[ "$n" =~ ^[0-9]+$ ]] && (( n > MAX_N )) && MAX_N="$n"
done < <(git tag -l "v${VER}-test*")
N=$((MAX_N + 1))
TAG="v${VER}-test${N}"

git tag -a "$TAG" -m "测试版 $TAG：$NOTE" "$SHA" || fail "打标签失败"
ok "已打测试标签：${TAG}（提交 ${SHA:0:7}，版本 v${VER}）"
echo "  包含: $NOTE"
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "  注意：测试标签**不发 Release、不进正式版**；晋升时另打正式标签 v${VER}"

if [[ "$DO_PUSH" == "1" ]]; then
    # 测试号标签只进**开发仓**（2026-09-28 仓库拆分后的口径；正式仓只放 master）
    DEV_REMOTES=(${TEST_TAG_DEV_REMOTES:-dev-gitee dev-gh})
    TARGETS=()
    for r in "${DEV_REMOTES[@]}"; do
        git remote get-url "$r" >/dev/null 2>&1 && TARGETS+=("$r")
    done
    if [[ ${#TARGETS[@]} -eq 0 ]]; then
        echo -e "${YELLOW}[WARN]${NC} 本机没有开发仓远程（${DEV_REMOTES[*]}）—— 测试标签只在本地，未推送"
        TARGETS=()
    fi
    for r in ${TARGETS[@]:-}; do
        info "推送测试标签到 $r（开发仓）"
        for i in 1 2 3; do
            if git push "$r" "$TAG" >/dev/null 2>&1; then break; fi
            [[ $i == 3 ]] && fail "$r 推送测试标签失败（网络问题？重跑本脚本即可）"
            sleep $((i * 3))
        done
        ok "$r 已同步"
    done
fi
