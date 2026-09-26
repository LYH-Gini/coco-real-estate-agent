#!/usr/bin/env bash
# =============================================================================
# Coco 版本晋升脚本（2026-09-21 加）：把验收通过的测试版(next)晋升为稳定版(master)
#
# 背景：要求"我测的版本跟别人装的不一样；我测通过了再正式发布"。
# 通道模型见 scripts/coco_channel.sh：master=稳定版（别人装这个）、next=测试版（测试机测这个）。
# 本脚本负责最后一步：验收通过之后，把 next 的内容快进到 master。
#
# 只做**快进**（要求 master 是 next 的祖先），不做自动合并 —— 合并会造出历史分叉，
# 已装实例的 `git pull --ff-only` 更新会失败，违反更新铁律。
#
# 用法:
#   bash scripts/promote_release.sh --dry-run          # 只看会晋升什么，不动仓库
#   bash scripts/promote_release.sh                    # 把 next 快进到 master 并双推
#   bash scripts/promote_release.sh --tag v0.21.3-65   # 顺手打标签并推送标签
#
# 前置条件（硬闸门）：晋升的提交上必须有验收登记（verified/* 标签），
# 用 `bash scripts/mark_verified.sh --note "..."` 在验收通过之后登记。
#
# 晋升后仍需人工做两件事（脚本只保证代码到位，避免误发对外内容）：
#   ① 在 Gitee/GitHub 建对应 Release（对外的"正式发布"动作）；
#   ② 更新博客的版本行（post 70）。
# =============================================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[1;34m'; NC='\033[0m'
info() { echo -e "${YELLOW}[INFO]${NC} $*"; }
ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

FROM_BRANCH="next"      # 测试通道
TO_BRANCH="master"      # 稳定通道
TAG=""
DRY_RUN=0
ONLY_COMMITS=()
REMOTES=("origin" "github")
LABELS=("Gitee" "GitHub")

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --tag) TAG="${2:-}"; shift 2 ;;
        --from) FROM_BRANCH="${2:-}"; shift 2 ;;
        --to) TO_BRANCH="${2:-}"; shift 2 ;;
        --only)   # 只推指定提交（见文件头：next 上滞留备用方案，全量快进会带上它们）
            shift
            while [[ $# -gt 0 && "$1" != --* ]]; do ONLY_COMMITS+=("$1"); shift; done
            ;;
        *) fail "未知参数: $1" ;;
    esac
done

# 参数级校验：标签非法时不该走到任何流程（含两道守卫）
if [[ -n "$TAG" ]]; then
    case "$TAG" in
        *-test*) fail "正式标签不能带 -test（那是测试号）—— 正式版请用 v<版本>" ;;
    esac
fi

# 只看已跟踪文件的改动（未跟踪的锁文件/.env.db/密钥不算 —— 与更新脚本口径一致）
if [[ -n "$(git status --porcelain | grep -vE '^\?\?' || true)" ]]; then
    fail "工作区有未提交的代码改动，先 commit（晋升必须是"仓库里的东西"）"
fi

info "取两个远程的最新状态..."
for r in "${REMOTES[@]}"; do
    git fetch -q "$r" || fail "拉取 $r 失败（网络问题？）"
done

# 晋升的必须是"测试通道上已经发布出去的内容"：本地测试分支若还有未推送的提交，
# 先把它推齐再晋升。否则会拿旧的 SHA 做校验（误报失败），还会把标签打到错误的位置。
LOCAL_TEST_SHA="$(git rev-parse "$FROM_BRANCH")"
SYNCED=0
for r in "${REMOTES[@]}"; do
    remote_test_sha="$(git ls-remote "$r" "refs/heads/$FROM_BRANCH" | cut -f1)"
    if [[ "$remote_test_sha" != "$LOCAL_TEST_SHA" ]]; then
        if [[ "$SYNCED" == "0" ]]; then
            info "测试通道本地领先远程，先把 $FROM_BRANCH 推齐（晋升的是测试通道上的内容）"
            SYNCED=1
        fi
        git push "$r" "$FROM_BRANCH" || fail "推送 $FROM_BRANCH 到 $r 失败（先让测试通道齐了再晋升）"
    fi
done

# ---- --only：只把指定提交推到正式版（2026-09-23 加）----
# 背景：next 上长期滞留 backup-* 标签钉住的“禁止推正式版”备用方案，全量快进会把它们一起带上，
# 所以常规晋升走本模式：只 cherry-pick 点名的那几个提交。
if [[ ${#ONLY_COMMITS[@]} -gt 0 ]]; then
    ORIG_BRANCH="$(git branch --show-current)"
    ORIG_TO_SHA="$(git rev-parse "$TO_BRANCH")"
    info "只推指定提交模式：${ONLY_COMMITS[*]}"
    for c in "${ONLY_COMMITS[@]}"; do
        sha="$(git rev-parse --verify "${c}^{commit}" 2>/dev/null || echo '')"
        [[ -n "$sha" ]] || fail "找不到提交：$c"
        git merge-base --is-ancestor "$sha" "$FROM_BRANCH" \
            || fail "$c 不在 $FROM_BRANCH 上 —— 只允许把测试通道上的提交推正式版"
        bad="$(git tag --points-at "$sha" | grep '^backup-' || true)"
        [[ -z "$bad" ]] || fail "$c 被“禁止推正式版”的标签钉住（$bad）—— 备用方案绝不推正式版"
    done

    # 验收覆盖检查（2026-09-23 要求）：只推指定提交也不能绕过验收闸门 ——
    # 要求存在一个 verified/* 标签（钉在某个测试通道提交上），且被点名的提交都在它之下。
    APPROVED_V=""
    while read -r _v; do
        [[ -n "$_v" ]] || continue
        _all=1
        for c in "${ONLY_COMMITS[@]}"; do
            _sha="$(git rev-parse --verify "${c}^{commit}")"
            if ! git merge-base --is-ancestor "$_sha" "$_v"; then _all=0; break; fi
        done
        if [[ "$_all" == "1" ]]; then APPROVED_V="$_v"; break; fi
    done < <(git tag -l 'verified/*' --format='%(objectname)' | sort -u)
    if [[ -z "$APPROVED_V" ]]; then
        fail "被点名的提交没有被验收登记覆盖 —— 只推指定提交也不能绕过验收闸门。
  要求：存在一个 verified/* 标签，且被点名的提交都在它之下（这批内容确实经过实测）。
  做法：验收通过之后，先在测试通道上登记：
        bash scripts/mark_verified.sh --note \"验收通过：<测了什么>\"
  然后再跑本模式。"
    fi
    info "验收登记：$(git tag --points-at "$APPROVED_V" | grep '^verified/' | head -1)（已覆盖本次点名的提交）"
    echo "  将 cherry-pick 到 $TO_BRANCH："
    for c in "${ONLY_COMMITS[@]}"; do git --no-pager log --oneline -1 "$c" | sed 's/^/    /'; done
    if [[ "$DRY_RUN" == "1" ]]; then
        warn "dry-run：以上是 --only 会 cherry-pick 的提交，未做任何改动"
        exit 0
    fi

    info "切到 $TO_BRANCH"
    git checkout -q "$TO_BRANCH" || fail "切到 $TO_BRANCH 失败"
    CP_OUT="$(git cherry-pick -x "${ONLY_COMMITS[@]}" 2>&1)" || {
        if printf '%s' "$CP_OUT" | grep -qi 'empty'; then
            git cherry-pick --abort >/dev/null 2>&1 || true
            git checkout -q "$ORIG_BRANCH" >/dev/null 2>&1 || true
            fail "要晋升的提交在 $TO_BRANCH 上已是同样内容（空提交）—— 无需晋升，请确认要推的提交"
        fi
        git cherry-pick --abort >/dev/null 2>&1 || true
        git checkout -q "$ORIG_BRANCH" >/dev/null 2>&1 || true
        fail "cherry-pick 冲突：已中止并回到 $ORIG_BRANCH；请手工处理后重跑"
    }
    NEW_TO_SHA="$(git rev-parse HEAD)"
    # 确认值用「内容摘要」而不是提交 SHA：cherry-pick 的 SHA 每次重跑都不同（含时间戳），
    # 内容相同则摘要相同 —— 重跑能对上，换了内容就对不上，逼人重新核对。
    DIFF_SUM="$(git --no-pager diff "$ORIG_TO_SHA..$NEW_TO_SHA" | git hash-object --stdin | cut -c1-7)"
    echo "----------------------------------------"
    echo "本次内容差异（相对旧 $TO_BRANCH ${ORIG_TO_SHA:0:7}）："
    git --no-pager diff --stat "$ORIG_TO_SHA..$NEW_TO_SHA"
    echo "----------------------------------------"
    if [[ "${PROMOTE_CONFIRM:-}" != "$DIFF_SUM" ]]; then
        git reset -q --hard "$ORIG_TO_SHA"          # 未确认就把 cherry-pick 结果回滚，不留半成品
        git checkout -q "$ORIG_BRANCH" >/dev/null 2>&1 || true
        fail "请核对上面的内容清单，确认无误后重跑（上面的 cherry-pick 已回滚）：
  PROMOTE_CONFIRM=$DIFF_SUM bash scripts/promote_release.sh --only ${ONLY_COMMITS[*]}${TAG:+ --tag $TAG}"
    fi
    if [[ -n "$TAG" ]]; then
        info "打标签 $TAG"
        git tag -a "$TAG" -m "Coco $TAG" "$NEW_TO_SHA" 2>/dev/null || warn "标签 $TAG 已存在，沿用现有标签"
        for r in "${REMOTES[@]}"; do git push "$r" "$TAG" || fail "标签推送到 $r 失败"; done
    fi
    for r in "${REMOTES[@]}"; do
        info "推送 $r：$TO_BRANCH"
        git push "$r" "${NEW_TO_SHA}:refs/heads/$TO_BRANCH" || fail "$r 推送 $TO_BRANCH 失败"
    done
    info "回到 $FROM_BRANCH 并合并 $TO_BRANCH（保持以后能快进）"
    git checkout -q "$FROM_BRANCH" || fail "切回 $FROM_BRANCH 失败"
    git merge -q -m "Merge branch '$TO_BRANCH' into $FROM_BRANCH" "$TO_BRANCH" \
        || warn "自动合并失败：请手工 git merge $TO_BRANCH（冲突保留 next 版）后再推 $FROM_BRANCH"
    for r in "${REMOTES[@]}"; do git push "$r" "$FROM_BRANCH" || warn "推送 $FROM_BRANCH 到 $r 失败"; done
    mismatch=0
    for idx in "${!REMOTES[@]}"; do
        r="${REMOTES[$idx]}"; label="${LABELS[$idx]}"
        got="$(git ls-remote "$r" "refs/heads/$TO_BRANCH" | cut -f1)"
        [[ "$got" == "$NEW_TO_SHA" ]] || { warn "$label 的 $TO_BRANCH 停在 ${got:0:7}，期望 ${NEW_TO_SHA:0:7}"; mismatch=1; }
    done
    [[ "$mismatch" == "1" ]] && fail "有远程未同步成功 —— 重跑前先看 git log（cherry-pick 可能已应用）"
    git merge-base --is-ancestor "$TO_BRANCH" "$FROM_BRANCH" \
        || warn "$TO_BRANCH 不是 $FROM_BRANCH 的祖先 —— 以后快进晋升会失败，请检查"
    ok "只推指定提交完成：$TO_BRANCH = ${NEW_TO_SHA:0:7}"
    echo "  还需人工完成：① 建 Release（Gitee/GitHub）② 更新博客版本行"
    exit 0
fi

FROM_SHA="$(git ls-remote origin "refs/heads/$FROM_BRANCH" | cut -f1)"
TO_SHA="$(git ls-remote origin "refs/heads/$TO_BRANCH" | cut -f1)"

if [[ "$FROM_SHA" == "$TO_SHA" ]]; then
    ok "$TO_BRANCH 与 $FROM_BRANCH 已经是同一个提交（${TO_SHA:0:7}），无需晋升"
    exit 0
fi

# 硬守卫①（2026-09-23 要求）：晋升清单里若含被 backup-* 钉住的提交（禁推正式版的备用方案），拒绝
GUARD_HIT=""
while read -r _sha; do
    [[ -n "$_sha" ]] || continue
    _tags="$(git tag --points-at "$_sha" | grep '^backup-' || true)"
    [[ -n "$_tags" ]] && GUARD_HIT="${_tags} (${_sha:0:7})"
done < <(git rev-list "$TO_SHA..$FROM_SHA")
if [[ -n "$GUARD_HIT" ]]; then
    fail "本次晋升会带上被标记为“禁止推正式版”的备用方案提交：$GUARD_HIT
  规则：备用方案绝不进正式版（见 references/release-checklist.md §3）。
  做法：改用只推指定提交：bash scripts/promote_release.sh --only <要晋升的提交…>"
fi
info "备用方案守卫：通过（清单里没有被 backup-* 钉住的提交）"

# 硬闸门（2026-09-21 要求"没经过我测试的功能绝对不能混进正式版本"）：
# 本次晋升的提交上必须有验收登记（verified/* 标签），否则拒绝晋升。
# 标签钉的是具体提交 —— 所以"验收的是 A、发布的是 B"这种情况不可能发生：
# 登记之后测试通道若又推了新提交，新提交上没有标签，晋升照样被拒。
APPROVED_TAG="$(git tag --points-at "$FROM_SHA" | grep '^verified/' | head -1 || true)"
if [[ -z "$APPROVED_TAG" ]]; then
    fail "本次晋升的提交没有验收登记（提交 ${FROM_SHA:0:7}）
  规则：没经过实测的功能不能进正式版。
  做法：验收通过之后，在测试通道上运行
        bash scripts/mark_verified.sh --note \"验收通过：<测了什么>\"
  然后在测试通道**不再新增提交**的前提下再次执行本脚本。"
fi
info "验收登记：${BLUE}${APPROVED_TAG}${NC} —— $(git tag -l --format='%(contents:subject)' "$APPROVED_TAG")"


# 关键校验：必须能快进（master 是 next 的祖先），否则拒绝
if ! git merge-base --is-ancestor "$TO_SHA" "$FROM_SHA"; then
    fail "$TO_BRANCH 不是 $FROM_BRANCH 的祖先，无法快进 —— 说明稳定线有测试线没有的提交，需要人工处理后再晋升"
fi

AHEAD="$(git rev-list --count "$TO_SHA..$FROM_SHA")"
VER="$(git show "$FROM_SHA:VERSION" 2>/dev/null | tr -d '[:space:]' || echo '未知')"
echo "----------------------------------------"
info "晋升 ${BLUE}$FROM_BRANCH${NC} → ${BLUE}$TO_BRANCH${NC}"
echo "  晋升内容: ${AHEAD} 个提交"
echo "  晋升到:   ${FROM_SHA:0:7}（版本 v${VER}）"
echo "  原本:     ${TO_SHA:0:7}"
echo "----------------------------------------"

if [[ "$DRY_RUN" == "1" ]]; then
    warn "dry-run：以上是将会发生的事，未做任何改动"
    exit 0
fi

# 硬守卫②（2026-09-23 要求）：把“将带上的提交清单 + 内容差异”打出来，
# 要求人工核对后显式确认，防止盲推（内容差 ≠ 提交清单，两者都要看）。
echo "----------------------------------------"
echo "本次会带上以下提交（$(git rev-list --count "$TO_SHA..$FROM_SHA") 个）："
git --no-pager log --oneline "$TO_SHA..$FROM_SHA"
echo ""
echo "内容差异（相对 $TO_BRANCH）："
git --no-pager diff --stat "$TO_SHA..$FROM_SHA"
echo "----------------------------------------"
if [[ "${PROMOTE_CONFIRM:-}" != "$(git rev-parse --short=7 "$FROM_SHA")" ]]; then
    fail "请先核对上面两份清单（提交清单 / 内容差异），确认无误后重跑：
  PROMOTE_CONFIRM=$(git rev-parse --short=7 "$FROM_SHA") bash scripts/promote_release.sh${TAG:+ --tag $TAG}"
fi

# 用 refspec 推（next:master）：不依赖本地当前在哪个分支，也不动工作区
for idx in "${!REMOTES[@]}"; do
    r="${REMOTES[$idx]}"; label="${LABELS[$idx]}"
    info "推送 $label：$FROM_BRANCH → $TO_BRANCH"
    git push "$r" "$FROM_BRANCH:$TO_BRANCH" || fail "$label 推送失败（重跑本脚本即可，快进是幂等的）"
done

# 本地 master 引用同步跟上（若当前就停在 master，用快进合并；否则直接更新引用）
if [[ "$(git branch --show-current)" == "$TO_BRANCH" ]]; then
    git merge --ff-only "$FROM_SHA" >/dev/null || warn "本地 $TO_BRANCH 快进失败（不影响远程，远程已更新）"
else
    git update-ref "refs/heads/$TO_BRANCH" "$FROM_SHA" || warn "本地 $TO_BRANCH 引用更新失败（不影响远程）"
fi

if [[ -n "$TAG" ]]; then
    info "打标签 $TAG 并推送"
    git tag -a "$TAG" -m "Coco $TAG" "$FROM_SHA" 2>/dev/null \
        || warn "标签 $TAG 已存在，沿用现有标签"
    for r in "${REMOTES[@]}"; do
        git push "$r" "$TAG" || fail "标签推送到 $r 失败"
    done
fi

# 复核：两个远程的 master 都必须等于 next 的提交
mismatch=0
for idx in "${!REMOTES[@]}"; do
    r="${REMOTES[$idx]}"; label="${LABELS[$idx]}"
    got="$(git ls-remote "$r" "refs/heads/$TO_BRANCH" | cut -f1)"
    if [[ "$got" != "$FROM_SHA" ]]; then
        warn "$label 的 $TO_BRANCH 停在 ${got:0:7}，期望 ${FROM_SHA:0:7}"
        mismatch=1
    fi
done
[[ "$mismatch" == "1" ]] && fail "有远程未同步成功 —— 重跑本脚本（快进幂等）"

ok "晋升完成：$TO_BRANCH = ${FROM_SHA:0:7}（版本 v${VER}）"
echo "  还需人工完成：① 建 Release（Gitee/GitHub）② 更新博客版本行"
