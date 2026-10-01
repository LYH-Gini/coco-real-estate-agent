"""Host-platform checks for hermes doctor: interpreter, SQLite, certificates, macOS TCC, gateway supervision, command install.
Split out of ``hermes_cli/doctor.py``, which re-exports every name so ``hermes_cli.doctor.<name>`` keeps resolving (and monkeypatching)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from hermes_cli.colors import Colors, color
from hermes_cli.config import is_nix_install_method, recommended_update_command_for_method
from hermes_cli.doctor_report import (
    Finding, _fail_and_issue, _section, check_bool, check_fail, check_info, check_ok, check_warn, doctor_check,
    warn_on_error,
)
from hermes_constants import is_termux as _is_termux


def _python_install_cmd() -> str:
    return "python -m pip install" if _is_termux() else "uv pip install"


def _system_package_install_cmd(pkg: str) -> str:
    return f"{'pkg' if _is_termux() else 'brew' if sys.platform == 'darwin' else 'sudo apt'} install {pkg}"


def _sqlite_upgrade_hint(install_method: str | None = None) -> str:
    """Return an actionable SQLite upgrade hint for this install layout."""
    from hermes_cli.doctor import PROJECT_ROOT
    from hermes_cli.config import detect_install_method
    method = install_method or detect_install_method(PROJECT_ROOT)
    cmd = recommended_update_command_for_method(method)
    action = cmd if is_nix_install_method(method) else {  # nix: prose guidance, not a shell command
        "docker": f"跑 `{cmd}`，然后重建全部容器", "apt": f"跑 `{cmd}`"}.get(method, "跑 `coco update`")
    return f"（{action}；修复版本：3.51.3+ / 3.50.7 / 3.44.6 —— 见 https://sqlite.org/wal.html#walresetbug）"


def _hermes_database_paths(hermes_home: Path) -> list[tuple[str, Path]]:
    """(display name, path) pairs for Hermes-managed SQLite databases: backup.py's per-profile store list + per-board kanban.db."""
    from hermes_cli.backup import _QUICK_STATE_FILES
    entries = [(name, hermes_home / name) for name in _QUICK_STATE_FILES if name.endswith(".db")]
    for board_db in sorted((hermes_home / "kanban" / "boards").glob("*/kanban.db")):
        entries.append((str(board_db.relative_to(hermes_home)), board_db))
    return entries


_SQLITE_HEADER_MAGIC = b"SQLite format 3\x00"


def _unreadable_reason(db_path: Path) -> str:
    """Explain why a database file could not be read, without opening it.

    ``read_header_bytes_preopen`` collapses every ``OSError`` into ``None``, but doctor must say *which*
    problem it hit. ``stat()``/``access()`` answer from directory metadata alone — no descriptor, no lock loss.
    """
    try:
        db_path.stat()
    except OSError as exc:
        return str(exc)
    return "file could not be read" if os.access(db_path, os.R_OK) else f"permission denied: {db_path}"


def _read_journal_mode(db_path: Path) -> tuple[str | None, str | None]:
    """Return (journal mode, error) from header byte 18 (2=WAL, 1=rollback) without opening the database.

    Opening through SQLite — even read-only — creates -wal/-shm sidecars, which a diagnostic must not do.
    ``read_header_bytes_preopen`` rather than a bare ``open()``: closing *any* descriptor cancels this
    process's POSIX advisory locks (see ``hermes_cli.sqlite_safe_read``), and the dashboard console runs
    ``run_doctor`` in-process with live ``SessionDB`` connections — the helper refuses then (unreadable).
    """
    from hermes_cli.sqlite_safe_read import has_live_connection, read_header_bytes_preopen
    header = read_header_bytes_preopen(db_path, length=20)
    if header is None:
        return None, "database is open in this process" if has_live_connection(db_path) else _unreadable_reason(db_path)
    if len(header) == 0:
        return None, "file is empty"
    if len(header) < 20 or not header.startswith(_SQLITE_HEADER_MAGIC):
        return None, "file is not a database"
    mode = {2: "wal", 1: "rollback"}.get(header[18])
    return (mode, None) if mode else (None, f"unrecognized file-format version {header[18]}")


def _format_db_size(db_path: Path) -> str:
    from hermes_cli.sizefmt import format_bytes as _format_size
    try:
        return _format_size(db_path.stat().st_size)
    except OSError:
        return "size unknown"


def _report_database_holders(name: str, db_path: Path) -> None:
    """Name the processes holding ``db_path`` (or a WAL sidecar) so the operator knows what to stop before the
    offline journal-mode conversion; a partial or unavailable scan is reported as "cannot prove quiet", never as
    an all-clear (the scan is the same fail-closed authority repair/VACUUM/checkpoint admission uses)."""
    from hermes_state_holders import describe_holder_pid, foreign_state_db_holders
    if sys.platform == "win32":
        check_warn(f"{name}：没法确认数据库此刻是安静的", "（Windows 上看不了占用进程）")
        return
    unknown: list[str] = []
    by_pid: dict[int, set[str]] = {}
    for pid, target in foreign_state_db_holders(db_path):
        if pid <= 0 or target.startswith("uninspectable"):
            unknown.append(target)
        else:
            by_pid.setdefault(pid, set()).add(Path(target.removesuffix(" (deleted)")).name)
    for pid in sorted(by_pid):
        check_info(f"{name} 正被占用：{describe_holder_pid(pid)} —— {', '.join(sorted(by_pid[pid]))}")
    if unknown:
        check_warn(f"{name}：没法确认数据库此刻是安静的",
                   f"(holder scan incomplete: {unknown[0][:120]}" + (f"; +{len(unknown) - 1} more" if len(unknown) > 1 else "") + ")")
    elif not by_pid:
        check_info(f"{name}：当前没有别的进程占用，离线转换可以跑")


def _report_database_journal_modes(hermes_home: Path | None = None, version_info: tuple[int, ...] | None = None) -> None:
    """List each database's journal mode; warn on WAL under a vulnerable SQLite, and on a configured
    ``database.journal_mode: delete`` that never took effect."""
    from hermes_cli.doctor import HERMES_HOME
    from hermes_state_wal import (
        _path_on_cross_vm_fs, _wal_reset_repair_hint, is_sqlite_wal_reset_vulnerable, resolve_journal_mode,
    )
    vulnerable = is_sqlite_wal_reset_vulnerable(version_info)
    configured = resolve_journal_mode()
    try:
        databases = _hermes_database_paths(hermes_home if hermes_home is not None else HERMES_HOME)
    except Exception as exc:
        check_warn(f"列不出数据库：{exc}")
        return
    exposed = []
    for name, path in databases:
        if not path.is_file():
            continue
        mode, error = _read_journal_mode(path)
        size = _format_db_size(path)
        if error is None and mode == "wal" and configured == "delete":
            # The operator configured `delete` because WAL is unsafe on their filesystem, but the runtime never
            # live-downgrades an existing WAL database (#68545: a downgrade under open connections corrupts it)
            # and says so only once per process in the gateway log (#85608). Doctor is the surface they check;
            # a plain "WAL journal mode" line here reads as protected. Outranks the sibling WAL messages: the
            # cross-VM hint's remedy ("set journal_mode: delete") is already applied.
            if vulnerable:
                exposed.append(name)
            check_warn(f"{name} 仍是 WAL 模式（{size}），但配置写的是 database.journal_mode=delete",
                       "（配置没生效：已有的 WAL 数据库不会在运行时降级"
                       + ("；而且还有 WAL 重置那个坑" if vulnerable else "")
                       + "。先停掉这个配置档的所有 Coco 进程，再跑 "
                       f"`coco cli sessions set-journal-mode delete{'' if name == 'state.db' else f' --db {path}'}`)")
            _report_database_holders(name, path)
        elif error is not None:
            if vulnerable:
                check_warn(f"{name}：读不到日志模式", f"（{error}；没法排除 WAL 风险）")
            else:
                check_info(f"{name}：读不到日志模式（{error}）")
        elif mode == "wal" and _path_on_cross_vm_fs(str(path)):
            # #110848: WAL shared-memory is not coherent across a virtiofs/9p bind mount; startup only refuses WAL
            # for FRESH databases, so an existing WAL file here keeps corrupting until the operator converts it.
            # Checked before the WAL-reset exposure: active cross-VM corruption outranks a latent bug class.
            if vulnerable:
                exposed.append(name)
            check_warn(f"{name} 在跨虚拟机文件系统（virtiofs/9p）上是 WAL 模式（{size}）",
                       "（跨虚拟机边界时 WAL 可能静默损坏：先停掉所有 Coco 进程，跑 "
                       f"`coco cli sessions set-journal-mode delete{'' if name == 'state.db' else f' --db {path}'}`，然后 "
                       "把 `database.journal_mode` 设成 delete —— 或者把数据库挪到本机卷/命名卷上）")
        elif mode == "wal" and vulnerable:
            exposed.append(name)
            check_warn(f"{name} 是 WAL 模式（{size}）", "（SQLite 升级前有 WAL 重置风险）")
        elif mode == "wal":
            check_info(f"{name}：WAL 日志模式（{size}）")
        else:
            check_info(f"{name}：回滚日志模式（{size}{'，无风险' if vulnerable else ''}）")
    if exposed:
        check_info(f"消除风险的办法：{_wal_reset_repair_hint()}")


def _read_pyproject_version() -> str | None:
    """Read the ``[project]`` version from pyproject.toml; None for installed wheels (no pyproject) or unreadable files."""
    from hermes_cli.doctor import PROJECT_ROOT
    try:
        text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    in_project = False
    for line in map(str.strip, text.splitlines()):
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
        elif in_project and line.startswith("version") and "=" in line:
            return line.split("=", 1)[1].split("#", 1)[0].strip().strip("\"'") or None
    return None


def _check_version_consistency(issues: list[str]) -> None:
    """Detect pyproject.toml vs hermes_cli.__version__ drift (a conflict resolution can revert one but not the
    other). Silent no-op for installed wheels (no pyproject)."""
    try:
        from hermes_cli import __version__ as init_version
    except Exception:
        return
    pyproject_version = _read_pyproject_version()
    if pyproject_version is None:
        return
    if pyproject_version == init_version:
        return check_ok("版本文件一致", f"（{init_version}）")
    _fail_and_issue("源码里的版本号不一致", f"（pyproject.toml {pyproject_version} != hermes_cli/__init__.py {init_version}）",
                    "版本文件不一致：重新同步（跑 'coco update'，或把 hermes_cli/__init__.py 的 __version__ 改成与 pyproject.toml 一致）", issues)


def _check_s6_supervision(issues: list[str]) -> None:
    """Under our s6 /init, report static services and the ONE host gateway slot; no-op elsewhere.
    Counterpart to :func:`_check_gateway_service_linger` (systemd-on-host)."""
    try:
        from hermes_cli.service_manager import S6ServiceManager, detect_service_manager
    except Exception:
        return
    if detect_service_manager() != "s6":
        return
    _section("s6 监管")
    mgr = S6ServiceManager()
    for static in ("main-hermes", "dashboard"):  # s6-rc symlinks under /run/service/, same s6-svstat probe
        up = mgr.is_running(static)
        (check_ok if up else check_info)(f"{static}: up" if up else f"{static}：未运行（没通过环境变量启用时属正常）")
    _report_host_gateway_slot(mgr, issues)


def _report_host_gateway_slot(mgr, issues: list[str]) -> None:
    """Multiplex-only: ONE gateway process serves N profiles, so report THAT process and its
    roster. The old ``Per-profile gateways: up/total`` line described a topology we no longer
    run — it counted supervision slots and never said which profiles were actually served."""
    from gateway.host_topology import host_gateway_topology
    slots = sorted(mgr.list_profile_gateways())
    topology = host_gateway_topology()
    if topology is None:
        if not slots:
            return check_info("还没有注册网关服务 —— 跑 `coco gateway install`")
        up = [p for p in slots if mgr.is_running(f"gateway-{p}")]
        issues.append("没有网关在承担网关角色 —— 启动那个唯一的主网关："
                      "coco -p default gateway start")
        return check_warn(f"没有网关承担网关角色（{len(up)}/{len(slots)} 个监管槽位在位）："
                          f"{', '.join(slots)}）", "（这些配置档没有网关在服务）")
    check_ok(f"主网关：{topology.describe()}")
    legacy_up = sorted(p for p in slots if p != "default" and mgr.is_running(f"gateway-{p}"))
    if legacy_up:
        check_warn(f"仍在监管的旧版按配置网关：{', '.join(legacy_up)}",
                   "（主网关进程已经同时服务所有配置档）")
        issues.append("把旧版按配置的网关并进主网关："
                      "coco -p default gateway migrate --multiplex")


def check_certificates(should_fix: bool = False, issues: "list | None" = None) -> None:
    """Verify the certifi CA bundle is loadable before the first HTTPS call tracebacks.

    ``--fix`` repairs a broken bundle (e.g. a brew Python upgrade rebuilt the venv) by force-reinstalling
    certifi into THIS interpreter's environment and re-verifying.
    """
    try:
        from agent.ssl_guard import verify_ca_bundle
        from agent.errors import SSLConfigurationError
    except Exception as e:
        return check_warn("跳过 SSL 证书检查", str(e))
    if issues is None:
        issues = []
    try:
        verify_ca_bundle()
        return check_ok("SSL 根证书包正常")
    except SSLConfigurationError as e:
        first_error = str(e)
    except Exception as e:
        return check_warn("跳过 SSL 证书检查", str(e))
    check_fail("SSL 根证书包有问题", first_error)
    pip_cmd = f"{sys.executable} -m pip install --force-reinstall certifi"
    if not should_fix:
        issues.append(f"修复 CA 证书包：跑 `coco doctor --fix`，或 `{pip_cmd}`")
        return
    print("    → 正在修复：强制重装 certifi...")
    try:
        result = subprocess.run([sys.executable, "-m", "pip", "install", "--force-reinstall", "certifi"],
                                capture_output=True, text=True, timeout=300)
        failure = ("certifi reinstall failed", (result.stderr or result.stdout or "")[-500:]) if result.returncode != 0 else None
    except Exception as exc:
        failure = ("certifi repair could not run pip", str(exc))
    if failure:
        return _fail_and_issue(*failure, f"手动重装 certifi：{pip_cmd}", issues)
    # Drop cached certifi modules so where() resolves the fresh install without a restart.
    import importlib
    for mod_name in [m for m in sys.modules if m == "certifi" or m.startswith("certifi.")]:
        sys.modules.pop(mod_name, None)
    importlib.invalidate_caches()
    try:
        verify_ca_bundle()
        check_ok("SSL 根证书包已修好（重装了 certifi）")
    except SSLConfigurationError as e:
        _fail_and_issue("SSL CA certificate bundle still broken after reinstall", str(e),
                        "重装 certifi 也没修好 CA 证书包 —— 看看是不是设了自定义 CA 环境变量 "
                        "（SSL_CERT_FILE/REQUESTS_CA_BUNDLE）指向一个不存在的文件，或者重建一下 venv。", issues)


def _check_gateway_service_linger(issues: list[str]) -> None:
    """Warn when a systemd user gateway service will stop after logout (skipped under s6: no linger concept).

    Multiplex-only: the HOST gateway runs under the default profile's unit, so a doctor run from a
    SERVED profile must still check it — gating on the active profile's own unit silently skipped
    the check for every profile that does not own a service of its own.
    """
    try:
        from hermes_cli.gateway import (
            _SERVICE_BASE, get_systemd_linger_status, get_systemd_unit_path, is_linux,
            user_systemd_unit_dir)
        from hermes_cli.service_manager import detect_service_manager
    except Exception as e:
        return check_warn("网关服务常驻（linger）", f"（导入网关辅助函数失败：{e}）")
    if not is_linux() or detect_service_manager() == "s6":
        return
    host_unit = user_systemd_unit_dir() / f"{_SERVICE_BASE}.service"
    if not (get_systemd_unit_path().exists() or host_unit.exists()):
        return
    _section("网关服务")
    linger_enabled, linger_detail = get_systemd_linger_status()
    if linger_enabled is None:
        return check_warn("没能确认 systemd linger 状态", f"（{linger_detail}）")
    if not check_bool(linger_enabled, ("systemd linger 已开启", "（退出登录后网关服务仍在跑）"),
                      ("systemd linger 未开启", "（退出登录后网关可能会停）")):
        check_info("执行：sudo loginctl enable-linger $USER")
        issues.append("让网关用户服务常驻：sudo loginctl enable-linger $USER")


_TCC_CDHASH_DETAIL = (
    "the desktop bundle's designated requirement is cdhash-pinned (pre-#73681 build) — rebuilds invalidate "
    "all permission grants. Run `coco update` to get the stable identifier-pinned signing identity, "
    "then re-grant permissions once.")
_TCC_STABLE_DETAIL = {
    True: "(certificate-anchored DR; grants survive rebuilds)",
    False: "(identifier-pinned DR; grants survive rebuilds — for the strongest anchor, see `hermes desktop --setup-tcc-identity`)",
}


def check_macos_tcc_grants() -> None:
    """Check macOS TCC grant persistence for a locally-built desktop bundle; silent on non-macOS / no bundle.

    TCC keys grants to the app's designated requirement (DR). A cdhash-pinned ad-hoc DR changes on every
    rebuild, so grants silently stop matching while the Settings toggle stays ON; identifier-pinned builds
    survive rebuilds, but grants made to older binaries stay stale until re-granted once. TCC.db needs Full
    Disk Access, so the DR string is the only readable signal (a proxy for the signing class, not DR wording).

    See #86385.
    """
    app = _desktop_app_bundle() if sys.platform == "darwin" else None
    if app is None:
        return
    dr = _macos_desktop_dr(app)
    if not dr:
        return check_warn("macOS 权限授予检查", "（读不到桌面版的签名要求）")
    if "cdhash" in dr.lower():
        return check_warn("macOS 的权限授予会在每次更新后重置", _TCC_CDHASH_DETAIL)
    # --setup-tcc-identity or notarized build (certificate-anchored) is the strongest anchor.
    check_ok("macOS 的签名身份稳定", _TCC_STABLE_DETAIL["certificate" in dr.lower()])
    check_info("如果 macOS 还是反复要权限（开关显示已开）：存的授权过期了 —— 执行 "
               "`tccutil reset ScreenCapture com.nousresearch.hermes` (repeat per affected service), toggle it ON in "
               "System Settings, then fully quit & relaunch Hermes once.")


def _desktop_app_bundle() -> Path | None:
    """Locate the locally-built desktop bundle (``apps/desktop/release/mac-<arch>/Hermes.app``), newest first.

    The only layout whose ad-hoc re-signed bundle can invalidate TCC grants. ``/Applications/Hermes.app`` is
    deliberately not probed: it is the separately-signed, certificate-anchored Hermes-Setup launcher.
    """
    release_dir = Path(__file__).resolve().parents[1] / "apps" / "desktop" / "release"
    candidates = [p for p in release_dir.glob("mac*/Hermes.app") if p.is_dir()]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def _macos_desktop_dr(app: Path) -> str | None:
    """Return the bundle's designated requirement string, or None on failure (a hanging codesign must never abort doctor)."""
    codesign = shutil.which("codesign")
    try:
        proc = subprocess.run([codesign, "-d", "--requirements", "-", str(app)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15) if codesign else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return None if proc is None or proc.returncode != 0 else (proc.stdout or "") + (proc.stderr or "")


def check_macos_tcc_anchor(should_fix: bool = False) -> None:
    """Report (and with --fix install) the dylib-complete TCC anchor; silent on non-macOS / non-uv interpreters.
    Never raises. Install is gated by the module's pre-install boot probe, so ``--fix`` cannot brick the CLI.

    See #95596.
    """
    with warn_on_error("macOS TCC anchor check failed"):
        from hermes_cli import macos_tcc_anchor as tcc
        status, detail = tcc.tcc_anchor_state()
        if status == "skip":
            return
        if status == "active":
            return check_ok("macOS 授权锚点生效", f"({detail})")
        anchored = tcc.ensure_tcc_anchor() if should_fix else None
        if anchored is not None:
            return check_ok("macOS 授权锚点已装好", f"({anchored})")
        check_warn("macOS 授权锚点缺失" if status == "missing" else "macOS 授权锚点是旧的", f"({detail})")


def check_macos_full_disk_access() -> None:
    """One-grant guidance: Full Disk Access silences every per-folder TCC prompt. Silent on non-macOS.

    Probe: listdir of ``~/Library/Application Support/com.apple.TCC`` — FDA-gated, and probing it does NOT
    trigger a prompt (the TCC dir just returns EPERM). A missing dir / other error is indeterminate: stay silent.

    macOS TCC prompts per-category (Desktop, then Downloads, then Documents, ...), so first-run agents
    drip-feed permission dialogs as they touch each folder. ONE Full Disk Access grant covers all of them,
    permanently — and with the stable signing identities now in place (#73681/#95091/#95131), it survives
    updates too. This check probes whether the terminal context already has FDA and, when it doesn't, prints
    the exact one-switch setup with the System Settings deep link.
    """
    if sys.platform != "darwin":
        return
    try:
        os.listdir(Path.home() / "Library" / "Application Support" / "com.apple.TCC")
    except PermissionError:
        check_info("一个开关能关掉 macOS 的所有目录弹窗：给终端 App 开「完全磁盘访问权限」，Coco "
                   "will never trip per-folder dialogs (Desktop/Downloads/Documents/...) again. Open: System Settings → "
                   "Privacy & Security → Full Disk Access — or run:\n"
                   "      open \"x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles\"\n"
                   "    then enable your terminal (and Hermes.app if you use Desktop), and restart them once. "
                   "With Hermes' stable signing identities the grant survives every update.")
    except OSError:
        pass  # missing dir / other error: indeterminate, stay silent
    else:
        check_ok("macOS 完全磁盘访问权限已授予", "（不会再逐个目录弹权限）")


@doctor_check("Security advisory check failed: {e}")
def _check_security_advisories(should_fix: bool, f: Finding) -> None:
    """Compromised-package advisories, funnelled into manual issues; a bug here must never block the rest of doctor."""
    from hermes_cli.security_advisories import detect_compromised, filter_unacked, full_remediation_text, get_acked_ids
    all_hits = detect_compromised()
    fresh_hits = filter_unacked(all_hits)
    if not fresh_hits:
        return check_ok("没有需要处理的安全公告")
    for hit in fresh_hits:
        # Fail row + remediation text indented under it as one section; also into the summary action list.
        _fail_and_issue(f"{hit.advisory.title}", f"({hit.package}=={hit.installed_version})",
                        f"处理安全公告 {hit.advisory.id}：卸掉 {hit.package}=={hit.installed_version} "
                        f"并轮换凭据，然后跑 `coco doctor --ack {hit.advisory.id}`。", f.manual_issues)
        for line in full_remediation_text(hit):
            print(f"    {color(line, Colors.YELLOW)}" if line else "")
    acked_ids = get_acked_ids()  # acked-but-still-installed stays visible
    for h in all_hits:
        if h.advisory.id in acked_ids:
            check_warn(f"仍处于安装状态（公告 {h.advisory.id} 已确认）")


@doctor_check()
def _check_python_environment(should_fix: bool, f: Finding) -> None:
    """Interpreter, linked SQLite, venv, macOS TCC anchors/FDA/grants, version-file drift."""
    v, label = sys.version_info, f"Python {'.'.join(map(str, sys.version_info[:3]))}"
    if v < (3, 8):
        _fail_and_issue(label, "（需要 3.10 以上）", "把 Python 升到 3.10 以上", f.issues)
    elif check_bool(v >= (3, 10), label, (label, "(建议 3.10+)")) and v < (3, 11):
        check_warn("RL 训练类工具建议 Python 3.11+（tinker 要求 >= 3.11）")
    # Linked SQLite: version + source id matter independently of the Python minor (uv's
    # python-build-standalone can keep a vulnerable SQLite across upgrades).
    with warn_on_error("SQLite version probe failed: {e}", ""):
        import sqlite3
        from hermes_state_wal import is_sqlite_wal_reset_vulnerable, sqlite_source_id
        src = sqlite_source_id()
        # Warn-only: Hermes already refuses WAL on fresh DBs and runtime repair is best-effort.
        check_bool(not is_sqlite_wal_reset_vulnerable(), f"SQLite {sqlite3.sqlite_version}",
                   (f"SQLite {sqlite3.sqlite_version}（有 WAL 重置风险）", _sqlite_upgrade_hint()))
        if src:
            check_info(f"SQLite 源码版本号：{(src[:48] + '…') if len(src) > 48 else src}")
        _report_database_journal_modes()
    check_bool(sys.prefix != sys.base_prefix, "虚拟环境正常", ("不在虚拟环境里", "（推荐用虚拟环境）"))
    # macOS TCC interpreter anchor (#95596): dylib-complete re-land of the mechanism reverted in #95563.
    # Silent on non-macOS.
    check_macos_tcc_anchor(should_fix=should_fix)
    # macOS Full Disk Access (issue #52010 follow-up): one grant silences every per-folder prompt
    # permanently. Silent on non-macOS.
    check_macos_full_disk_access()
    _check_version_consistency(f.issues)
    # macOS TCC grant persistence (issue #86385): a locally-built desktop bundle whose DR is cdhash-pinned
    # loses every permission grant on each rebuild; a post-#73681 identifier-pinned DR survives, but grants
    # made to older binaries stay stale (toggle shows ON while macOS re-prompts).
    check_macos_tcc_grants()


@doctor_check()
def _check_certificates(should_fix: bool, f: Finding) -> None:
    check_certificates(should_fix=should_fix, issues=f.manual_issues)


# (import name, display name, optional)
_PACKAGES = (
    ("openai", "OpenAI SDK", False), ("rich", "Rich（终端界面）", False), ("dotenv", "python-dotenv", False),
    ("yaml", "PyYAML", False), ("httpx", "HTTPX", False),
    ("croniter", "Croniter（cron 表达式）", True), ("telegram", "python-telegram-bot", True), ("discord", "discord.py", True),
)


@doctor_check()
def _check_required_packages(should_fix: bool, f: Finding) -> None:
    for module, name, optional in _PACKAGES:
        try:
            __import__(module)
            check_ok(name, "（可选）" if optional else "")
        except ImportError:
            if optional:
                check_warn(name, "（可选，未安装）")
            else:
                _fail_and_issue(name, "(missing)", f"装 {name}：{_python_install_cmd()} {module}", f.issues)


@doctor_check()
def _check_gateway_supervision(should_fix: bool, f: Finding) -> None:
    _check_gateway_service_linger(f.issues)
    _check_s6_supervision(f.issues)


@doctor_check()
def _check_command_installation(should_fix: bool, f: Finding) -> None:
    """Venv entry point and the ~/.local/bin (or $PREFIX/bin) symlink; skipped on Windows."""
    from hermes_cli.doctor import PROJECT_ROOT
    if sys.platform == "win32":
        return
    _section("命令入口")
    venv_bin = next((c for c in (PROJECT_ROOT / n / "bin" / "hermes" for n in ("venv", ".venv")) if c.exists()), None)
    if venv_bin is None:
        check_warn("虚拟环境入口缺失", "（venv/bin/ 或 .venv/bin/ 里没有 hermes —— 用 pip install -e '.[all]' 重装）")
        return f.manual_issues.append(f"重装入口：cd {PROJECT_ROOT} && source venv/bin/activate && pip install -e '.[all]'")
    check_ok(f"虚拟环境入口在位（{venv_bin.relative_to(PROJECT_ROOT)}）")
    # Expected command link directory (mirrors install.sh logic).
    prefix = os.environ.get("PREFIX", "")
    termux = prefix and (os.environ.get("TERMUX_VERSION") or "com.termux/files/usr" in prefix)
    link_dir, display = (Path(prefix) / "bin", "$PREFIX/bin") if termux else (Path.home() / ".local" / "bin", "~/.local/bin")
    # Coco 只对外暴露 coco 命令（安装与更新会移除 hermes 软链），所以这里查的是 coco 链接。
    # 照官方查 hermes 会让每次体检都报一个假问题，`--fix` 还会把 hermes 命令装回来。
    _coco_only = (PROJECT_ROOT / "scripts" / "coco.sh").exists()
    link_name = "coco" if _coco_only else "hermes"
    link = link_dir / link_name
    if link.is_symlink():
        target, expected = link.resolve(), venv_bin.resolve()
        if target == expected:
            return check_ok(f"{display}/{link_name} → 指向正确")
        check_warn(f"{display}/{link_name} 指向的目标不对", f"（→ {target}，应该是 → {expected}）")
        if not should_fix:
            return f.issues.append(f"{display}/{link_name} 是坏链接 —— 跑「coco doctor --fix」")
        link.unlink()
        verb = "已修复"
    elif link.exists():  # regular file (wrapper script), not a symlink
        return check_ok(f"{display}/{link_name} 存在（是普通文件）")
    else:
        check_fail(f"找不到 {display}/{link_name}", f"({link_name} 命令在虚拟环境外可能不可用)")
        if not should_fix:
            return f.issues.append(f"缺 {display}/{link_name} 链接 —— 跑「coco doctor --fix」")
        link_dir.mkdir(parents=True, exist_ok=True)
        verb = "已创建"
    link.symlink_to(venv_bin)
    check_ok(f"{verb}链接：{display}/{link_name} → {venv_bin}")
    f.fixed += 1
    if verb == "已创建" and str(link_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        check_warn(f"{display} 不在你的 PATH 里", "（加到你的 shell 配置里：export PATH=\"$HOME/.local/bin:$PATH\"）")
        f.manual_issues.append(f"把 {display} 加到 PATH 里")
