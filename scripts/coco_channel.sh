#!/usr/bin/env bash
# =============================================================================
# Coco 通道工具（2026-09-21 加）：稳定版 / 测试版双通道
#
# 仓库布局（2026-09-28 起，两个仓库各管一条线）：
#   正式仓（公开，别人照文档装的那个）：只有 master = 正式版；**不放 next**。
#   开发仓（私有，测试机用密钥拉）    ：只有 next   = 开发版；**不放 master**。
# 分支名不变（master/next），所以本工具与已装实例的 `git pull` 都不用改；变的是
# "哪个仓库放哪个分支"，由 scripts/push_all.sh 按分支选仓库、.github/workflows/channel-guard.yml 守着。
#
# 设计要点：**默认分支不动**。master 永远是稳定版（别人照文档安装拿到的就是它），
# 测试版放在 next 分支上（只有测试机切过去）。
# 这样已装实例零迁移 —— `git pull` 不带分支名 = 拉"当前分支对应的线上分支"，
# 测试机在 next 上就自动拉测试版，别人的机器在 master 上就照旧拿稳定版。
#
# 用法（可被其它脚本 source，也可直接执行）：
#   bash scripts/coco_channel.sh show            # 打印当前通道（如 "测试通道（next）"）
#   bash scripts/coco_channel.sh want            # 打印"应该用哪个分支"（env/文件/当前分支）
#   bash scripts/coco_channel.sh switch <分支>   # 切通道（工作区不干净时拒绝）
#   bash scripts/coco_channel.sh test-tag        # 打印当前测试号（如 v0.21.3-67-test1）
# =============================================================================
set -uo pipefail

STABLE_BRANCH="master"
TEST_BRANCH="next"

# 通道标签：给人看的中文名
coco_channel_label() {          # $1=分支名
    case "${1:-}" in
        "$STABLE_BRANCH") echo "稳定通道" ;;
        "$TEST_BRANCH")   echo "测试通道" ;;
        "")               echo "未知通道" ;;
        *)                echo "自定义通道" ;;
    esac
}

# 测试标签：测试通道上离当前提交最近的测试号（如 v0.21.3-67-test1 [+N 提交]）
coco_test_label() {             # $1=仓库根
    local root="${1:-.}" tag ahead
    tag="$(git -C "$root" describe --tags --match 'v*-test*' --abbrev=0 2>/dev/null || echo '')"
    [[ -z "$tag" ]] && return 0
    ahead="$(git -C "$root" rev-list --count "${tag}..HEAD" 2>/dev/null || echo 0)"
    if [[ "${ahead:-0}" != "0" ]]; then
        echo "${tag} +${ahead} 提交"
    else
        echo "$tag"
    fi
}

coco_channel_current() {        # $1=仓库根（可选）；给了就用 -C 定位，避免被"当前目录恰好也是某个 git 仓库"带偏
    local root="${1:-}"
    if [[ -n "$root" ]]; then
        git -C "$root" branch --show-current 2>/dev/null || echo ""
    else
        git branch --show-current 2>/dev/null || echo ""
    fi
}

# 期望分支的优先级：环境变量 COCO_CHANNEL > 仓库内 .coco-channel 文件 > 当前分支
coco_channel_want() {           # $1=仓库根
    local root="${1:-.}" want="${COCO_CHANNEL:-}"
    if [[ -z "$want" && -f "$root/.coco-channel" ]]; then
        want="$(tr -d '[:space:]' < "$root/.coco-channel")"
    fi
    if [[ -z "$want" ]]; then
        want="$(coco_channel_current "$root")"
    fi
    echo "$want"
}

# 切通道：只做"快进式切换"，工作区不干净就拒绝（绝不覆盖本地改动）
coco_channel_switch() {         # $1=目标分支  $2=仓库根
    local target="$1" root="${2:-.}"
    if [[ -z "$target" ]]; then
        echo "切通道失败：没有指定目标分支" >&2
        return 1
    fi
    # 只看已跟踪文件的改动：未跟踪文件（更新锁、.env.db、加密密钥）不影响切分支，
    # 也不该挡住切通道 —— 这一条是被真事逼出来的（锁文件由更新脚本自己创建，导致永远切不动）。
    if [[ -n "$(git -C "$root" status --porcelain 2>/dev/null | grep -vE '^\?\?' || true)" ]]; then
        echo "切通道失败：$root 有未提交的代码改动（已跟踪文件），请先处理（本工具绝不覆盖你的改动）" >&2
        return 1
    fi
    git -C "$root" fetch -q origin "$target" || {
        echo "切通道失败：拉取 origin/$target 失败（网络或分支不存在）" >&2
        return 1
    }
    git -C "$root" checkout -q -B "$target" "origin/$target" || {
        echo "切通道失败：切到 $target 失败" >&2
        return 1
    }
    echo "$target"
    return 0
}

# ---------- 直接执行时的命令行入口 ----------
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    cmd="${1:-show}"
    case "$cmd" in
        show)
            cur="$(coco_channel_current "$ROOT")"
            printf "%s（%s）\n" "$(coco_channel_label "$cur")" "${cur:-游离}"
            ;;
        want)
            coco_channel_want "$ROOT"
            ;;
        switch)
            coco_channel_switch "${2:-}" "$ROOT" >/dev/null || exit 1
            cur="$(coco_channel_current)"
            printf "%s（%s）\n" "$(coco_channel_label "$cur")" "$cur"
            ;;
        label)
            coco_channel_label "${2:-}"
            ;;
        test-tag)
            # 只在测试通道显示测试号：稳定版/自定义分支上，即使仓库里有测试标签也不显示
            # （2026-09-22 修：稳定版上曾显示 "稳定通道 · 测试号 v...-test5"，两个口径打架）
            if [[ "$(coco_channel_current "$ROOT")" == "$TEST_BRANCH" ]]; then
                coco_test_label "$ROOT"
            fi
            ;;
        *)
            echo "用法: bash scripts/coco_channel.sh [show|want|switch <分支>|label <分支>|test-tag]" >&2
            exit 2
            ;;
    esac
fi
