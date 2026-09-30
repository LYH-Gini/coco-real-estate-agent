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
    ('hermes_cli/doctor.py', "    ('Security Advisories', _check_security_advisories), ('MCP Server Security', _check_mcp_security),\n    ('Python Environment', _check_python_environment), ('SSL / CA Certificates', _check_certificates),\n    ('Required Packages', _check_required_packages), ('Configuration Files', _check_env_file),",
     "    ('安全公告', _check_security_advisories), ('MCP 服务安全', _check_mcp_security),\n    ('Python 环境', _check_python_environment), ('SSL 证书', _check_certificates),\n    ('依赖包', _check_required_packages), ('配置文件', _check_env_file),"),
    ('hermes_cli/doctor.py', "    ('xAI Model Retirement (May 15, 2026)', _check_xai_retirement),\n    ('Plugin import paths (removed Sep 14, 2026)', _check_plugin_compat), ('Auth Providers', _check_auth_providers),\n    ('Directory Structure', _check_directory_structure), (None, _check_state_db), (None, _check_checkpoint_store),",
     "    ('xAI 模型下线（2026-05-15）', _check_xai_retirement),\n    ('插件导入路径（2026-09-14 移除）', _check_plugin_compat), ('模型服务商登录', _check_auth_providers),\n    ('目录结构', _check_directory_structure), (None, _check_state_db), (None, _check_checkpoint_store),"),
    ('hermes_cli/doctor.py', "    ('External Tools', _check_git_and_rg), (None, _check_terminal_backend), (None, _check_node_and_browser),\n    (None, _check_npm_audit), ('API Connectivity', _check_api_connectivity),\n    ('Tool Availability', _check_tool_availability), ('Skills Hub', _check_skills_hub),\n    ('Memory Provider', _check_memory_provider), (None, _check_profiles),",
     "    ('外部工具', _check_git_and_rg), (None, _check_terminal_backend), (None, _check_node_and_browser),\n    (None, _check_npm_audit), ('模型服务连通性', _check_api_connectivity),\n    ('可用工具', _check_tool_availability), ('技能源', _check_skills_hub),\n    ('记忆存储', _check_memory_provider), (None, _check_profiles),"),
    ('hermes_cli/doctor.py', '        print(color(f"Unknown advisory ID: {ack_target!r}. Known IDs: {\', \'.join(sorted(valid_ids)) or \'(none)\'}", Colors.RED))',
     '        print(color(f"认不出这条公告编号：{ack_target!r}。可用编号：{\', \'.join(sorted(valid_ids)) or \'（无）\'}", Colors.RED))'),
    ('hermes_cli/doctor.py', '        print(color(f"  ✓ Acknowledged advisory {ack_target}. It will no longer trigger startup banners.", Colors.GREEN))',
     '        print(color(f"  ✓ 已确认公告 {ack_target}，启动时不再提示。", Colors.GREEN))'),
    ('hermes_cli/doctor.py', '        print(color(f"  ✗ Could not save the acknowledgement for {ack_target}. Make sure {_DHH}/config.yaml is "\n                    f"writable (`hermes config path` prints the exact file), then re-run "\n                    f"`hermes doctor --ack {ack_target}`.", Colors.RED))',
     '        print(color(f"  ✗ 没能保存 {ack_target} 的确认记录。请确认 {_DHH}/config.yaml 可写"\n                    f"（「coco config path」能打印它的确切路径），然后重跑 "\n                    f"「coco doctor --ack {ack_target}」。", Colors.RED))'),
    ('hermes_cli/doctor.py', '        print(color(f"  Fixed {total.fixed} issue(s).", Colors.GREEN, Colors.BOLD), end="")\n        print(color(f" {len(remaining)} issue(s) require manual intervention.", Colors.YELLOW, Colors.BOLD) if remaining else "")',
     '        print(color(f"  已自动修好 {total.fixed} 个问题。", Colors.GREEN, Colors.BOLD), end="")\n        print(color(f" 另有 {len(remaining)} 个问题需要你手动处理。", Colors.YELLOW, Colors.BOLD) if remaining else "")'),
    ('hermes_cli/doctor.py', '        print(color(f"  Found {len(remaining)} issue(s) to address:", Colors.YELLOW, Colors.BOLD))',
     '        print(color(f"  发现 {len(remaining)} 个需要处理的问题：", Colors.YELLOW, Colors.BOLD))'),
    ('hermes_cli/doctor.py', '            print(color("  Tip: run \'hermes doctor --fix\' to auto-fix what\'s possible.", Colors.DIM))',
     '            print(color("  提示：能自动修的问题，跑「coco doctor --fix」会帮你修。", Colors.DIM))'),
    ('hermes_cli/doctor.py', '        print(color("  All checks passed! 🎉", Colors.GREEN, Colors.BOLD))',
     '        print(color("  全部检查通过！🎉", Colors.GREEN, Colors.BOLD))'),
    ('hermes_cli/doctor.py', '                 "│                 🩺 Hermes Doctor                        │",',
     '                 "│                 🩺 Coco 部署体检                        │",'),
    ('hermes_cli/doctor_platform.py', '        "docker": f"run `{cmd}`, then recreate all Hermes containers", "apt": f"run `{cmd}`"}.get(method, "run `hermes update`")',
     '        "docker": f"跑 `{cmd}`，然后重建全部容器", "apt": f"跑 `{cmd}`"}.get(method, "跑 `coco update`")'),
    ('hermes_cli/doctor_platform.py', '                    "Re-sync version files (e.g. run \'hermes update\', or set hermes_cli/__init__.py __version__ to match pyproject.toml)", issues)',
     '                    "版本文件不一致：重新同步（跑 \'coco update\'，或把 hermes_cli/__init__.py 的 __version__ 改成与 pyproject.toml 一致）", issues)'),
    ('hermes_cli/doctor_platform.py', '            return check_info("No gateway registered yet — run `hermes gateway install`")',
     '            return check_info("还没有注册网关服务 —— 跑 `coco gateway install`")'),
    ('hermes_cli/doctor_platform.py', '        issues.append(f"Repair the CA bundle: run `hermes doctor --fix`, or `{pip_cmd}`")',
     '        issues.append(f"修复 CA 证书包：跑 `coco doctor --fix`，或 `{pip_cmd}`")'),
    ('hermes_cli/doctor_platform.py', '    "all permission grants. Run `hermes update` to get the stable identifier-pinned signing identity, "',
     '    "all permission grants. Run `coco update` to get the stable identifier-pinned signing identity, "'),
    ('hermes_cli/doctor_platform.py', '                        f"and rotate credentials, then run `hermes doctor --ack {hit.advisory.id}`.", f.manual_issues)',
     '                        f"and rotate credentials, then run `coco doctor --ack {hit.advisory.id}`.", f.manual_issues)'),
    ('hermes_cli/doctor_platform.py', '    link = link_dir / "hermes"',
     '    # Coco 只对外暴露 coco 命令（安装与更新会移除 hermes 软链），所以这里查的是 coco 链接。\n    # 照官方查 hermes 会让每次体检都报一个假问题，`--fix` 还会把 hermes 命令装回来。\n    _coco_only = (PROJECT_ROOT / "scripts" / "coco.sh").exists()\n    link_name = "coco" if _coco_only else "hermes"\n    link = link_dir / link_name'),
    ('tests/hermes_cli/test_doctor.py', '        assert "run `pkg upgrade hermes-agent`" in hint',
     '        assert "跑 `pkg upgrade hermes-agent`" in hint'),
    ('tests/hermes_cli/test_doctor.py', '        assert "Memory Provider" in out',
     '        assert "记忆存储" in out'),
    ('tests/hermes_cli/test_doctor.py', '        assert "Auth Providers" in out',
     '        assert "模型服务商登录" in out'),
    ('tests/hermes_cli/test_doctor_exit_status.py', '    assert ("fixture unresolved problem" if unresolved else "All checks passed") in result.stdout',
     '    assert ("fixture unresolved problem" if unresolved else "全部检查通过") in result.stdout'),
    ('hermes_cli/doctor_platform.py', '        check_warn(f"{name}: cannot prove the database is quiet", "(holder scan is unavailable on Windows)")',
     '        check_warn(f"{name}：没法确认数据库此刻是安静的", "（Windows 上看不了占用进程）")'),
    ('hermes_cli/doctor_platform.py', '        check_info(f"{name} is held by {describe_holder_pid(pid)}: {\', \'.join(sorted(by_pid[pid]))}")',
     '        check_info(f"{name} 正被占用：{describe_holder_pid(pid)} —— {\', \'.join(sorted(by_pid[pid]))}")'),
    ('hermes_cli/doctor_platform.py', '        check_warn(f"{name}: cannot prove the database is quiet",',
     '        check_warn(f"{name}：没法确认数据库此刻是安静的",'),
    ('hermes_cli/doctor_platform.py', '        check_info(f"{name}: no other process holds it right now — the offline conversion can run")',
     '        check_info(f"{name}：当前没有别的进程占用，离线转换可以跑")'),
    ('hermes_cli/doctor_platform.py', '        check_warn(f"Could not list Hermes databases: {exc}")',
     '        check_warn(f"列不出数据库：{exc}")'),
    ('hermes_cli/doctor_platform.py', '            check_warn(f"{name} is in WAL mode ({size}) despite database.journal_mode=delete",',
     '            check_warn(f"{name} 仍是 WAL 模式（{size}），但配置写的是 database.journal_mode=delete",'),
    ('hermes_cli/doctor_platform.py', '                check_warn(f"{name}: journal mode could not be read", f"({error}; cannot rule out WAL exposure)")',
     '                check_warn(f"{name}：读不到日志模式", f"（{error}；没法排除 WAL 风险）")'),
    ('hermes_cli/doctor_platform.py', '                check_info(f"{name}: journal mode could not be read ({error})")',
     '                check_info(f"{name}：读不到日志模式（{error}）")'),
    ('hermes_cli/doctor_platform.py', '            check_warn(f"{name} is in WAL mode on a cross-VM filesystem (virtiofs/9p, {size})",',
     '            check_warn(f"{name} 在跨虚拟机文件系统（virtiofs/9p）上是 WAL 模式（{size}）",'),
    ('hermes_cli/doctor_platform.py', '            check_warn(f"{name} is in WAL mode ({size})", "(exposed to the WAL-reset bug until SQLite is upgraded)")',
     '            check_warn(f"{name} 是 WAL 模式（{size}）", "（SQLite 升级前有 WAL 重置风险）")'),
    ('hermes_cli/doctor_platform.py', '            check_info(f"{name}: WAL journal mode ({size})")',
     '            check_info(f"{name}：WAL 日志模式（{size}）")'),
    ('hermes_cli/doctor_platform.py', '            check_info(f"{name}: rollback journal mode ({size}{\', not exposed\' if vulnerable else \'\'})")',
     '            check_info(f"{name}：回滚日志模式（{size}{\'，无风险\' if vulnerable else \'\'}）")'),
    ('hermes_cli/doctor_platform.py', '        check_info(f"To clear the exposure: {_wal_reset_repair_hint()}")',
     '        check_info(f"消除风险的办法：{_wal_reset_repair_hint()}")'),
    ('hermes_cli/doctor_platform.py', '        return check_ok("Version files consistent", f"({init_version})")',
     '        return check_ok("版本文件一致", f"({init_version})")'),
    ('hermes_cli/doctor_platform.py', '        (check_ok if up else check_info)(f"{static}: up" if up else f"{static}: down (expected if not enabled via env)")',
     '        (check_ok if up else check_info)(f"{static}: up" if up else f"{static}：未运行（没通过环境变量启用时属正常）")'),
    ('hermes_cli/doctor_platform.py', '        issues.append("No host gateway owns the gateway role — start the ONE host multiplexer: "',
     '        issues.append("没有网关在承担网关角色 —— 启动那个唯一的主网关："'),
    ('hermes_cli/doctor_platform.py', '        return check_warn(f"No host gateway owns the gateway role ({len(up)}/{len(slots)} supervision "',
     '        return check_warn(f"没有网关承担网关角色（{len(up)}/{len(slots)} 个监管槽位在位）："'),
    ('hermes_cli/doctor_platform.py', '    check_ok(f"Host gateway: {topology.describe()}")',
     '    check_ok(f"主网关：{topology.describe()}")'),
    ('hermes_cli/doctor_platform.py', '        check_warn(f"LEGACY per-profile gateway slots still supervised: {\', \'.join(legacy_up)}",',
     '        check_warn(f"仍在监管的旧版按配置网关：{\', \'.join(legacy_up)}",'),
    ('hermes_cli/doctor_platform.py', '        issues.append("Fold the legacy per-profile gateways into the host gateway: "',
     '        issues.append("把旧版按配置的网关并进主网关："'),
    ('hermes_cli/doctor_platform.py', '        return check_warn("SSL certificate check skipped", str(e))',
     '        return check_warn("跳过 SSL 证书检查", str(e))'),
    ('hermes_cli/doctor_platform.py', '        return check_ok("SSL CA certificate bundle is valid")',
     '        return check_ok("SSL 根证书包正常")'),
    ('hermes_cli/doctor_platform.py', '        return check_warn("SSL certificate check skipped", str(e))\n    check_fail("SSL CA certificate bundle is broken", first_error)',
     '        return check_warn("跳过 SSL 证书检查", str(e))\n    check_fail("SSL 根证书包有问题", first_error)'),
    ('hermes_cli/doctor_platform.py', '        check_ok("SSL CA certificate bundle repaired (certifi reinstalled)")',
     '        check_ok("SSL 根证书包已修好（重装了 certifi）")'),
    ('hermes_cli/doctor_platform.py', '        return check_warn("Gateway service linger", f"(could not import gateway helpers: {e})")',
     '        return check_warn("网关服务常驻（linger）", f"（导入网关辅助函数失败：{e}）")'),
    ('hermes_cli/doctor_platform.py', '        return check_warn("Could not verify systemd linger", f"({linger_detail})")',
     '        return check_warn("没能确认 systemd linger 状态", f"({linger_detail})")'),
    ('hermes_cli/doctor_platform.py', '        check_info("Run: sudo loginctl enable-linger $USER")\n        issues.append("Enable linger for the gateway user service: sudo loginctl enable-linger $USER")',
     '        check_info("执行：sudo loginctl enable-linger $USER")\n        issues.append("让网关用户服务常驻：sudo loginctl enable-linger $USER")'),
    ('hermes_cli/doctor_platform.py', '        return check_warn("macOS TCC grant check", "(could not read code-signing requirement of the desktop bundle)")',
     '        return check_warn("macOS 权限授予检查", "（读不到桌面版的签名要求）")'),
    ('hermes_cli/doctor_platform.py', '        return check_warn("macOS TCC grants will reset after every update", _TCC_CDHASH_DETAIL)',
     '        return check_warn("macOS 的权限授予会在每次更新后重置", _TCC_CDHASH_DETAIL)'),
    ('hermes_cli/doctor_platform.py', '    check_ok("macOS TCC signing identity is stable", _TCC_STABLE_DETAIL["certificate" in dr.lower()])\n    check_info("If macOS still re-prompts for permissions (toggle shows ON): the stored grant is stale — run "',
     '    check_ok("macOS 的签名身份稳定", _TCC_STABLE_DETAIL["certificate" in dr.lower()])\n    check_info("如果 macOS 还是反复要权限（开关显示已开）：存的授权过期了 —— 执行 "'),
    ('hermes_cli/doctor_platform.py', '            return check_ok("macOS TCC anchor active", f"({detail})")',
     '            return check_ok("macOS 授权锚点生效", f"({detail})")'),
    ('hermes_cli/doctor_platform.py', '            return check_ok("macOS TCC anchor installed", f"({anchored})")\n        check_warn("macOS TCC anchor missing" if status == "missing" else "macOS TCC anchor stale", f"({detail})")',
     '            return check_ok("macOS 授权锚点已装好", f"({anchored})")\n        check_warn("macOS 授权锚点缺失" if status == "missing" else "macOS 授权锚点是旧的", f"({detail})")'),
    ('hermes_cli/doctor_platform.py', '        check_info("One switch silences all macOS folder prompts: grant your terminal app Full Disk Access and Hermes "',
     '        check_info("一个开关能关掉 macOS 的所有目录弹窗：给终端 App 开「完全磁盘访问权限」，Coco "'),
    ('hermes_cli/doctor_platform.py', '        check_ok("macOS Full Disk Access granted", "(no per-folder permission prompts will occur)")',
     '        check_ok("macOS 完全磁盘访问权限已授予", "（不会再逐个目录弹权限）")'),
    ('hermes_cli/doctor_platform.py', '        return check_ok("No active security advisories")',
     '        return check_ok("没有需要处理的安全公告")'),
    ('hermes_cli/doctor_platform.py', '            check_warn(f"{h.package}=={h.installed_version} still installed (advisory {h.advisory.id} acknowledged)")',
     '            check_warn(f"仍处于安装状态（公告 {h.advisory.id} 已确认）")'),
    ('hermes_cli/doctor_platform.py', '            check_info(f"SQLite source id: {(src[:48] + \'…\') if len(src) > 48 else src}")',
     '            check_info(f"SQLite 源码版本号：{(src[:48] + \'…\') if len(src) > 48 else src}")'),
    ('hermes_cli/doctor_platform.py', '                check_warn(name, "(optional, not installed)")',
     '                check_warn(name, "（可选，未安装）")'),
    ('hermes_cli/doctor_platform.py', '        check_warn("Venv entry point not found", "(hermes not in venv/bin/ or .venv/bin/ — reinstall with pip install -e \'.[all]\')")\n        return f.manual_issues.append(f"Reinstall entry point: cd {PROJECT_ROOT} && source venv/bin/activate && pip install -e \'.[all]\'")\n    check_ok(f"Venv entry point exists ({venv_bin.relative_to(PROJECT_ROOT)})")',
     '        check_warn("虚拟环境入口缺失", "（venv/bin/ 或 .venv/bin/ 里没有 hermes —— 用 pip install -e \'.[all]\' 重装）")\n        return f.manual_issues.append(f"重装入口：cd {PROJECT_ROOT} && source venv/bin/activate && pip install -e \'.[all]\'")\n    check_ok(f"虚拟环境入口在位（{venv_bin.relative_to(PROJECT_ROOT)}）")'),
    ('hermes_cli/doctor_platform.py', '            return check_ok(f"{display}/{link_name} → correct target")\n        check_warn(f"{display}/{link_name} points to wrong target", f"(→ {target}, expected → {expected})")',
     '            return check_ok(f"{display}/{link_name} → 指向正确")\n        check_warn(f"{display}/{link_name} 指向的目标不对", f"（→ {target}，应该是 → {expected}）")'),
    ('hermes_cli/doctor_platform.py', '            return f.issues.append(f"Broken symlink at {display}/{link_name} — run \'coco doctor --fix\'")',
     '            return f.issues.append(f"{display}/{link_name} 是坏链接 —— 跑「coco doctor --fix」")'),
    ('hermes_cli/doctor_platform.py', '        verb = "Fixed"',
     '        verb = "已修复"'),
    ('hermes_cli/doctor_platform.py', '        return check_ok(f"{display}/{link_name} exists (non-symlink)")',
     '        return check_ok(f"{display}/{link_name} 存在（是普通文件）")'),
    ('hermes_cli/doctor_platform.py', '        check_fail(f"{display}/{link_name} not found", f"({link_name} 命令在虚拟环境外可能不可用)")',
     '        check_fail(f"找不到 {display}/{link_name}", f"({link_name} 命令在虚拟环境外可能不可用)")'),
    ('hermes_cli/doctor_platform.py', '            return f.issues.append(f"Missing {display}/{link_name} symlink — run \'coco doctor --fix\'")',
     '            return f.issues.append(f"缺 {display}/{link_name} 链接 —— 跑「coco doctor --fix」")'),
    ('hermes_cli/doctor_platform.py', '        verb = "Created"',
     '        verb = "已创建"'),
    ('hermes_cli/doctor_platform.py', '    check_ok(f"{verb} symlink: {display}/{link_name} → {venv_bin}")',
     '    check_ok(f"{verb}链接：{display}/{link_name} → {venv_bin}")'),
    ('hermes_cli/doctor_platform.py', '    if verb == "Created" and str(link_dir) not in os.environ.get("PATH", "").split(os.pathsep):\n        check_warn(f"{display} is not on your PATH", "(add it to your shell config: export PATH=\\"$HOME/.local/bin:$PATH\\")")\n        f.manual_issues.append(f"Add {display} to your PATH")',
     '    if verb == "已创建" and str(link_dir) not in os.environ.get("PATH", "").split(os.pathsep):\n        check_warn(f"{display} 不在你的 PATH 里", "（加到你的 shell 配置里：export PATH=\\"$HOME/.local/bin:$PATH\\"）")\n        f.manual_issues.append(f"把 {display} 加到 PATH 里")'),
    ('hermes_cli/doctor_tools.py', '    "image_gen": "(image generation unavailable — check the provider selection and its key or SDK with \'hermes tools\')",',
     '    "image_gen": "（图片生成不可用 —— 在「coco tools」里检查服务商选择与对应的密钥或 SDK）",'),
    ('hermes_cli/doctor_tools.py', '        check_info(f"Install for faster search: {_system_package_install_cmd(\'ripgrep\')}")',
     '        check_info(f"想搜得更快可以装：{_system_package_install_cmd(\'ripgrep\')}")'),
    ('hermes_cli/doctor_tools.py', '        check_info("Docker backend is not available inside Termux (expected on Android)")',
     '        check_info("Termux 里用不了 Docker（Android 上属正常）")'),
    ('hermes_cli/doctor_tools.py', '        check_warn("Docker/Podman not found", "(optional)")',
     '        check_warn("没装 Docker/Podman", "(optional)")'),
    ('hermes_cli/doctor_tools.py', '        check_info(f"Vercel auth {line}")',
     '        check_info(f"Vercel 登录 {line}")'),
    ('hermes_cli/doctor_tools.py', '    check_info("Vercel persistence: snapshot filesystem only; live processes do not survive sandbox recreation"',
     '    check_info("Vercel 持久化：只快照文件系统；沙箱重建后运行中的进程不会保留"'),
    ('hermes_cli/doctor_tools.py', '        check_info("Running inside a container — using local terminal backend (docker-in-docker is not configured by default)")',
     '        check_info("跑在容器里 —— 用本机终端后端（默认没配 docker-in-docker）")'),
    ('hermes_cli/doctor_tools.py', '        check_ok("agent-browser", "(resolves via npx on first use)")',
     '        check_ok("agent-browser", "（首次使用时通过 npx 解析）")'),
    ('hermes_cli/doctor_tools.py', '            check_info("  Warmed npx cache for agent-browser" if warm_agent_browser_npx_cache()\n                       else "  Could not warm npx cache (offline or npx unavailable)")',
     '            check_info("已预热 agent-browser 的 npx 缓存" if warm_agent_browser_npx_cache()\n                       else "  没能预热 npx 缓存（离线或没有 npx）")'),
    ('hermes_cli/doctor_tools.py', '        check_ok("agent-browser", "(browser automation)")',
     '        check_ok("agent-browser", "（浏览器自动化）")'),
    ('hermes_cli/doctor_tools.py', '        check_warn("agent-browser found but not runnable", f"(broken symlink at {resolved}? run: npx agent-browser --version)")',
     '        check_warn("找到了 agent-browser 但跑不起来", f"（{resolved} 是坏链接？跑一下：npx agent-browser --version）")'),
    ('hermes_cli/doctor_tools.py', '        check_warn("agent-browser not installed", "(requires npm/npx on PATH)")',
     '        check_warn("没装 agent-browser", "（需要 PATH 里有 npm/npx）")'),
    ('hermes_cli/doctor_tools.py', '    check_info("Termux browser setup:")',
     '    check_info("Termux 浏览器设置：")'),
    ('hermes_cli/doctor_tools.py', '        check_info(f"Install with: cd {PROJECT_ROOT} && npx playwright install {with_deps}chromium")',
     '        check_info(f"安装方式：cd {PROJECT_ROOT} && npx playwright install {with_deps}chromium")'),
    ('hermes_cli/doctor_tools.py', '        check_warn("browser.engine=lightpanda is shadowed", f"({reason})")\n        check_info("Fix: pick Lightpanda in `hermes tools` → Browser Automation, or set browser.engine: auto")',
     '        check_warn("browser.engine=lightpanda 被遮蔽了", f"({reason})")\n        check_info("修法：在「coco tools」→ Browser Automation 里选 Lightpanda，或把 browser.engine 设成 auto")'),
    ('hermes_cli/doctor_tools.py', '                        ("Lightpanda selected but binary not found", "(browser tools will fail until it is installed)")):',
     '                        ("选了 Lightpanda 但找不到可执行文件", "（装上之前浏览器工具用不了）")):'),
    ('hermes_cli/doctor_tools.py', '        _termux_browser_hints("Node.js not found (browser tools are optional in the tested Termux path)",',
     '        _termux_browser_hints("没装 Node.js (browser tools are optional in the tested Termux path)",'),
    ('hermes_cli/doctor_tools.py', '        check_warn("Node.js not found", "(optional, needed for browser tools)")',
     '        check_warn("没装 Node.js", "（可选，浏览器工具需要）")'),
    ('hermes_cli/doctor_tools.py', '            check_ok(f"{label} deps", "(no known vulnerabilities)")',
     '            check_ok(f"{label} 依赖", "（没有已知漏洞）")'),
    ('hermes_cli/doctor_tools.py', '            check_warn(f"{label} deps", f"({critical} critical, {high} high, {moderate} moderate — {remedy})")',
     '            check_warn(f"{label} 依赖", f"（严重 {critical}、高 {high}、中 {moderate} —— {remedy}）")'),
    ('hermes_cli/doctor_tools.py', '                check_info("  ^ build-time tooling (not runtime); if manual npm remediation "',
     '                check_info("构建期工具（不影响运行）；如果要手工修 npm "'),
    ('hermes_cli/doctor_tools.py', '                check_info(f"  ^ {detail}; report/pin the fix in package-lock.json — see #116774")\n            issues.append(f"{label} has {total} npm {_plural(total)}")',
     '                check_info(f"{detail}；把修复写进 package-lock.json（见 #116774）")\n            issues.append(f"{label} 有 {total} 个 npm 漏洞")'),
    ('hermes_cli/doctor_tools.py', '            check_ok(f"{label} deps", f"({moderate} moderate {_plural(moderate)})")',
     '            check_ok(f"{label} 依赖", f"（其中中危 {moderate} 个）")'),
    ('hermes_cli/doctor_tools.py', '        check_info("Termux compatibility fallbacks:")',
     '        check_info("Termux 兼容回退：")'),
    ('hermes_cli/doctor_tools.py', '        detail = f"(missing {\', \'.join(env_vars)})" if env_vars else _TOOLSET_SETUP_HINTS.get(item["name"], "(system dependency not met)")',
     '        detail = f"(missing {\', \'.join(env_vars)})" if env_vars else _TOOLSET_SETUP_HINTS.get(item["name"], "（系统依赖没装齐）")'),
    ('hermes_cli/doctor_tools.py', '        f.issues.append("Run \'hermes setup\' to configure missing API keys for full tool access")',
     '        f.issues.append("跑「coco setup」把缺的密钥配上，工具才能全用")'),
    ('tests/hermes_cli/test_doctor.py', '        assert "hermes tools" in image_line and "system dependency" not in image_line and "unavailable" in image_line\n        assert "system dependency not met" in next(line for line in out.splitlines() if "homeassistant" in line)\n        assert any("hermes setup" in issue for issue in f.issues)',
     '        assert "coco tools" in image_line and "系统依赖" not in image_line and "不可用" in image_line\n        assert "系统依赖没装齐" in next(line for line in out.splitlines() if "homeassistant" in line)\n        assert any("coco setup" in issue for issue in f.issues)'),
    ('tests/hermes_cli/test_doctor.py', '    assert "resolves via npx on first use" in out',
     '    assert "首次使用时通过 npx 解析" in out'),
    ('tests/hermes_cli/test_doctor.py', '    assert "Warmed npx cache for agent-browser" in out\n    assert "Could not warm npx cache" not in out',
     '    assert "已预热 agent-browser 的 npx 缓存" in out\n    assert "没能预热 npx 缓存" not in out'),
    ('tests/hermes_cli/test_doctor.py', '    assert "Could not warm npx cache (offline or npx unavailable)" in out',
     '    assert "没能预热 npx 缓存（离线或没有 npx）" in out'),
    ('tests/hermes_cli/test_doctor.py', '    assert "browser.engine=lightpanda is shadowed" in out',
     '    assert "browser.engine=lightpanda 被遮蔽了" in out'),
    ('tests/hermes_cli/test_doctor.py', '    assert "Lightpanda selected but binary not found" in out',
     '    assert "选了 Lightpanda 但找不到可执行文件" in out'),
    ('tests/hermes_cli/test_doctor_audit_remedy.py', '    assert issues == ["Browser tools (agent-browser) has 2 npm vulnerabilities"]',
     '    assert issues == ["Browser tools (agent-browser) 有 2 个 npm 漏洞"]'),
    ('tests/hermes_cli/test_doctor_audit_remedy.py', '    assert "no known vulnerabilities" in out',
     '    assert "没有已知漏洞" in out'),
    ('tests/hermes_cli/test_doctor_command_install.py', '        assert "Fixed symlink" in out',
     '        assert "已修复链接" in out'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', 'EXPOSED_TEXT = "exposed to the WAL-reset bug"',
     'EXPOSED_TEXT = "SQLite 升级前有 WAL 重置风险"'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', '        assert "state.db: journal mode could not be read" in out',
     '        assert "state.db：读不到日志模式" in out'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', '        assert "cannot rule out WAL exposure" in out',
     '        assert "没法排除 WAL 风险" in out'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', '        assert "state.db is in WAL mode on a cross-VM filesystem" in out',
     '        assert "state.db 在跨虚拟机文件系统（virtiofs/9p）上是 WAL 模式" in out'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', '        assert "state.db is in WAL mode" in out',
     '        assert "state.db 是 WAL 模式" in out'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', '        assert "state.db: rollback journal mode" in out',
     '        assert "state.db：回滚日志模式" in out'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', '        assert "state.db: WAL journal mode" in out',
     '        assert "state.db：WAL 日志模式" in out'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', '        assert "state.db is in WAL mode" in out\n        assert "projects.db: rollback journal mode" in out\n        assert "kanban.db: rollback journal mode" in out\n        assert "kanban/boards/myboard/kanban.db is in WAL mode" in out',
     '        assert "state.db 是 WAL 模式" in out\n        assert "projects.db：回滚日志模式" in out\n        assert "kanban.db：回滚日志模式" in out\n        assert "kanban/boards/myboard/kanban.db 是 WAL 模式" in out'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', '        assert "state.db: rollback journal mode" in out',
     '        assert "state.db：回滚日志模式" in out'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', '        assert "state.db: journal mode could not be read" in out\n        assert "cannot rule out WAL exposure" in out',
     '        assert "state.db：读不到日志模式" in out\n        assert "没法排除 WAL 风险" in out'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', '        assert "state.db: journal mode could not be read" in out',
     '        assert "state.db：读不到日志模式" in out'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', '        assert "state.db: journal mode could not be read" in out\n        assert "cannot rule out WAL exposure" not in out',
     '        assert "state.db：读不到日志模式" in out\n        assert "没法排除 WAL 风险" not in out'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', '        assert "state.db is in WAL mode" in out and "despite database.journal_mode=delete" in out',
     '        assert "state.db 仍是 WAL 模式" in out and "database.journal_mode=delete" in out'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', '        assert "state.db: WAL journal mode" not in out\n        assert ("To clear the exposure:" in out) is exposed',
     '        assert "state.db：WAL 日志模式" not in out\n        assert ("消除风险的办法：" in out) is exposed'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', '        assert f"state.db is held by PID {holder.pid}" in out and "state.db" in out.split("held by PID")[1]',
     '        assert f"state.db 正被占用：PID {holder.pid}" in out and "state.db" in out.split("正被占用：PID")[1]'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', '        assert "cannot prove the database is quiet" in out and "open-file scan unavailable" in out',
     '        assert "没法确认数据库此刻是安静的" in out and "open-file scan unavailable" in out'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', '        assert "state.db: WAL journal mode" in out',
     '        assert "state.db：WAL 日志模式" in out'),
    ('tests/hermes_cli/test_doctor_journal_modes.py', '        assert re.search(r"\\(\\d[\\d.]* [KMGT]?B\\)", out)\n        assert "To clear the exposure:" in out',
     '        assert re.search(r"（(\\d[\\d.]* [KMGT]?B)）", out)\n        assert "消除风险的办法：" in out'),
]


def classify(root: Path, entry) -> tuple:
    """返回 (状态, 详情)：OK / APPLY / ANCHOR"""
    rel, old, new = entry
    path = root / rel
    if not path.exists():
        return "ANCHOR", "文件不存在"
    text = path.read_text(encoding="utf-8")
    if old in text:
        # 同一条官方文案在文件里可能出现多次（例如同一条断言写了两处），
        # 只要官方原文还在就算没改完，避免"一半中文一半英文"被当成功。
        return "APPLY", f"{text.count(old)} 处"
    if new in text:
        return "OK", ""
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

    # 条目之间有包含关系（同一条文案被两轮改动覆盖时）：长条目先处理，
    # 否则短的先替换会把长条目的原文切碎，长条目永远匹配不上。
    ordered = sorted(ENTRIES, key=lambda e: -len(e[1]))
    applied, pending, anchors = [], [], []
    for entry in ordered:
        status, detail = classify(REPO_ROOT, entry)
        if status == "OK":
            continue
        if status == "APPLY":
            if args.apply:
                path = REPO_ROOT / entry[0]
                text = path.read_text(encoding="utf-8")
                hits = text.count(entry[1])
                path.write_text(text.replace(entry[1], entry[2]), encoding="utf-8")
                applied.append(f"{entry[0]}（{hits} 处）")
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
