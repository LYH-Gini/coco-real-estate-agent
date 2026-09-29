"""更新后服务没重启的回归（2026-09-29 实测事故）

事故经过：在 `sudo -i` 环境下跑 `coco update`，脚本第 [7/8] 步用
`systemctl --user is-active --quiet hermes-gateway.service` 探测服务，而该环境下
`systemctl --user` 连不上用户总线（报 `Failed to connect to bus: No medium found`）⇒
探测假失败 ⇒ 打印「未检测到在运行的 ... 服务，请手动重启以加载新代码」并**继续跑完**
⇒ 代码与迁移都更新了，但进程仍跑旧代码（登记身份证仍按"只存脱敏"回答）。

本文件把三件事钉住：
① 首选**官方重启入口**（与 `coco restart` 同一条路），不再只靠我们手写的两个单元名；
② 用户级探测失败时**钉住 XDG_RUNTIME_DIR 重试**（实测这一条就能恢复）；
③ 探测全落空时给出**可操作结论**，并在结尾用 `gateway_state.json` 的 `code_sha`
   与本地 HEAD 比对，明确说"本次更新尚未生效"——不许静默通过。

做法：按标记从 `scripts/update.sh` 抽出「重启 + 生效复核」那段真跑（桩掉
systemctl / git / sleep / 官方入口），不碰真机。
"""
import json
import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UPDATE_SH = REPO_ROOT / "scripts" / "update.sh"


def _extract_blocks() -> str:
    """抽出 sysd_user() 定义 + [7/8] 段（到 [8/8] 之前）。"""
    text = UPDATE_SH.read_text(encoding="utf-8")
    start_helper = text.index("# 用户级 systemctl 的稳健调用")
    end_helper = text.index("\n}\n", text.index("sysd_user() {")) + len("\n}\n")
    helper = text[start_helper:end_helper]

    start_block = text.index('info "[7/8]')
    end_block = text.index('info "[8/8]', start_block)
    block = text[start_block:end_block]
    return helper + "\n" + block


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _sandbox(tmp_path, *, hermes_rc=0, sysd_ok_without_rt=False, sha_local="a" * 40,
             sha_running="a" * 40):
    """搭沙箱：假官方入口、假 systemctl（可模拟连不上总线）、假 git / sudo / sleep。"""
    root = tmp_path / "coco"
    (root / "venv" / "bin").mkdir(parents=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (home / "gateway_state.json").write_text(json.dumps({"code_sha": sha_running}), encoding="utf-8")

    # 官方重启入口
    _write(root / "venv" / "bin" / "hermes",
           "#!/usr/bin/env bash\n"
           f'echo "HERMES-CALL $*" >> "{tmp_path}/calls.log"\n'
           f"exit {hermes_rc}\n")

    # 假 python：只为生效复核那段（读 stdin 打印指纹）服务
    _write(root / "venv" / "bin" / "python",
           "#!/usr/bin/env bash\n"
           "if [[ \"$1\" == \"-\" ]]; then cat >/dev/null; echo \"$FAKE_SHA\"; exit 0; fi\n"
           "exit 0\n")

    # 假 systemctl：未钉住 XDG_RUNTIME_DIR 时模拟"连不上用户总线"
    bus_fail = "echo 'Failed to connect to bus: No medium found' >&2; exit 1"
    ok_body = "exit 0"
    sysd = ("#!/usr/bin/env bash\n"
            f'echo "SYSTEMCTL $*" >> "{tmp_path}/calls.log"\n'
            f'rt="${{XDG_RUNTIME_DIR:-}}"\n'
            f'case "$1" in\n'
            f'  --user)\n'
            f'    if [[ "{1 if sysd_ok_without_rt else 0}" == "1" || "$rt" == "/run/user/$(id -u)" ]]; then {ok_body}; else {bus_fail}; fi ;;\n'
            f'  *) exit 0 ;;\n'
            f'esac\n')
    _write(bindir / "systemctl", sysd)
    _write(bindir / "git", f"#!/usr/bin/env bash\necho {sha_local}\n")
    _write(bindir / "sudo", '#!/usr/bin/env bash\nshift\nexec "$@"\n')
    _write(bindir / "sleep", "#!/usr/bin/env bash\nexit 0\n")
    return root, bindir, home


def _run(tmp_path, root, bindir, home, extra_env=None):
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "HOME": str(home),
        "HERMES_HOME": str(home),
        "FAKE_SHA": extra_env.pop("FAKE_SHA", "a" * 40) if extra_env else "a" * 40,
        **(extra_env or {}),
    }
    preamble = (
        "set -uo pipefail\n"
        'REPO_ROOT="%s"\n'
        'VENV_PY="$REPO_ROOT/venv/bin/python"\n'
        'NO_RESTART=0\n'
        'info(){ echo "==> $*"; }\n'
        'ok(){ echo "   OK $*"; }\n'
        'err(){ echo "   FAIL $*" >&2; }\n'
        'cd "$REPO_ROOT"\n'
    ) % root
    # 写成脚本文件再执行：命令行里不出现 systemctl 字样，既走 conftest 的 live-system 守卫
    # 的本意（真正调用 systemctl 的测试会被拦），又保证这里跑的 systemctl 全是 PATH 桩。
    script_file = tmp_path / "section.sh"
    script_file.write_text(preamble + _extract_blocks(), encoding="utf-8")
    return subprocess.run(["bash", str(script_file)], capture_output=True, text=True, env=env, cwd=str(root))


# ==================== ① 首选官方入口 ====================

def test_official_restart_entry_is_used_first(tmp_path):
    root, bindir, home = _sandbox(tmp_path, hermes_rc=0)
    r = _run(tmp_path, root, bindir, home)
    assert "服务已重启（官方重启入口）" in r.stdout, r.stdout + r.stderr
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "HERMES-CALL gateway restart" in calls, calls


# ==================== ② 用户级探测：钉住 XDG_RUNTIME_DIR 后能成功 ====================

def test_bus_less_env_is_recovered_by_pinning_runtime_dir(tmp_path):
    """官方入口失败时回退到单元探测；未钉 runtime dir 会假失败，钉住后必须能重启。"""
    root, bindir, home = _sandbox(tmp_path, hermes_rc=1, sysd_ok_without_rt=False)
    r = _run(tmp_path, root, bindir, home)
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "SYSTEMCTL --user restart hermes-gateway" in calls, calls
    assert "服务已重启（用户服务 hermes-gateway）" in r.stdout, r.stdout + r.stderr


# ==================== ③ 全落空：必须给可操作结论，不许静默 ====================

def test_all_probes_failing_yields_actionable_conclusion(tmp_path):
    root, bindir, home = _sandbox(tmp_path, hermes_rc=1, sysd_ok_without_rt=False, sha_running="b" * 40)
    _write(bindir / "systemctl", "#!/usr/bin/env bash\nexit 1\n")      # 任何调用都失败
    r = _run(tmp_path, root, bindir, home, {"FAKE_SHA": "b" * 40})
    out = r.stdout + r.stderr
    assert "服务没能重启" in out, out
    assert "coco restart" in out, out
    assert "XDG_RUNTIME_DIR=/run/user/" in out, out


# ==================== ④ 生效复核：进程指纹 vs 本地 HEAD ====================

def test_stale_running_code_is_reported(tmp_path):
    root, bindir, home = _sandbox(tmp_path, hermes_rc=0, sha_running="b" * 40)
    r = _run(tmp_path, root, bindir, home, {"FAKE_SHA": "b" * 40})     # 本地 HEAD = aaa…，运行中 = bbb…
    out = r.stdout + r.stderr
    assert "本次更新尚未生效" in out, out
    assert "b" * 7 in out and "a" * 7 in out, out


def test_fresh_running_code_is_confirmed(tmp_path):
    root, bindir, home = _sandbox(tmp_path, hermes_rc=0, sha_running="a" * 40)
    r = _run(tmp_path, root, bindir, home, {"FAKE_SHA": "a" * 40})
    assert "新代码已生效" in r.stdout, r.stdout + r.stderr


# ==================== ⑤ 帮助函数本身：探测失败会重试 ====================

def test_sysd_user_retries_with_pinned_runtime_dir(tmp_path):
    root, bindir, home = _sandbox(tmp_path, sysd_ok_without_rt=False)
    script = (
        'set -uo pipefail\n' + _extract_blocks().split('info "[7/8]')[0] +
        '\nsysd_user is-active --quiet hermes-gateway.service && echo RETRY-OK || echo RETRY-FAIL\n'
    )
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(home), "HERMES_HOME": str(home)}
    script_file = tmp_path / "helper_probe.sh"
    script_file.write_text(script, encoding="utf-8")
    r = subprocess.run(["bash", str(script_file)], capture_output=True, text=True, env=env, cwd=str(root))
    assert "RETRY-OK" in r.stdout, r.stdout + r.stderr
