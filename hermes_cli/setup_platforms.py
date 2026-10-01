"""Messaging-platform setup wizards (Telegram, BlueBubbles, webhooks) and the ``hermes setup
gateway`` flow. setup.py re-exports the public names, and tests monkeypatch prompt/print/env
helpers on hermes_cli.setup, so those are imported lazily per function."""

import contextlib
import logging
import re
from pathlib import Path

logger = logging.getLogger("hermes_cli.setup")

_TELEGRAM_BOT_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")
_RULE = "━" * 50


def _is_valid_telegram_bot_token(token: str) -> bool:
    return bool(_TELEGRAM_BOT_TOKEN_RE.match(token))


def _profile_name_from_hermes_home(hermes_home) -> str | None:
    """Return the active profile name when HERMES_HOME is a profile dir."""
    return hermes_home.name if hermes_home.parent.name == "profiles" else None


def _setup_telegram_auto_result():
    """Attempt automatic Telegram bot creation via managed QR onboarding."""
    from hermes_cli.setup import get_hermes_home
    try:
        from hermes_cli.telegram_managed_bot import auto_setup_telegram_bot_result
    except ImportError:
        return None
    profile_name: str | None = None
    with contextlib.suppress(Exception):
        profile_name = _profile_name_from_hermes_home(Path(get_hermes_home()))
    return auto_setup_telegram_bot_result(profile_name=profile_name)


def declines_reconfigure(label: str, question: str, *env_vars: str) -> bool:
    """True when any of ``env_vars`` is already set and the user does NOT want to reconfigure.

    Shared by the core wizards and every platform plugin's ``interactive_setup`` so the
    "already configured? Reconfigure? [y/N]" gate has one wording and one default.
    """
    from hermes_cli.setup import get_env_value, print_info, prompt_yes_no
    if not any(get_env_value(v) for v in env_vars):
        return False
    print_info(f"{label}：已经配好了")
    return not prompt_yes_no(question, False)


def save_prompted(env_var: str, question: str, *, password: bool = False, success_msg: str | None = None,
                   skip_msg: str | None = None, transform=None) -> str:
    """Prompt, persist the (optionally transformed) answer when non-empty, and report either way.

    ``success_msg`` may reference ``{value}``. Returns the raw answer ("" when skipped).
    """
    from hermes_cli.setup import print_success, print_warning, prompt, save_env_value
    value = prompt(question, password=password)
    if value:
        save_env_value(env_var, transform(value) if transform else value)
        if success_msg:
            print_success(success_msg.format(value=value))
    elif skip_msg:
        print_warning(skip_msg)
    return value


def _save_allowlist(env_var: str, users: str, success_msg: str) -> None:
    """Strip spaces, persist the allowlist, and confirm."""
    from hermes_cli.setup import print_success, save_env_value
    save_env_value(env_var, users.replace(" ", ""))
    print_success(success_msg)


def _prompt_allowlist(env_var: str, question: str, success_msg: str, open_msg: str, preset: str | None = None) -> str:
    """Persist ``preset`` (or the prompted answer) as an allowlist, warning when it stays open."""
    from hermes_cli.setup import print_info, prompt
    users = prompt(question) if preset is None else preset
    if users:
        _save_allowlist(env_var, users, success_msg)
    else:
        print_info(open_msg)
    return users.replace(" ", "")


def _save_port(env_var: str, value: str, default: str) -> None:
    """Persist ``value`` as an int port; warn (keeping ``default``) when it isn't one."""
    from hermes_cli.setup import print_success, print_warning, save_env_value
    if not value:
        return
    try:
        save_env_value(env_var, str(int(value)))
        print_success(f"Webhook 端口已设为 {value}")
    except ValueError:
        print_warning(f"端口不是数字，改用默认值 {default}")


def _prompt_telegram_bot_token() -> str | None:
    from hermes_cli.setup import print_error, print_info, prompt
    print_info("在 Telegram 里找 @BotFather 建一个机器人")
    while True:
        token = prompt("Telegram 机器人 token", password=True)
        if not token or _is_valid_telegram_bot_token(token):
            return token or None
        print_error("token 格式不对，应该是「数字ID:字母数字串」"
                    "（例如 123456789:ABCdefGHI-jklMNOpqrSTUvwxYZ）")


def _telegram_allowlist_nudge() -> None:
    """Existing config kept as-is: warn when it has no user allowlist."""
    from hermes_cli.setup import get_env_value, print_info, prompt, prompt_yes_no
    if get_env_value("TELEGRAM_ALLOWED_USERS"):
        return
    print_info("⚠️ Telegram 没设白名单 —— 谁都能用你的机器人。")
    if prompt_yes_no("现在就加白名单用户吗？", True):
        print_info("   查自己的 Telegram 用户 ID：给 @userinfobot 发条消息")
        allowed_users = prompt("白名单用户 ID（逗号分隔）")
        if allowed_users:
            _save_allowlist("TELEGRAM_ALLOWED_USERS", allowed_users, "白名单已保存（只有名单里的用户能用机器人）")


def _obtain_telegram_token():
    """Return (token, setup_result); auto flow first when chosen, else manual paste."""
    from hermes_cli.setup import _info, print_error, prompt
    _info("你想怎么建这个 Telegram 机器人？", None,
          "  [1] 自动（推荐）",
          "      扫码 → 在 Telegram 里确认 → 完成。",
          "      不用手动复制 token。", None,
          "  [2] 手动",
          "      自己去 @BotFather 建，再把 token 粘进来。", None)
    token = setup_result = None
    if prompt("请选择 [1/2]", default="1").strip() == "1":
        setup_result = _setup_telegram_auto_result()
        if setup_result:
            token = setup_result.token
            if not _is_valid_telegram_bot_token(token):
                print_error("自动配置返回的 Telegram token 不合法。")
                token = setup_result = None
        if not token:
            _info(None, "改走手动配置…", None)
    if not token:
        token = _prompt_telegram_bot_token()
    return token, setup_result


def _setup_telegram():
    """Configure Telegram bot credentials and allowlist."""
    from hermes_cli.setup import _info, print_info, print_header, print_success, prompt, prompt_yes_no, save_env_value
    print_header("Telegram")
    if declines_reconfigure("Telegram", "要重新配置 Telegram 吗？", "TELEGRAM_BOT_TOKEN"):
        _telegram_allowlist_nudge()
        return
    token, setup_result = _obtain_telegram_token()
    if not token:
        return
    save_env_value("TELEGRAM_BOT_TOKEN", token)
    print_success("Telegram token 已保存")
    _info(None, "🔒 安全：限制谁能用你的机器人",
          "   查自己的 Telegram 用户 ID：",
          "   1. 在 Telegram 里给 @userinfobot 发条消息",
          "   2. 它会回你的数字 ID（例如 123456789）", None)
    allowed_users = None
    detected_id = str(getattr(setup_result, "owner_user_id", None) or "")
    if detected_id:
        print_success(f"识别到你的 Telegram 用户 ID：{detected_id}")
        if prompt_yes_no("允许这个 Telegram 账号使用机器人吗？", True):
            extra = prompt("还要加哪些用户 ID（逗号分隔，可留空）")
            allowed_users = ",".join(dict.fromkeys([detected_id, *filter(None, extra.replace(" ", "").split(","))]))
    allowed_users = _prompt_allowlist(
        "TELEGRAM_ALLOWED_USERS", "白名单用户 ID（逗号分隔；留空 = 谁都能用）",
        "白名单已保存 —— 只有名单里的用户能用机器人",
        "⚠️ 没设白名单 —— 谁找到你的机器人都能用。", preset=allowed_users)
    _info(None, "📬 主页频道：Coco 把定时任务结果、跨平台消息和通知送到这里。",
          "   Telegram 私聊就填你的用户 ID（和上面同一个）。")
    first_user_id = allowed_users.split(",")[0].strip() if allowed_users else ""
    if not first_user_id:
        print_info("   以后也可以在 Telegram 聊天里发 /set-home 设置。")
        save_prompted("TELEGRAM_HOME_CHANNEL", "主页频道 ID（留空表示以后再设）")
    elif prompt_yes_no(f"把你的用户 ID（{first_user_id}）设为主页频道吗？", True):
        save_env_value("TELEGRAM_HOME_CHANNEL", first_user_id)
        print_success(f"Telegram 主页频道已设为 {first_user_id}")
    else:
        save_prompted("TELEGRAM_HOME_CHANNEL", "主页频道 ID（或留空，以后在 Telegram 里用 /set-home 设置）")


# _setup_slack and _write_slack_manifest_and_instruct moved to the slack plugin:
# plugins/platforms/slack/adapter.py::interactive_setup (registered via setup_fn and dispatched through the
# plugin path). #41112 / #3823.
def _setup_bluebubbles():
    """Configure BlueBubbles iMessage gateway."""
    from hermes_cli.setup import _info, print_header, print_success, prompt, prompt_yes_no
    print_header("BlueBubbles (iMessage)")
    if declines_reconfigure("BlueBubbles", "要重新配置 BlueBubbles 吗？", "BLUEBUBBLES_SERVER_URL"):
        return
    _info("把 Coco 接到 iMessage：靠 BlueBubbles —— 一个免费开源的 macOS 服务端，",
          "把 iMessage 转给任何设备。",
          "   需要一台 Mac 跑 BlueBubbles Server v1.0.0+",
          "   下载：https://bluebubbles.app/", None,
          "在 BlueBubbles Server → Settings → API 里记下 Server URL 和 Password。", None)
    for label, env_var, secret, what, transform in (
        ("BlueBubbles 服务端地址（如 http://192.168.1.10:1234）", "BLUEBUBBLES_SERVER_URL", False, "服务端地址",
         lambda v: v.rstrip("/")),
        ("BlueBubbles 服务端密码", "BLUEBUBBLES_PASSWORD", True, "密码", None),
    ):
        if not save_prompted(env_var, label, password=secret, transform=transform,
                              skip_msg=f"{what} 是必填的 —— 跳过 BlueBubbles 配置"):
            return
    print_success("BlueBubbles 凭据已保存")
    _info(None, "🔒 安全：限制谁能给我的机器人发消息",
          "   用 iMessage 地址：邮箱（user@icloud.com）或手机号（+155****4567 这种带区号的格式）", None)
    _prompt_allowlist("BLUEBUBBLES_ALLOWED_USERS", "iMessage 白名单（逗号分隔；留空 = 谁都能用）",
                      "BlueBubbles 白名单已保存", "⚠️ 没设白名单 —— 能给你发 iMessage 的人都能用这个机器人。")
    _info(None, "📬 主页频道：填手机号或邮箱，用来接收定时任务结果和通知。",
          "   以后也可以在 iMessage 聊天里用 /set-home 设置。")
    save_prompted("BLUEBUBBLES_HOME_CHANNEL", "主页频道地址（留空表示以后再设）")
    _info(None, "高级设置（多数情况用默认值就行）：")
    if prompt_yes_no("要配置 Webhook 监听端口吗？", False):
        _save_port("BLUEBUBBLES_WEBHOOK_PORT", prompt("Webhook 监听端口（默认 8645）"), "8645")
    _info(None, "想要「正在输入」、已读回执和表情回应，需要装 BlueBubbles Private API 辅助程序；",
          "不装也能正常收发消息。",
          "   安装：https://docs.bluebubbles.app/helper-bundle/installation")


def _setup_webhooks():
    """Configure webhook integration."""
    from hermes_cli.setup import _info, print_header, print_success, print_warning, prompt, save_env_value
    print_header("Webhooks")
    if declines_reconfigure("Webhooks", "要重新配置 Webhook 吗？", "WEBHOOK_ENABLED"):
        return
    print()
    print_warning("⚠  Webhook 与短信类平台需要把网关端口暴露到公网。")
    print_warning("   为了安全，建议把网关跑在沙箱环境（Docker、虚拟机等）里，")
    print_warning("   万一被提示词注入攻击，影响范围也小得多。")
    print()
    _info("   完整文档：https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks/", None)
    _save_port("WEBHOOK_PORT", prompt("Webhook 端口（默认 8644）"), "8644")
    save_prompted("WEBHOOK_SECRET", "全局 HMAC 密钥（所有路由共用）", password=True,
                   success_msg="Webhook 密钥已保存",
                   skip_msg="没设密钥 —— 你需要在 config.yaml 里给每条路由单独配密钥")
    save_env_value("WEBHOOK_ENABLED", "true")
    print()
    print_success("Webhook 已启用，接下来：")
    from hermes_constants import display_hermes_home as _dhh
    _info(f"   1. 在 {_dhh()}/config.yaml 里定义 webhook 路由",
          "   2. 把你的服务（GitHub、GitLab 等）指向：",
          "      http://your-server:8644/webhooks/<route-name>", None,
          "   路由配置文档：",
          "   https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks/#configuring-routes",
          None,
          # Printed twice upstream; kept verbatim for output parity.
          "   用编辑器打开配置：coco config edit",
          "   用编辑器打开配置：coco config edit")


# (platform label, credential env var, home-channel env vars — any one satisfies)
_HOME_CHANNEL_CHECKS = (
    ("Telegram", "TELEGRAM_BOT_TOKEN", ("TELEGRAM_HOME_CHANNEL",)), ("Discord", "DISCORD_BOT_TOKEN", ("DISCORD_HOME_CHANNEL",)),
    ("Slack", "SLACK_BOT_TOKEN", ("SLACK_HOME_CHANNEL",)), ("BlueBubbles", "BLUEBUBBLES_SERVER_URL", ("BLUEBUBBLES_HOME_CHANNEL",)),
    ("QQBot", "QQ_APP_ID", ("QQBOT_HOME_CHANNEL", "QQ_HOME_CHANNEL")),
)


def _is_progress(status: str) -> bool:
    """A platform counts as configured unless its status says otherwise."""
    s = status.lower()
    return not (s == "not configured" or s.startswith(("partially", "plugin disabled")))


def _warn_missing_home_channels() -> None:
    """Platforms with a token but no home channel."""
    from hermes_cli.setup import get_env_value, _info, print_warning
    missing_home = [
        plat for plat, token_var, home_vars in _HOME_CHANNEL_CHECKS
        if get_env_value(token_var) and not any(get_env_value(v) for v in home_vars)]
    if not missing_home:
        return
    print()
    print_warning(f"这些平台还没设主页频道：{', '.join(missing_home)}")
    _info("   没设主页频道，定时任务和跨平台消息就送不到这些平台。",
          "   以后可以在聊天里发 /set-home 设置，或者：",
          *(f"     coco config set {plat.upper()}_HOME_CHANNEL <频道ID>" for plat in missing_home))


def _restart_running_gateway(any_messaging: bool, supports_systemd: bool) -> None:
    """Already running: offer a restart only when this pass may have changed platform config —
    a restart interrupts any active session, so it stays behind a prompt."""
    from hermes_cli.setup import print_error, prompt_yes_no
    from hermes_cli.gateway import (
        systemd_restart, launchd_restart, UserSystemdUnavailableError, SystemScopeRequiresRootError,
        _system_scope_wizard_would_need_root, _print_system_scope_remediation,
    )
    import platform as _platform
    if supports_systemd and _system_scope_wizard_would_need_root():
        _print_system_scope_remediation("restart")
        return
    if not (any_messaging and prompt_yes_no("  重启网关让改动生效吗？", True)):
        return
    try:
        if supports_systemd:
            systemd_restart()
        elif _platform.system() == "Darwin":
            launchd_restart()
        elif _platform.system() == "Windows":
            from hermes_cli import gateway_windows
            gateway_windows.restart()
    except UserSystemdUnavailableError as e:
        print_error("  重启失败 —— 连不上用户级 systemd：")
        for line in str(e).splitlines():
            print(f"  {line}")
    except SystemScopeRequiresRootError as e:
        # Defense in depth: a race (unit file appearing mid-run) can slip past the pre-check;
        # this used to sys.exit(1) the whole wizard.
        print_error(f"  重启失败：{e}")
        _print_system_scope_remediation("restart")
    except Exception as e:
        print_error(f"  重启失败：{e}")


def setup_gateway(config: dict):
    """Configure messaging platform integrations."""
    from hermes_cli.setup import _info, print_header, print_info, print_success, prompt_checklist
    from hermes_cli.gateway import _all_platforms, _platform_status, _configure_platform
    print_header("接入通道")
    _info("接上你的聊天工具，随时随地跟 Coco 对话。",
          "空格键勾选，回车确认。", None)
    platforms = _all_platforms()

    # Build checklist, pre-selecting already-configured platforms.
    statuses = [_platform_status(plat) for plat in platforms]
    items = [f"{plat['emoji']} {plat['label']}  ({status})" for plat, status in zip(platforms, statuses)]
    pre_selected = [i for i, status in enumerate(statuses) if status == "configured"]
    selected = prompt_checklist("选择要配置的平台：", items, pre_selected)
    if not selected:
        print_info("没有选择平台。以后想配，跑「coco setup gateway」。")
    for idx in selected or ():
        _configure_platform(platforms[idx])

    # Any platform (built-in or plugin) configured in this pass — via ``_platform_status`` so
    # plugin platforms like IRC are counted without another hard-coded env-var list.
    any_messaging = any(_is_progress(_platform_status(p)) for p in _all_platforms())
    if any_messaging:
        print()
        print_info(_RULE)
        print_success("接入通道已配置好。")
        _warn_missing_home_channels()

    # Gateway service setup runs UNCONDITIONALLY — a gateway with zero platforms is a supported
    # mode (cron keeps running; adapters come up once tokens are added via `hermes import` /
    # `hermes setup gateway`). Gating it on messaging config left install-then-import machines
    # with cron jobs and bot tokens but no process to serve them.
    from hermes_cli.gateway import _is_service_running, supports_systemd_services, ensure_gateway_service
    supports_systemd = supports_systemd_services()
    print()
    if _is_service_running():
        _restart_running_gateway(any_messaging, supports_systemd)
    else:
        # Not running: install (if needed) and start, no questions asked.
        ensure_gateway_service(context="setup")
    print_info(_RULE)
