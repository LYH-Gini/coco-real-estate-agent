"""HERMES_HOME state checks for hermes doctor: directories, memory files, state.db health, skills hub, memory provider, profiles.
Split out of ``hermes_cli/doctor.py``, which re-exports every name so ``hermes_cli.doctor.<name>`` keeps resolving (and monkeypatching)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from hermes_cli.doctor_report import (
    Finding, _fail_and_issue, _section, check_bool, check_info, check_ok, check_warn, doctor_check, ensure_dir,
    warn_on_error,
)
from hermes_cli.sizefmt import format_bytes as _human_bytes
from hermes_state_common import FTS_STORAGE_VERSION
from hermes_state_holders import read_only_db_uri


def _honcho_is_configured_for_doctor() -> bool:
    """Return True when Honcho is configured, even if this process has no active session."""
    try:
        from plugins.memory import import_provider_module
        cfg = import_provider_module("honcho", "client").HonchoClientConfig.from_global_config()
        return bool(cfg.enabled and (cfg.api_key or cfg.base_url))
    except Exception:
        return False


def _doctor_memory_config(hermes_home: Path | None = None) -> dict:
    """Return the effective memory section used by doctor diagnostics."""
    from hermes_cli.doctor import HERMES_HOME
    try:
        from hermes_cli.config_effective import load_user_config_effective
        config_path = (hermes_home if hermes_home is not None else HERMES_HOME) / "config.yaml"
        if not config_path.exists():
            return {}
        section = load_user_config_effective(config_path).get("memory")
        return section if isinstance(section, dict) else {}
    except Exception:
        return {}


# state.db size threshold — advisory only; deliberately a module constant, not config (doctor warnings are guidance, not policy).
STATE_DB_SIZE_WARN_BYTES = 1 * 1024 * 1024 * 1024   # 1 GiB logical size


def _bits(*pairs) -> list:
    """``[fmt() for value, fmt in pairs if value is not None]`` — present-only stat fragments."""
    return [fmt() for value, fmt in pairs if value is not None]


def host_gateway_note() -> str:
    """``" (the host gateway (PID 42) serving profiles default, coder)"`` when one gateway process
    owns this host, else ``""``. Multiplex-only: the state.db holder and WAL lines used to imply a
    gateway per profile; the truth is one shared process serving N profiles, and stopping it stops
    every one of them."""
    try:
        from gateway.host_topology import host_gateway_topology
        topology = host_gateway_topology()
    except Exception:
        return ""
    return f" ({topology.describe()})" if topology is not None else ""


def _render_state_db_stats(stats: dict, holders=None, host_note: str = "") -> list:
    """Turn a collect_state_db_stats() dict into ``(kind, text, detail)`` rows, kind 'info' / 'warn'.

    Pure formatting — no I/O — so it is unit-testable without the doctor CLI. Tolerates None in every field.
    ``host_note`` names the shared host gateway among the holders (see :func:`host_gateway_note`).
    """
    lines: list = []
    stats = stats or {}
    logical, wal, freelist = (stats.get(k) for k in ("logical_size_bytes", "wal_size_bytes", "freelist_count"))
    size_bits = _bits(
        (logical, lambda: f"占用 {_human_bytes(logical)}"),
        (stats.get("page_count"), lambda: f"{stats['page_count']:,} 页"),
        (freelist, lambda: f"{freelist:,} 页空闲"),
        (wal, lambda: f"WAL {_human_bytes(wal)}"),
    )
    if size_bits:
        lines.append(("info", "state.db " + ", ".join(size_bits), ""))
    row_bits = _bits(
        (stats.get("messages"), lambda: f"{stats['messages']:,} 条消息"),
        (stats.get("sessions"), lambda: f"{stats['sessions']:,} 个会话"),
        (stats.get("journal_mode") or None, lambda: f"journal_mode={stats['journal_mode']}"),
        (holders, lambda: f"{holders} 个进程打开着这个库{host_note}"),
    )
    if row_bits:
        lines.append(("info", ", ".join(row_bits), ""))
    fts = stats.get("fts_tables")
    if fts:
        present = [t for t, ok in fts.items() if ok]
        lines.append(("info", "全文检索表：" + (", ".join(present) if present else "无"), ""))
    deferral = stats.get("fts_rebuild_deferral")
    if isinstance(deferral, dict):
        pids = deferral.get("holder_pids") or "unknown"
        if deferral.get("futile"):
            lines.append(("warn", f"state.db FTS repair is blocked by the same holder(s) PID(s) {pids} for "
                          f"{deferral.get('holders_attempts') or '?'} consecutive deferral(s); waiting is futile",
                          "(stop ONLY the listed process(es) — the host gateway keeps running and its own "
                          "retry rebuilds within a minute of the holder leaving)"))
        else:
            lines.append(("warn", f"state.db FTS repair is blocked after {deferral.get('attempts') or '?'} deferral(s) "
                          f"by PID(s) {pids}",
                          "（先停掉上面列的进程；主网关自己的重试会重建，或者停掉所有占用者后跑 "
                          "`coco cli sessions optimize-storage`）"))
    # Oversized DB: suggest auto_prune, plus the offline optimize-storage pass when the FTS rebuild is
    # pending OR the DB predates the current trigram layout (fts_storage_version < FTS_STORAGE_VERSION).
    if logical is not None and logical > STATE_DB_SIZE_WARN_BYTES:
        detail = "建议在 config.yaml 里开 sessions.auto_prune 控制增长"
        stale_trigram = (fts is not None and fts.get("messages_fts_trigram")
                         and (stats.get("fts_storage_version") or 0) < FTS_STORAGE_VERSION)
        if stats.get("fts_rebuild_pending") or stale_trigram:
            detail += "；停掉主网关后离线跑 `coco cli sessions optimize-storage` 压缩 FTS 存储"
        lines.append(("warn", f"state.db 偏大（{_human_bytes(logical)}）", f"（{detail}）"))
    # WAL runaway is deliberately NOT warned here: _state_db_wal already warns above 50 MB and offers --fix.
    return lines


def _memory_store_flags(hermes_home: Path) -> tuple:
    from tools.memory_tool import get_builtin_memory_store_flags
    return get_builtin_memory_store_flags({"memory": _doctor_memory_config(hermes_home)})


@doctor_check()
def _check_directory_structure(should_fix: bool, f: Finding) -> None:
    """HERMES_HOME, expected subdirs, SOUL.md, and the enabled built-in memory files."""
    from hermes_cli.doctor import HERMES_HOME, _DHH
    hermes_home = HERMES_HOME
    ensure_dir(f, should_fix, hermes_home, f"{_DHH} 目录存在", f"已创建 {_DHH} 目录", f"找不到 {_DHH}")
    _memory_enabled, _user_profile_enabled = _memory_store_flags(hermes_home)
    memory_on = bool(_memory_enabled or _user_profile_enabled)
    # The built-in file store neither creates nor consumes memories/ when both targets are disabled.
    for subdir_name in ["cron", "sessions", "logs", "skills"] + (["memories"] if memory_on else []):
        ensure_dir(f, should_fix, hermes_home / subdir_name, f"{_DHH}/{subdir_name}/ 存在",
                   f"已创建 {_DHH}/{subdir_name}/", f"找不到 {_DHH}/{subdir_name}/")
    _check_scratch_dir(hermes_home, _DHH)
    # SOUL.md persona file
    soul_path = hermes_home / "SOUL.md"
    if soul_path.exists():
        lines = soul_path.read_text(encoding="utf-8").strip().splitlines()
        if any(l.strip() and not l.strip().startswith(("<!--", "-->", "#")) for l in lines):
            check_ok(f"{_DHH}/SOUL.md 存在（已配置人格）")
        else:  # template comments only (no real content)
            check_info(f"{_DHH}/SOUL.md 存在但是空的 —— 编辑它可以定制人格")
    else:
        check_warn(f"找不到 {_DHH}/SOUL.md", "（创建它可以给 Coco 定制人格）")
        if should_fix:
            soul_path.parent.mkdir(parents=True, exist_ok=True)
            soul_path.write_text("# Hermes Agent Persona\n\n<!-- Edit this file to customize how Hermes communicates. -->\n\n"
                                 "You are Hermes, a helpful AI assistant.\n", encoding="utf-8")
            check_ok(f"已用基础模板创建 {_DHH}/SOUL.md")
            f.fixed += 1
    # Only enabled built-in stores: users can disable either legacy file target, and stale migration files
    # must not read as active memory usage.
    memories_dir = hermes_home / "memories"
    if not memory_on:
        return check_info("记忆文件已被配置关掉")
    existed = memories_dir.exists()
    ensure_dir(f, should_fix, memories_dir, f"{_DHH}/memories/ 目录存在", f"已创建 {_DHH}/memories/",
               f"{_DHH}/memories/ not found")
    for fname in [n for on, n in ((_memory_enabled, "MEMORY.md"), (_user_profile_enabled, "USER.md")) if on and existed]:
        if (memories_dir / fname).exists():
            check_ok(f"{fname} 存在（{len((memories_dir / fname).read_text(encoding='utf-8').strip())} 字符）")
        else:
            check_info(f"{fname} 还没创建（第一次写记忆时会自动建）")


# Cache-root entries at least this big that no pruner covers get a doctor warning.
_UNPRUNED_CACHE_WARN_BYTES = 1 << 30
_PRUNED_CACHE_DIRS = frozenset({"scratch", "terminal"})


def unpruned_cache_hogs(hermes_home: Path, min_bytes: int = _UNPRUNED_CACHE_WARN_BYTES) -> list[tuple[str, int]]:
    """``(name, bytes)`` for ``cache/`` entries outside the pruned dirs that exceed *min_bytes*.

    Finished campaign trees parked at the cache root sat for weeks (95 GB on one host)
    because only ``scratch/`` and ``terminal/`` are reaped; doctor is where that shows."""
    from hermes_constants import scratch_dir_usage_bytes

    cache = hermes_home / "cache"
    hogs: list[tuple[str, int]] = []
    try:
        entries = [e for e in cache.iterdir() if e.is_dir() and not e.is_symlink() and e.name not in _PRUNED_CACHE_DIRS]
    except OSError:
        return hogs
    for entry in entries:
        size = scratch_dir_usage_bytes(entry)
        if size >= min_bytes:
            hogs.append((entry.name, size))
    return sorted(hogs, key=lambda item: -item[1])


def _check_scratch_dir(hermes_home: Path, _DHH: str) -> None:
    """Report the scratch dir (TMPDIR target) and its size; a user-set TMPDIR elsewhere is shown, not judged."""
    from hermes_constants import (
        SCRATCH_DIR_MARKER_ENV, SCRATCH_MAX_IDLE_HOURS, get_scratch_dir, scratch_dir_usage_bytes)
    scratch = get_scratch_dir(hermes_home, prune=False)
    size = _human_bytes(scratch_dir_usage_bytes(scratch))
    check_ok(f"{_DHH}/cache/scratch/ 是临时目录（TMPDIR；{size}，闲置 {SCRATCH_MAX_IDLE_HOURS} 小时后自动清理）")
    for name, nbytes in unpruned_cache_hogs(hermes_home):
        check_warn(
            f"{_DHH}/cache/{name}/ 占 {_human_bytes(nbytes)}，不在任何自动清理范围内 "
            f"（只有 cache/scratch/ 和 cache/terminal/ 会被清）—— 把任务文件挪到 "
            f"cache/scratch/<任务>/ 下，或者直接删掉"
        )
    tmpdir = os.environ.get("TMPDIR", "")
    if tmpdir and tmpdir != os.environ.get(SCRATCH_DIR_MARKER_ENV, ""):
        check_info(f"TMPDIR={tmpdir} 是你或系统设的，Coco 不会去动它")


def _session_count(state_db_path: Path):
    import sqlite3
    # mode=ro: doctor is a reader; a writable open of a gateway-held WAL DB is the second-writer class (#103339).
    conn = sqlite3.connect(read_only_db_uri(state_db_path), uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    finally:
        conn.close()


# Above this the snapshot copy a held store needs costs more than the probe is worth; --fix still probes.
_WRITE_PROBE_SNAPSHOT_MAX_BYTES = 1 << 30


def _write_health_reason(state_db_path: Path, *, should_fix: bool):
    """FTS/write-health probe (a rolled-back BEGIN IMMEDIATE). Against a store a live writer holds,
    that probe is the second-writer class (#103339), so probe a read-only snapshot instead; a quiet
    store is probed in place. Returns the failure reason, or None when healthy or skipped."""
    from hermes_state_repair import _db_opens_cleanly, _live_writer_holds_db
    if not _live_writer_holds_db(state_db_path):
        return _db_opens_cleanly(state_db_path)
    if not should_fix and state_db_path.stat().st_size > _WRITE_PROBE_SNAPSHOT_MAX_BYTES:
        check_info("跳过 state.db 的写入体检：库正被写入进程占用且大于 1 GB "
                   "（跑「coco doctor --fix」可以强制检查）")
        return None
    import sqlite3
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "state.db"
        src = sqlite3.connect(read_only_db_uri(state_db_path), uri=True, timeout=1.0)
        try:
            dest = sqlite3.connect(str(snapshot))
            try:
                src.backup(dest)
            finally:
                dest.close()
        finally:
            src.close()
        return _db_opens_cleanly(snapshot)


# Corruption class -> (ok label, not-fixed label, failed issue, fix hint). ``{count}`` = recovered sessions.
# ``structural`` has no in-place repair: an FTS rebuild cannot fix a canonical b-tree, and the
# ``.malformed-backup`` the repair path would leave beside state.db is a copy of the same damage (#88587).
_STATE_DB_REPAIRS = {
    "fts": ("已修好 state.db 的 FTS 写入健康",
            "state.db 的 FTS 写入问题没能自动修好",
            "state.db 的 FTS 写入损坏且自动修复失败 —— 用 state.db 旁边的备份副本恢复",
            "state.db 的 FTS 写入损坏 —— 跑「coco doctor --fix」（或 `coco cli sessions repair`）重建 FTS 索引"),
    "schema": ("已修好 state.db 表结构（找回 {count} 个会话）",
               "state.db 表结构没能自动修好",
               "state.db 表结构损坏且自动修复失败 —— 用旁边的备份副本恢复",
               "state.db 表结构损坏 —— 跑「coco doctor --fix」（或 `coco cli sessions repair`）找回被藏起来的会话"),
}
_STATE_DB_STRUCTURAL_ISSUE = (
    "state.db 结构性损坏（主表和索引坏了，不是 FTS 索引）—— 重建 FTS 修不了。先停网关，"
    "再跑 `coco cli {profile_arg}sessions recover --source {db_path} --inspect-only`；"
    "如果它说可以恢复，再跑 `coco cli {profile_arg}sessions recover --source {db_path} "
    "--output recovered-state.db`。别去恢复 state.db 旁边那个 .malformed-backup 副本："
    "它跟坏掉的文件是同一份快照。"
)


def _repair_state_db(f: Finding, should_fix: bool, state_db_path: Path, kind: str) -> None:
    """Shared --fix path for both state.db corruption classes (FTS write health, malformed schema)."""
    if kind == "structural":
        from hermes_constants import profile_cli_selector
        return f.manual_issues.append(_STATE_DB_STRUCTURAL_ISSUE.format(
            profile_arg=profile_cli_selector(), db_path=state_db_path))
    ok_label, not_fixed_label, failed_issue, fix_hint = _STATE_DB_REPAIRS[kind]
    if not should_fix:
        return f.issues.append(fix_hint)
    from hermes_state_repair import repair_state_db_schema
    report = repair_state_db_schema(state_db_path)
    if not report.get("repaired"):
        check_warn(not_fixed_label, f"（{report.get('error')}；备份：{report.get('backup_path')}）")
        return f.issues.append(failed_issue)
    if "{count}" in ok_label:
        try:
            ok_label = ok_label.format(count=_session_count(state_db_path))
        except Exception:
            ok_label = ok_label.format(count="?")
    backup_name = Path(report["backup_path"]).name if report.get("backup_path") else "n/a"
    check_ok(ok_label, f"（策略：{report.get('strategy')}；备份：{backup_name}）")
    f.fixed += 1


def _report_structural_damage(f: Finding, should_fix: bool, state_db_path: Path, _DHH: str, reason) -> bool:
    """True (and reported/repaired) when the canonical b-tree, not just the FTS index, is damaged."""
    from hermes_state_repair import state_db_has_structural_damage
    if not state_db_has_structural_damage(state_db_path):
        return False
    check_warn(f"{_DHH}/state.db 结构性损坏（核心表/索引坏了，不是 FTS 索引）", f"（{reason}）")
    _repair_state_db(f, should_fix, state_db_path, "structural")
    return True


def _classify_unreadable_state_db(f: Finding, should_fix: bool, state_db_path: Path, _DHH: str, exc: Exception) -> None:
    """Structural damage first; only then schema repair. Avoids SessionDB auto-repair side effects."""
    from hermes_state import is_malformed_db_error
    if _report_structural_damage(f, should_fix, state_db_path, _DHH, exc):
        return
    if not is_malformed_db_error(exc):
        return check_warn(f"{_DHH}/state.db 存在但有问题：{exc}")
    # sqlite_master itself is malformed (e.g. duplicate messages_fts): every statement fails before it runs,
    # so this is NOT a plain FTS rebuild — repair sqlite_master in place (backup first).
    check_warn(f"{_DHH}/state.db 表结构异常（修好之前会话会被隐藏）", f"（{exc}）")
    _repair_state_db(f, should_fix, state_db_path, "schema")


def _state_db_health(f: Finding, should_fix: bool, state_db_path: Path, _DHH: str) -> None:
    """Session count + FTS write-health probe; malformed-schema path when even COUNT(*) fails."""
    try:
        check_ok(f"{_DHH}/state.db 存在（{_session_count(state_db_path)} 个会话）")
        # COUNT(*) succeeds even when the FTS index is corrupt and every write fails through the triggers.
        _write_reason = _write_health_reason(state_db_path, should_fix=should_fix)
    except Exception as e:
        return _classify_unreadable_state_db(f, should_fix, state_db_path, _DHH, e)
    if _write_reason is not None:
        if _report_structural_damage(f, should_fix, state_db_path, _DHH, _write_reason):
            return
        check_warn(f"{_DHH}/state.db 写入体检不通过（FTS 索引可能坏了）", f"（{_write_reason}）")
        _repair_state_db(f, should_fix, state_db_path, "fts")


def _state_db_stats(issues: list, state_db_path: Path) -> None:
    """Health/stats snapshot: strictly read-only (mode=ro) so it is safe against a live DB held by
    the gateway; any failure degrades to one info line rather than failing doctor."""
    with warn_on_error("取不到 state.db 统计（{e}）", "", report=lambda t, _d: check_info(t)):
        from hermes_state_dbfile import collect_state_db_stats, count_db_holders
        rows = _render_state_db_stats(collect_state_db_stats(state_db_path), holders=count_db_holders(state_db_path),
                                      host_note=host_gateway_note())
        for _kind, _text, _detail in rows:
            if _kind != "warn":
                check_info(_text + (f" {_detail}" if _detail else ""))
                continue
            check_warn(_text, _detail)
            if "auto_prune" in _detail:
                issues.append("state.db 偏大 —— 可以在 config.yaml 里打开 sessions.auto_prune"
                              + ("；停掉网关后离线跑 `coco cli sessions optimize-storage`" if "optimize-storage" in _detail else ""))


def _state_db_wal(f: Finding, should_fix: bool, state_db_path: Path) -> None:
    """WAL file size (unbounded growth indicates missed checkpoints)."""
    wal_path = state_db_path.parent / "state.db-wal"
    wal_size = lambda: wal_path.stat().st_size if wal_path.exists() else 0  # noqa: E731
    with warn_on_error(""):
        size = wal_size()
        if size > 50 * 1024 * 1024:  # 50 MB
            # Checkpoint-lock premise (#40177, #103339): a bare connect runs WAL recovery and the checkpoint
            # joins the live WAL — under a running gateway that second-writer handling corrupts state.db.
            # Holder scan first (any other process holding the DB, or an unknown, fails closed), then run the
            # checkpoint on the exclusive repair guard so an opener arriving in between is refused, not joined.
            from hermes_state_repair import _exclusive_repair_db_guard, _live_writer_holds_db
            title = f"WAL file is large ({size // (1024*1024)} MB)"
            _SKIP = ("WAL 文件偏大 —— 没法确认 state.db 没人写（先停掉这个配置档的网关，"
                     "再跑「coco doctor --fix」做检查点）")
            # Honest disjunction (gate C1): a True here means "held OR unprovable" — never assert a live
            # writer as fact.
            if _live_writer_holds_db(state_db_path):
                # A large WAL is normal while Desktop or the gateway is running; a bare "run --fix" here sent
                # users straight into the second-writer trap (#110054).
                check_warn(title, "（桌面端或网关在跑时属正常；也可能是 state.db 读不了 —— "
                                  "只有把它们都停掉才能做检查点）")
                return f.issues.append(_SKIP)
            check_warn(title, "（可能是漏了检查点）")
            if not should_fix:
                return f.issues.append(
                    "WAL 文件偏大 —— 先停掉这个配置档的网关，再跑「coco doctor --fix」做检查点")
            with _exclusive_repair_db_guard(state_db_path) as (guard, guard_error):
                if guard is None:
                    check_warn("跳过 WAL 检查点：拿不到 state.db 的独占所有权",
                               f"（{guard_error}；停掉这个配置档的网关，再跑「coco doctor --fix」）")
                    return f.issues.append(_SKIP)
                guard.execute("PRAGMA wal_checkpoint(PASSIVE)")
            check_ok(f"已做 WAL 检查点（{size // 1024}K → {wal_size() // 1024}K）")
            f.fixed += 1
        elif size > 10 * 1024 * 1024:  # 10 MB
            check_info(f"WAL 文件 {size // (1024*1024)} MB（会话活跃时属正常）")


def _retired_wal_holders(f: Finding, state_db_path: Path, _DHH: str) -> bool:
    """Name the processes holding a retired -wal/-shm generation (#110054). Every SessionDB open is
    refused while they live, and the current inode has no holders, so the plain holder count says
    "0 holding the DB open" beside a green state.db line — the opposite of the truth."""
    from hermes_constants import profile_cli_selector
    from hermes_state_dbfile import iter_deleted_sqlite_sidecar_holders
    from hermes_state_holders import describe_holder_pid
    pids = list(dict.fromkeys(pid for pid, _ in iter_deleted_sqlite_sidecar_holders(state_db_path)))
    if not pids:
        return False
    rendered = ", ".join(describe_holder_pid(pid) for pid in pids)
    check_warn(f"{_DHH}/state.db：还有 {len(pids)} 个进程占着已废弃的 WAL 版本（{rendered}）",
               "（它们不退出，新会话就打不开；健康检查与统计也会跳过）")
    f.issues.append(f"state.db 的旧 WAL 版本被 {rendered}{host_gateway_note()} 占着 —— 请把这些进程里的网关、"
                    f"看板与定时写入都停掉（「coco {profile_cli_selector()}gateway stop」会停掉那个服务全部配置的唯一主进程；"
                    "顺带退出桌面端），不要自己删 WAL，然后重跑「coco doctor」")
    return True


@doctor_check()
def _check_state_db(should_fix: bool, f: Finding) -> None:
    """state.db session count, FTS write health, schema repair, stats snapshot, WAL size."""
    from hermes_cli.doctor import HERMES_HOME, _DHH
    state_db_path = HERMES_HOME / "state.db"
    # A read-only connect on the new generation is itself another opener, so nothing below may run.
    if _retired_wal_holders(f, state_db_path, _DHH):
        return
    if state_db_path.exists():
        _state_db_health(f, should_fix, state_db_path, _DHH)
        _state_db_stats(f.issues, state_db_path)
    else:
        check_info(f"{_DHH}/state.db 还没创建（第一次会话时自动建）")
    _state_db_wal(f, should_fix, state_db_path)


@doctor_check()
def _check_checkpoint_store(should_fix: bool, f: Finding) -> None:
    """/rollback store footprint: warn when checkpoints are on and the store sits above its cap."""
    from tools.checkpoint_manager import checkpoint_footprint_notice
    notice = checkpoint_footprint_notice()
    if notice:
        check_warn(notice)


def _gh_authenticated() -> bool:
    """Check if gh CLI is authenticated via token file or device flow.

    Plain ``gh auth status`` (exit code only): gh 2.98+ dropped the
    ``authenticated`` JSON field, so ``--json authenticated`` exits 1 even
    when logged in, and the doctor falsely reported "No GITHUB_TOKEN".
    """
    try:
        result = subprocess.run(["gh", "auth", "status"], capture_output=True, timeout=10)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


@doctor_check()
def _check_skills_hub(should_fix: bool, f: Finding) -> None:
    from hermes_cli.doctor import HERMES_HOME, _DHH
    hub_dir = HERMES_HOME / "skills" / ".hub"
    if check_bool(hub_dir.exists(), "技能源目录存在", ("技能源目录还没初始化", "（跑：coco cli skills list）")):
        lock_file = hub_dir / "lock.json"
        if lock_file.exists():
            with warn_on_error("Lock file", "(corrupted or unreadable)"):
                import json
                count = len(json.loads(lock_file.read_text(encoding="utf-8")).get("installed", {}))
                check_ok(f"锁文件正常（{count} 个从技能源装的技能）")
        quarantine = hub_dir / "quarantine"
        q_count = sum(1 for d in quarantine.iterdir() if d.is_dir()) if quarantine.exists() else 0
        if q_count > 0:
            check_warn(f"{q_count} 个技能在隔离区", "（待复核）")
    from hermes_cli.config import get_env_value
    if get_env_value("GITHUB_TOKEN") or get_env_value("GH_TOKEN"):
        check_ok("GitHub 令牌已配置", "（有效性在「模型服务连通性」里检查）")
    else:
        check_bool(_gh_authenticated(), ("已通过 gh CLI 登录 GitHub", "（完整 API 权限 —— 不需要配 GITHUB_TOKEN）"),
                   ("没配 GITHUB_TOKEN", f"（每小时限 60 次 —— 在 {_DHH}/.env 里配上能提高额度）"))


def _memory_provider_honcho(issues: list) -> None:
    from plugins.memory import import_provider_module
    client = import_provider_module("honcho", "client")
    hcfg = client.HonchoClientConfig.from_global_config()
    cfg_path = client.resolve_config_path()
    if not cfg_path.exists():
        # Config file missing — env-var fallback may still have resolved it.
        check_bool(hcfg.api_key or hcfg.base_url,
                   ("Honcho configured via environment variables", f"config file {cfg_path} not found, using HONCHO_API_KEY env var"),
                   ("Honcho config not found", "跑：coco cli memory setup"))
    elif not hcfg.enabled:
        check_info(f"Honcho 没启用（在 {cfg_path} 里设 enabled: true 可打开）")
    elif not (hcfg.api_key or hcfg.base_url):
        _fail_and_issue("Honcho API key or base URL not set", "跑：coco cli memory setup",
                        "没配 Honcho 的密钥 —— 跑 `coco cli memory setup`", issues)
    else:
        client.reset_honcho_client()
        try:
            client.get_honcho_client(hcfg)
            check_ok("Honcho 已连接", f"工作区={hcfg.workspace_id} 模式={hcfg.recall_mode} 频率={hcfg.write_frequency}")
        except Exception as _e:
            _fail_and_issue("Honcho connection failed", str(_e), f"Honcho 连不上：{_e}", issues)


def _memory_provider_mem0(issues: list) -> None:
    from plugins.memory import import_provider_module
    mem0_cfg = import_provider_module("mem0")._load_config()
    if mem0_cfg.get("api_key", ""):
        check_ok("Mem0 的密钥已配置")
        check_info(f"用户={mem0_cfg.get('user_id', '?')}  智能体={mem0_cfg.get('agent_id', '?')}")
    else:
        _fail_and_issue("Mem0 的密钥没配", "（在 .env 里设 MEM0_API_KEY，或跑 `coco cli memory setup`）",
                        "指定用 Mem0 当记忆服务，但没配它的密钥", issues)


# provider -> (checker, ImportError row, ImportError issue, label for "check failed")
_MEMORY_PROVIDER_CHECKS = {
    "honcho": (_memory_provider_honcho, ("没装 honcho-ai", "pip install honcho-ai"),
               "指定用 Honcho 当记忆服务，但没装 honcho-ai", "Honcho"),
    "mem0": (_memory_provider_mem0, ("Mem0 插件加载不了", "pip install mem0ai"),
             "指定用 Mem0 当记忆服务，但没装 mem0ai", "Mem0"),
}


def _memory_provider_generic(name: str) -> None:
    """Generic check for other memory providers (openviking, hindsight, etc.)."""
    from plugins.memory import load_memory_provider
    _provider = load_memory_provider(name)
    if _provider and _provider.is_available():
        check_ok(f"{name} 记忆服务已启用")
    elif _provider:
        check_warn(f"{name} 配置了但不可用", "跑：coco cli memory status")
    else:
        check_warn(f"找不到 {name} 插件", "跑：coco cli memory setup")


@doctor_check()
def _check_memory_provider(should_fix: bool, f: Finding) -> None:
    from hermes_cli.doctor import HERMES_HOME
    from agent.memory_provider import is_core_memory_provider
    name = _doctor_memory_config(HERMES_HOME).get("provider", "")
    if is_core_memory_provider(name):
        check_ok("内置记忆已启用", "（没配外部记忆服务，这样就行）")
        return
    checker, missing_row, missing_issue, label = _MEMORY_PROVIDER_CHECKS.get(name, (None, None, None, name))
    try:
        checker(f.issues) if checker else _memory_provider_generic(name)
    except ImportError as _e:
        if missing_row is None:
            check_warn(f"{label} 检查失败", str(_e))
        else:
            _fail_and_issue(*missing_row, missing_issue, f.issues)
    except Exception as _e:
        check_warn(f"{label} 检查失败", str(_e))


@doctor_check("")  # best-effort: profile enumeration must never break doctor
def _check_profiles(should_fix: bool, f: Finding) -> None:
    from hermes_cli.profiles import list_profiles, _get_wrapper_dir, profile_exists
    import re as _re
    named_profiles = [p for p in list_profiles() if not p.is_default]
    if not named_profiles:
        return
    _section("配置档（profiles）")
    check_ok(f"找到 {len(named_profiles)} 个配置档")
    wrapper_dir = _get_wrapper_dir()
    for p in named_profiles:
        parts = [text for cond, text in (
            (p.gateway_running, "gateway running"), (p.model, (p.model or "")[:30]),
            (not (p.path / "config.yaml").exists(), "⚠ missing config"), (not (p.path / ".env").exists(), "no .env"),
            (not (wrapper_dir / p.name).exists(), "no alias")) if cond]
        check_ok(f"  {p.name}：{', '.join(parts) if parts else '已配置'}")
    # Orphan wrappers
    if wrapper_dir.is_dir():
        for wrapper in wrapper_dir.iterdir():
            if not wrapper.is_file():
                continue
            with warn_on_error(""):
                _m = _re.search(r"hermes -p (\S+)", wrapper.read_text(encoding="utf-8"))
                if _m and not profile_exists(_m.group(1)):
                    check_warn(f"多余的命令别名：{wrapper.name} → 指向的配置档「{_m.group(1)}」已不存在")
    # Same helper as the multiplex migration preflight, so doctor names the duplicates that make
    # `hermes gateway migrate --multiplex` refuse (and made pre-multiplex standalone gateways race).
    from hermes_cli.gateway_migrate import duplicate_credential_findings
    for line in duplicate_credential_findings():
        check_warn("多个配置档用了同一套平台凭据", f"（{line}）")
        f.manual_issues.append(line)
