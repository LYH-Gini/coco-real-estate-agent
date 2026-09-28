"""稳定版 / 测试版双通道（2026-09-21 要求）。

需求原话："我发的版本只有我能拉下来测试，其他人还是拉之前测试通过的版本，
等我测试没问题了再正式发布。" 方案 = 双分支双通道，**默认分支不动**：

  · master = 稳定版 —— 别人照文档安装/更新的就是它（零迁移）
  · next   = 测试版 —— 只有测试机切到这个分支

关键机制：`git pull` 不带分支名 = 拉"当前分支对应的线上分支"，所以测试机切到 next
之后，`git pull && bash scripts/update.sh` 这条老命令**一个字都不用改**就自动拉测试版。

本文件钉住三件事：① 通道标签与切换（含"工作区脏就拒绝"）；② 版本输出里能看到通道；
③ 晋升脚本只做快进（master 不是 next 的祖先时必须拒绝，否则会破坏 --ff-only 更新）。
"""
import json
import re
import os
import shutil
import subprocess
import sys

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _run(cmd, cwd=None, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or REPO_ROOT, env=env)


def _promote(work, *args, confirm=True):
    """跑 promote_release.sh：默认补上闸② 要的人工确认值（按 next 当前提交算）。

    2026-09-23 起晋升会先打印“将带上的提交清单 + 内容差异”并要求 PROMOTE_CONFIRM，
    所以测试要么显式给确认值，要么就是在验证“不给确认会被拦”。
    """
    env = dict(os.environ)
    if confirm:
        sha = subprocess.run(["git", "-C", str(work), "rev-parse", "--short=7", "next"],
                             capture_output=True, text=True).stdout.strip()
        env["PROMOTE_CONFIRM"] = sha
    return _run(["bash", "scripts/promote_release.sh", *args], cwd=work, env=env)


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _init_repo(path, branch="master"):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(path), "config", k, v], check=True)
    (path / "VERSION").write_text("0.0.0-1\n", encoding="utf-8")
    return path


def _commit_all(repo, msg):
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", msg], check=True)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


class TestChannelTool:
    """scripts/coco_channel.sh：通道标签 / 当前通道 / 期望通道 / 切换"""

    def test_labels(self):
        for branch, label in (("master", "稳定通道"), ("next", "测试通道"), ("feat/x", "自定义通道")):
            r = _run(["bash", str(SCRIPTS / "coco_channel.sh"), "label", branch])
            assert r.stdout.strip() == label, r.stdout

    def test_show_reports_current_channel(self, tmp_path):
        work = _init_repo(tmp_path / "work")
        (work / "scripts").mkdir(exist_ok=True)
        shutil.copy(SCRIPTS / "coco_channel.sh", work / "scripts" / "coco_channel.sh")
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "next"], check=True)
        r = _run(["bash", str(work / "scripts" / "coco_channel.sh"), "show"], cwd=work)
        assert "测试通道" in r.stdout and "next" in r.stdout, r.stdout

    def test_want_priority_env_then_file_then_current(self, tmp_path):
        work = _init_repo(tmp_path / "work")
        (work / "scripts").mkdir(exist_ok=True)
        shutil.copy(SCRIPTS / "coco_channel.sh", work / "scripts" / "coco_channel.sh")
        assert _run(["bash", str(work / "scripts" / "coco_channel.sh"), "want"], cwd=work).stdout.strip() == "master"
        (work / ".coco-channel").write_text("next\n", encoding="utf-8")
        assert _run(["bash", str(work / "scripts" / "coco_channel.sh"), "want"], cwd=work).stdout.strip() == "next"
        env = {**os.environ, "COCO_CHANNEL": "custom"}
        assert subprocess.run(["bash", str(work / "scripts" / "coco_channel.sh"), "want"], cwd=work,
                              capture_output=True, text=True, env=env).stdout.strip() == "custom"

    def test_switch_moves_branch(self, tmp_path):
        origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        work = _init_repo(tmp_path / "work")
        (work / "scripts").mkdir(exist_ok=True)
        shutil.copy(SCRIPTS / "coco_channel.sh", work / "scripts" / "coco_channel.sh")
        _commit_all(work, "init")
        subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(origin)], check=True)
        subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "master"], check=True)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "next"], check=True)
        (work / "f.txt").write_text("x", encoding="utf-8")
        _commit_all(work, "next work")
        subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "next"], check=True)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "master"], check=True)

        r = _run(["bash", str(work / "scripts" / "coco_channel.sh"), "switch", "next"], cwd=work)
        assert r.returncode == 0, r.stdout + r.stderr
        assert _git(work, "branch", "--show-current").stdout.strip() == "next"

    def test_switch_refuses_dirty_worktree(self, tmp_path):
        """绝不覆盖本地改动：工作区脏时必须拒绝切换"""
        origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        work = _init_repo(tmp_path / "work")
        (work / "scripts").mkdir(exist_ok=True)
        shutil.copy(SCRIPTS / "coco_channel.sh", work / "scripts" / "coco_channel.sh")
        _commit_all(work, "init")
        subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(origin)], check=True)
        subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "master"], check=True)
        (work / "VERSION").write_text("dirty\n", encoding="utf-8")      # 制造已跟踪文件的未提交改动

        r = _run(["bash", str(work / "scripts" / "coco_channel.sh"), "switch", "next"], cwd=work)
        assert r.returncode != 0
        assert "未提交的代码改动" in (r.stdout + r.stderr)
        assert _git(work, "branch", "--show-current").stdout.strip() == "master", "拒绝切换后不应换分支"


class TestChannelVisible:
    """通道要能看见 —— 否则看不出自己在测哪条线"""

    def test_coco_version_shows_channel(self):
        r = _run(["bash", "scripts/coco.sh", "version"])
        assert r.returncode == 0, r.stderr
        assert any(x in r.stdout for x in ("稳定通道", "测试通道")), r.stdout

    def test_version_tool_reports_channel(self):
        sys.path.insert(0, str(REPO_ROOT))
        from tools.real_estate_version import get_coco_version

        data = json.loads(get_coco_version())
        assert data["channel"] in ("正式版", "测试版", "未知")
        # 2026-09-27：通道只在结构字段里（内部排查用），message 不对外说通道（测试版是老板自用）
        assert "测试版" not in data["message"] and "测试通道" not in data["message"], data["message"]

    def test_update_script_prints_channel_and_supports_switch(self):
        text = (SCRIPTS / "update.sh").read_text(encoding="utf-8")
        assert "通道" in text and "coco_channel.sh" in text, "更新脚本没有显示/处理通道"
        assert ".coco-channel" in (SCRIPTS / "coco_channel.sh").read_text(encoding="utf-8")

    def test_install_script_supports_channel(self):
        text = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        assert "COCO_CHANNEL" in text
        assert 'git clone --branch "$COCO_CHANNEL"' in text, "安装脚本没有按通道克隆"


class TestPromoteRelease:
    """scripts/promote_release.sh：把测试版(next)晋升为稳定版(master)，只做快进"""

    def _setup(self, tmp_path, diverge=False):
        work = _init_repo(tmp_path / "work")
        (work / "scripts").mkdir(exist_ok=True)
        for name in ("coco_channel.sh", "promote_release.sh"):
            shutil.copy(SCRIPTS / name, work / "scripts" / name)
        _commit_all(work, "init")
        for name in ("gitee.git", "github.git"):
            subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / name)], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(tmp_path / "gitee.git")], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "github", str(tmp_path / "github.git")], check=True)
        subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "master"], check=True)
        subprocess.run(["git", "-C", str(work), "push", "-q", "github", "master"], check=True)
        # 造一个"测试版"：master 之上加提交
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "next"], check=True)
        if diverge:
            # 让两条线分叉（稳定线有测试线没有的提交）→ 必须拒绝晋升
            subprocess.run(["git", "-C", str(work), "checkout", "-q", "master"], check=True)
            (work / "hotfix.txt").write_text("hotfix", encoding="utf-8")
            _commit_all(work, "hotfix on master")
            subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "master"], check=True)
            subprocess.run(["git", "-C", str(work), "push", "-q", "github", "master"], check=True)
            subprocess.run(["git", "-C", str(work), "checkout", "-q", "next"], check=True)
        (work / "feature.txt").write_text("feature", encoding="utf-8")
        next_tip = _commit_all(work, "feature on next")
        subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "next"], check=True)
        subprocess.run(["git", "-C", str(work), "push", "-q", "github", "next"], check=True)
        # 晋升的硬闸门：必须有验收登记（verified/* 标签钉在晋升的提交上）
        subprocess.run(["git", "-C", str(work), "tag", "-a", "verified/v0.0.0-1-" + next_tip[:7],
                        "-m", "已验收：测试用例", next_tip], check=True)
        return work, next_tip

    def _remote_master(self, tmp_path, which):
        return subprocess.run(["git", "-C", str(tmp_path / which), "rev-parse", "master"],
                              capture_output=True, text=True).stdout.strip()

    def test_dry_run_changes_nothing(self, tmp_path):
        work, next_tip = self._setup(tmp_path)
        before = self._remote_master(tmp_path, "gitee.git")
        r = _run(["bash", "scripts/promote_release.sh", "--dry-run"], cwd=work)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "dry-run" in r.stdout
        assert self._remote_master(tmp_path, "gitee.git") == before, "dry-run 不应改动远程"
        assert next_tip not in r.stdout

    def test_promotes_next_to_master_on_both_remotes(self, tmp_path):
        work, next_tip = self._setup(tmp_path)
        r = _promote(work)
        assert r.returncode == 0, r.stdout + r.stderr
        assert self._remote_master(tmp_path, "gitee.git") == next_tip
        assert self._remote_master(tmp_path, "github.git") == next_tip
        assert "晋升完成" in r.stdout

    def test_refuses_when_master_is_not_ancestor(self, tmp_path):
        """稳定线有测试线没有的提交（分叉）时必须拒绝 —— 否则会破坏 --ff-only 更新"""
        work, next_tip = self._setup(tmp_path, diverge=True)
        before = self._remote_master(tmp_path, "gitee.git")
        r = _run(["bash", "scripts/promote_release.sh"], cwd=work)
        assert r.returncode != 0
        assert "无法快进" in (r.stdout + r.stderr)
        assert self._remote_master(tmp_path, "gitee.git") == before, "拒绝时不应改动 master"

    def test_tag_option_creates_and_pushes_tag(self, tmp_path):
        work, next_tip = self._setup(tmp_path)
        r = _promote(work, "--tag", "v0.0.0-99")
        assert r.returncode == 0, r.stdout + r.stderr
        tags = subprocess.run(["git", "-C", str(tmp_path / "gitee.git"), "tag"], capture_output=True, text=True).stdout
        assert "v0.0.0-99" in tags


class TestUpdateScriptStartsCleanly:
    """更新脚本自身必须能正常启动 —— 这一条是被真事逼出来的：

    曾经把通道代码写在 info()/fail() 定义**之前**，脚本一启动就 "command not found"
    直接中断（还调了一个根本不存在的 fail）。所以这里真跑一次脚本，钉住：
    ① 不出现 command not found；② 能打印当前通道；③ 能走到 venv 前提检查（说明前面全过了）。
    """

    def _fake_repo(self, tmp_path, branch="master"):
        work = tmp_path / "work"
        (work / "scripts").mkdir(parents=True)
        for name in ("update.sh", "coco_channel.sh"):
            shutil.copy(SCRIPTS / name, work / "scripts" / name)
        subprocess.run(["git", "init", "-q", "-b", branch, str(work)], check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "-C", str(work), "config", k, v], check=True)
        (work / "VERSION").write_text("0.0.0-1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), "commit", "-qm", "init"], check=True)
        return work

    def test_script_starts_and_reaches_preflight(self, tmp_path):
        work = self._fake_repo(tmp_path)
        r = _run(["bash", str(work / "scripts" / "update.sh")], cwd=work)
        out = r.stdout + r.stderr
        assert "command not found" not in out, out
        assert "当前通道" in out, out
        assert r.returncode != 0 and "虚拟环境" in out, "应当停在 venv 前提检查（本临时仓库不是真实安装）"

    def test_test_flag_switches_to_test_channel(self, tmp_path):
        """update.sh --test 应自动切到测试通道"""
        work = self._fake_repo(tmp_path)
        origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(origin)], check=True)
        subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "master"], check=True)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "next"], check=True)
        (work / "f.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), "commit", "-qm", "next"], check=True)
        subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "next"], check=True)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "master"], check=True)

        r = _run(["bash", str(work / "scripts" / "update.sh"), "--test"], cwd=work)
        out = r.stdout + r.stderr
        assert "command not found" not in out, out
        assert "测试通道" in out, out
        assert _git(work, "branch", "--show-current").stdout.strip() == "next", "没有切到测试通道"

    def test_stable_flag_switches_back(self, tmp_path):
        work = self._fake_repo(tmp_path, branch="next")
        origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(origin)], check=True)
        subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "next"], check=True)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "master"], check=True)
        subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "master"], check=True)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "next"], check=True)

        r = _run(["bash", str(work / "scripts" / "update.sh"), "--stable"], cwd=work)
        out = r.stdout + r.stderr
        assert "稳定通道" in out, out
        assert _git(work, "branch", "--show-current").stdout.strip() == "master", "没有切回稳定通道"

    def test_flag_refuses_to_switch_with_dirty_tree(self, tmp_path):
        work = self._fake_repo(tmp_path)
        origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(origin)], check=True)
        subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "master"], check=True)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "next"], check=True)
        subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "next"], check=True)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "master"], check=True)
        (work / "VERSION").write_text("dirty\n", encoding="utf-8")     # 已跟踪文件的未提交改动

        r = _run(["bash", str(work / "scripts" / "update.sh"), "--test"], cwd=work)
        out = r.stdout + r.stderr
        assert "未提交改动" in out, out
        assert _git(work, "branch", "--show-current").stdout.strip() == "master", "脏工作区不应换分支"

    def test_unknown_flag_is_rejected(self, tmp_path):
        work = self._fake_repo(tmp_path)
        r = _run(["bash", str(work / "scripts" / "update.sh"), "--nonsense"], cwd=work)
        assert r.returncode != 0 and "未知参数" in (r.stdout + r.stderr)


class TestRuntimeFilesDoNotBlockSwitch:
    """运行时文件（更新锁 / 通道标记 / .env.db / 密钥）是未跟踪的，不该挡住切通道。

    真缺陷记录：更新脚本自己会创建 .coco-update.lock，若切通道的干净检查把未跟踪文件
    也算进去，测试机就会永远切不动通道（第一次更新后就再也切不了）。
    """

    def test_lock_file_does_not_block_switch(self, tmp_path):
        work = _init_repo(tmp_path / "work")
        (work / "scripts").mkdir(exist_ok=True)
        shutil.copy(SCRIPTS / "coco_channel.sh", work / "scripts" / "coco_channel.sh")
        _commit_all(work, "init")
        origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(origin)], check=True)
        subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "master"], check=True)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "next"], check=True)
        subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "next"], check=True)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "master"], check=True)
        # 模拟更新脚本留下的运行时文件
        (work / ".coco-update.lock").write_text("", encoding="utf-8")
        (work / ".coco-channel").write_text("next\n", encoding="utf-8")
        (work / ".env.db").write_text("secret", encoding="utf-8")

        r = _run(["bash", str(work / "scripts" / "coco_channel.sh"), "switch", "next"], cwd=work)
        assert r.returncode == 0, r.stdout + r.stderr
        assert _git(work, "branch", "--show-current").stdout.strip() == "next"


class TestPromoteSyncsTestChannelFirst:
    """晋升前必须先让测试通道与远程一致 —— 真缺陷记录：

    本地 next 有未推送提交时，旧脚本按 origin/next（旧 SHA）校验，结果**误报失败**
    （远程其实已更新），并且把标签打到了旧提交上。所以现在先推齐测试分支，再按远程 SHA 晋升。
    """

    def test_promote_pushes_test_channel_before_promoting(self, tmp_path):
        work = _init_repo(tmp_path / "work")
        (work / "scripts").mkdir(exist_ok=True)
        for name in ("coco_channel.sh", "promote_release.sh"):
            shutil.copy(SCRIPTS / name, work / "scripts" / name)
        _commit_all(work, "init")
        for name in ("gitee.git", "github.git", "dev-gitee.git", "dev-gh.git"):
            subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / name)], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(tmp_path / "gitee.git")], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "github", str(tmp_path / "github.git")], check=True)
        # 2026-09-28：测试通道（next）住在**开发仓**，正式仓只放 master
        subprocess.run(["git", "-C", str(work), "remote", "add", "dev-gitee", str(tmp_path / "dev-gitee.git")], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "dev-gh", str(tmp_path / "dev-gh.git")], check=True)
        for r in ("origin", "github"):
            subprocess.run(["git", "-C", str(work), "push", "-q", r, "master"], check=True)
        # 测试分支：推一版旧的到开发仓，然后在本地再加一个"未推送"的提交
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "next"], check=True)
        (work / "old.txt").write_text("old", encoding="utf-8")
        _commit_all(work, "pushed to test channel")
        for r in ("dev-gitee", "dev-gh"):
            subprocess.run(["git", "-C", str(work), "push", "-q", r, "next"], check=True)
        (work / "new.txt").write_text("new", encoding="utf-8")
        tip = _commit_all(work, "not pushed yet")
        subprocess.run(["git", "-C", str(work), "tag", "-a", "verified/v0.0.0-1-" + tip[:7],
                        "-m", "已验收：测试用例", tip], check=True)

        r = _promote(work)
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert "推齐" in out, out
        assert subprocess.run(["git", "-C", str(tmp_path / "gitee.git"), "rev-parse", "master"],
                              capture_output=True, text=True).stdout.strip() == tip
        for name in ("dev-gitee.git", "dev-gh.git"):
            got = subprocess.run(["git", "-C", str(tmp_path / name), "rev-parse", "--verify", "--quiet",
                                  "refs/heads/next"], capture_output=True, text=True).stdout.strip()
            assert got == tip, f"{name} 的 next 应被推齐（推的是开发仓）：{out}"
        for name in ("gitee.git", "github.git"):
            got = subprocess.run(["git", "-C", str(tmp_path / name), "rev-parse", "--verify", "--quiet",
                                  "refs/heads/next"], capture_output=True, text=True).stdout.strip()
            assert got == "", f"正式仓（{name}）不该出现 next：{out}"


class TestApprovalGate:
    """硬闸门：没经过验收登记的功能不能进正式版（2026-09-21 要求）。

    机制：验收登记 = 钉在"测试通道那个提交"上的 verified/* 标签；
    promote_release.sh 只认带这个标签的提交。所以"验收的是 A、发布的是 B"不可能发生。
    """

    def _setup(self, tmp_path, mark=True, extra_commit_after_mark=False):
        work = _init_repo(tmp_path / "work")
        (work / "scripts").mkdir(exist_ok=True)
        for name in ("coco_channel.sh", "promote_release.sh", "mark_verified.sh"):
            shutil.copy(SCRIPTS / name, work / "scripts" / name)
        _commit_all(work, "init")
        for name in ("gitee.git", "github.git"):
            subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / name)], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(tmp_path / "gitee.git")], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "github", str(tmp_path / "github.git")], check=True)
        for r in ("origin", "github"):
            subprocess.run(["git", "-C", str(work), "push", "-q", r, "master"], check=True)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "next"], check=True)
        (work / "feature.txt").write_text("feature", encoding="utf-8")
        _commit_all(work, "feature on next")
        for r in ("origin", "github"):
            subprocess.run(["git", "-C", str(work), "push", "-q", r, "next"], check=True)
        if mark:
            subprocess.run(["bash", "scripts/mark_verified.sh", "--note", "验收通过：某功能",
                            "--no-push"], cwd=work, check=True, capture_output=True)
        if extra_commit_after_mark:
            (work / "later.txt").write_text("later", encoding="utf-8")
            _commit_all(work, "验收之后又推的新提交")
        return work

    def test_promote_refused_without_verification(self, tmp_path):
        work = self._setup(tmp_path, mark=False)
        before = subprocess.run(["git", "-C", str(tmp_path / "gitee.git"), "rev-parse", "master"],
                                capture_output=True, text=True).stdout.strip()
        r = _run(["bash", "scripts/promote_release.sh"], cwd=work)
        out = r.stdout + r.stderr
        assert r.returncode != 0, out
        assert "验收登记" in out, out
        assert subprocess.run(["git", "-C", str(tmp_path / "gitee.git"), "rev-parse", "master"],
                              capture_output=True, text=True).stdout.strip() == before, "被拒时不得改动 master"

    def test_promote_allowed_after_verification(self, tmp_path):
        work = self._setup(tmp_path)
        r = _promote(work)
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert "验收登记" in out and "晋升完成" in out, out

    def test_verification_is_pinned_to_a_commit(self, tmp_path):
        """登记之后测试通道又推新提交 → 新提交没有登记 → 晋升必须被拒（不能"验收A发布B"）"""
        work = self._setup(tmp_path, extra_commit_after_mark=True)
        r = _run(["bash", "scripts/promote_release.sh"], cwd=work)
        out = r.stdout + r.stderr
        assert r.returncode != 0, out
        assert "验收登记" in out, out

    def test_mark_verified_refuses_on_stable_branch(self, tmp_path):
        work = self._setup(tmp_path, mark=False)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "master"], check=True)
        r = _run(["bash", "scripts/mark_verified.sh", "--note", "x", "--no-push"], cwd=work)
        assert r.returncode != 0
        assert "只能在测试通道" in (r.stdout + r.stderr)

    def test_mark_verified_requires_note(self, tmp_path):
        work = self._setup(tmp_path, mark=False)
        r = _run(["bash", "scripts/mark_verified.sh", "--no-push"], cwd=work)
        assert r.returncode != 0 and "--note" in (r.stdout + r.stderr)

    def test_mark_verified_list(self, tmp_path):
        work = self._setup(tmp_path)
        r = _run(["bash", "scripts/mark_verified.sh", "--list"], cwd=work)
        assert r.returncode == 0, r.stderr
        # 说明里的「验收通过：」前缀会被脚本归一掉（只保留一份前缀），所以这里断言说明内容本身
        assert "verified/" in r.stdout and "某功能" in r.stdout, r.stdout

    def test_mark_verified_note_prefix_is_not_repeated(self, tmp_path):
        """说明里自带「已验收 / 验收通过 / v<版本> 已验收」前缀时，标签说明只保留一份前缀。

        历史事故（2026-09-28）：说明里已经写了「v0.21.5-3 已验收：…」，脚本又补一遍，
        公网上的标签说明变成「v0.21.5-3 已验收：v0.21.5-3 已验收：…」。
        标签说明是公网可见元数据，重复的前缀既是噪音也把内部话带出去。
        """
        work = self._setup(tmp_path, mark=False)
        ver = (work / "VERSION").read_text(encoding="utf-8").strip()
        with_note = f"v{ver} 已验收：体检通过"
        cases = [
            ("体检通过", with_note),
            (with_note, with_note),
            ("已验收：体检通过", with_note),
            ("验收通过：体检通过", with_note),
            ("已验收", f"v{ver} 已验收"),
        ]
        for i, (note, want) in enumerate(cases):
            (work / f"note{i}.txt").write_text(note, encoding="utf-8")
            sha = _commit_all(work, f"note {i}")
            r = _run(["bash", "scripts/mark_verified.sh", "--note", note, "--no-push"], cwd=work)
            assert r.returncode == 0, r.stdout + r.stderr
            got = _git(work, "for-each-ref", f"refs/tags/verified/v{ver}-{sha[:7]}",
                       "--format=%(contents:subject)").stdout.strip()
            assert got == want, f"说明 {note!r} 得到 {got!r}，应为 {want!r}"


class TestTestVersionTags:
    """测试版也要有号（2026-09-21 要求："要不然你和我都区分不了测试版的版本"）。

    约号：正式版 v<版本>，测试版 v<版本>-test<N>（N 递增）；测试号不发 Release、不进正式版。
    """

    def _repo(self, tmp_path, branch="next"):
        work = _init_repo(tmp_path / "work", branch=branch)
        (work / "scripts").mkdir(exist_ok=True)
        for name in ("tag_test_version.sh", "coco_channel.sh", "promote_release.sh", "coco.sh"):
            shutil.copy(SCRIPTS / name, work / "scripts" / name)
        _commit_all(work, "init")
        return work

    def test_test_tag_increments(self, tmp_path):
        work = self._repo(tmp_path)
        r1 = _run(["bash", "scripts/tag_test_version.sh", "--note", "第一批", "--no-push"], cwd=work)
        assert r1.returncode == 0, r1.stdout + r1.stderr
        assert "v0.0.0-1-test1" in r1.stdout, r1.stdout
        r2 = _run(["bash", "scripts/tag_test_version.sh", "--note", "第二批", "--no-push"], cwd=work)
        assert r2.returncode == 0, r2.stdout + r2.stderr
        assert "v0.0.0-1-test2" in r2.stdout, r2.stdout
        tags = subprocess.run(["git", "-C", str(work), "tag"], capture_output=True, text=True).stdout.split()
        assert "v0.0.0-1-test1" in tags and "v0.0.0-1-test2" in tags

    def test_test_tag_requires_note_and_test_branch(self, tmp_path):
        work = self._repo(tmp_path)
        r = _run(["bash", "scripts/tag_test_version.sh", "--no-push"], cwd=work)
        assert r.returncode != 0 and "--note" in (r.stdout + r.stderr)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "master2"], check=True)
        r2 = _run(["bash", "scripts/tag_test_version.sh", "--note", "x", "--no-push"], cwd=work)
        assert r2.returncode != 0 and "只能在测试通道" in (r2.stdout + r2.stderr)

    def test_test_number_visible_in_version_output(self, tmp_path):
        work = self._repo(tmp_path)
        subprocess.run(["bash", "scripts/tag_test_version.sh", "--note", "第一批", "--no-push"],
                       cwd=work, check=True, capture_output=True)
        r = _run(["bash", "scripts/coco.sh", "version"], cwd=work)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "测试号" in r.stdout and "v0.0.0-1-test1" in r.stdout, r.stdout

    def test_channel_tool_reports_test_tag(self, tmp_path):
        work = self._repo(tmp_path)
        subprocess.run(["bash", "scripts/tag_test_version.sh", "--note", "第一批", "--no-push"],
                       cwd=work, check=True, capture_output=True)
        r = _run(["bash", str(work / "scripts" / "coco_channel.sh"), "test-tag"], cwd=work)
        assert r.stdout.strip() == "v0.0.0-1-test1", r.stdout
        # 标签之后又有新提交 → 显示 +N 提交（避免"以为还是那一版"）
        (work / "more.txt").write_text("more", encoding="utf-8")
        _commit_all(work, "标签之后的新提交")
        r2 = _run(["bash", str(work / "scripts" / "coco_channel.sh"), "test-tag"], cwd=work)
        assert r2.stdout.strip() == "v0.0.0-1-test1 +1 提交", r2.stdout

    def test_promote_refuses_test_tag_as_release_tag(self, tmp_path):
        # 稳定通道要在位（先 master 提交 + 推送），再切到测试通道
        work = _init_repo(tmp_path / "work")
        (work / "scripts").mkdir(exist_ok=True)
        for name in ("tag_test_version.sh", "coco_channel.sh", "promote_release.sh"):
            shutil.copy(SCRIPTS / name, work / "scripts" / name)
        _commit_all(work, "init")
        origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(origin)], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "github", str(origin)], check=True)
        for r in ("origin", "github"):
            subprocess.run(["git", "-C", str(work), "push", "-q", r, "master"], check=True)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "next"], check=True)
        (work / "feature.txt").write_text("f", encoding="utf-8")
        _commit_all(work, "feature on next")
        for r in ("origin", "github"):
            subprocess.run(["git", "-C", str(work), "push", "-q", r, "next"], check=True)
        subprocess.run(["bash", "scripts/tag_test_version.sh", "--note", "第一批", "--no-push"],
                       cwd=work, check=True, capture_output=True)
        tip = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "-C", str(work), "tag", "-a", "verified/v0.0.0-1-" + tip[:7],
                        "-m", "已验收：测试", tip], check=True)
        r = _run(["bash", "scripts/promote_release.sh", "--tag", "v0.0.0-1-test9"], cwd=work)
        assert r.returncode != 0
        assert "正式标签不能带 -test" in (r.stdout + r.stderr), r.stdout + r.stderr


class TestTestChannelMatchesStableFlow:
    """测试通道的更新流程必须与正式版**完全一致**（2026-09-21 撤回"测试机不备份"）。

    证据要求：`--test` / `--stable` 只负责换通道，不能顺带打开"跳过备份"之类的差异开关；
    跳过备份只能由使用者显式传 `--skip-backup`。
    """

    def test_channel_flags_only_switch_branch(self):
        text = (SCRIPTS / "update.sh").read_text(encoding="utf-8")
        assert re.search(r'--test\)\s+FORCE_CHANNEL="next"', text), "缺少 --test → 测试通道的映射"
        assert re.search(r'--stable\)\s+FORCE_CHANNEL="master"', text), "缺少 --stable 映射"
        # 每个 SKIP_BACKUP=1 都必须出现在 --skip-backup 那一行（不得被通道参数暗开）
        for line in text.split("\n"):
            if "SKIP_BACKUP=1" in line:
                assert "--skip-backup" in line, f"SKIP_BACKUP 被非显式参数打开：{line.strip()}"

    def test_no_skip_backup_in_recommended_channel_commands(self):
        """交付的命令里不得再出现 --skip-backup（测试机也照常备份）"""
        for rel in ("README.md", "README.zh-CN.md", "scripts/coco.sh"):
            t = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert "--skip-backup" not in t, f"{rel} 里出现了 --skip-backup（不应作为推荐命令）"


class TestPromoteGuards:
    """2026-09-23 要求的三道改动（防把滞留/未核对内容推上正式版）：

    ① 闸①：晋升清单里含被 backup-* 标签钉住的提交（禁推正式版的备用方案）→ 拒绝并提示改走 --only；
    ② 闸②：推送前打印“提交清单 + 内容差异”，要求 PROMOTE_CONFIRM 才继续；
    ③ --only：只把点名的提交 cherry-pick 到正式版（next 上滞留内容不会被带上）。
    """

    def _setup_with_backup(self, tmp_path):
        work, _next_tip = TestPromoteRelease()._setup(tmp_path)
        (work / "backup.txt").write_text("backup", encoding="utf-8")
        _commit_all(work, "备用方案（禁推正式版）")
        tip = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "-C", str(work), "tag", "-a", "backup-test-v1",
                        "-m", "【备用方案·禁止推正式版】测试用", tip], check=True)
        subprocess.run(["git", "-C", str(work), "tag", "-a", "verified/v0.0.0-1-" + tip[:7],
                        "-m", "已验收：测试", tip], check=True)
        for r in ("origin", "github"):
            subprocess.run(["git", "-C", str(work), "push", "-q", r, "next"], check=True)
        return work, tip

    def test_guard1_backup_tagged_commit_blocks_fast_forward(self, tmp_path):
        work, tip = self._setup_with_backup(tmp_path)
        before = subprocess.run(["git", "-C", str(tmp_path / "gitee.git"), "rev-parse", "master"],
                                capture_output=True, text=True).stdout.strip()
        r = _promote(work)
        out = r.stdout + r.stderr
        assert r.returncode != 0, out
        assert "备用方案" in out and "backup-test-v1" in out, out
        assert "--only" in out, "应提示改走只推指定提交"
        assert subprocess.run(["git", "-C", str(tmp_path / "gitee.git"), "rev-parse", "master"],
                              capture_output=True, text=True).stdout.strip() == before, "被拒时不得改动正式版"

    def test_guard2_requires_confirm_and_lists_what_will_be_promoted(self, tmp_path):
        work, next_tip = TestPromoteRelease()._setup(tmp_path)
        before = subprocess.run(["git", "-C", str(tmp_path / "gitee.git"), "rev-parse", "master"],
                                capture_output=True, text=True).stdout.strip()
        r = _promote(work, confirm=False)
        out = r.stdout + r.stderr
        assert r.returncode != 0, out
        assert "提交清单" in out and "内容差异" in out, out
        assert "PROMOTE_CONFIRM=" in out, out
        assert next_tip[:7] in out, "清单里应列出会带上的提交"
        assert subprocess.run(["git", "-C", str(tmp_path / "gitee.git"), "rev-parse", "master"],
                              capture_output=True, text=True).stdout.strip() == before, "未确认时不得推送"

    def test_only_mode_promotes_selected_commit_and_leaves_rest(self, tmp_path):
        work, next_tip = TestPromoteRelease()._setup(tmp_path)
        # next 上再压一个“不打算晋升”的提交
        (work / "later.txt").write_text("later", encoding="utf-8")
        _commit_all(work, "不打算晋升的提交")
        for r in ("origin", "github"):
            subprocess.run(["git", "-C", str(work), "push", "-q", r, "next"], check=True)

        sha = next_tip[:7]
        r1 = _promote(work, "--only", sha, confirm=False)
        out1 = r1.stdout + r1.stderr
        assert r1.returncode != 0, out1
        m = re.search(r"PROMOTE_CONFIRM=([0-9a-f]+)", out1)
        assert m, out1
        env = dict(os.environ)
        env["PROMOTE_CONFIRM"] = m.group(1)
        r2 = _run(["bash", "scripts/promote_release.sh", "--only", sha], cwd=work, env=env)
        out2 = r2.stdout + r2.stderr
        assert r2.returncode == 0, out2
        # cherry-pick 会生成新提交，所以比“内容”（树）而不是比 SHA
        tree_master = subprocess.run(["git", "-C", str(tmp_path / "gitee.git"), "rev-parse", "master^{tree}"],
                                     capture_output=True, text=True).stdout.strip()
        tree_named = subprocess.run(["git", "-C", str(work), "rev-parse", f"{next_tip}^{{tree}}"],
                                    capture_output=True, text=True).stdout.strip()
        assert tree_master == tree_named, "正式版内容应恰好等于被点名那个提交的内容"
        # 后面那个“不打算晋升”的提交带来的文件不该出现在正式版里
        later = subprocess.run(["git", "-C", str(tmp_path / "gitee.git"), "cat-file", "-e", "master:later.txt"],
                               capture_output=True, text=True)
        assert later.returncode != 0, "未点名的提交被一起带上正式版了"
        assert "只推指定提交完成" in out2, out2

    def test_only_requires_verified_tag(self, tmp_path):
        """--only 也不能绕过验收闸门：没有 verified/* 覆盖时拒绝"""
        work, next_tip = TestPromoteRelease()._setup(tmp_path)
        # 把验收标签摘掉（模拟“还没验收”）
        subprocess.run(["git", "-C", str(work), "tag", "-d", f"verified/v0.0.0-1-{next_tip[:7]}"],
                       check=True, capture_output=True)
        r = _promote(work, "--only", next_tip[:7])
        out = r.stdout + r.stderr
        assert r.returncode != 0, out
        assert "验收" in out, out

    def test_only_rejects_commit_not_covered_by_verification(self, tmp_path):
        """验收标签只覆盖到 A，点名 A 之后的提交 B → 必须拒绝（不能“验收 A 发布 B”）"""
        work, next_tip = TestPromoteRelease()._setup(tmp_path)
        (work / "later.txt").write_text("later", encoding="utf-8")
        _commit_all(work, "验收之后又推的新提交")
        later = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                               capture_output=True, text=True).stdout.strip()
        for r in ("origin", "github"):
            subprocess.run(["git", "-C", str(work), "push", "-q", r, "next"], check=True)
        res = _promote(work, "--only", later[:7])
        out = res.stdout + res.stderr
        assert res.returncode != 0, out
        assert "验收" in out, out

    def test_only_dry_run_changes_nothing(self, tmp_path):
        work, next_tip = TestPromoteRelease()._setup(tmp_path)
        before = subprocess.run(["git", "-C", str(tmp_path / "gitee.git"), "rev-parse", "master"],
                                capture_output=True, text=True).stdout.strip()
        r = _run(["bash", "scripts/promote_release.sh", "--only", next_tip[:7], "--dry-run"], cwd=work)
        out = r.stdout + r.stderr
        assert r.returncode == 0 and "dry-run" in out, out
        after = subprocess.run(["git", "-C", str(tmp_path / "gitee.git"), "rev-parse", "master"],
                               capture_output=True, text=True).stdout.strip()
        assert after == before, "dry-run 不得改动正式版"
        branch = subprocess.run(["git", "-C", str(work), "branch", "--show-current"],
                                capture_output=True, text=True).stdout.strip()
        assert branch == "next", f"dry-run 不该切分支（现在在 {branch}）"

    def test_only_mode_refuses_backup_tagged_commit(self, tmp_path):
        work, tip = self._setup_with_backup(tmp_path)
        r = _promote(work, "--only", tip[:7])
        out = r.stdout + r.stderr
        assert r.returncode != 0, out
        assert "禁止推正式版" in out or "备用方案" in out, out
