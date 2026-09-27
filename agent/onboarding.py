"""Contextual first-touch onboarding hints.

Each hint is shown once per install the *first* time a user hits a behavior
fork, tracked in ``config.yaml`` under ``onboarding.seen.<flag>``. Kept tiny and
dependency-free so both the CLI and gateway can import it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


# Flag names (stable — used as config.yaml keys under onboarding.seen)
BUSY_INPUT_FLAG = "busy_input_prompt"
TOOL_PROGRESS_FLAG = "tool_progress_prompt"
OPENCLAW_RESIDUE_FLAG = "openclaw_residue_cleanup"
PROFILE_BUILD_FLAG = "profile_build_offered"


# Busy-input hints are keyed by the effective busy_input_mode that was just
# applied so the message matches reality; "interrupt" is the default branch.
# Coco: 官方这几句是英文，会直接发到经纪人的飞书会话里，改成中文短句（仅网关版；CLI 版经纪人用不到，保持官方英文）。
_BUSY_INPUT_HINTS_GATEWAY = {
    "queue": (
        "💡 提示：你这条已排队，等当前任务做完就处理。想让新消息立刻改用当前任务，发 `/busy interrupt`；"
        "看当前模式发 `/busy status`。仅提示一次。"
    ),
    "steer": (
        "💡 提示：你这条会在下一步动作时带上。想改成打断或排队，发 `/busy interrupt` 或 `/busy queue`；"
        "看当前模式发 `/busy status`。仅提示一次。"
    ),
    "redirect": (
        "💡 提示：新消息会改用当前任务继续做，前面做的不会丢。想让它先做完手头的再处理，发 `/busy queue`。"
        "仅提示一次。"
    ),
}
_BUSY_INPUT_HINT_GATEWAY_DEFAULT = (
    "💡 提示：我停下了手头的任务先回你。想让新消息排队等做完，发 `/busy queue`；想中途插话不改道，"
    "发 `/busy steer`。仅提示一次。"
)

_BUSY_INPUT_HINTS_CLI = {
    "queue": (
        "(tip) Your message was queued for the next turn. Use /busy interrupt to make Enter stop the current "
        "run instead, or /busy steer to inject mid-run. This tip only shows once."
    ),
    "steer": (
        "(tip) Your message was steered into the current run; it arrives after the next tool call. Use /busy "
        "interrupt or /busy queue to change this. This tip only shows once."
    ),
    "redirect": (
        "(tip) Your correction redirected the current run without discarding completed work. Use /stop to "
        "cancel or /busy queue to wait for a separate turn. This tip only shows once."
    ),
}
_BUSY_INPUT_HINT_CLI_DEFAULT = (
    "(tip) Your message interrupted the current run. Use /busy queue to queue messages for the next turn "
    "instead, or /busy steer to inject mid-run. This tip only shows once."
)


def busy_input_hint_gateway(mode: str) -> str:
    """Hint shown the first time a user messages while the agent is busy (markdown)."""
    return _BUSY_INPUT_HINTS_GATEWAY.get(mode, _BUSY_INPUT_HINT_GATEWAY_DEFAULT)


def busy_input_hint_cli(mode: str) -> str:
    """CLI version of the busy-input hint (plain text, no markdown)."""
    return _BUSY_INPUT_HINTS_CLI.get(mode, _BUSY_INPUT_HINT_CLI_DEFAULT)


def tool_progress_hint_gateway() -> str:
    # Coco: 官方这句是英文，会发到经纪人飞书会话里，改中文
    return ("💡 提示：刚才那个步骤跑得比较久，我把过程都发出来了。嫌吵可以发 `/verbose` 切换显示方式"
            "（全部 → 仅新步骤 → 关闭）。仅提示一次。")


def tool_progress_hint_cli() -> str:
    return ("(tip) That tool ran for a while. Use /verbose to cycle tool-progress "
            "display modes (all -> new -> off -> verbose). This tip only shows once.")


def openclaw_residue_hint_cli() -> str:
    """Banner shown the first time Hermes finds ``~/.openclaw/``: migrate first, cleanup (which breaks OpenClaw) after."""
    return (
        "A legacy OpenClaw directory was detected at ~/.openclaw/.\n"
        "To port your config, memory, and skills over to Hermes, run `hermes claw migrate`.\n"
        "If you've already migrated and want to archive the old directory, run `hermes claw cleanup` "
        "(renames it to ~/.openclaw.pre-migration — OpenClaw will stop working after this).\n"
        "This tip only shows once."
    )


def detect_openclaw_residue(home: Optional[Path] = None) -> bool:
    """True if ``$HOME/.openclaw`` is a directory (``home`` override for tests)."""
    try:
        return ((home or Path.home()) / ".openclaw").is_dir()
    except OSError:
        return False


def _onboarding_section(config: Mapping[str, Any]) -> Mapping[str, Any]:
    onboarding = config.get("onboarding") if isinstance(config, Mapping) else None
    return onboarding if isinstance(onboarding, Mapping) else {}


def profile_build_mode(config: Mapping[str, Any]) -> str:
    """``config.onboarding.profile_build``: ``"off"`` never offers; anything else -> ``"ask"``.

    Only governs whether the offer is made; lookups inside the flow are
    consented to separately in conversation.
    """
    mode = _onboarding_section(config).get("profile_build")
    return "off" if isinstance(mode, str) and mode.strip().lower() == "off" else "ask"


def profile_build_directive() -> str:
    """System-note directive appended to the very first message ever.

    Short opt-in profile-build flow persisting to the user-profile memory store;
    phrased so the agent ASKS before any lookup and never silently reads
    connected accounts.
    """
    return (
        "\n\n"
        "[System note: This is the user's very first message ever. After a one-sentence introduction (mention /help "
        "shows commands), OFFER — do not assume — to build a short profile of them so you can be more useful, and "
        "explain they can decline or do it later. If and ONLY IF they accept:\n"
        "  1. Ask for whatever they're comfortable sharing (name, what they do, how they like you to work). "
        "Volunteered facts come first.\n"
        "  2. Before ANY external lookup, say what you intend to look up and get explicit consent for that step. Never "
        "read their connected accounts (email, calendar, etc.) silently — ask each time.\n"
        "  3. With consent, you may use web_search to confirm public details (e.g. employer, public profiles) from the "
        "data points they gave.\n"
        "  4. Save each confirmed, durable fact with the memory tool using target=\"user\" — keep entries compact and "
        "high-signal.\n"
        "If they decline at any point, stop immediately and continue normally. Keep the whole exchange light and "
        "conversational, not an interrogation.]"
    )


def is_seen(config: Mapping[str, Any], flag: str) -> bool:
    """True if the user has already been shown this first-touch hint."""
    seen = _onboarding_section(config).get("seen")
    return bool(seen.get(flag)) if isinstance(seen, Mapping) else False


def mark_seen(config_path: Path, flag: str) -> bool:
    """Persist ``onboarding.seen.<flag> = True`` atomically; False on any error (best-effort)."""
    try:
        from hermes_cli.config import atomic_config_write, read_user_config_raw
    except Exception as e:  # pragma: no cover — dependency issue
        logger.debug("onboarding: failed to import config helpers: %s", e)
        return False
    try:
        cfg: dict = read_user_config_raw(config_path)
        if not isinstance(cfg.get("onboarding"), dict):
            cfg["onboarding"] = {}
        seen = cfg["onboarding"].get("seen")
        if not isinstance(seen, dict):
            seen = cfg["onboarding"]["seen"] = {}
        if seen.get(flag) is not True:
            seen[flag] = True
            atomic_config_write(config_path, cfg)
        return True
    except Exception as e:
        logger.debug("onboarding: failed to mark flag %s: %s", flag, e)
        return False


__all__ = [
    "BUSY_INPUT_FLAG", "TOOL_PROGRESS_FLAG", "OPENCLAW_RESIDUE_FLAG", "PROFILE_BUILD_FLAG",
    "busy_input_hint_gateway", "busy_input_hint_cli", "tool_progress_hint_gateway", "tool_progress_hint_cli",
    "openclaw_residue_hint_cli", "detect_openclaw_residue", "profile_build_mode", "profile_build_directive",
    "is_seen", "mark_seen",
]
