#!/usr/bin/env bash
# =============================================================================
# Coco 验收登记脚本（2026-09-21 加）：把"验收通过"这件事**写进仓库**，
# 让晋升脚本能机械校验 —— 没登记过的提交绝对进不了正式版。
#
# 流程（约定）：
#   ① 改动推到测试通道 next 后，把测试命令贴出来，在本机拉取测试版；
#   ② 按测试命令实测；
#   ③ 验收通过之后，运行本脚本，在当前测试通道提交上打验收标签；
#   ④ 晋升脚本 `promote_release.sh` 要求"晋升的提交上必须有验收标签"，否则拒绝。
#
# 为什么用标签而不是文件：标签**钉得住具体提交**。如果登记之后测试通道又有了新提交，
# 那个新提交上没有验收标签 → 晋升会被拒绝（不会出现"验收的是 A、发布的是 B"）。
#
# 用法：
#   bash scripts/mark_verified.sh --note "<测了什么>"                # 登记验收（说明里不用带「已验收」前缀，脚本会补）
#   bash scripts/mark_verified.sh --list                              # 查看已登记的验收
#   bash scripts/mark_verified.sh --note "..." --no-push              # 只打本地标签，不推远程
# =============================================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[1;34m'; NC='\033[0m'
info() { echo -e "${YELLOW}[INFO]${NC} $*"; }
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*" >&2; exit 1; }

# 验收标签说明统一成「v<版本> 已验收（：<测了什么>）」（标签说明是公网可见元数据）。
# 说明里若已经带了「v<版本> 已验收 / 已验收 / 验收通过」这类前缀，先去重再补，
# 免得出现「v0.21.5-3 已验收：v0.21.5-3 已验收：…」这种重复前缀。
verified_message() {
    local ver="$1" note="${2:-}"
    case "$note" in
        "v${ver} 已验收"*) printf '%s' "$note"; return ;;
    esac
    note="${note#已验收}"; note="${note#验收通过}"
    note="${note#：}"; note="${note#:}"
    if [[ -n "$note" ]]; then printf 'v%s 已验收：%s' "$ver" "$note"
    else printf 'v%s 已验收' "$ver"; fi
}

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

NOTE=""
DO_PUSH=1
# 验收标签钉的是**测试通道**（next）上的提交 → 推**开发仓**（2026-09-28 仓库拆分后的口径）：
# 正式仓只放 master，别把开发线的标签与对象塞过去。本机没有开发仓远程时退回正式仓（只影响可审计性）。
DEV_REMOTES=(${VERIFY_DEV_REMOTES:-dev-gitee dev-gh})
RELEASE_REMOTES=("origin" "github")
REMOTES=()
LABELS=()
for r in "${DEV_REMOTES[@]}"; do
    if git remote get-url "$r" >/dev/null 2>&1; then REMOTES+=("$r"); LABELS+=("$r（开发仓）"); fi
done
if [[ ${#REMOTES[@]} -eq 0 ]]; then
    REMOTES=("${RELEASE_REMOTES[@]}")
    LABELS=("origin" "github")
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --note) NOTE="${2:-}"; shift 2 ;;
        --no-push) DO_PUSH=0; shift ;;
        --list) LIST=1; shift ;;
        *) fail "未知参数: $1（支持 --note \"说明\" / --list / --no-push）" ;;
    esac
done

if [[ -n "${LIST:-}" ]]; then
    echo "已登记的验收（越新越靠下）："
    git tag -l 'verified/*' --sort=creatordate --format='  %(refname:short)  →  %(objectname:short)  %(contents:subject)'
    exit 0
fi

[[ -n "$NOTE" ]] || fail "请用 --note \"说明\" 写清这次验收测了什么（例如：验收通过：海报参考图风格）"

# 只在测试通道上登记（正式通道的提交不需要验收登记，它已经是验收过的）
BRANCH="$(git branch --show-current 2>/dev/null || echo '')"
[[ "$BRANCH" == "next" ]] || fail "只能在测试通道（next）上登记验收，当前分支：${BRANCH:-游离}"

# 只看已跟踪文件的改动（未跟踪的锁/密钥不算）
if [[ -n "$(git status --porcelain | grep -vE '^\?\?' || true)" ]]; then
    fail "工作区有未提交的代码改动，先 commit 再登记（保证登记的就是仓库里的内容）"
fi

SHA="$(git rev-parse HEAD)"
VER="$(tr -d '[:space:]' < VERSION)"
TAG="verified/v${VER}-${SHA:0:7}"

MSG="$(verified_message "$VER" "$NOTE")"
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null 2>&1; then
    ok "该提交已有验收登记：$TAG（无需重复）"
else
    git tag -a "$TAG" -m "$MSG" "$SHA" || fail "打标签失败"
    ok "已登记验收：$TAG（提交 ${SHA:0:7}，版本 v$VER）"
fi
echo "  版本: v$VER"
echo "  提交: ${SHA:0:7}"
echo "  说明: $MSG"
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S %Z')"

if [[ "$DO_PUSH" == "1" ]]; then
    for idx in "${!REMOTES[@]}"; do
        r="${REMOTES[$idx]}"; label="${LABELS[$idx]}"
        info "推送验收标签到 $label"
        for i in 1 2 3; do
            if git push "$r" "$TAG" >/dev/null 2>&1; then break; fi
            [[ $i == 3 ]] && fail "$label 推送验收标签失败（网络问题？重跑本脚本即可）"
            sleep $((i * 3))
        done
        ok "$label 已同步"
    done
fi

echo ""
echo "下一步（确认后）：bash scripts/promote_release.sh --tag v$VER"
echo "  （晋升脚本只承认带验收标签的提交；若测试通道之后又有新提交，需重新验收）"
