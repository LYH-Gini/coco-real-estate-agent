#!/usr/bin/env bash
# =============================================================================
# coco 命令入口的 PATH 兜底
#
# 背景：coco 命令入口优先建在 /usr/local/bin；那条路走不通时才退回
#       ~/.local/bin，而不少系统的 PATH 里没有这个目录 —— 用户会遇到
#       "装完了敲 coco 却说命令找不到"。官方 install.sh 有一模一样的兜底做法，
#       本脚本照它写：按登录 shell 把 export 行写进对应配置文件（幂等）。
#
# 用法: bash scripts/path_guard.sh [ensure|status]
#   ensure（默认）: 确保 ~/.local/bin 在 PATH（必要时写配置）
#   status        : 只看状态，不改任何文件
# =============================================================================
set -euo pipefail

LOCAL_BIN="$HOME/.local/bin"
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
PATH_COMMENT='# Coco：确保 ~/.local/bin 在 PATH（coco 命令入口在此）'

on_path() {
    case ":$PATH:" in *":$LOCAL_BIN:"*) return 0 ;; *) return 1 ;; esac
}

is_fish() {
    [[ "$(basename "${SHELL:-/bin/bash}")" == "fish" ]]
}

# 要写入的配置文件：按登录 shell 判（不看当前执行脚本的 shell）
target_configs() {
    local login_shell
    login_shell="$(basename "${SHELL:-/bin/bash}")"
    case "$login_shell" in
        zsh)
            [[ -f "$HOME/.zshrc" ]] || touch "$HOME/.zshrc"
            printf '%s\n' "$HOME/.zshrc"
            if [[ -f "$HOME/.zprofile" ]]; then printf '%s\n' "$HOME/.zprofile"; fi
            ;;
        fish)
            local fish_cfg="$HOME/.config/fish/config.fish"
            mkdir -p "$(dirname "$fish_cfg")"
            [[ -f "$fish_cfg" ]] || touch "$fish_cfg"
            printf '%s\n' "$fish_cfg"
            ;;
        *)
            [[ -f "$HOME/.bashrc" ]] || touch "$HOME/.bashrc"
            printf '%s\n' "$HOME/.bashrc"
            if [[ -f "$HOME/.bash_profile" ]]; then printf '%s\n' "$HOME/.bash_profile"; fi
            ;;
    esac
}

# 该文件里是否已有非注释的 ~/.local/bin 行（幂等判据）
already_guarded() {
    local f="$1"
    [[ -f "$f" ]] || return 1
    grep -v '^[[:space:]]*#' "$f" 2>/dev/null | grep -q '\.local/bin'
}

write_guard() {
    local f="$1"
    if already_guarded "$f"; then
        echo "  已有 PATH 配置，跳过：$f"
        return 0
    fi
    {
        echo ""
        echo "$PATH_COMMENT"
        if is_fish; then
            echo 'fish_add_path $HOME/.local/bin'
        else
            echo "$PATH_LINE"
        fi
    } >> "$f" || { echo "  写入失败（无权限？）：$f"; return 1; }
    echo "  已把 ~/.local/bin 加入 PATH：$f"
    return 0
}

CMD="${1:-ensure}"
case "$CMD" in
    status)
        if on_path; then echo "~/.local/bin 已在当前 PATH 中"; else echo "~/.local/bin 不在当前 PATH 中"; fi
        while IFS= read -r f; do
            [[ -n "$f" ]] || continue
            if already_guarded "$f"; then echo "  已配置：$f"; else echo "  未配置：$f"; fi
        done < <(target_configs)
        exit 0
        ;;
    ensure)
        if on_path; then
            echo "  ~/.local/bin 已在 PATH 中，无需改动"
            exit 0
        fi
        case "$(basename "${SHELL:-/bin/bash}")" in
            zsh)  RC_FILE="$HOME/.zshrc"; RELOAD_HINT="source ~/.zshrc" ;;
            fish) RC_FILE="$HOME/.config/fish/config.fish"; RELOAD_HINT="source ~/.config/fish/config.fish" ;;
            *)    RC_FILE="$HOME/.bashrc"; RELOAD_HINT="source ~/.bashrc" ;;
        esac
        rc=0
        while IFS= read -r f; do
            [[ -n "$f" ]] || continue
            write_guard "$f" || rc=1
        done < <(target_configs)
        if [[ "$rc" != "0" ]]; then
            echo "  提示: 可手动执行 echo '$PATH_LINE' >> $RC_FILE"
            exit 1
        fi
        echo "  提示: 新开的终端生效；当前终端可执行 $RELOAD_HINT"
        exit 0
        ;;
    *)
        echo "用法: bash scripts/path_guard.sh [ensure|status]" >&2
        exit 2
        ;;
esac
