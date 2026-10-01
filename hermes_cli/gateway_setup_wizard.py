"""Gateway platform setup wizard (hermes gateway setup): platform registry/status table, per-platform setup prompts, service offer.

Extracted from ``hermes_cli/gateway.py``. Bodies read facade helpers through ``_gw()`` (late
binding on ``hermes_cli.gateway``) so the seams tests and callers patch on the facade keep
intercepting the moved code.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from hermes_cli.setup import _boxed, platform_status_label, print_success  # def-time binding (table value)
from hermes_cli.setup import print_warning  # def-time binding (table value)


def _gw():
    from hermes_cli import gateway  # late: the facade imports this module
    return gateway


# Built-in per-platform setup config (env vars, instructions, prompts). Telegram, WhatsApp, Email,
# SMS, etc. live in plugins/platforms/<name>/ and are discovered via the platform registry.
_PLATFORMS = [
    {
        "key": "mattermost", "label": "Mattermost", "emoji": "💬", "token_var": "MATTERMOST_TOKEN",
        "setup_instructions": [
            "1. 在 Mattermost 里：Integrations → Bot Accounts → Add Bot Account",
            "   （需要先在 System Console → Integrations → Bot Accounts 里开启）",
            "2. 起个用户名（例如 coco），复制它的机器人 token",
            "3. 自建的 Mattermost 都能用 —— 填你的服务地址",
            "4. 查你的用户 ID：点左上角头像 → Profile",
            "   那里显示的就是用户 ID，点一下即可复制。",
            "   ⚠ 这不是用户名 —— 是一串 26 位字母数字 ID。",
            "5. 拿频道 ID：点频道名 → View Info → 复制 ID",
        ],
        "vars": [
            {"name": "MATTERMOST_URL", "prompt": "服务地址（如 https://mm.example.com）",
             "password": False, "help": "你的 Mattermost 服务地址，自建实例都行。"},
            {"name": "MATTERMOST_TOKEN", "prompt": "机器人 token", "password": True,
             "help": "粘贴上面第 2 步拿到的机器人 token。"},
            {"name": "MATTERMOST_ALLOWED_USERS", "prompt": "白名单用户 ID（逗号分隔）",
             "password": False, "is_allowlist": True, "help": "上面第 4 步拿到的用户 ID。"},
            {"name": "MATTERMOST_HOME_CHANNEL",
             "prompt": "主页频道 ID（接收定时任务与通知；留空则以后用 /set-home 设置）",
             "password": False, "help": "Coco 送定时任务结果与通知的频道 ID。"},
            {"name": "MATTERMOST_REPLY_MODE",
             "prompt": "回复方式 —— off 平铺、thread 收在话题里（默认 off）",
             "password": False,
             "help": "off = 平铺在频道里，thread = 回复收在你的消息下。"},
        ],
    },
    {"key": "signal", "label": "Signal", "emoji": "📡", "token_var": "SIGNAL_HTTP_URL"},
    {"key": "weixin", "label": "Weixin / WeChat", "emoji": "💬", "token_var": "WEIXIN_ACCOUNT_ID"},
    {
        "key": "bluebubbles", "label": "BlueBubbles (iMessage)",
        "emoji": "💬", "token_var": "BLUEBUBBLES_SERVER_URL",
        "setup_instructions": [
            "1. 在一台充当 iMessage 服务端的 Mac 上装 BlueBubbles：",
            "   https://bluebubbles.app/",
            "2. 走完 BlueBubbles 的配置向导 —— 用你的 Apple ID 登录",
            "3. 在 BlueBubbles Settings → API 里记下服务地址与密码",
            "4. 服务地址通常是 http://<你的MacIP>:1234",
            "5. Coco 通过 BlueBubbles 的 REST 接口连上，",
            "   用本机 webhook 收消息。",
            "6. 授权用户用配对码：coco pairing generate bluebubbles",
            "   把配对码发给对方，他在 iMessage 里发回来即可通过。",
        ],
        "vars": [
            {"name": "BLUEBUBBLES_SERVER_URL",
             "prompt": "BlueBubbles 服务端地址（如 http://192.168.1.10:1234）", "password": False,
             "help": "BlueBubbles Settings → API 里显示的那个地址。"},
            {"name": "BLUEBUBBLES_PASSWORD", "prompt": "BlueBubbles 服务端密码", "password": True,
             "help": "BlueBubbles Settings → API 里显示的那个密码。"},
            {"name": "BLUEBUBBLES_ALLOWED_USERS",
             "prompt": "预授权的手机号或 iMessage ID（逗号分隔；留空则走配对授权）",
             "password": False, "is_allowlist": True,
             "help": "可选 —— 预授权指定用户；留空就用配对授权（推荐）。"},
            {"name": "BLUEBUBBLES_HOME_CHANNEL",
             "prompt": "主页频道（接收定时任务与通知的手机号或 iMessage ID，可留空）",
             "password": False,
             "help": "接收定时任务结果与通知的手机号或 Apple ID。"},
        ],
    },
    {
        "key": "qqbot", "label": "QQ Bot", "emoji": "🐧", "token_var": "QQ_APP_ID",
        "setup_instructions": [
            "1. 去 q.qq.com 注册一个 QQ 机器人应用",
            "2. 在应用页面记下 App ID 与 App Secret",
            "3. 开启需要的事件（C2C、群聊、频道消息）",
            "4. 配置沙箱或发布机器人",
        ],
        "vars": [
            {"name": "QQ_APP_ID", "prompt": "QQ 机器人 App ID", "password": False,
             "help": "q.qq.com 上那个 App ID。"},
            {"name": "QQ_CLIENT_SECRET", "prompt": "QQ 机器人 App Secret", "password": True,
             "help": "q.qq.com 上那个 App Secret。"},
            {"name": "QQ_ALLOWED_USERS",
             "prompt": "白名单用户 OpenID（逗号分隔；留空 = 谁都能用）",
             "password": False, "is_allowlist": True,
             "help": "可选 —— 把私聊限制在指定用户 OpenID。"},
            {"name": "QQBOT_HOME_CHANNEL",
             "prompt": "主页频道（接收定时任务的用户/群 OpenID，可留空）", "password": False,
             "help": "接收定时任务结果与通知的 OpenID。"},
        ],
    },
    {
        "key": "yuanbao", "label": "Yuanbao", "emoji": "💎", "token_var": "YUANBAO_APP_ID",
        "setup_instructions": [
            "1. 从 https://yuanbao.tencent.com/ 下载元宝 App",
            "2. 在 App 里进 PAI → My Bot，新建一个机器人",
            "3. 建好后复制 App ID 与 App Secret",
            "4. 填在下面，Coco 会自动用 WebSocket 连上",
        ],
        "vars": [
            {"name": "YUANBAO_APP_ID", "prompt": "App ID", "password": False,
             "help": "元宝 IM 机器人凭据里的 App ID。"},
            {"name": "YUANBAO_APP_SECRET", "prompt": "App Secret", "password": True,
             "help": "元宝 IM 机器人凭据里的 App Secret（用于 HMAC 签名）。"},
        ],
    },
]


def _all_platforms() -> list[dict]:
    """Built-in ``_PLATFORMS`` plus registry plugin platforms (same dict shape, source in
    ``_registry_entry``). Plugins are discovered here (idempotent) so the setup menu works without a
    running gateway; user-installed ones still need ``plugins.enabled`` (untrusted code). Matrix is
    hidden on Windows: python-olm has no wheel or native build (use WSL)."""
    try:
        from hermes_cli.plugins import discover_plugins
        discover_plugins()
    except Exception as e:
        _gw().logger.debug("plugin discovery failed during platform enumeration: %s", e)

    hide_matrix = sys.platform == "win32"
    platforms = [dict(p) for p in _gw()._PLATFORMS if not (hide_matrix and p.get("key") == "matrix")]
    by_key = {p["key"]: p for p in platforms}

    try:
        from gateway.platform_registry import platform_registry
    except Exception:
        return platforms

    for entry in platform_registry.all_entries():
        if entry.name in by_key or (hide_matrix and entry.name == "matrix"):
            continue
        platforms.append({
            "key": entry.name, "label": entry.label, "emoji": entry.emoji,
            "token_var": entry.required_env[0] if entry.required_env else "",
            "install_hint": entry.install_hint, "_registry_entry": entry,
        })
    return platforms


def _platform_status(platform: dict) -> str:
    """Plain-text status string; uncolored because ANSI codes break curses menu width math."""
    entry = platform.get("_registry_entry")
    if entry is not None:
        # Prefer is_connected (env + config.yaml) over check_fn (a coarse deps gate). Never fall back
        # to check_fn when is_connected returned False, or "SDK installed" would override "no token".
        try:
            if entry.is_connected is not None:
                from gateway.config import PlatformConfig
                configured = bool(entry.is_connected(PlatformConfig(enabled=True)))
            else:
                configured = bool(entry.check_fn())
        except Exception:
            configured = False
        return "configured" if configured else "not configured"

    token_var = platform.get("token_var", "")
    if not token_var:
        return "not configured"
    # Built-ins needing a second credential to count as fully configured.
    second_var = {"signal": "SIGNAL_ACCOUNT", "weixin": "WEIXIN_TOKEN"}.get(platform.get("key"))
    present = [bool(_gw().get_env_value(v)) for v in (token_var, second_var) if v]
    if all(present):
        return "configured"
    return "partially configured" if any(present) else "not configured"


def _set_platform_unauthorized_dm_behavior(platform_key: str, behavior: str) -> None:
    """Persist a platform-specific unauthorized-DM policy in config.yaml."""
    _gw().write_platform_config_field(platform_key, "unauthorized_dm_behavior", behavior, raw=True)


def _print_setup_header(title: str) -> None:
    print()
    print(_gw().color(f"  ─── {title} 配置 ───", _gw().Colors.CYAN))


def _confirm_reconfigure(label: str, *env_vars: str) -> bool:
    """False when ``label`` is already configured (all ``env_vars`` set) and the user declines."""
    if all(_gw().get_env_value(v) for v in env_vars):
        print()
        _gw().print_success(f"{label} 已经配好了。")
        return _gw().prompt_yes_no(f"  要重新配置 {label} 吗？", False)
    return True


def _offer_home_channel(home_var: str, user_id: str, what: str) -> None:
    """Offer to persist ``user_id`` as ``home_var`` (e.g. "your Telegram user ID")."""
    if _gw().prompt_yes_no(f"  把 {what}（{user_id}）设为主页频道吗？", True):
        _gw().save_env_value(home_var, user_id)
        _gw().print_success(f"  主页频道已设为 {user_id}")


def _save_env_values(**values: str) -> None:
    for name, value in values.items():
        _gw().save_env_value(name, value)


def _prompt_csv(prompt_text: str, default: str) -> str:
    """Comma-separated ID prompt with whitespace stripped."""
    return _gw().prompt(prompt_text, default, password=False).replace(" ", "")


# (default index, *choices) for the no-allowlist access prompt, keyed by is_email.
_UNAUTHORIZED_ACCESS_CHOICES = {
    True: (3,
        "开放访问（任何邮箱都能给我的机器人发消息）",
        "配对授权（陌生邮箱会收到一个配对码）",
        "礼貌拒绝（回一条提示，之后不再响应）",
        "陌生来信一律不回应"),
    False: (1,
        "开放访问（谁都能给我的机器人发消息）",
        "配对授权（陌生用户申请，你用「coco pairing approve」批准）",
        "礼貌拒绝（回一条提示，之后不再响应）",
        "先跳过（配置前机器人一律不回应）"),
}


def _prompt_unauthorized_access(platform_key: str) -> None:
    """No allowlist was given — ask open access vs DM pairing vs decline vs skip/silent, and persist."""
    is_email = platform_key == "email"
    print()
    default_idx, *access_choices = _UNAUTHORIZED_ACCESS_CHOICES[is_email]
    access_idx = _gw().prompt_choice("  陌生用户怎么处理？", access_choices, default_idx)
    if access_idx == 0:
        _gw().save_env_value("EMAIL_ALLOW_ALL_USERS" if is_email else "GATEWAY_ALLOW_ALL_USERS", "true")
        _gw().print_warning("  已开放访问 —— 谁都能用你的机器人。")
    elif access_idx == 1:
        if is_email:
            _set_platform_unauthorized_dm_behavior("email", "pair")
        _gw().print_success("  已设为配对授权 —— 陌生用户会收到一个配对码来申请。")
        _gw().print_info("  批准方式：coco pairing approve <平台> <配对码>")
    elif access_idx == 2:
        _set_platform_unauthorized_dm_behavior(platform_key, "decline")
        _gw().print_success("  陌生来信会收到一次礼貌拒绝，之后不再响应。")
    elif is_email:
        _gw().print_success("  陌生邮箱来信一律不回应。")
    else:
        _gw().print_info("  已跳过 —— 以后可以跑「coco gateway setup」再配。")


def _telegram_auto_setup(token_var: str) -> tuple[bool, object]:
    """Offer the managed-bot QR flow. Returns (token_saved, owner_user_id)."""
    print()
    _gw()._print_info_lines(
        "  Telegram 可以自动配（托管机器人）：",
        "  [1] 自动（扫码 → 在 Telegram 里确认 → 完成）", "  [2] 手动（用 BotFather 的 token）",
    )
    if _gw().prompt("  请选择 [1/2]", default="1").strip() != "1":
        return False, None
    try:
        from hermes_cli.telegram_managed_bot import (
            auto_setup_telegram_bot_result, is_valid_telegram_bot_token,
        )
    except ImportError:
        _gw().print_warning("  这台机器上没法自动配置。")
        return False, None
    result = auto_setup_telegram_bot_result()
    if result and is_valid_telegram_bot_token(result.token):
        _gw().save_env_value(token_var, result.token)
        _gw().print_success("  已保存 TELEGRAM_BOT_TOKEN")
        return True, result.owner_user_id
    if result:
        _gw().print_warning("  自动配置返回的 Telegram token 不合法。")
    print()
    _gw().print_info("  改走手动配置…")
    return False, None


def _clean_discord_ids(cleaned: str) -> str:
    """Strip common Discord prefixes (user:123, <@123>, <@!123>) from a comma-separated list."""
    parts = []
    for uid in cleaned.split(","):
        uid = uid.strip()
        if uid.startswith("<@") and uid.endswith(">"):
            uid = uid.lstrip("<@!").rstrip(">")
        if uid.lower().startswith("user:"):
            uid = uid[5:]
        if uid:
            parts.append(uid)
    return ",".join(parts)


def _prompt_allowlist_var(var: dict, platform_key: str, auto_owner_user_id) -> str | None:
    """Allowlist prompt for one var; returns the saved value or None (open-access prompt shown)."""
    if "TELEGRAM" in var["name"] and auto_owner_user_id:
        detected_id = str(auto_owner_user_id)
        _gw().print_success(f"  识别到你的 Telegram 用户 ID：{detected_id}")
        if _gw().prompt_yes_no("  允许这个 Telegram 账号使用机器人吗？", True):
            extra = _gw().prompt("  还要加哪些用户 ID（逗号分隔，可留空）", password=False)
            ids = [detected_id]
            for uid in extra.replace(" ", "").split(","):
                if uid and uid not in ids:
                    ids.append(uid)
            cleaned = ",".join(ids)
            _gw().save_env_value(var["name"], cleaned)
            _gw().print_success("  已保存 —— 只有这些用户能用这个机器人。")
            return cleaned

    _gw()._print_info_lines(
        "  出于安全，网关默认拒绝所有用户。",
        "  填用户 ID 建白名单；留空的话，下一步我会问你开放访问怎么处理。",
    )
    value = _gw().prompt(f"  {var['prompt']}", password=False)
    if not value:
        _prompt_unauthorized_access(platform_key)
        return None
    cleaned = value.replace(" ", "")
    if "DISCORD" in var["name"]:
        cleaned = _clean_discord_ids(cleaned)
    _gw().save_env_value(var["name"], cleaned)
    _gw().print_success("  已保存 —— 只有这些用户能用这个机器人。")
    return cleaned


def _setup_standard_platform(platform: dict):
    """Interactive setup for Telegram, Discord, or Slack."""
    from hermes_cli.setup_hidden_env import is_setup_hidden_env as _is_setup_hidden_env
    emoji, label, token_var = platform["emoji"], platform["label"], platform["token_var"]
    _print_setup_header(f"{emoji} {label}")

    instructions = platform.get("setup_instructions")
    if instructions:
        print()
        _gw()._print_info_lines(*(f"  {line}" for line in instructions))

    if not _confirm_reconfigure(label, token_var):
        return

    auto_token_saved, auto_owner_user_id = False, None
    if platform.get("key") == "telegram":
        auto_token_saved, auto_owner_user_id = _telegram_auto_setup(token_var)

    allowed_val_set = None  # Track if user set an allowlist (for home channel offer)

    # Skip knobs the setup forms hide (home channel, reply mode, proxy...): they're self-configuring.
    setup_vars = [
        v for v in platform["vars"]
        if v["name"] == token_var or v.get("is_allowlist") or not _is_setup_hidden_env(v["name"])
    ]

    for var in setup_vars:
        print()
        _gw().print_info(f"  {var['help']}")
        existing = _gw().get_env_value(var["name"])
        if existing and var["name"] != token_var:
            _gw().print_info(f"  当前值：{existing}")

        if auto_token_saved and var["name"] == token_var:
            _gw().print_info("  已由自动配置保存 token。")
            continue

        if var.get("is_allowlist"):
            saved = _prompt_allowlist_var(var, platform.get("key"), auto_owner_user_id)
            if saved is not None:
                allowed_val_set = saved
            continue

        value = _gw().prompt(f"  {var['prompt']}", password=var.get("password", False))
        if value:
            _gw().save_env_value(var["name"], value)
            _gw().print_success(f"  已保存 {var['name']}")
        elif var["name"] == token_var:
            _gw().print_warning(f"  已跳过 —— 没有这个，{label} 用不了。")
            return
        else:
            _gw().print_info("  已跳过（以后可以再配）")

    # Offer the first allowlisted user ID as home channel when none is set (Telegram DMs).
    home_var = f"{label.upper()}_HOME_CHANNEL"
    home_val = _gw().get_env_value(home_var)
    if allowed_val_set and not home_val and label == "Telegram":
        first_id = allowed_val_set.split(",")[0].strip()
        if first_id:
            _offer_home_channel(home_var, first_id, "your user ID")

    print()
    _gw().print_success(f"{emoji} {label} 配置完成！")


# Weixin DM policy by menu index (index 2 = allowlist is prompted separately).
_WEIXIN_DM_POLICIES = {
    0: ("pairing", "false", print_success, "  已启用配对授权。"),
    1: ("open", "true", print_warning, "  Open DM access enabled for Weixin."),
    3: ("disabled", "false", print_warning, "  Direct messages disabled."),
}


_WEIXIN_GROUP_NOTE = (
    "  Note: QR login connects an iLink bot identity (e.g. ...@im.bot), not a",
    "  scriptable personal WeChat account. Ordinary WeChat groups typically cannot",
    "  invite an @im.bot identity, and iLink does not deliver ordinary-group events",
    "  to most bot accounts. The settings below only apply when iLink actually",
    "  delivers group events for your account type — otherwise DM remains the only",
    "  working channel regardless of this choice.",
)


def _setup_weixin():
    """Interactive setup for Weixin / WeChat personal accounts."""
    _print_setup_header("💬 Weixin / WeChat")
    print()
    _gw()._print_info_lines(
        "  1. Coco 会在当前终端里打开腾讯 iLink 扫码登录。",
        "  2. 用微信扫码并确认。",
        "  3. 登录返回的 account_id/token 会存进 ~/.hermes/.env。",
        "  4. 这条通道支持文本、图片、视频和文件。",
    )

    if not _confirm_reconfigure("Weixin", "WEIXIN_ACCOUNT_ID", "WEIXIN_TOKEN"):
        return

    try:
        from gateway.platforms.weixin import check_weixin_requirements, qr_login
    except Exception as exc:
        _gw().print_error(f"  微信通道加载失败：{exc}")
        _gw().print_info("  先装网关依赖再重试。")
        return

    if not check_weixin_requirements():
        _gw().print_error("  缺依赖：微信通道需要 aiohttp 和 cryptography。")
        _gw().print_info("  装好后重新跑「coco gateway setup」。")
        return

    print()
    if not _gw().prompt_yes_no("  现在开始扫码登录吗？", True):
        _gw().print_info("  已取消。")
        return

    try:
        credentials = _gw().asyncio.run(qr_login(str(_gw().get_hermes_home())))
    except KeyboardInterrupt:
        print()
        _gw().print_warning("  微信配置已取消。")
        return
    except Exception as exc:
        _gw().print_error(f"  扫码登录失败：{exc}")
        return

    if not credentials:
        _gw().print_warning("  扫码登录没完成。")
        return

    account_id = credentials.get("account_id", "")
    user_id = credentials.get("user_id", "")
    _gw().save_env_value("WEIXIN_ACCOUNT_ID", account_id)
    _gw().save_env_value("WEIXIN_TOKEN", credentials.get("token", ""))
    if credentials.get("base_url", ""):
        _gw().save_env_value("WEIXIN_BASE_URL", credentials.get("base_url", ""))
    _gw().save_env_value(
        "WEIXIN_CDN_BASE_URL", _gw().get_env_value("WEIXIN_CDN_BASE_URL") or "https://novac2c.cdn.weixin.qq.com/c2c"
    )

    print()
    access_choices = [
        "用私聊配对审批（推荐）", "允许所有私聊", "只允许名单里的用户 ID",
        "不放行私聊",
    ]
    access_idx = _gw().prompt_choice("  私聊怎么授权？", access_choices, 0)
    if access_idx == 2:
        allowlist = _prompt_csv("  微信白名单用户 ID（逗号分隔）", user_id or "")
        _save_env_values(
            WEIXIN_DM_POLICY="allowlist", WEIXIN_ALLOW_ALL_USERS="false", WEIXIN_ALLOWED_USERS=allowlist
        )
        _gw().print_success("  微信白名单已保存。")
    else:
        policy, allow_all, emit, message = _WEIXIN_DM_POLICIES.get(access_idx, _WEIXIN_DM_POLICIES[3])
        _save_env_values(WEIXIN_DM_POLICY=policy, WEIXIN_ALLOW_ALL_USERS=allow_all, WEIXIN_ALLOWED_USERS="")
        emit(message)
        if access_idx == 0:
            _gw().print_info(
                "  陌生私聊用户会来申请，你用「coco pairing approve」批准。"
            )

    print()
    _gw()._print_info_lines(*_WEIXIN_GROUP_NOTE)
    group_choices = [
        "关闭群聊（推荐）", "允许所有群聊", "只允许名单里的群 ID",
    ]
    group_idx = _gw().prompt_choice("  群聊怎么处理？", group_choices, 0)
    if group_idx == 0:
        _save_env_values(WEIXIN_GROUP_POLICY="disabled", WEIXIN_GROUP_ALLOWED_USERS="")
        _gw().print_info("  已关闭群聊。")
    elif group_idx == 1:
        _save_env_values(WEIXIN_GROUP_POLICY="open", WEIXIN_GROUP_ALLOWED_USERS="")
        _gw().print_warning("  已放行所有群（只有 iLink 投递群事件时才生效）。")
    else:
        allow_groups = _prompt_csv("  群白名单 ID（逗号分隔，填群 ID 不是成员 ID）", "")
        _save_env_values(WEIXIN_GROUP_POLICY="allowlist", WEIXIN_GROUP_ALLOWED_USERS=allow_groups)
        _gw().print_success("  群白名单已保存（只有 iLink 投递群事件时才生效）。")

    if user_id:
        print()
        _offer_home_channel("WEIXIN_HOME_CHANNEL", user_id, "your Weixin user ID")

    print()
    _gw().print_success("微信配置完成！")
    _gw().print_info(f"  账号 ID：{account_id}")
    if user_id:
        _gw().print_info(f"  用户 ID：{user_id}")


def _setup_qqbot():
    """Interactive setup for QQ Bot — scan-to-configure or manual credentials."""
    _print_setup_header("🐧 QQ Bot")

    if not _confirm_reconfigure("QQ Bot", "QQ_APP_ID", "QQ_CLIENT_SECRET"):
        return

    print()
    method_choices = ["扫码自动添加机器人（推荐）", "手动填写已有的 App ID 与 App Secret"]
    credentials = None
    if _gw().prompt_choice("  How would you like to set up QQ Bot?", method_choices, 0) == 0:
        try:
            from gateway.platforms.qqbot import qr_register
            credentials = qr_register()
        except KeyboardInterrupt:
            print()
            _gw().print_warning("  QQ 机器人配置已取消。")
            return
        if not credentials:
            _gw().print_info("  扫码没完成，改用手动填写。")

    if not credentials:
        print()
        _gw()._print_info_lines(
            "  去 https://q.qq.com 注册一个 QQ 机器人应用。",
            "  在应用页面记下 App ID 和 App Secret。",
        )
        print()
        app_id = _gw().prompt("  App ID", password=False)
        if not app_id:
            _gw().print_warning("  已跳过 —— 没有 App ID，QQ 机器人用不了。")
            return
        app_secret = _gw().prompt("  App Secret", password=True)
        if not app_secret:
            _gw().print_warning("  已跳过 —— 没有 App Secret，QQ 机器人用不了。")
            return
        credentials = {"app_id": app_id.strip(), "client_secret": app_secret.strip(), "user_openid": ""}

    _gw().save_env_value("QQ_APP_ID", credentials["app_id"])
    _gw().save_env_value("QQ_CLIENT_SECRET", credentials["client_secret"])

    user_openid = credentials.get("user_openid", "")

    print()
    access_choices = ["用私聊配对审批（推荐）", "允许所有私聊", "只允许名单里的用户 OpenID"]
    access_idx = _gw().prompt_choice("  私聊怎么授权？", access_choices, 0)
    if access_idx == 0:
        _gw().save_env_value("QQ_ALLOW_ALL_USERS", "false")
        allowed = ""
        if user_openid:
            print()
            if _gw().prompt_yes_no(f"  把你自己（{user_openid}）加进白名单吗？", True):
                allowed = user_openid
                _gw().print_success(f"  白名单已设为 {user_openid}")
        _gw().save_env_value("QQ_ALLOWED_USERS", allowed)
        _gw().print_success("  已启用配对授权。")
        _gw().print_info("  陌生用户会来申请，你用「coco pairing approve」批准。")
    elif access_idx == 1:
        _save_env_values(QQ_ALLOW_ALL_USERS="true", QQ_ALLOWED_USERS="")
        _gw().print_warning("  已放开 QQ 机器人的私聊访问。")
    else:
        allowlist = _prompt_csv("  白名单用户 OpenID（逗号分隔）", user_openid or "")
        _save_env_values(QQ_ALLOW_ALL_USERS="false", QQ_ALLOWED_USERS=allowlist)
        _gw().print_success("  白名单已保存。")

    print()
    if user_openid:
        _offer_home_channel("QQBOT_HOME_CHANNEL", user_openid, "your QQ user ID")
    else:
        home_channel = _gw().prompt("  主页频道 OpenID（接收定时任务与通知，可留空）", password=False)
        if home_channel:
            _gw().save_env_value("QQBOT_HOME_CHANNEL", home_channel.strip())
            _gw().print_success(f"  主页频道已设为 {home_channel.strip()}")

    print()
    _gw().print_success("🐧 QQ Bot 配置完成！")
    _gw().print_info(f"  App ID：{credentials['app_id']}")


def _signal_line_input(prompt_text: str) -> str | None:
    """``line_input`` for the Signal wizard; None (after printing the cancel line) on EOF/Ctrl+C."""
    try:
        return _gw().line_input(prompt_text).strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  已取消配置。")
        return None


def _setup_signal():
    """Interactive setup for Signal messenger."""
    _print_setup_header("📡 Signal")

    existing_url = _gw().get_env_value("SIGNAL_HTTP_URL")
    existing_account = _gw().get_env_value("SIGNAL_ACCOUNT")
    if not _confirm_reconfigure("Signal", "SIGNAL_HTTP_URL", "SIGNAL_ACCOUNT"):
        return

    print()
    if shutil.which("signal-cli"):
        _gw().print_success("PATH 里找到了 signal-cli。")
    else:
        _gw().print_warning("PATH 里没找到 signal-cli。")
        _gw()._print_info_lines(
            "  Signal 需要一个跑成 HTTP 服务的 signal-cli。", "  安装方式：",
            "    Linux:  download from https://github.com/AsamK/signal-cli/releases",
            "    macOS:  brew install signal-cli", "    Docker: bbernhard/signal-cli-rest-api",
        )
        print()
        _gw()._print_info_lines(
            "  装好后，先链接账号再启动服务：",
            '    signal-cli link -n "Coco"',
            "    signal-cli --account +YOURNUMBER daemon --http 127.0.0.1:8080",
        )
        print()

    print()
    _gw().print_info("  填 signal-cli 的 HTTP 服务地址。")
    default_url = existing_url or "http://127.0.0.1:8080"
    url = _signal_line_input(f"  HTTP 地址 [{default_url}]： ")
    if url is None:
        return
    url = url or default_url

    _gw().print_info("  正在测试连接…")
    try:
        import httpx
        resp = httpx.get(f"{url.rstrip('/')}/api/v1/check", timeout=10.0)
        if resp.status_code == 200:
            _gw().print_success("  signal-cli 服务可以连上。")
        else:
            _gw().print_warning(f"  signal-cli 返回状态码 {resp.status_code}。")
            if not _gw().prompt_yes_no("  还要继续吗？", False):
                return
    except Exception as e:
        _gw().print_warning(f"  连不上 {url} 上的 signal-cli：{e}")
        if not _gw().prompt_yes_no("  还是保存这个地址吗？（你可以稍后再启动 signal-cli）", True):
            return

    _gw().save_env_value("SIGNAL_HTTP_URL", url)

    print()
    _gw()._print_info_lines("  填你的 Signal 账号手机号，用 E.164 格式。", "  例如：+15551234567")
    default_account = existing_account or ""
    account = _signal_line_input(f"  账号号码{f' [{default_account}]' if default_account else ''}： ")
    if account is None:
        return
    account = account or default_account
    if not account:
        _gw().print_error("  账号号码是必填的。")
        return

    _gw().save_env_value("SIGNAL_ACCOUNT", account)

    print()
    _gw()._print_info_lines(
        "  出于安全，网关默认拒绝所有用户。",
        "  填白名单用户的手机号或 UUID（逗号分隔）。",
    )
    default_allowed = _gw().get_env_value("SIGNAL_ALLOWED_USERS") or account
    allowed = _signal_line_input(f"  白名单 [{default_allowed}]： ")
    if allowed is None:
        return
    _gw().save_env_value("SIGNAL_ALLOWED_USERS", allowed or default_allowed)

    print()
    if _gw().prompt_yes_no("  要开启群消息吗？（出于安全默认关闭）", False):
        print()
        _gw().print_info("  填放行的群 ID，* 表示所有群。")
        existing_groups = _gw().get_env_value("SIGNAL_GROUP_ALLOWED_USERS") or ""
        groups = _signal_line_input(f"  群 ID [{existing_groups or '*'}]： ")
        if groups is None:
            return
        _gw().save_env_value("SIGNAL_GROUP_ALLOWED_USERS", groups or existing_groups or "*")

    print()
    _gw().print_success("Signal 配置完成！")
    _gw()._print_info_lines(
        f"  地址：{url}", f"  账号：{account}", "  私聊授权：靠白名单 + 配对",
        f"  群聊：{'已开启' if _gw().get_env_value('SIGNAL_GROUP_ALLOWED_USERS') else '已关闭'}",
    )


def _builtin_setup_fn(key: str):
    """Resolve a built-in platform's setup function; late-bound to dodge the hermes_cli.setup cycle."""
    from hermes_cli import setup as _s
    return {
        # telegram/discord/slack/whatsapp/dingtalk/feishu/wecom setup_fns come from their plugins.
        "bluebubbles": _gw().setup_platforms._setup_bluebubbles,
        "webhooks": _gw().setup_platforms._setup_webhooks,
        "signal": _setup_signal,
        "weixin": _setup_weixin,
        "qqbot": _setup_qqbot,
    }.get(key)


def _configure_platform(platform: dict) -> None:
    """Plugin ``setup_fn`` -> built-in by key -> ``_setup_standard_platform`` (``vars``) -> env-var hint.
    Bundled plugins auto-load; user plugins must already be in ``plugins.enabled``."""
    entry = platform.get("_registry_entry")
    fn = entry.setup_fn if entry is not None else None
    if fn is None:
        fn = _builtin_setup_fn(platform["key"])
    if fn is not None:
        fn()
        return
    if platform.get("vars"):
        _gw()._setup_standard_platform(platform)
        return

    label = platform.get("label", platform["key"])
    _print_setup_header(f"{platform.get('emoji', '🔌')} {label}")
    required = entry.required_env if entry else []
    if required:
        _gw().print_info(f"  需要在这些环境变量里填（~/.hermes/.env）：{', '.join(required)}")
    else:
        _gw().print_info(f"  在 config.yaml 的 gateway.platforms.{platform['key']} 里配置 {label}")
    if platform.get("install_hint"):
        _gw().print_info(f"  {platform['install_hint']}")


def _wizard_offer_service_action(action: str, question: str, failed_label: str, **kwargs) -> None:
    """Wizard start/restart prompt; prints remediation instead when system scope would need root."""
    if _gw().supports_systemd_services() and _gw()._system_scope_wizard_would_need_root():
        _gw()._print_system_scope_remediation(action)
    elif _gw().prompt_yes_no(question, True):
        _gw()._setup_service_action(action, failed_label=failed_label, **kwargs)


def _setup_service_action(
    action: str, *, failed_label: str, windows: bool = True, system: bool = False
) -> None:
    """Run a wizard service start/restart, printing remediation instead of raising. ``windows=False``
    skips Windows (pre-platform status block never offers it); ``system`` is a fresh install's scope."""
    try:
        backend = _gw()._service_backend(windows=windows)
        if backend is not None:
            _gw()._service_call(backend, action, None if action == "restart" else system)
        elif action == "restart" and windows:
            _gw().stop_profile_gateway()
            _gw().print_info("手动启动：coco gateway")
    except _gw().UserSystemdUnavailableError as e:
        _gw().print_error(f"  {failed_label} —— 连不上用户级 systemd：")
        _gw()._print_indented(str(e))
    except _gw().SystemScopeRequiresRootError as e:
        # Defense in depth: the wizard's root pre-check should have caught this.
        _gw().print_error(f"  {failed_label}：{e}")
        _gw()._print_system_scope_remediation(action)
    except subprocess.CalledProcessError as e:
        _gw().print_error(f"  {failed_label}：{e}")


_WIZARD_BANNER = (
    "┌─────────────────────────────────────────────────────────┐",
    _boxed("             ☤ Coco 网关配置"),
    "├─────────────────────────────────────────────────────────┤",
    _boxed("  配置接入通道与网关服务。"),
    _boxed("  任何时候按 Ctrl+C 都可以退出。"),
    "└─────────────────────────────────────────────────────────┘",
)


_WIZARD_BACKEND_LABELS = {"systemd": "systemd", "launchd": "launchd", "windows": "计划任务"}


# Post-setup guidance when no service backend applies, keyed by the fallthrough reason.
_WIZARD_NO_SERVICE_LINES = {
    "wsl": (
        "  检测到 WSL，但 systemd 没在跑。", "  前台运行：coco gateway run",
        "  想让它常驻：tmux new -s coco 'coco gateway run'",
        "  想启用 systemd：在 /etc/wsl.conf 里加 systemd=true，然后执行 wsl --shutdown",
    ),
    "termux": (
        "  Termux 不用 systemd/launchd 服务。", "  前台运行：coco gateway run",
        "  或在后台手动拉起（尽力而为）：nohup coco gateway run >{home}/logs/gateway.log 2>&1 &",
    ),
    "unsupported": (
        "  这个平台不支持安装服务。", "  前台运行：coco gateway run",
    ),
}


def _wizard_service_status_block() -> None:
    """Pre-platform service status: warnings, then offer to start an installed-but-stopped service."""
    print()
    service_installed = _gw()._is_service_installed()
    service_running = _gw()._is_service_running()

    if _gw().supports_systemd_services() and _gw().has_conflicting_systemd_units():
        _gw().print_systemd_scope_conflict_warning()
        print()

    if _gw().supports_systemd_services() and _gw().has_legacy_hermes_units():
        _gw().print_legacy_unit_warning()
        print()

    if service_installed and service_running:
        _gw().print_success("网关服务已安装并在运行。")
    elif service_installed:
        _gw().print_warning("网关服务已安装，但没在运行。")
        _wizard_offer_service_action("start", "  现在启动吗？", "启动失败", windows=False)
    else:
        _gw().print_info("网关服务还没安装。")
        _gw().print_info("配完平台后我会问你要不要安装。")


def _wizard_platform_loop() -> None:
    while True:
        print()
        _gw().print_header("接入通道")

        platforms = _gw()._all_platforms()
        menu_items = [f"{p['emoji']} {p['label']}  ({platform_status_label(_platform_status(p))})"
                      for p in platforms] + ["完成"]
        choice = _gw().prompt_choice("选择要配置的平台：", menu_items, len(menu_items) - 1)
        if choice == len(platforms):
            break
        _gw()._configure_platform(platforms[choice])


def _wizard_install_service(backend: str) -> None:
    """Fresh install from the wizard: ask start-now / start-on-login once, install, then start.

    The Windows installer owns its start decision (Scheduled Task and Startup-folder
    paths start the gateway themselves when start_now is true, and a UAC hand-off
    installs and starts in the elevated child), so the wizard forwards the answers
    and returns without a second start. Each install-intent question is asked exactly
    once per setup run."""
    wsl_note = "（注意：WSL 重启后服务可能不会自动起）" if _gw().is_wsl() else ""
    start_now = _gw().prompt_yes_no("  现在启动网关吗？", True)
    start_on_login = _gw().prompt_yes_no(
        f"  要不要把网关装成 {_WIZARD_BACKEND_LABELS[backend]} 服务，登录/开机自动起？"
        f"{wsl_note}",
        True,
    )
    if not (start_now or start_on_login):
        _gw().print_info("  已跳过启动与开机自启设置。")
        _gw().print_info("  以后想装：coco gateway install")
        if _gw().supports_systemd_services():
            _gw().print_info("  或装成开机服务：sudo coco gateway install --system")
        _gw().print_info("  或前台直接跑：coco gateway run")
        return
    try:
        installed_scope, did_install = None, True
        if backend == "systemd":
            installed_scope, did_install = _gw().install_linux_gateway_from_setup(
                force=False, enable_on_startup=start_on_login
            )
        elif backend == "launchd":
            _gw().launchd_install(force=False, start_now=start_now)
        else:
            _gw()._gw_windows().install(force=False, start_now=start_now, start_on_login=start_on_login)
            return
        print()
        if did_install and start_now:
            _gw()._setup_service_action("start", failed_label="启动失败", system=installed_scope == "system")
    except subprocess.CalledProcessError as e:
        _gw().print_error(f"  安装失败：{e}")
        _gw().print_info("  可以手动重试：coco gateway install")


def _wizard_post_setup() -> None:
    """Offer to install/start/restart the gateway once at least one platform has progress."""
    print()
    print(_gw().color("─" * 58, _gw().Colors.DIM))
    if _gw()._served_profile_needs_no_service():
        return
    service_installed = _gw()._is_service_installed()
    service_running = _gw()._is_service_running()

    if service_running:
        _wizard_offer_service_action("restart", "  重启网关让改动生效吗？", "重启失败")
    elif service_installed:
        _wizard_offer_service_action("start", "  启动网关服务吗？", "启动失败")
    else:
        print()
        backend = _gw()._service_backend()
        if backend is not None:
            _gw()._wizard_install_service(backend)
            return
        if _gw().is_wsl():
            reason, home = "wsl", ""
        elif _gw().is_termux():
            from hermes_constants import display_hermes_home as _dhh
            reason, home = "termux", _dhh()
        else:
            reason, home = "unsupported", ""
        _gw()._print_info_lines(*(line.format(home=home) for line in _WIZARD_NO_SERVICE_LINES[reason]))


def gateway_setup():
    """Interactive setup for messaging platforms + gateway service."""
    if _gw().is_managed():
        _gw().managed_error("run gateway setup")
        return

    print()
    for banner_line in _WIZARD_BANNER:
        print(_gw().color(banner_line, _gw().Colors.MAGENTA))

    _wizard_service_status_block()
    _wizard_platform_loop()

    # Meaningful progress on any platform; ``_platform_status`` already handles plugin dual states.
    def _is_progress(status: str) -> bool:
        s = status.lower()
        return not (s == "not configured" or s.startswith("partially") or s.startswith("plugin disabled"))

    if any(_is_progress(_gw()._platform_status(p)) for p in _gw()._all_platforms()):
        _gw()._wizard_post_setup()
    else:
        print()
        _gw().print_info("还没配任何平台。想配的时候跑「coco gateway setup」。")

    print()
