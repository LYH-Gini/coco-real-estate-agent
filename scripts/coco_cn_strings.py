"""Coco 中文文案重打：同步官方底座之后跑一条命令，把中文改回来

为什么需要它：同步官方是「快照式替换」——我们改过的官方文件会被换回英文原文。
本脚本把这些**纯文案**改动登记成一张表（文件 + 官方原文 + 我们的中文），
同步之后执行：

    python3 scripts/coco_cn_strings.py --apply     # 自动改回中文
    python3 scripts/coco_cn_strings.py             # 只检查（默认），不写文件
    python3 scripts/coco_cn_strings.py --list      # 列出全部条目

三种结果，**绝不静默**：
  OK      —— 已经是中文（跳过）
  APPLY   —— 官方原文还在，已替换（--apply）/ 待替换（--check）
  ANCHOR  —— 官方原文和中文都找不到：官方改过这段文案，需按 patches/README.md
             （第 23 / 24 处）在新版里重新实现，再更新本表

退出码：0 = 全部到位；1 = 有待替换或锚点失效。
配套：check_coco_hooks.py 第 58–68 项、patches/README.md 第 23 / 24 处。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# (相对路径, 官方原文, 中文文案) —— 由改动提交的 diff 生成，改文案时同步更新这里
ENTRIES = [
    ('gateway/platforms/base.py', '            hint = "Reply with the number, the option text, or your own answer."',
     '            # COCO-PATCH 2026-09-30：选项提示行改中文（官方是英文，经纪人每次选选项都看得见）\n            hint = "回数字、选项原文，或直接说你的答案。"'),
    ('gateway/platforms/base.py', '                hint = ("Multiple selections allowed — reply with the numbers separated by commas "\n                        "or spaces (e.g. \\"1, 3\\"), the option text, or your own answer.")',
     '                hint = "可以多选：回数字（用逗号或空格隔开，如「1, 3」）、选项原文，或直接说你的答案。"'),
    ('gateway/relay/adapter.py', '            options.append({"id": "other", "label": "✏️ Other (type your answer)"})',
     '            options.append({"id": "other", "label": "✏️ 其他（我来补充）"})'),
    ('gateway/run_notifications.py', '                f"☤ **Update needs your input:**\\n\\n{prompt_text}{default_hint}\\n\\n"\n                f"Reply `{_p}approve` (yes) or `{_p}deny` (no), or type your answer directly."',
     '                f"☤ **更新需要你确认：**\\n\\n{prompt_text}{default_hint}\\n\\n"\n                f"回 `{_p}approve` 表示同意、`{_p}deny` 表示不同意，也可以直接说你的答案。"'),
    ('hermes_cli/auth_codex.py', '                "Import these credentials? (a separate login is recommended) [y/N]: ", default="n"):',
     '                "要导入这些凭据吗？（更建议单独登录一次）[y/N]: ", default="n"):'),
    ('hermes_cli/cli_commands_mixin.py', '                "  Restart recommended for gateway/dashboard processes to pick up state.db changes.")',
     '                "  建议重启网关/看板进程，让它们读新的 state.db。")'),
    ('hermes_cli/cli_tui_mixin.py', '                        other_label = f"  ❯ {mid}. " + (other_suffix or "Other (type below)")',
     '                        other_label = f"  ❯ {mid}. " + (other_suffix or "其他（在下面输入）")'),
    ('hermes_cli/cli_tui_mixin.py', '                        other_label = f"  ❯ {mid}. " + (other_suffix or "Other (type your answer)")',
     '                        other_label = f"  ❯ {mid}. " + (other_suffix or "其他（我来补充）")'),
    ('hermes_cli/cli_tui_mixin.py', '                        other_label = f"    {mid}. " + (other_suffix or "Other (type your answer)")',
     '                        other_label = f"    {mid}. " + (other_suffix or "其他（我来补充）")'),
    ('hermes_cli/cli_tui_mixin.py', '        other_label = _label(other_idx, "Other (type below)" if freetext else "Other (type your answer)")',
     '        other_label = _label(other_idx, "其他（在下面输入）" if freetext else "其他（我来补充）")'),
    ('hermes_cli/doctor_platform.py', '    elif check_bool(v >= (3, 10), label, (label, "(3.10+ recommended)")) and v < (3, 11):\n        check_warn("Python 3.11+ recommended for RL Training tools (tinker requires >= 3.11)")',
     '    elif check_bool(v >= (3, 10), label, (label, "(建议 3.10+)")) and v < (3, 11):\n        check_warn("RL 训练类工具建议 Python 3.11+（tinker 要求 >= 3.11）")'),
    ('hermes_cli/doctor_platform.py', '    check_bool(sys.prefix != sys.base_prefix, "Virtual environment active", ("Not in virtual environment", "(recommended)"))',
     '    check_bool(sys.prefix != sys.base_prefix, "Virtual environment active", ("Not in virtual environment", "(推荐)"))'),
    ('hermes_cli/gateway.py', '        print_info("  This is fine for LXC/container environments but not recommended on bare-metal hosts.")',
     '        print_info("  这在 LXC/容器环境里没问题，但裸机主机上不建议这样做。")'),
    ('hermes_cli/gateway.py', '    print_info("    2. Switch to a per-user service (recommended for personal use):")',
     '    print_info("    2. 换成为当前用户运行的服务（个人自用推荐这个）：")'),
    ('hermes_cli/gateway.py', '        "  Pass --force to start a foreground gateway anyway (not recommended\\n"\n        "  while the service is running)."',
     '        "  确实要前台起一个网关，就加 --force（不建议：服务还在跑的时候这样会冲突）。"'),
    ('hermes_cli/gateway.py', '        "  This is the recommended setup for the s6 container image — the\\n"',
     '        "  这是 s6 容器镜像下推荐的方式 —— 网关崩了也会自动重启。\\n"'),
    ('hermes_cli/gateway.py', '        "WSL note:", "  The gateway is running in foreground/manual mode (recommended for WSL).",',
     '        "WSL 说明：", "  网关正以前台/手动方式运行（WSL 下推荐这样）。",'),
    ('hermes_cli/gateway_migrate.py', '        "⚠ Your profiles each run their own gateway. A single multiplexed gateway is the recommended",',
     '        "⚠ 每个 profile 各自跑一个网关。更推荐用单个多路复用网关：",'),
    ('hermes_cli/gateway_setup_wizard.py', '        "Use DM pairing approval (recommended)", "Allow all direct messages", "Only allow listed user IDs",',
     '        "用私聊配对审批（推荐）", "允许所有私聊", "只允许名单里的用户 ID",'),
    ('hermes_cli/gateway_setup_wizard.py', '        "Disable group chats (recommended)", "Allow all group chats", "Only allow listed group chat IDs",',
     '        "关闭群聊（推荐）", "允许所有群聊", "只允许名单里的群 ID",'),
    ('hermes_cli/gateway_setup_wizard.py', '    method_choices = ["Scan QR code to add bot automatically (recommended)", "Enter existing App ID and App Secret manually"]',
     '    method_choices = ["扫码自动添加机器人（推荐）", "手动填写已有的 App ID 与 App Secret"]'),
    ('hermes_cli/gateway_setup_wizard.py', '    access_choices = ["Use DM pairing approval (recommended)", "Allow all direct messages", "Only allow listed user OpenIDs"]',
     '    access_choices = ["用私聊配对审批（推荐）", "允许所有私聊", "只允许名单里的用户 OpenID"]'),
    ('hermes_cli/main_platform_setup.py', '         "  1. Separate bot number (recommended)",',
     '         "  1. Separate bot number（推荐）",'),
    ('hermes_cli/model_setup_flows_azure.py', '         "     Recommended by Microsoft. Works for both OpenAI-style and Anthropic-style endpoints.",',
     '         "     微软推荐的方式，兼容 OpenAI 风格与 Anthropic 风格的端点。",'),
    ('hermes_cli/model_setup_flows_bedrock.py', '    _say("  Choose authentication method:", "", "    1. IAM credential chain (recommended)",',
     '    _say("  Choose authentication method:", "", "    1. IAM 凭据链（推荐）",'),
    ('hermes_cli/onepassword_secrets_cli.py', '            flag("--no-verify", "Store without probing 1Password first (not recommended)"),',
     '            flag("--no-verify", "不先探测 1Password 直接存（不建议）"),'),
    ('hermes_cli/secrets_cli.py', '            flag("--no-verify", "Store without probing Bitwarden first (not recommended)"),',
     '            flag("--no-verify", "不先探测 Bitwarden 直接存（不建议）"),'),
    ('hermes_cli/setup_platforms.py', '          "  [1] Automatic (recommended)",',
     '          "  [1] 自动（推荐）",'),
    ('hermes_cli/setup_quick.py', '        "Set up messaging now (recommended)", "Skip — set up later with \'hermes setup gateway\'",',
     '        "现在就接消息通道（推荐）", "先跳过 —— 之后用 \'coco setup gateway\' 再接",'),
    ('hermes_cli/subcommands/gateway.py', '    "(not recommended: two pollers on one bot token, port conflicts)")',
     '    "（不建议：同一个机器人令牌会有两个轮询者，端口也容易冲突）")'),
    ('hermes_cli/subcommands/gateway.py', '        "run", help="Run gateway in foreground (recommended for WSL, Docker, Termux)")',
     '        "run", help="前台运行网关（WSL / Docker / Termux 下推荐）")'),
    ('hermes_cli/subcommands/sessions.py', '        help="Skip the timestamped state.db backup taken before writing (not recommended)")',
     '        help="跳过写入前的带时间戳 state.db 备份（不建议）")'),
    ('hermes_cli/subcommands/sessions.py', '    _flag(sessions_repair, "--no-backup", help="Skip the timestamped backup copy (not recommended)")',
     '    _flag(sessions_repair, "--no-backup", help="跳过带时间戳的备份副本（不建议）")'),
    ('hermes_cli/tools_config_providers.py', '    choices = ["Enable public URLs without automatic expiry (recommended)", "Disable stored public URLs",',
     '    choices = ["开启公开链接、不自动过期（推荐）", "关闭保存的公开链接",'),
    ('hermes_cli/tools_config_providers.py', '        "Auto — use your main model / aggregator fallback (recommended)",',
     '        "自动 —— 用你的主模型 / 聚合服务兜底（推荐）",'),
    ('hermes_cli/uninstall.py', '    print("     (Recommended - you can reinstall later with your settings intact)")',
     '    print("     (推荐：只删程序，配置和会话都留着，之后重装还在)")'),
    ('hermes_cli/uninstall.py', '    print("     (Warning: This deletes all configs, sessions, and logs permanently)")',
     '    print("     (注意：配置、会话、日志会一起永久删除)")'),
    ('tools/clarify_tool.py', '        "as recommended, and auto-appends an "\n        "\'Other\' free-text row), multi-select (multi_select=true), or "',
     '        "as recommended. Some surfaces append an \'Other\' free-text row and "\n        "some do not — when in doubt add your own \'其他（我来补充）\' as the last "\n        "choice), multi-select (multi_select=true), or "'),
]


def classify(root: Path, entry) -> tuple:
    """返回 (状态, 详情)：OK / APPLY / ANCHOR"""
    rel, old, new = entry
    path = root / rel
    if not path.exists():
        return "ANCHOR", "文件不存在"
    text = path.read_text(encoding="utf-8")
    if new in text:
        return "OK", ""
    if old in text:
        return "APPLY", ""
    return "ANCHOR", "官方原文与中文都找不到"


def main() -> int:
    ap = argparse.ArgumentParser(description="把 Coco 的中文文案重打到官方文件里")
    ap.add_argument("--apply", action="store_true", help="真的写文件（默认只检查）")
    ap.add_argument("--list", action="store_true", help="只列出条目")
    args = ap.parse_args()

    if args.list:
        for rel, old, new in ENTRIES:
            print(f"{rel}\n  官方: {old.strip()[:70]}\n  中文: {new.strip()[:70]}")
        print(f"\n共 {len(ENTRIES)} 条")
        return 0

    applied, pending, anchors = [], [], []
    for entry in ENTRIES:
        status, detail = classify(REPO_ROOT, entry)
        if status == "OK":
            continue
        if status == "APPLY":
            if args.apply:
                path = REPO_ROOT / entry[0]
                text = path.read_text(encoding="utf-8")
                path.write_text(text.replace(entry[1], entry[2], 1), encoding="utf-8")
                applied.append(entry[0])
            else:
                pending.append(entry[0])
        else:
            anchors.append((entry[0], detail))

    for rel, detail in anchors:
        print(f"  [ANCHOR] {rel} —— {detail}：按 patches/README.md 23/24 人工重做后更新本表")
    if applied:
        print(f"  已重打 {len(applied)} 处中文文案：{', '.join(sorted(set(applied)))}")
    if pending:
        print(f"  待重打 {len(pending)} 处（加 --apply 执行）：{', '.join(sorted(set(pending)))}")
    if not (anchors or applied or pending):
        print("  全部已是中文，无需处理。")
    return 1 if (anchors or pending) else 0


if __name__ == "__main__":
    sys.exit(main())
