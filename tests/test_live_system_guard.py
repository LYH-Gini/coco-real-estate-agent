"""Regression tests for the conftest live-system guard's argv handling.

The guard must treat only argv[0] of a list/tuple command as the executable
(arguments are data: a file named ``skill`` is not the ``skill`` binary),
while still scanning every token of wrapper invocations like ``bash -c``.
All blocked-case commands use patterns that match no real process, so a
guard regression cannot kill anything.
"""

import os
import subprocess

import pytest


def test_argv_arguments_are_not_treated_as_executables(tmp_path):
    """A file argument whose basename is a killer name must not trip the
    guard (the path contains "hermes" via the pytest tmp root)."""
    target = tmp_path / "skill"
    target.write_text("just a filename\n")
    result = subprocess.run(["cat", str(target)], capture_output=True, text=True)
    assert result.returncode == 0
    assert "just a filename" in result.stdout


def test_direct_killer_argv_is_still_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["pkill", "-f", "hermes-guard-regression-nomatch"])


def test_wrapped_killer_command_is_still_blocked():
    """argv[0]-only scanning must not exempt commands hidden behind a
    shell wrapper."""
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["bash", "-c", "pkill -f hermes-guard-regression-nomatch"])


def test_env_wrapped_killer_command_is_still_blocked():
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["env", "GUARD_TEST=1", "pkill", "-f", "hermes-guard-regression-nomatch"])


def test_gateway_start_inside_a_container_exec_is_not_blocked():
    """``docker exec <ctr> hermes gateway start`` launches the gateway INSIDE the container,
    where it cannot reach the host's systemd unit or webhook port; tests/docker/ depends on it.
    The binary is a stub so the argv stays exact without needing a Docker daemon."""
    import os
    import stat

    stub_dir = os.path.join(os.environ["HERMES_HOME"], "stub-bin")
    os.makedirs(stub_dir, exist_ok=True)
    stub = os.path.join(stub_dir, "docker")
    with open(stub, "w") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(stub, os.stat(stub).st_mode | stat.S_IXUSR)
    result = subprocess.run(
        [stub, "exec", "-u", "hermes", "ctr", "sh", "-c", "hermes -p prof gateway start"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_gateway_start_on_the_host_is_still_blocked():
    with pytest.raises(RuntimeError, match="REAL.*gateway runtime"):
        subprocess.run(["python", "-m", "hermes_cli.main", "gateway", "start"])


def test_fixture_script_under_a_hermes_named_tmp_path_is_not_blocked(tmp_path):
    """A fixture script whose *path* contains "hermes" (pytest's tmp root is
    ``hermes-pytest-tmproot-*``) must not be mistaken for ``hermes update`` —
    only program tokens count, arguments are data."""
    root = tmp_path / "hermes-pytest-tmproot-sim" / "fake-coco"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "update.sh").write_text("#!/usr/bin/env bash\necho FAKE-UPDATE ran\n",
                                                encoding="utf-8")
    (root / "scripts" / "coco.sh").write_text(
        '#!/usr/bin/env bash\nexec bash "$(dirname "$0")/update.sh"\n', encoding="utf-8")
    result = subprocess.run(["bash", str(root / "scripts" / "coco.sh"), "update"],
                            capture_output=True, text=True)
    assert "live-system guard" not in (result.stderr or "")
    assert "FAKE-UPDATE ran" in result.stdout, result.stdout


def _stub_path(tmp_path, names):
    """PATH whose ``names`` are harmless stubs: if the guard ever regresses the
    stub prints instead of touching the developer's real install."""
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir(exist_ok=True)
    for name in names:
        p = stub_dir / name
        p.write_text("#!/bin/sh\necho STUB-RAN\n", encoding="utf-8")
        p.chmod(0o755)
    return f"{stub_dir}:{os.environ.get('PATH', '')}"


def test_hermes_update_argv_is_still_blocked(tmp_path):
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["hermes", "update"], env=dict(os.environ, PATH=_stub_path(tmp_path, ["hermes"])),
                       capture_output=True, text=True)


def test_wrapped_hermes_update_is_still_blocked(tmp_path):
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["bash", "-c", "setsid hermes update"],
                       env=dict(os.environ, PATH=_stub_path(tmp_path, ["hermes"])),
                       capture_output=True, text=True)


def test_env_wrapped_hermes_update_is_still_blocked(tmp_path):
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["env", "GUARD_TEST=1", "hermes", "update"],
                       env=dict(os.environ, PATH=_stub_path(tmp_path, ["hermes"])),
                       capture_output=True, text=True)


def test_python_module_hermes_update_is_still_blocked(tmp_path):
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run(["python", "-m", "hermes_cli.main", "update", "--gateway"],
                       env=dict(os.environ, PATH=_stub_path(tmp_path, ["python", "python3"])),
                       capture_output=True, text=True)


def test_venv_hermes_update_is_still_blocked(tmp_path):
    venv_hermes = tmp_path / "app" / ".venv" / "bin" / "hermes"
    venv_hermes.parent.mkdir(parents=True)
    venv_hermes.write_text("#!/bin/sh\necho STUB-RAN\n", encoding="utf-8")
    venv_hermes.chmod(0o755)
    with pytest.raises(RuntimeError, match="live-system guard"):
        subprocess.run([str(venv_hermes), "update"], capture_output=True, text=True)
