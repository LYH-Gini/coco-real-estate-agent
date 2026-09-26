#!/usr/bin/env bash
#
# Coco 仓库双远程推送脚本：Gitee + GitHub 同步推 + SHA 一致性验证（不一致自动补推）
# 用法:
#   bash scripts/push_all.sh                # 推 master
#   bash scripts/push_all.sh --with-tags    # 推 master + 所有标签
#   bash scripts/push_all.sh <branch>       # 推指定分支
#
# 为什么要自动重试：推送会遇到瞬时网络故障（实测 GitHub 出现过 DNS 瞬时解析失败），
# 旧版直接 FAIL 退出，两个远程就停在"一新一旧"的状态等人工补推；而不同机器的安装源
# （Gitee/GitHub）不一样，这期间装出来的版本会不同。所以现在：
#   ① 每个远程各自重试 3 次；
#   ② 推完逐个远程与本地 HEAD 对照，落后的自动补推；
#   ③ 三轮后仍不一致才报失败，并把两边 SHA 都打出来。
#
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${YELLOW}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="master"
WITH_TAGS=0
RETRY_SLEEP="${PUSH_RETRY_SLEEP:-5}"   # 重试间隔（秒）；测试里可设为 0 以跑得快
REMOTES=("origin" "github")          # origin = Gitee，github = GitHub
REMOTE_LABELS=("Gitee" "GitHub")

for arg in "$@"; do
    case "$arg" in
        --with-tags) WITH_TAGS=1 ;;
        *) BRANCH="$arg" ;;
    esac
done

cd "$REPO_DIR"

# 推送前确认工作区干净（未提交改动会导致两边推的内容不是最新）
if [[ -n "$(git status --porcelain)" ]]; then
    fail "工作区有未提交改动，先 commit 再推送"
fi

LOCAL_SHA="$(git rev-parse "refs/heads/$BRANCH")"   # 按"被推的分支"算，不是当前 HEAD
info "推送分支: $BRANCH（本地 ${LOCAL_SHA:0:7}）"
echo "----------------------------------------"

# 带重试的单远程推送（瞬时网络故障不再直接失败）
push_with_retry() {           # $1=remote  $2=refspec  $3=label
    local remote="$1" refspec="$2" label="$3" i
    for i in 1 2 3; do
        if git push "$remote" "$refspec" 2>&1; then
            [[ $i -gt 1 ]] && ok "$label 第 $i 次尝试成功"
            return 0
        fi
        warn "$label 推送失败（第 $i 次），$((i * RETRY_SLEEP)) 秒后重试…"
        sleep $((i * RETRY_SLEEP))
    done
    return 1
}

for idx in "${!REMOTES[@]}"; do
    remote="${REMOTES[$idx]}"; label="${REMOTE_LABELS[$idx]}"
    info "推送 $label ($remote) → $BRANCH"
    push_with_retry "$remote" "$BRANCH" "$label" || fail "$label 推送失败（已重试 3 次，检查网络/凭据后重跑）"
done

if [[ "$WITH_TAGS" == "1" ]]; then
    info "同步推送标签..."
    for idx in "${!REMOTES[@]}"; do
        remote="${REMOTES[$idx]}"; label="${REMOTE_LABELS[$idx]}"
        push_with_retry "$remote" "--tags" "$label 标签" || fail "$label 标签推送失败（已重试 3 次）"
    done
fi

# 一致性复核：逐个远程与本地 HEAD 对照，落后的补推（最多三轮）
echo "----------------------------------------"
info "复核两个远程是否与本地一致（不一致会自动补推）..."
for round in 1 2 3; do
    mismatch=0
    for idx in "${!REMOTES[@]}"; do
        remote="${REMOTES[$idx]}"; label="${REMOTE_LABELS[$idx]}"
        remote_sha="$(git ls-remote "$remote" "refs/heads/$BRANCH" | cut -f1)"
        if [[ -z "$remote_sha" ]]; then
            warn "$label：查不到 $BRANCH 的 SHA（网络问题？）"
            mismatch=1
            continue
        fi
        if [[ "$remote_sha" != "$LOCAL_SHA" ]]; then
            warn "$label 停在 ${remote_sha:0:7}，落后本地 ${LOCAL_SHA:0:7} → 补推"
            push_with_retry "$remote" "$BRANCH" "$label" || true
            mismatch=1
        fi
    done
    if [[ "$mismatch" == "0" ]]; then
        ok "两仓库同步完成 ${LOCAL_SHA:0:7} ($BRANCH)"
        exit 0
    fi
    [[ $round -lt 3 ]] && sleep "$RETRY_SLEEP"
done

for idx in "${!REMOTES[@]}"; do
    remote="${REMOTES[$idx]}"; label="${REMOTE_LABELS[$idx]}"
    warn "$label $(git ls-remote "$remote" "refs/heads/$BRANCH" | cut -f1 | cut -c1-7)"
done
fail "两远程 SHA 不一致（本地 ${LOCAL_SHA:0:7}）—— 确认网络与凭据后重跑本脚本即可"
