"""File-mutation verification footers and turn-completion explanations for ``AIAgent``.

The footer tells the model (and user) when a claimed file mutation did not land; the explainer
summarises why a turn ended without a final answer. Every method resolves through ``AIAgent``'s MRO.
"""
import os
import re
from contextlib import suppress
from typing import Any, Dict, Optional

from agent.tool_dispatch_helpers import (
    _extract_error_preview, _extract_file_mutation_targets, _extract_landed_file_mutation_paths
)
from agent.tool_result_classification import (
    FILE_MUTATING_TOOL_NAMES as _FILE_MUTATING_TOOLS, file_mutation_result_landed
)

_NO_REPLY = "⚠️ 没有回复："

# One text for "the model produced nothing after retries" on every surface (CLI explainer,
# gateway ``(empty)`` rewrite, desktop); the model name is filled in by the explainer.
EMPTY_RESPONSE_EXPLANATION = (
    "{model} 这次没能给出回复（已经重试过）。"
    "发 `continue` 再试一次，或者用 /model 换个模型。"
)

# Exact ``turn_exit_reason`` → explanation body (prefixed with ``_NO_REPLY``).
_EXIT_REASON_EXPLANATIONS: Dict[str, str] = {
    "empty_response_exhausted": EMPTY_RESPONSE_EXPLANATION,
    "all_retries_exhausted_no_response": (
        "模型服务商在所有重试之后都没响应。"
        "发 /retry，或者用 /model 换个模型。"
    ),
    "partial_stream_recovery": (
        "流式输出中途断了，只恢复出一部分回复。"
        "发 `continue` 从断的地方接着来。"
    ),
    "fallback_prior_turn_content": (
        "这一轮没有产出新内容，显示的是恢复出来的上一轮上下文。发 `continue` 重试。"
    ),
    "redirect_restart_limit_exceeded": (
        "每次尝试都被新的纠正打断，所以这一轮停了，没有无限重试。"
        "你最后那条纠正已排进下一条消息。"
    ),
    "rebuilt_restart_limit_exceeded": (
        "备用链上的每个服务商都在反复失败，所以这一轮停了，没有无限重试。"
        "发 `continue`，或者换个服务商。"
    ),
    "budget_exhausted": (
        "单轮的迭代/花费额度用完了，还没到最终答案。发 `continue` 继续。"
    ),
    "ollama_runtime_context_too_small": (
        "本地模型的上下文窗口太小，没跑完。把上下文调大，或者换个更大的模型。"
    ),
    "pending_tool_result": (
        "这一轮停在工具结果还没回来的时候，模型也没再补文字。"
        "发 `continue` 让它接着总结。"
    ),
}

# Parameterised reasons (``max_iterations_reached(3/3)`` …) matched by prefix.
_EXIT_REASON_PREFIX_EXPLANATIONS = (
    # ``interrupted_during_api_call(<issuer>)`` names a system watchdog (#112647).
    ("interrupted_during_api_call", (
        "请求在调用中途被打断，还没收到回复。发 `continue` 重试。"
    )),
    ("max_iterations_reached", (
        "还没给出最终答案就到了最大工具迭代次数。发 `continue` 继续，"
        "或者把 `max_iterations` 调大。"
    )),
    ("error_near_max_iterations", (
        "在接近迭代上限时报错，还没给出最终答案。看看上面的工具输出，然后发 `continue`。"
    )),
    ("repeated_outer_errors", (
        "这一轮反复报同样的错，所以提前停了，没有无限重试。"
        "看看上面的报错，再发 `continue` 重试。"
    )),
)

# ``session_persistence_failed`` refined by the classified cause (lock contention ≠ disk full).
_PERSISTENCE_CAUSE_EXPLANATIONS: Dict[str, str] = {
    "compression": (
        "这一轮停了：另一个进程正在压缩这个会话。你的消息应该已经存好了 —— "
        "等压缩完再发一次。"
    ),
    "compression_closed": (
        "这一轮停了：这个会话被上下文压缩轮换了，续跑没能接上。"
        "存储本身是好的 —— 刷新客户端（或者开新一轮）让新会话号生效，"
        "然后再发一次。"
    ),
    "turn_lease": (
        "这一轮停了：另一个 Coco 进程接管了这个会话。你的回复没存下来 —— "
        "等那个进程结束，然后再发一次。"
    ),
    "locked": (
        "这一轮停了：会话存储正忙（另一个 Coco 进程在写 state 库）。"
        "你的消息应该已经存好了 —— 过一会儿再发一次。"
    ),
    # The forensic runbook for both (WAL generations, manifest.json, sidecars) lives in the
    # logger.error at hermes_state.py::_raise_if_db_replaced — never in the chat reply.
    "replaced": (
        "会话数据库文件在 Coco 运行时被换掉了，所以这条消息没存下来"
        "（{home}/sessions/ 里留了一份）。先停掉 Coco"
        "（`coco cli {profile_arg}gateway stop`），跑 `coco cli {profile_arg}doctor` —— 别跑 "
        "`coco cli {profile_arg}doctor --fix`，那会去修错的那个文件 —— "
        "然后再启动、把消息重发一次。进阶恢复步骤在日志里。"
    ),
    "deleted_wal": (
        "还有别的 Coco 进程占着会话数据库旧版本的预写日志（WAL），所以 Coco 为了保住"
        "文件停了写入，这条消息没存下来（{home}/sessions/ 里留了一份）。什么都没丢。"
        "把这个配置档上的 Coco 进程全退掉（桌面端、`coco cli {profile_arg}gateway stop`、"
        "看板、定时任务），跑 `coco cli {profile_arg}doctor` —— 它会点名还占着日志的进程 —— "
        "然后再启动 Coco、把消息重发一次。它们还在跑的时候别跑 `doctor --fix`，"
        "也别删任何 state.db 文件。指南：{recovery_docs}"
    ),
    "corrupt": (
        "这一轮停了：state 库报了结构性损坏（转录会在重启时丢）。清磁盘空间没用。"
        "恢复办法：\n"
        "1. 跑 `coco cli {profile_arg}doctor --fix`\n"
        "2. 停掉网关，然后这样恢复：\n"
        "   coco cli {profile_arg}sessions recover --source {db_path} --inspect-only\n"
        "   （如果它说可以恢复）coco cli {profile_arg}sessions recover "
        "--source {db_path} --output recovered-state.db\n"
        "   —— 恢复会先给坏文件做快照；别对正在使用的 "
        "state.db 跑 `sqlite3 ... \".recover\"`，有漏洞的 sqlite3 CLI 会把它弄得更坏\n"
        "3. 从 {backups_dir}/ 里的备份恢复\n"
        "然后重发你的消息。"
    ),
    # SQLite scoped the corruption to the FTS index and the derived indexes could not be
    # detached, so this write did not land; the message store itself is intact (#97794).
    "fts_index": (
        "这一轮停了：会话搜索索引（FTS5）损坏且没法剥离，所以这条消息没存下来。"
        "消息库本身没坏：别跑恢复工具、也别回滚备份。跑 `coco cli {profile_arg}doctor --fix` "
        "（或者重启 Coco，它打开时会修索引），然后重发消息。"
    ),
    "disk": (
        "Coco 没法把这段对话写进磁盘，所以停下来，而不是把你的消息弄丢。"
        "多半是磁盘满了：腾点空间（或者修 {home}/state.db 的权限），然后重发消息。"
    ),
}
_PERSISTENCE_DEFAULT_EXPLANATION = (
    "Coco 没能把这段对话存下来，所以停下来，而不是把你的消息弄丢。 "
    "可能原因：磁盘满了，或者别的 Coco 进程占着数据库。 "
    "关掉其它 Coco 窗口，跑 `coco cli {profile_arg}doctor` 查一下存储，然后重发消息。"
)


def _file_mutation_identity(path: str, task_id: Optional[str]) -> str:
    """One key per on-disk target: the file tools' task-resolved absolute path, case-folded
    on case-insensitive hosts. A failure recorded as ``notes.md`` and the write that later
    lands as ``/repo/notes.md`` (or ``Notes.md`` on Windows) must meet on the same key."""
    try:
        from tools.file_tools_paths import _resolve_path_for_task

        resolved = str(_resolve_path_for_task(path, task_id or "default"))
    except Exception:
        resolved = os.path.abspath(os.path.expanduser(path))
    return os.path.normcase(os.path.normpath(resolved))


def _file_stat_signature(identity: str) -> Optional[tuple]:
    """``(mtime_ns, size)`` of the target, ``None`` when it does not exist (or cannot be read)."""
    try:
        st = os.stat(identity)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _display_flag_enabled(agent, *, env_var: str, config_key: str, cache_attr: str) -> bool:
    """``display.<config_key>`` (default True), cached per agent on ``cache_attr``.

    ``env_var`` overrides on every call and is never cached. Reads the persisted config.yaml
    so gateway and CLI share the setting; ``load_config`` is imported lazily (startup cycle,
    and tests patch it at ``hermes_cli.config``). Any failure → True (safe default: on)."""
    try:
        env = os.environ.get(env_var)
        if env is not None:
            return env.strip().lower() not in {"0", "false", "no", "off"}
        cached = getattr(agent, cache_attr, None)
        if cached is not None:
            return cached
        try:
            from hermes_cli.config import load_config as _load_config
            _cfg = _load_config() or {}
        except Exception:
            _cfg = {}
        _display = _cfg.get("display") if isinstance(_cfg, dict) else None
        if isinstance(_display, dict) and config_key in _display:
            enabled = bool(_display.get(config_key))
        else:
            enabled = True
        setattr(agent, cache_attr, enabled)
        return enabled
    except Exception:
        return True


class TurnExplainersMixin:
    """File-mutation failure footer + turn-completion explainer (see module docstring)."""

    def _record_file_mutation_result(
        self, tool_name: str, args: Dict[str, Any], result: Any, is_error: bool,
        *, task_id: Optional[str] = None,
    ) -> None:
        """Record a ``write_file`` / ``patch`` outcome for the turn-end verifier.

        Failures store ``{path: {error_preview, tool, identity, stat}}`` keyed by the model's
        spelling; ``identity`` is the resolved on-disk target and ``stat`` its signature at
        failure time. A later success on the same identity (any spelling) removes the entry.
        No-op when the per-turn state dict is not initialised (tool dispatched outside ``run_conversation``).
        """
        if tool_name not in _FILE_MUTATING_TOOLS:
            return
        state = getattr(self, "_turn_failed_file_mutations", None)
        if state is None:
            return
        targets = _extract_file_mutation_targets(tool_name, args)
        if not targets:
            return
        landed = file_mutation_result_landed(tool_name, result)
        if landed:
            landed_paths = _extract_landed_file_mutation_paths(tool_name, args, result)
            changed = getattr(self, "_turn_file_mutation_paths", None)
            if changed is not None:
                changed.update(landed_paths)
            # Feed the checkpoint agent-write ledger so /rollback's safe mode can tell
            # Hermes-authored content from later user hand-edits.
            mgr = getattr(self, "_checkpoint_mgr", None)
            if mgr is not None and getattr(mgr, "enabled", False):
                from tools.file_tools_paths import container_backend_for_task
                if container_backend_for_task(task_id or "default") is None:  # container paths carry no host ledger entry
                    for _p in landed_paths:
                        with suppress(Exception):
                            mgr.record_agent_write(_p)
        if is_error and not landed:
            # Keep the FIRST error per path unless a later success replaces it.
            preview = _extract_error_preview(result)
            for path in targets:
                identity = _file_mutation_identity(path, task_id)
                state.setdefault(path, {
                    "tool": tool_name, "error_preview": preview,
                    "identity": identity, "stat": _file_stat_signature(identity),
                })
        else:
            cleared = {
                _file_mutation_identity(p, task_id)
                for p in (landed_paths if landed else targets)
            }
            for path, info in list(state.items()):
                if info.get("identity", _file_mutation_identity(path, task_id)) in cleared:
                    state.pop(path, None)

    @staticmethod
    def _file_mutations_still_failed(failed: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Drop entries whose target changed on disk since the failed call.

        The recorder only sees write_file/patch receipts; a terminal redirect or an
        execute_code write leaves none. Re-checking the stat signature at turn end keeps
        the footer from listing a file that was in fact modified later this turn. Entries
        without a snapshot (hand-built dicts) are kept as-is.
        """
        return {
            path: info for path, info in failed.items()
            if "stat" not in info or _file_stat_signature(info["identity"]) == info["stat"]
        }

    def _file_mutation_verifier_enabled(self) -> bool:
        """``display.file_mutation_verifier`` / ``HERMES_FILE_MUTATION_VERIFIER`` (a patchable seam)."""
        return _display_flag_enabled(
            self, env_var="HERMES_FILE_MUTATION_VERIFIER", config_key="file_mutation_verifier",
            cache_attr="_file_mutation_verifier_enabled_cache",
        )

    def _turn_completion_explainer_enabled(self) -> bool:
        """``display.turn_completion_explainer`` / ``HERMES_TURN_COMPLETION_EXPLAINER``."""
        return _display_flag_enabled(
            self, env_var="HERMES_TURN_COMPLETION_EXPLAINER", config_key="turn_completion_explainer",
            cache_attr="_turn_completion_explainer_enabled_cache",
        )

    # Bare absolute / home / Windows-drive paths in a footer line. Mirrors the gateway's
    # extract_local_files detector so anything it WOULD auto-attach is backticked first (#35584).
    _FOOTER_PATH_RE = re.compile(
        r"(?<![/:\w.`])(?:~/|/|[A-Za-z]:[/\\])(?:[\w.\-]+[/\\])*[\w.\-]+\.[\w]+",
    )

    @classmethod
    def _neutralize_footer_paths(cls, text: str) -> str:
        """Backtick bare file paths so the gateway's ``extract_local_files`` never auto-attaches them.

        The extractor skips inline-code spans; already-backticked paths are left alone (no double-wrap).
        """
        if not text:
            return text
        return cls._FOOTER_PATH_RE.sub(lambda m: f"`{m.group(0)}`", text)

    @classmethod
    def _format_file_mutation_failure_footer(cls, failed: Dict[str, Dict[str, Any]]) -> str:
        """Render the per-turn failed-mutation dict as a user-facing footer.

        Up to 10 paths with their first error preview, then an overflow count; "" when nothing failed.
        Every path is backtick-wrapped via ``_neutralize_footer_paths`` so protected files cannot be
        auto-delivered.
        """
        if not failed:
            return ""
        lines = [
            "⚠️ 文件改动核对："
            f"这一轮有 {len(failed)} 个文件编辑失败（上面若有说成功的，以这里为准）。"
            "用 `git status` 或 `read_file` 看实际落盘了什么。"
        ]
        shown = list(failed.items())[:10]
        for path, info in shown:
            preview = (info.get("error_preview") or "").strip()
            tool = info.get("tool") or "patch"
            lines.append(f"  • `{path}` — [{tool}] {preview or '失败'}")
        remaining = len(failed) - len(shown)
        if remaining > 0:
            lines.append(f"  • … 还有 {remaining} 个")
        # Neutralize paths the preview echoed; the lookbehind prevents double-wrapping the bullet path.
        return cls._neutralize_footer_paths("\n".join(lines))

    @staticmethod
    def _format_turn_completion_explanation(
        turn_exit_reason: str, persistence_cause: Optional[str] = None, db_path=None, model: str = "",
    ) -> str:
        """User-facing explanation for an abnormal turn ending, or "" for normal / unknown reasons.

        ``text_response(...)`` is the healthy terminal; unknown/diagnostic-only reasons (e.g.
        ``guardrail_halt``, which surfaces its own message) are not second-guessed.
        """
        if not turn_exit_reason:
            return ""
        reason = str(turn_exit_reason)
        if reason.startswith("text_response"):
            return ""
        body = _EXIT_REASON_EXPLANATIONS.get(reason)
        if body is None:
            for prefix, text in _EXIT_REASON_PREFIX_EXPLANATIONS:
                if reason.startswith(prefix):
                    body = text
                    break
        if body is not None and "{model}" in body:
            body = body.format(model=model or "The model")
        if body is None and reason == "session_persistence_failed":
            from hermes_constants import display_hermes_home, profile_cli_selector
            from hermes_state_errors import STORAGE_RECOVERY_DOCS_URL

            # Copy-pasteable, so pin every `hermes` command to the profile whose store failed:
            # a multi-profile backend (Desktop serve) hosts sessions whose state.db is NOT the
            # process default, and a bare `hermes` follows active_profile (#105887).
            body = (
                _PERSISTENCE_CAUSE_EXPLANATIONS.get(
                    persistence_cause or "unknown", _PERSISTENCE_DEFAULT_EXPLANATION
                )
                .replace("{home}", display_hermes_home())
                .replace("{profile_arg}", profile_cli_selector())
                .replace("{recovery_docs}", STORAGE_RECOVERY_DOCS_URL)
            )
            if persistence_cause in ("corrupt", "fts_index"):
                from hermes_constants import get_default_hermes_root
                from hermes_state import _default_db_path

                body = body.replace("{db_path}", str(db_path or _default_db_path()))
                body = body.replace(
                    "{backups_dir}", str(get_default_hermes_root() / "backups")
                )
        return _NO_REPLY + body if body else ""
