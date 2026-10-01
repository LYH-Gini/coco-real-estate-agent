"""Terminal-backend setup wizard (local/docker/singularity/modal/daytona/vercel/ssh/plugin).
setup.py names are resolved through the module object so test patches on ``hermes_cli.setup.<name>``
take effect; setup.py re-exports the public entry points."""

import json
import logging
import os
import shutil
import sys
from pathlib import Path
from tools import tool_backend_helpers
from tools.environments.docker import docker_runtime_name, find_docker
from hermes_cli import nous_subscription

logger = logging.getLogger("hermes_cli.setup")

_SANDBOX_IMAGE = "nikolaik/python-nodejs:python3.11-nodejs20"
_RUN_KW = dict(capture_output=True, text=True, encoding="utf-8", errors="replace")


def _prompt_vercel_sandbox_settings(config: dict):
    """Prompt for Vercel Sandbox settings without exposing unsupported disk sizing."""
    terminal = config.setdefault("terminal", {})
    _setup._info(None, "Vercel Sandbox 设置：", "  文件持久化靠 Vercel 快照。",
                 "  快照只恢复文件；沙箱重建后原来的进程不会继续跑。")
    from tools.terminal_tool_backends import _SUPPORTED_VERCEL_RUNTIMES
    current_runtime = terminal.get("vercel_runtime") or "node24"
    supported_label = ", ".join(_SUPPORTED_VERCEL_RUNTIMES)
    runtime = _setup.prompt(f"  运行时（{supported_label}）", current_runtime).strip() or current_runtime
    if runtime not in _SUPPORTED_VERCEL_RUNTIMES:
        _setup.print_warning(f"不支持的 Vercel 运行时「{runtime}」，保持 {current_runtime}。")
        runtime = current_runtime if current_runtime in _SUPPORTED_VERCEL_RUNTIMES else "node24"
    terminal["vercel_runtime"] = runtime
    _setup.save_env_value("TERMINAL_VERCEL_RUNTIME", runtime)
    persist_label = "yes" if terminal.get("container_persistent", True) else "no"
    persist = _setup.prompt("  要不要用快照持久化文件？（yes/no）", persist_label).lower()
    terminal["container_persistent"] = persist in {"yes", "true", "y", "1"}
    # (key, prompt label, default, parser) — unparseable input leaves the value untouched.
    for key, label, default, parse in (
        ("container_cpu", "  CPU cores", 1, float),
        ("container_memory", "  Memory in MB (5120 = 5GB)", 5120, int)):
        try:
            terminal[key] = parse(_setup.prompt(label, str(terminal.get(key, default))))
        except ValueError:
            pass
    if terminal.get("container_disk", 51200) not in {0, 51200}:
        _setup.print_warning(
            "Vercel Sandbox 不支持自定义磁盘大小；已把 container_disk 重置为 51200。")
    terminal["container_disk"] = 51200
    _setup._info(None, "Vercel 鉴权：", "  用一个长期有效的 Vercel access token，外加 project/team ID。")
    linked = _read_nearest_vercel_project()
    if linked:
        _setup.print_info("  在最近的 .vercel/project.json 里找到了默认值。")
    _setup.remove_env_value("VERCEL_OIDC_TOKEN")
    # (label, env var, linked-project fallback key, secret) — prompted in order, saved when non-empty.
    for label, env_var, linked_key, secret in (
        ("    Vercel access token", "VERCEL_TOKEN", None, True),
        ("    Vercel project ID", "VERCEL_PROJECT_ID", "projectId", False),
        ("    Vercel team ID", "VERCEL_TEAM_ID", "orgId", False)):
        default = _setup.get_env_value(env_var) or (linked.get(linked_key, "") if linked_key else "")
        value = _setup.prompt(label, default, password=secret)
        if value:
            _setup.save_env_value(env_var, value)


def _read_nearest_vercel_project(start: Path | None = None) -> dict[str, str]:
    """Read project/team defaults from the nearest Vercel link file."""
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        project_file = directory / ".vercel" / "project.json"
        if not project_file.exists():
            continue
        try:
            data = json.loads(project_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {key: data[key] for key in ("projectId", "orgId")
                if isinstance(data.get(key), str) and data[key].strip()}
    return {}


def _prompt_secret_env(label: str, env_var: str, *, confirm_msg: str = "") -> None:
    """Prompt for a secret and persist it to .env when non-empty."""
    value = _setup.prompt(label, password=True)
    if value:
        _setup.save_env_value(env_var, value)
        if confirm_msg:
            _setup.print_success(confirm_msg)


def _existing_secret_keeps(env_var: str, label: str, question: str) -> bool:
    """True when ``env_var`` is already set and the user declines to update it."""
    if not _setup.get_env_value(env_var):
        return False
    _setup.print_info(f"  {label}：已经配好了")
    return not _setup.prompt_yes_no(question, False)


def _pip_install_vercel(package):
    """uv when Hermes has one ($HERMES_HOME/bin is never on PATH, so which() misses it and
    bootstrapping mid-wizard is fine), else pip — a `uv venv` venv may not even have pip."""
    import subprocess
    from hermes_cli.managed_uv import ensure_uv
    uv_bin = ensure_uv()
    cmd = ([uv_bin, "pip", "install", "--python", sys.executable, package] if uv_bin
           else [sys.executable, "-m", "pip", "install", package])
    return subprocess.run(cmd, **_RUN_KW)


def _ensure_sdk(package: str, manual_hint: str, *, show_stderr: bool = False, install=None) -> None:
    """Import *package*; if missing, install it (default: the venv pip ladder)."""
    try:
        __import__(package)
    except ImportError:
        _setup.print_info(f"正在安装 {package} SDK…")
        if install is None:
            from hermes_cli.tools_config import _pip_install
            install = lambda pkg: _pip_install([pkg])  # noqa: E731
        result = install(package)
        if result.returncode == 0:
            _setup.print_success(f"{package} SDK 已安装")
        else:
            _setup.print_warning(f"安装失败 —— 请手动执行：{manual_hint}")
            if show_stderr and result.stderr:
                _setup.print_info(f"  错误：{result.stderr.strip().splitlines()[-1]}")


def _report_binary(found: str | None, missing: str, install_hint: str, found_prefix: str = "已找到： ") -> None:
    if found:
        _setup.print_info(f"{found_prefix}{found}")
    else:
        _setup.print_warning(missing)
        _setup.print_info(install_hint)


def _setup_backend_local(config: dict) -> None:
    _setup.print_success("终端后端：本机")
    _setup.print_info("命令直接在这台机器上跑。")
    # Gateway cwd defaults to home; sudo stays off. Both configurable via `hermes setup terminal`.
    config["terminal"].setdefault("cwd", str(Path.home()))


def _setup_backend_docker(config: dict) -> None:
    _setup.print_success("终端后端：Docker / Podman")
    docker_exe = find_docker()
    _report_binary(docker_exe, "PATH 里没找到 Docker 或 Podman。",
                   "Install Docker: https://docs.docker.com/get-docker/ "
                   "or Podman: https://podman.io/docs/installation",
                   f"{docker_runtime_name(docker_exe)} 已找到： " if docker_exe else "")
    # Image and resource limits use defaults; tune via `hermes setup terminal`.
    config["terminal"].setdefault("docker_image", _SANDBOX_IMAGE)
    _setup._info(None, "Docker 沙箱可以用出口凭据防火墙保护：",
                 "它把沙箱流量经 iron-proxy 转发，容器拿到的是 "
                 "代理令牌、不是真实 API key。",
                 "   目前只支持 Docker；Modal、SSH、Daytona、Singularity 还没接。")
    if _setup.prompt_yes_no("  要给 Docker 沙箱开出口防火墙吗？", False):
        proxy_cfg = config.setdefault("proxy", {})
        proxy_cfg["enabled"] = True
        proxy_cfg.setdefault("enforce_on_docker", True)
        _setup.print_success("配置里已开启出口防火墙")
        _setup.print_info(
            "跑「coco cli egress setup」再跑「coco cli egress start」，就能签发令牌并启动代理。")
    else:
        _setup.print_info("跳过出口防火墙。以后想开，跑「coco cli egress setup」。")


def _setup_backend_singularity(config: dict) -> None:
    _setup.print_success("终端后端：Singularity/Apptainer")
    _report_binary(shutil.which("apptainer") or shutil.which("singularity"),
                   "Singularity/Apptainer not found in PATH!",
                   "Install: https://apptainer.org/docs/admin/main/installation.html")
    config["terminal"].setdefault("singularity_image", f"docker://{_SANDBOX_IMAGE}")


def _setup_backend_modal(config: dict) -> None:
    _setup.print_success("终端后端：Modal")
    _setup.print_info("无服务器云沙箱，每个会话一个独立容器。")
    from tools.managed_tool_gateway import is_managed_tool_gateway_ready
    from tools.tool_backend_helpers import normalize_modal_mode
    managed_modal_available = bool(
        tool_backend_helpers.managed_nous_tools_enabled()
        and nous_subscription.get_nous_subscription_features(config).nous_auth_present
        and is_managed_tool_gateway_ready("modal"))
    modal_mode = normalize_modal_mode(_setup.cfg_get(config, "terminal", "modal_mode"))
    use_managed_modal = False
    if managed_modal_available:
        # Default to the configured mode; when unset, to "direct" only if Modal creds exist.
        default_idx = {"managed": 0, "direct": 1}.get(modal_mode, 1 if _setup.get_env_value("MODAL_TOKEN_ID") else 0)
        use_managed_modal = _setup.prompt_choice(
            "选择 Modal 执行怎么计费：",
            ["用我的 Nous 订阅", "用我自己的 Modal 账号"], default_idx) == 0
    if use_managed_modal:
        config["terminal"]["modal_mode"] = "managed"
        _setup.print_info("Modal 执行会走 Nous 托管网关，费用记在你的订阅上。")
        if _setup.get_env_value("MODAL_TOKEN_ID") or _setup.get_env_value("MODAL_TOKEN_SECRET"):
            _setup.print_info(
                "你配了直连 Modal 的凭据，但这个后端被固定为托管模式。")
        return
    config["terminal"]["modal_mode"] = "direct"
    _setup.print_info("需要一个 Modal 账号：https://modal.com")
    _ensure_sdk("modal", "uv pip install modal")
    _setup._info(None, "Modal 鉴权：", "  在这里拿 token：https://modal.com/settings")
    if _existing_secret_keeps("MODAL_TOKEN_ID", "Modal token", "  Update Modal credentials?"):
        return
    _prompt_secret_env("    Modal Token ID", "MODAL_TOKEN_ID")
    _prompt_secret_env("    Modal Token Secret", "MODAL_TOKEN_SECRET")


def _setup_backend_daytona(config: dict) -> None:
    _setup.print_success("终端后端：Daytona")
    _setup._info("带持久化的云开发环境。",
                 "每个会话一个专属沙箱，文件会留档。",
                 "注册地址：https://daytona.io")
    _ensure_sdk("daytona", "uv pip install daytona", show_stderr=True)
    print()
    had_key = bool(_setup.get_env_value("DAYTONA_API_KEY"))
    if not _existing_secret_keeps("DAYTONA_API_KEY", "Daytona API key", "  Update API key?"):
        _prompt_secret_env("    Daytona API key", "DAYTONA_API_KEY",
                           confirm_msg="    已更新" if had_key else "    已配置")
    config["terminal"].setdefault("daytona_image", _SANDBOX_IMAGE)


def _setup_backend_vercel(config: dict) -> None:
    _setup.print_success("终端后端：Vercel Sandbox")
    _setup._info("云上 microVM 沙箱，文件靠快照持久化。",
                 "需要装可选 SDK：pip install 'hermes-agent[vercel]'")
    _ensure_sdk("vercel", "pip install 'hermes-agent[vercel]'", show_stderr=True, install=_pip_install_vercel)
    _prompt_vercel_sandbox_settings(config)


def _setup_backend_ssh(config: dict) -> None:
    _setup.print_success("终端后端：SSH")
    _setup.print_info("通过 SSH 在远程机器上跑命令。")
    # (label, env var, fallback default when .env is empty); the port is only saved when not 22.
    fields = (
        ("  SSH 主机（主机名或 IP）", "TERMINAL_SSH_HOST", ""),
        ("  SSH user", "TERMINAL_SSH_USER", os.getenv("USER", "")),
        ("  SSH port", "TERMINAL_SSH_PORT", "22"),
        ("  SSH private key path", "TERMINAL_SSH_KEY", str(Path.home() / ".ssh" / "id_rsa")))
    values = []
    for label, env_var, default in fields:
        value = _setup.prompt(label, _setup.get_env_value(env_var) or default)
        values.append(value)
        if value and (env_var != "TERMINAL_SSH_PORT" or value != "22"):
            _setup.save_env_value(env_var, value)
        elif env_var == "TERMINAL_SSH_PORT" and value == "22":
            # Answering the default must undo a previously saved non-default port:
            # skipping the save alone would leave the stale value in .env.
            _setup.remove_env_value(env_var)
    host, user, port, ssh_key = values
    if host and _setup.prompt_yes_no("  要测一下 SSH 连接吗？", True):
        _setup.print_info("  正在测试连接…")
        import subprocess
        ssh_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", *(["-i", ssh_key] if ssh_key else []),
                   *(["-p", port] if port and port != "22" else []), f"{user}@{host}" if user else host, "echo ok"]
        result = subprocess.run(ssh_cmd, timeout=10, **_RUN_KW)
        if result.returncode == 0:
            _setup.print_success("  SSH 连接成功。")
        else:
            _setup.print_warning(f"  SSH 连接失败：{result.stderr.strip()}")
            _setup.print_info("  检查一下 SSH 密钥和主机设置。")


def _setup_backend_plugin(config: dict, backend: str) -> None:
    try:
        from agent.terminal_env_registry import get_provider
        provider = get_provider(backend)
        _setup.print_success(f"Terminal backend: {provider.display_name}")
        for line in provider.setup_instructions():
            _setup.print_info(line)
        provider.post_setup()
    except Exception as exc:
        _setup.print_warning(f"后端插件的配置钩子失败了：{exc}")


_BUILTIN_TERMINAL_BACKENDS = [
    ("local", "本机 —— 直接在这台机器上跑（默认）"),
    ("docker", "Docker/Podman - isolated container with configurable resources"),
    ("modal", "Modal - serverless cloud sandbox"), ("ssh", "SSH - run on a remote machine"),
    ("daytona", "Daytona - persistent cloud development environment"),
    ("vercel_sandbox", "Vercel Sandbox - cloud microVM with snapshot filesystem persistence")]
_TERMINAL_BACKEND_SETUP = {
    "local": _setup_backend_local, "docker": _setup_backend_docker, "singularity": _setup_backend_singularity,
    "modal": _setup_backend_modal, "daytona": _setup_backend_daytona, "vercel_sandbox": _setup_backend_vercel,
    "ssh": _setup_backend_ssh}
# Backend -> env var mirrored from config after setup (config.yaml is the source of truth, but
# terminal_tool reads these from .env).
_BACKEND_ENV_MIRROR = {"modal": ("TERMINAL_MODAL_MODE", "modal_mode", "auto"),
                       "vercel_sandbox": ("TERMINAL_VERCEL_RUNTIME", "vercel_runtime", "node24")}


def setup_terminal_backend(config: dict):
    """Configure the terminal execution backend."""
    import platform as _platform
    _setup.print_header("终端后端")
    _setup._info("选择 Coco 在哪里跑命令和代码。",
                 "这会影响工具执行、文件访问和隔离性。",
                 f"   文档：{_setup._DOCS_BASE}/user-guide/configuration#terminal-backend-configuration", None)
    current_backend = _setup.cfg_get(config, "terminal", "backend", default="local")
    backends = list(_BUILTIN_TERMINAL_BACKENDS)
    if _platform.system() == "Linux":
        backends.append(("singularity", "Singularity/Apptainer - HPC-friendly container"))
    # Plugin-registered backends (~/.hermes/plugins/). Fail-soft: a broken plugin must not take
    # the wizard down.
    plugin_backend_names = []
    try:
        from hermes_cli.plugins import discover_plugins
        discover_plugins()  # idempotent — plugin state may not be loaded yet
        from agent.terminal_env_registry import list_providers
        for provider in list_providers():
            pname = provider.name.strip().lower()
            backends.append((pname, f"{provider.display_name} - {provider.description}"))
            plugin_backend_names.append(pname)
    except Exception:
        pass
    terminal_choices = [label for _, label in backends] + [f"保持当前（{current_backend}）"]
    terminal_idx = _setup.prompt_choice("选择终端后端：", terminal_choices, len(backends))
    if terminal_idx == len(backends):
        _setup.print_info(f"保持当前后端：{current_backend}")
        return
    selected_backend = backends[terminal_idx][0] if 0 <= terminal_idx < len(backends) else None
    config.setdefault("terminal", {})["backend"] = selected_backend
    # Plugin names shadow only the ssh built-in (dispatch order of the original chain).
    handler = _TERMINAL_BACKEND_SETUP.get(selected_backend)
    if handler is not None and (selected_backend != "ssh" or selected_backend not in plugin_backend_names):
        handler(config)
    elif selected_backend in plugin_backend_names:
        _setup_backend_plugin(config, selected_backend)
    _setup.save_env_value("TERMINAL_ENV", selected_backend)
    if selected_backend in _BACKEND_ENV_MIRROR:
        env_var, key, default = _BACKEND_ENV_MIRROR[selected_backend]
        _setup.save_env_value(env_var, config["terminal"].get(key, default))
    _setup.save_config(config)
    print()
    _setup.print_success(f"终端后端已设为：{selected_backend}")


import hermes_cli.setup as _setup  # noqa: E402  (bottom: hermes_cli.setup imports this module)
