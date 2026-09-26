"""安装布局变更的回归测试（2026-09-21 拍板 ①+②）：

  ① `hermes` 命令不再对外暴露（统一用 coco）——install.sh 不建软链、update.sh 每次清理；
  ② 安装目录 ~/hermes-agent → ~/coco，脚本自定位（按自身位置推导）。

全部用临时假环境，不动真机器。
"""
import os
import re
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _run(cmd, cwd=None, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or REPO_ROOT, env=env)


class TestInstallLayout:
    def test_install_sh_uses_coco_dir(self):
        t = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        assert 'INSTALL_DIR="${COCO_INSTALL_DIR:-$HOME/coco}"' in t, "默认安装目录应为 ~/coco"
        assert 'INSTALL_DIR="$HOME/hermes-agent"' not in t, "不应再默认装到 ~/hermes-agent"

    def test_install_sh_no_longer_exposes_hermes(self):
        t = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        assert "COCO_EXPOSE_HERMES" in t, "应保留显式开关"
        # 无条件创建 hermes 软链的代码必须消失：所有创建动作都要落在这个开关分支里
        for m in re.finditer(r'ln -sf "\$HERMES_BIN" /usr/local/bin/hermes', t):
            head = t[max(0, m.start() - 900):m.start()]
            assert "COCO_EXPOSE_HERMES" in head, "存在未受开关保护的 hermes 软链创建"

    def test_update_sh_removes_hermes_symlink(self):
        t = (SCRIPTS / "update.sh").read_text(encoding="utf-8")
        assert "已移除 hermes 命令入口" in t, "更新时应清掉旧的 hermes 软链"
        assert 'readlink -f "$_hlink"' in t, "只应删除指向本安装目录的软链"

    def test_uninstall_cleans_both_symlinks(self):
        t = (SCRIPTS / "uninstall.sh").read_text(encoding="utf-8")
        assert "/usr/local/bin/hermes" in t and "/usr/local/bin/coco" in t, "卸载应同时清理两个命令入口"


class TestSelfLocating:
    """脚本自定位：不再写死安装目录（以后改目录名不用改代码）"""

    def test_no_hardcoded_install_dir_in_our_scripts(self):
        for rel in ("scripts/healthcheck.py", "scripts/backup_db.py", "tools/real_estate_update_guard.py"):
            t = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert 'expanduser("~/hermes-agent")' not in t, f"{rel} 仍写死旧安装目录"

    def test_derive_repo_root_from_file(self):
        for rel in ("scripts/healthcheck.py", "scripts/backup_db.py", "tools/real_estate_update_guard.py"):
            t = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert "Path(__file__).resolve()" in t, f"{rel} 未按自身位置推导仓库根"

    def test_backup_db_finds_env_db_relative_to_repo(self, tmp_path):
        """把仓库根换成一个临时目录，备份工具应去那里找 .env.db（证明不是写死路径）"""
        import importlib.util
        import shutil

        fake = tmp_path / "fake-coco"
        (fake / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPTS / "backup_db.py", fake / "scripts" / "backup_db.py")
        (fake / ".env.db").write_text("DATABASE_URL=postgresql://u:p@localhost:5432/db\n", encoding="utf-8")
        spec = importlib.util.spec_from_file_location("bd_fake", fake / "scripts" / "backup_db.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert Path(mod._REPO_ROOT) == fake, f"仓库根推导错误：{mod._REPO_ROOT}"
        assert (Path(mod._REPO_ROOT) / ".env.db").exists(), "应在临时目录里找到 .env.db"


class TestDocsUseNewLayout:
    def test_readmes_use_coco_dir(self):
        for rel in ("README.md", "README.zh-CN.md"):
            t = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert "~/coco" in t, f"{rel} 未使用新安装目录"
            assert "~/hermes-agent" not in t, f"{rel} 残留旧安装目录（要求不写老实例迁移说明）"




class TestInstallScriptSymlinkPolicyBehaviour:
    """真跑一遍 install.sh 里的软链逻辑（属功能级核验，不是看字符串）：

    · 默认（不设 COCO_EXPOSE_HERMES）：清掉指向本安装目录的旧 hermes 软链；
    · COCO_EXPOSE_HERMES=1：创建 hermes 软链；
    · 指向别处的同名软链一律不动。
    """

    def _run_block(self, tmp_path, expose_hermes=None):
        import os

        src = (REPO_ROOT / "install.sh").read_text(encoding="utf-8").split("\n")
        start = next(i for i, l in enumerate(src) if l.strip().startswith("# hermes 命令（2026-09-21"))
        # 到该块的收尾 fi（下一处与 start 同缩进的 fi）
        indent = len(src[start]) - len(src[start].lstrip())
        end = next(i for i in range(start + 1, len(src))
                   if src[i].strip() == "fi" and (len(src[i]) - len(src[i].lstrip())) == indent)
        block = "\n".join(src[start:end + 1])

        install_dir = tmp_path / "coco-install"
        (install_dir / "venv" / "bin").mkdir(parents=True)
        (install_dir / "venv" / "bin" / "hermes").write_text("#!/bin/sh\n", encoding="utf-8")
        (install_dir / "venv" / "bin" / "hermes").chmod(0o755)

        fake_bin = tmp_path / "usr-local-bin"
        fake_bin.mkdir()
        fake_home = tmp_path / "home"
        (fake_home / ".local" / "bin").mkdir(parents=True)
        ours = fake_bin / "hermes"
        ours.symlink_to(install_dir / "venv" / "bin" / "hermes")
        theirs = fake_bin / "other-hermes"
        theirs.symlink_to("/bin/true")

        script = tmp_path / "block.sh"
        script.write_text(
            "set -uo pipefail\n"
            'INSTALL_DIR="' + str(install_dir) + '"\n'
            'HOME="' + str(fake_home) + '"\n'
            'ok() { echo "  OK $*"; }\n'
            'warn() { echo "  WARN $*"; }\n'
            + block.replace("/usr/local/bin/hermes", str(ours)).replace("$HOME/.local/bin/hermes", str(fake_home / ".local" / "bin" / "hermes"))
            + "\n",
            encoding="utf-8")
        env = dict(os.environ)
        if expose_hermes is not None:
            env["COCO_EXPOSE_HERMES"] = expose_hermes
        else:
            env.pop("COCO_EXPOSE_HERMES", None)
        r = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)
        return r, ours, theirs

    def test_default_removes_our_symlink(self, tmp_path):
        r, ours, theirs = self._run_block(tmp_path)
        assert r.returncode == 0, r.stdout + r.stderr
        assert not ours.is_symlink(), "默认应当清掉我们自己的 hermes 软链"
        assert theirs.is_symlink(), "指向别处的同名软链不能动"

    def test_expose_flag_creates_symlink(self, tmp_path):
        r, ours, _ = self._run_block(tmp_path, expose_hermes="1")
        assert r.returncode == 0, r.stdout + r.stderr
        assert ours.is_symlink(), "COCO_EXPOSE_HERMES=1 时应创建 hermes 软链"


class TestSelfLocatingBehaviour:
    def test_healthcheck_install_dir_follows_file_location(self, tmp_path):
        import importlib.util
        import shutil

        fake = tmp_path / "renamed-coco"
        (fake / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPTS / "healthcheck.py", fake / "scripts" / "healthcheck.py")
        spec = importlib.util.spec_from_file_location("hc_fake", fake / "scripts" / "healthcheck.py")
        mod = importlib.util.module_from_spec(spec)
        # 只执行到读取常量即可：捕获导入期异常（该模块导入时会定义常量）
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            pass
        assert str(mod.INSTALL_DIR).startswith(str(fake)), f"安装目录未跟随文件位置：{mod.INSTALL_DIR}"


class TestDocsCommandsExist:
    """文档里出现的每个 coco 子命令都必须真实存在（防文档写了不存在的命令）"""

    def test_every_documented_coco_command_exists(self):
        import re

        coco = (SCRIPTS / "coco.sh").read_text(encoding="utf-8")
        # 解析 case 分支标签（如 `version|--version|-v|"")`、`model|setup)`），只保留像命令的 token
        known = set()
        for group in re.findall(r"^\s{2}([^)\n]+)\)", coco, re.M):
            for token in group.split("|"):
                token = token.strip().strip('"')
                if re.fullmatch(r"[a-z][a-z\-]*", token):
                    known.add(token)
        assert {"version", "check", "backup", "update", "uninstall", "model", "setup"} <= known, known
        assert known, "未从 coco.sh 解析出子命令"
        for rel in ("README.md", "README.zh-CN.md"):
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            # 只认"命令位置"的 coco（行首/空白/&&/;/|/反引号之后），避免把 `git -C ~/coco pull` 误判
            for cmd in set(re.findall(r"(?:^|[\s;&|`])coco\s+([a-z][a-z\-]{2,})", text, re.M)):
                assert cmd in known, f"{rel} 写了不存在的命令：coco {cmd}（已知：{sorted(known)}）"
        # 安装脚本结尾打印给用户的命令，同样必须真实存在（2026-09-22 加）
        install = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        for cmd in set(re.findall(r"(?:^|[\s;&|`])(?:echo\s+[\"']*)?coco\s+([a-z][a-z\-]{2,})", install, re.M)):
            assert cmd in known, f"install.sh 打印了不存在的命令：coco {cmd}（已知：{sorted(known)}）"

    def test_printed_commands_are_coco_not_hermes(self):
        """用户照着敲的命令文案必须是 coco 口径（2026-09-22 要求）。

        改这些文案的地方分两类：Coco 自有文件（install.sh / coco.sh）与官方层文件
        （setup_summary.py / gateway/run_inbound_unauthorized.py / update_cmd_config.py）——
        后者会被上游同步覆盖，所以有挂钩点自检守着（check_coco_hooks.py 17/18/19）。
        """
        install = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        for step in ("coco model", "coco setup", "coco restart", "coco pairing approve feishu"):
            assert step in install, f"安装收尾提示应给 coco 命令：{step}"

        summary = (REPO_ROOT / "hermes_cli" / "setup_summary.py").read_text(encoding="utf-8")
        for row in ('("coco setup",', '("coco config",', '("coco status",'):
            assert row in summary, f"设置向导收尾屏应印 coco 命令：{row}"
        assert not re.search(r'\(\s*"hermes ', summary), "收尾屏不该再出现官方 hermes 命令"

        # 官方 v0.21.5 把配对提示拆到独立模块（gateway/run_inbound_unauthorized.py）
        inbound = (REPO_ROOT / "gateway" / "run_inbound_unauthorized.py").read_text(encoding="utf-8")
        assert "coco {profile_arg}pairing approve " in inbound, "配对提示应给 coco 命令"
        assert "hermes {profile_arg}pairing approve " not in inbound, "配对提示不该是 hermes 命令"

    def test_test_tag_only_on_test_channel(self, tmp_path):
        """测试号只在测试通道显示（2026-09-22 稳定版上曾出现 "稳定通道 · 测试号 v…-test5"）。"""
        import shutil
        import subprocess

        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPTS / "coco_channel.sh", repo / "scripts" / "coco_channel.sh")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)

        git("init", "-q", "-b", "master")
        (repo / "f.txt").write_text("x", encoding="utf-8")
        git("add", "-A")
        git("commit", "-qm", "c1")
        git("tag", "v0.21.3-68-test5")
        (repo / "f.txt").write_text("y", encoding="utf-8")
        git("commit", "-aqm", "c2")

        def test_tag():
            return subprocess.run(["bash", str(repo / "scripts" / "coco_channel.sh"), "test-tag"],
                                  capture_output=True, text=True, env=env).stdout.strip()

        assert test_tag() == "", "稳定通道（master）不该显示测试号"
        git("checkout", "-q", "-b", "next")
        assert "test5" in test_tag(), "测试通道应显示测试号"


class TestReadmesStayInSync:
    """两份 README 内容必须完全一致（2026-09-22 定的约定）。

    背景：两个平台的仓库首页读的文件不同 —— GitHub 读 `README.md`、Gitee 读
    `README.zh-CN.md`。两份各自漂移过一次，结果是「同一个项目，两个平台看到的说明不一样」
    （一边有「完整操作手册见 docs/BACKUP_MIGRATION.md」、另一边有「智能体不回复」排查节）。
    约定：**改 README 必须同时改两份**；这条断言把约定钉在 CI 上，谁只改一份就会红。
    """

    def test_two_readmes_are_identical(self):
        main = (REPO_ROOT / "README.md").read_bytes()
        zh = (REPO_ROOT / "README.zh-CN.md").read_bytes()
        if main == zh:
            return
        import difflib
        diff = "".join(difflib.unified_diff(
            main.decode("utf-8").splitlines(keepends=True),
            zh.decode("utf-8").splitlines(keepends=True),
            "README.md", "README.zh-CN.md"))[:2000]
        raise AssertionError(
            "两份 README 内容不一致 —— 必须同时改两份"
            "（GitHub 首页读 README.md、Gitee 首页读 README.zh-CN.md）：\n" + diff)


class TestPython313DownloadPath:
    """系统 Python 超出窗口时（更老的发行版、或将来的 3.15）会走"准备 Python 3.13"这条路径 ——
    必须国内加速、有超时、失败可读。

    背景：这段以前把输出全丢掉、且没有任何超时，国内机器上看着像"卡死一小时"；
    而且它先用 `pip install` 装 uv —— Ubuntu 23.04 起系统 Python 禁止 pip 装包（PEP 668），
    这一步必然失败，于是只剩"去境外下 20MB uv"一条路。
    （26.04 自带的 3.14 现在已在版本窗口内，不再走这条路；更老的系统与将来的 3.15 仍然要靠它。）
    """

    def test_apt_get_waits_for_dpkg_lock(self):
        """全新装机的机器上 unattended-upgrades 常占着 apt 锁（真实事故：Ubuntu 26.04 新装机跑
        安装脚本，第一步 apt 就报 Could not get lock /var/lib/dpkg/lock-frontend 并中断）。"""
        t = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        assert "wait_for_apt_lock" in t, "apt 之前应先等锁释放"
        assert "DPkg::Lock::Timeout" in t, "apt 自己也应带锁等待参数（双保险）"
        assert "apt_get update" in t and "apt_get install" in t, "apt 调用统一走带锁等待的入口"
        assert "自动更新" in t, "等待时要说清是系统自动更新占着锁（便于用户排查）"
        assert "apt_get install -y -qq python3 python3-pip" in t, "系统依赖仍要一次装齐"

    def test_domestic_mirror_for_cn(self):
        t = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        assert "UV_PYTHON_INSTALL_MIRROR" in t, "国内机器下载 Python 应走国内镜像"
        assert "mirror.nju.edu.cn/github-release/astral-sh/python-build-standalone" in t
        assert 'COCO_CHOSEN_SOURCE' in t, "应按仓库源判断国内外（国内→镜像）"
        # 用户自己设过镜像时必须尊重（不要硬覆盖）
        assert '-z "${UV_PYTHON_INSTALL_MIRROR:-}"' in t

    def test_uv_comes_from_pypi_wheel_not_pip_install(self):
        """取 uv 不能靠 pip install（PEP 668 会拦），要下 wheel 再解出里面的二进制"""
        t = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        assert "pip download" in t and "--only-binary=:all:" in t, "应用 pip download 取 uv 的 wheel"
        assert "pip install -q -i" not in t, "pip install 装 uv 在 Ubuntu 23.04+ 会被 PEP 668 拦掉"
        assert "zipfile" in t and ".data/scripts/uv" in t, "应解出 wheel 里的 uv 可执行文件"

    def test_uv_pypi_sources_cover_both_networks(self):
        t = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        assert "https://pypi.tuna.tsinghua.edu.cn/simple" in t, "国内机器应能走国内 PyPI 镜像"
        assert "https://pypi.org/simple" in t, "海外机器应能走官方 PyPI"

    def test_every_download_has_timeout_and_visible_output(self):
        t = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        assert "timeout 900" in t, "uv 下载 Python 必须带超时"
        assert "timeout 300" in t, "官方安装脚本必须带超时（它自己下载时没有超时）"
        assert "--max-time 120" in t, "下载 uv 安装脚本必须带超时"
        assert "--max-time 900" in t, "直接下载 Python 包必须带超时"
        assert "正在下载并准备 Python 3.13" in t, "应打印进度提示"
        assert "正在下载 Python 3.13" in t, "应打印进度提示"
        assert ">/dev/null 2>&1 | sh" not in t, "不能把安装脚本的输出丢掉（否则看着像死机）"

    def test_tarball_fallback_without_uv(self):
        """uv 这条路走不通时，要能不用 uv 直接下 Python（国内镜像 / GitHub 双源、按架构选包）"""
        t = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        assert "install_python_tarball" in t, "应有无需 uv 的兜底路径"
        assert "python-build-standalone" in t
        assert "install_only" in t and "_stripped" in t, "应优先取体积更小的 stripped 包"
        assert "expanded_assets" in t and "releases/download" in t, "海外走 GitHub Release"
        assert "aarch64" in t, "要按 CPU 架构选包"

    def test_no_manual_export_required_in_messages(self):
        """对外提示不应要求用户手动 export 环境变量（标准是"一条命令"）"""
        t = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        for line in t.split("\n"):
            if "echo" not in line:
                continue
            # 只禁止"让用户自己设置"的提示（允许提到变量名/告诉怎么关掉）
            if "export UV_PYTHON_INSTALL_MIRROR=" in line:
                raise AssertionError(f"不要在用户提示里让他 export：{line.strip()}")


class TestPythonVersionWindow:
    """版本窗口 3.11 ~ 3.14：两端都要判（3.14 实测可用；3.15 未验证要拦住）。

    判据与 pyproject.toml 的 requires-python 必须一致 —— 只改一边，装机时就会出现
    "脚本放行、pip 拒绝"或反过来的错配。
    """

    def _py_ok_snippet(self):
        t = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        m = re.search(r"^_py_ok\(\) \{.*\}$", t, re.M)
        assert m, "install.sh 里应有 _py_ok 版本窗口判据"
        return m.group(0)

    def _py_ok_accepts(self, tmp_path, version):
        """拿 install.sh 里那段判据跑一遍：假 python 谎报版本，看放不放行。"""
        fake = tmp_path / f"fakepython-{version}"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "sys.version_info = tuple(int(p) for p in os.environ['FAKE_VER'].split('.'))\n"
            "code = sys.argv[sys.argv.index('-c') + 1]\n"
            "exec(compile(code, '<fake>', 'exec'), {'__name__': '__main__'})\n",
            encoding="utf-8")
        fake.chmod(0o755)
        runner = tmp_path / f"run-{version}.sh"
        runner.write_text(
            "set -uo pipefail\n" + self._py_ok_snippet() + f'\n_py_ok "{fake}"\n',
            encoding="utf-8")
        env = dict(os.environ, FAKE_VER=version)
        r = subprocess.run(["bash", str(runner)], env=env, capture_output=True, text=True)
        return r.returncode == 0

    def test_window_boundaries(self, tmp_path):
        for version, expected in (
            ("3.10.14", False),
            ("3.11.0", True),
            ("3.12.9", True),
            ("3.13.7", True),
            ("3.14.7", True),
            ("3.15.0", False),
        ):
            got = self._py_ok_accepts(tmp_path, version)
            assert got is expected, f"Python {version} 的判定应为 {expected}，实际 {got}"

    def test_pyproject_and_install_sh_agree(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        install = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        assert 'requires-python = ">=3.11,<3.15"' in pyproject, "pyproject 的窗口应放到 3.14"
        assert "< (3, 15)" in install, "install.sh 的窗口判据要与 pyproject 一致"
