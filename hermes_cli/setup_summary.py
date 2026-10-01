"""Setup-completion summary (tool availability + "Setup Complete!" banner). setup.py names are
resolved through the module object so test patches on ``hermes_cli.setup.<name>`` take effect."""

import logging
from tools import tool_backend_helpers
from hermes_cli import nous_subscription

logger = logging.getLogger("hermes_cli.setup")

# provider -> (label, env vars: any one set means available; empty = always).
# Local engines are (label, module, hint) and must be importable.
_TTS_SUMMARY_ROWS = {
    "elevenlabs": ("ElevenLabs", ("ELEVENLABS_API_KEY",)),
    "openai": ("OpenAI", ("VOICE_TOOLS_OPENAI_KEY", "OPENAI_API_KEY")),
    "minimax": ("MiniMax", ("MINIMAX_API_KEY",)), "mistral": ("Mistral Voxtral", ("MISTRAL_API_KEY",)),
    "gemini": ("Google Gemini", ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
    "neutts": ("NeuTTS", "neutts", "跑「coco setup tts」"),
    "kittentts": ("KittenTTS", "kittentts", "跑「coco setup tts」")}
_TTS_SUMMARY_DEFAULT = ("Edge TTS", ())
_STT_SUMMARY_ROWS = {
    "openai": ("OpenAI", ("VOICE_TOOLS_OPENAI_KEY", "OPENAI_API_KEY")), "groq": ("Groq Whisper", ("GROQ_API_KEY",)),
    "elevenlabs": ("ElevenLabs Scribe", ("ELEVENLABS_API_KEY",)), "xai": ("xAI", ()),
    "deepinfra": ("DeepInfra", ("DEEPINFRA_API_KEY",))}
_STT_SUMMARY_DEFAULT = ("Local Whisper", "faster_whisper", "跑「coco tools」→ 语音转文字")

# Browser "missing" hint keyed by the configured provider; anything else gets the generic hint.
_BROWSER_MISSING_HINTS = {
    "Browserbase": "npm install -g agent-browser and set BROWSERBASE_API_KEY/BROWSERBASE_PROJECT_ID",
    "Browser Use": "npm install -g agent-browser and set BROWSER_USE_API_KEY",
    "Camofox": "CAMOFOX_URL",
    "Local browser": "npm install -g agent-browser && agent-browser install --with-deps"}
_BROWSER_MISSING_DEFAULT = "npm install -g agent-browser, set CAMOFOX_URL, or configure Browser Use or Browserbase"
_WEB_MISSING = ("缺 EXA_API_KEY, PARALLEL_API_KEY, FIRECRAWL_API_KEY/FIRECRAWL_API_URL, TAVILY_API_KEY, "
                "PERPLEXITY_API_KEY, KEENABLE_API_KEY, or SEARXNG_URL")

_DONE_BANNER = (
    "┌─────────────────────────────────────────────────────────┐",
    "│" + " " * 22 + "✓ 配置完成！" + " " * 23 + "│",
    "└─────────────────────────────────────────────────────────┘")
# (command, description) rows; the description carries its own alignment padding.
_EDIT_WIZARD_ROWS = (
    ("coco setup", "            重跑完整向导"), ("coco setup model", "      换模型·服务商"),
    ("coco setup terminal", "   换终端后端"), ("coco setup gateway", "    配置接入通道"),
    ("coco setup tools", "      配置工具服务商"))
_EDIT_CONFIG_ROWS = (
    ("coco config", "           查看当前配置"), ("coco config edit", "      用编辑器打开配置"),
    ("coco config set <key> <value>", ""))
_READY_ROWS = (
    ("coco status", "           服务状态"), ("coco logs", "             跟踪服务日志"),
    ("coco check", "            部署体检"))


def _voice_provider_status(kind: str, provider: str, rows: dict, default: tuple) -> tuple:
    """Summary row for a TTS/STT provider. A keyed provider whose key is missing
    falls through to the default row, matching the runtime fallback."""
    row = rows.get(provider, default)
    if isinstance(row[1], tuple) and row[1] and not any(_setup.get_env_value(v) for v in row[1]):
        row = default
    if isinstance(row[1], tuple):
        return (f"{kind}（{row[0]}）", True, None)
    label, module, hint = row
    if _setup._module_installed(module):
        return (f"{kind}（{label}{'（本机）' if kind == '语音合成' else ''}）", True, None)
    return (f"{kind}（{label} —— 未安装）", False, hint)


def _first_available_plugin_provider(registry: str, skip: str = None):
    """display_name of the first plugin-registered provider in ``agent.<registry>`` that reports
    available (fail-soft: any error means none), skipping ``skip``."""
    try:
        import importlib
        from hermes_cli.plugins import _ensure_plugins_discovered
        _ensure_plugins_discovered()
        for provider in importlib.import_module(f"agent.{registry}").list_providers():
            if provider.name == skip:
                continue
            try:
                if provider.is_available():
                    return provider.display_name
            except Exception:
                continue
    except Exception:
        pass
    return None


# ---- tool_status row builders: each takes (config, subscription_features) and returns
# a (name, available, hint) row or None (row omitted). Evaluated in _TOOL_ROW_BUILDERS order.

def _vision_row(config, feats):
    # Use the same runtime resolver as the actual vision tools.
    try:
        from agent.auxiliary_client import get_available_vision_backends
        ok = bool(get_available_vision_backends())
    except Exception:
        ok = False
    return ("图像理解", ok, None if ok else "跑「coco setup」配置")


def _managed_or_provider_row(feature, name: str, managed_label: str, missing_hint: str):
    """Row for a Nous-manageable feature: managed > available (with provider) > missing hint."""
    if feature.managed_by_nous:
        return (f"{name} ({managed_label})", True, None)
    if feature.available:
        return (f"{name} ({feature.current_provider})" if feature.current_provider else name, True, None)
    return (name, False, missing_hint)


def _web_row(config, feats):
    # Web tools (Exa, Parallel, Firecrawl, Tavily, or Keenable)
    return _managed_or_provider_row(feats.web, "联网搜索与抓取", "Nous 订阅", _WEB_MISSING)


def _browser_row(config, feats):
    # Browser tools (local Chromium, Camofox, Browserbase, Browser Use, or Firecrawl)
    hint = _BROWSER_MISSING_HINTS.get(feats.browser.current_provider, _BROWSER_MISSING_DEFAULT)
    return _managed_or_provider_row(feats.browser, "浏览器自动化", "Nous Browser Use", hint)


def _image_gen_row(config, feats):
    # FAL (direct or via Nous), or any plugin-registered provider (OpenAI, etc.)
    if feats.image_gen.managed_by_nous:
        return ("图片生成（Nous 订阅）", True, None)
    if feats.image_gen.available:
        return ("图片生成", True, None)
    # Probe plugin-registered providers so OpenAI-only setups don't show as "missing FAL_KEY".
    backend = _first_available_plugin_provider("image_gen_registry", skip="fal")
    if backend:
        return (f"图片生成（{backend}）", True, None)
    return ("图片生成", False, "缺 FAL_KEY or OPENAI_API_KEY")


def _video_gen_row(config, feats):
    # Opt-in via `hermes tools` → Video Generation. Only show the row when a plugin reports
    # available so we don't badger users who don't care about video gen with a "missing" line.
    if feats.video_gen.managed_by_nous:
        return ("视频生成（FAL · Nous 订阅）", True, None)
    backend = _first_available_plugin_provider("video_gen_registry")
    return (f"视频生成（{backend}）", True, None) if backend else None


def _tts_row(config, feats):
    # Configured provider, gated on its key (or local install)
    if feats.tts.managed_by_nous:
        return ("语音合成（OpenAI · Nous 订阅）", True, None)
    provider = _setup.cfg_get(config, "tts", "provider", default="edge")
    return _voice_provider_status("语音合成", provider, _TTS_SUMMARY_ROWS, _TTS_SUMMARY_DEFAULT)


def _stt_row(config, feats):
    stt_feature = feats.features.get("stt")
    if stt_feature is not None and stt_feature.managed_by_nous:
        return ("语音转文字（OpenAI · Nous 订阅）", True, None)
    provider = _setup.cfg_get(config, "stt", "provider", default="local") or "local"
    return _voice_provider_status("语音转文字", provider, _STT_SUMMARY_ROWS, _STT_SUMMARY_DEFAULT)


def _modal_row(config, feats):
    if feats.modal.managed_by_nous:
        return ("Modal 执行（Nous 订阅）", True, None)
    if _setup.cfg_get(config, "terminal", "backend") == "modal":
        if feats.modal.direct_override:
            return ("Modal 执行（直连 Modal）", True, None)
        return ("Modal 执行", False, "跑「coco setup terminal」")
    if tool_backend_helpers.managed_nous_tools_enabled() and feats.nous_auth_present:
        return ("Modal 执行（可选 · Nous 订阅）", True, None)
    return None


def _home_assistant_row(config, feats):
    return ("智能家居（Home Assistant）", True, None) if _setup.get_env_value("HASS_TOKEN") else None


def _spotify_row(config, feats):
    # OAuth via hermes auth spotify — check auth.json, not env vars
    try:
        from hermes_cli.auth import get_provider_auth_state
        state = get_provider_auth_state("spotify") or {}
        if state.get("access_token") or state.get("refresh_token"):
            return ("Spotify（PKCE 登录）", True, None)
    except Exception:
        pass
    return None


def _skills_hub_row(config, feats):
    ok = bool(_setup.get_env_value("GITHUB_TOKEN"))
    return ("技能仓库（GitHub）", ok, None if ok else "缺 GITHUB_TOKEN")


def _always_on_rows(config, feats):
    # Terminal (system deps met), task planning (in-memory), skills (bundled + user-created).
    return [("终端/命令", True, None), ("任务规划（待办）", True, None),
            ("技能（查看·新建·编辑）", True, None)]


_TOOL_ROW_BUILDERS = (
    _vision_row, _web_row, _browser_row, _image_gen_row, _video_gen_row, _tts_row, _stt_row,
    _modal_row, _home_assistant_row, _spotify_row, _skills_hub_row, _always_on_rows)


def _print_cmd_rows(rows):
    """Print (command, description) rows as '   <green cmd><desc>'."""
    for cmd, desc in rows:
        print(f"   {_setup.color(cmd, _setup.Colors.GREEN)}{desc}")


def _print_section_header(title):
    print(_setup.color("─" * 60, _setup.Colors.DIM), end="\n\n")
    print(_setup.color(title, _setup.Colors.CYAN, _setup.Colors.BOLD), end="\n\n")


def _print_setup_summary(config: dict, hermes_home):
    """Print the setup completion summary."""
    from hermes_constants import display_hermes_home as _dhh
    # Provider readiness — the one thing setup must produce. A user who cancelled the API-key
    # prompt mid-wizard used to exit "successfully" with NO working model; say so loudly.
    try:
        from hermes_cli.auth import resolve_provider
        resolve_provider()
    except Exception:
        print()
        _setup.print_warning("还没配模型服务商 —— Coco 现在还不能对话。")
        _setup._info("  补上这一步，二选一：",
              "    coco model              （任选一个服务商/模型）",
              "    coco setup --portal     （Nous Portal 登录，不用 API Key）")

    print()
    _setup.print_header("工具可用情况")

    tool_status = []
    subscription_features = nous_subscription.get_nous_subscription_features(config)
    for build in _TOOL_ROW_BUILDERS:
        row = build(config, subscription_features)
        tool_status.extend(row if isinstance(row, list) else [] if row is None else [row])

    available_count = sum(1 for _, avail, _ in tool_status if avail)
    _setup._info(f"{available_count}/{len(tool_status)} 类工具可用：", None)
    for name, available, missing_var in tool_status:
        print(f"   {_setup.color('✓', _setup.Colors.GREEN)} {name}" if available else
              f"   {_setup.color('✗', _setup.Colors.RED)} {name} "
              f"{_setup.color(f'（{missing_var}）', _setup.Colors.DIM)}")
    print()

    if available_count < len(tool_status):
        _setup.print_warning("有些工具还没启用。跑「coco setup tools」可以配，")
        _setup.print_warning(f"或者直接编辑 {_dhh()}/.env 补上缺的密钥。")
        print()

    print()
    for line in _DONE_BANNER:
        print(_setup.color(line, _setup.Colors.GREEN))
    print()
    print(_setup.color(f"📁 你的文件都在 {_dhh()}/：", _setup.Colors.CYAN, _setup.Colors.BOLD), end="\n\n")
    for label, value in (("设置：", f"  {_setup.get_config_path()}"), ("密钥：", f"  {_setup.get_env_path()}"),
                         ("数据：", f"      {hermes_home}/cron/, sessions/, logs/")):
        print(f"   {_setup.color(label, _setup.Colors.YELLOW)}{value}")
    print()

    _print_section_header("📝 改配置：")
    _print_cmd_rows(_EDIT_WIZARD_ROWS)
    print()
    _print_cmd_rows(_EDIT_CONFIG_ROWS)
    print("                          设置某一项的值\n\n   或者直接改文件：")
    for path in (_setup.get_config_path(), _setup.get_env_path()):
        print(f"   {_setup.color(f'nano {path}', _setup.Colors.DIM)}")
    print()

    _print_section_header("🚀 可以开始用了")
    _print_cmd_rows(_READY_ROWS)
    print()


import hermes_cli.setup as _setup  # noqa: E402  (bottom: hermes_cli.setup imports this module)
