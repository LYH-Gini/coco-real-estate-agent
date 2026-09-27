#!/usr/bin/env bash
#
# Coco 双仓库推送脚本：按分支选仓库，同仓库的两个远程（Gitee + GitHub）同步推 + SHA 一致性验证（不一致自动补推）
#
# 仓库布局（2026-09-28 起）：
#   master = 正式版 → **正式仓**（公开，别人照文档装的那个）：origin=Gitee、github=GitHub
#   next   = 开发版 → **开发仓**（私有，测试机用密钥拉）：dev-gitee=Gitee、dev-gh=GitHub
#   正式仓不放 next、开发仓不放 master —— 由 .github/workflows/channel-guard.yml 守着。
#
# 用法:
#   bash scripts/push_all.sh                # 推当前分支（自动选对应仓库）
#   bash scripts/push_all.sh master         # 推 master 到正式仓
#   bash scripts/push_all.sh next           # 推 next 到开发仓
#   bash scripts/push_all.sh --with-tags    # 同时推所有标签
#   PUSH_REMOTES="r1 r2" bash scripts/push_all.sh next   # 手动指定远程（覆盖自动选择）
#
# 为什么要自动重试：推送会遇到瞬时网络故障（实测 GitHub 出现过 DNS 瞬时解析失败），
# 旧版直接 FAIL 退出，两个远程就停在"一新一旧"的状态等人工补推；而不同机器的安装源
# （Gitee/GitHub）不一样，这期间装出来的版本会不同。所以现在：
#   ① 每个远程各自重试 3 次；
#   ② 推完逐个远程与"被推分支"的 SHA 对照，落后的自动补推；
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
BRANCH="$(git -C "$REPO_DIR" symbolic-ref --quiet --short HEAD 2>/dev/null || echo master)"  # 默认推当前分支
WITH_TAGS=0
RETRY_SLEEP="${PUSH_RETRY_SLEEP:-5}"   # 重试间隔（秒）；测试里可设为 0 以跑得快
RELEASE_BRANCH="master"                # 正式线 → 正式仓（公开）
RELEASE_REMOTES=("origin" "github")    # origin = Gitee 正式仓，github = GitHub 正式仓
DEV_REMOTES=("dev-gitee" "dev-gh")     # Gitee 开发仓 / GitHub 开发仓（私有，凭据由本机助手提供）

for arg in "$@"; do
    case "$arg" in
        --with-tags) WITH_TAGS=1 ;;
        *) BRANCH="$arg" ;;
    esac
done

cd "$REPO_DIR"

# 按分支选仓库：master → 正式仓；其余（next 等开发分支）→ 开发仓。PUSH_REMOTES 可手动覆盖。
if [[ -n "${PUSH_REMOTES:-}" ]]; then
    read -r -a REMOTES <<<"$PUSH_REMOTES"
    REMOTE_LABELS=("${REMOTES[@]}")
    CHANNEL_LABEL="指定远程（PUSH_REMOTES）"
elif [[ "$BRANCH" == "$RELEASE_BRANCH" ]]; then
    REMOTES=("${RELEASE_REMOTES[@]}")
    REMOTE_LABELS=("Gitee（正式仓）" "GitHub（正式仓）")
    CHANNEL_LABEL="正式线（$BRANCH → 正式仓）"
else
    REMOTES=("${DEV_REMOTES[@]}")
    REMOTE_LABELS=("Gitee（开发仓）" "GitHub（开发仓）")
    CHANNEL_LABEL="开发线（$BRANCH → 开发仓）"
fi

# 推送前确认工作区干净（未提交改动会导致两边推的内容不是最新）
if [[ -n "$(git status --porcelain)" ]]; then
    fail "工作区有未提交改动，先 commit 再推送"
fi

# 远程得先配好（换机器/换克隆时最容易缺这一步）：缺了就把该敲的命令打出来
missing=()
for r in "${REMOTES[@]}"; do
    git remote get-url "$r" >/dev/null 2>&1 || missing+=("$r")
done
if (( ${#missing[@]} > 0 )); then
    fail "缺少远程：${missing[*]} —— 该仓库的远程还没配。补上再跑：
  git remote add dev-gitee https://gitee.com/LYH-Gini/coco-real-estate-agent-dev.git
  git remote add dev-gh    https://github.com/LYH-Gini/coco-real-estate-agent-dev.git
（正式仓的 origin=Gitee / github=GitHub 在安装目录里已配好；凭据由本机的令牌助手提供，不要写进远程地址）"
fi

LOCAL_SHA="$(git rev-parse "refs/heads/$BRANCH")"   # 按"被推的分支"算，不是当前 HEAD
info "$CHANNEL_LABEL"
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

# 一致性复核：逐个远程与"被推分支"的 SHA 对照，落后的补推（最多三轮）
echo "----------------------------------------"
info "复核 $CHANNEL_LABEL 的两个远程是否与本地一致（不一致会自动补推）..."
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
fail "$CHANNEL_LABEL 的两个远程 SHA 不一致（本地 ${LOCAL_SHA:0:7}）—— 确认网络与凭据后重跑本脚本即可"
