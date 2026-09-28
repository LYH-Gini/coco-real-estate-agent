"""发版防混乱的三道保险（2026-09-21 要求，1/2/3 全做）。

背景：「发版时如果有人正好在拉代码安装，会不会装到混乱的版本」。查证结论：
git 推送是原子的，clone/pull 只会拿到"推完前"或"推完后"的完整快照，不会半截；
但有三处时间窗会造成版本不一致 —— ① 两个远程（Gitee/GitHub）推送之间的时间差；
② 同一远程上"功能提交"与"版本号提交"分两次推的时间差；③ 标签/发行版晚于推送创建。
为此加了三道保险，本文件把它们钉住：
  1. 安装/更新/`coco version`/版本工具都会打印**提交短哈希**（对上"哪一次提交"）；
  2. `push_all.sh` 推送**带重试**，并在推完后**逐个远程复核、落后的自动补推**；
  3. `update.sh` 用 **flock 独占锁**，同一实例不会并发跑两次更新。
"""
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _run(cmd, cwd=None, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or REPO_ROOT, env=env)


class TestCommitHashVisible:
    """保险 1：到处都能看到"提交号"，出问题一眼对上"""

    def test_shell_scripts_syntax_ok(self):
        for f in ("install.sh", "scripts/update.sh", "scripts/push_all.sh", "scripts/coco.sh"):
            r = _run(["bash", "-n", f])
            assert r.returncode == 0, f"{f} 语法错误：{r.stderr}"
        for f in ("install.sh", "scripts/update.sh"):
            text = (REPO_ROOT / f).read_text(encoding="utf-8")
            assert "提交" in text and "rev-parse --short HEAD" in text, f"{f} 没有打印提交号"

    def test_coco_version_prints_commit(self):
        r = _run(["bash", "scripts/coco.sh", "version"])
        assert r.returncode == 0, r.stderr
        out = r.stdout.strip()
        assert "Coco v" in out and "提交" in out, out
        head = _run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
        assert head and head in out, f"输出里没有当前提交 {head}：{out}"

    def test_version_tool_exposes_commit(self):
        sys.path.insert(0, str(REPO_ROOT))
        from tools.real_estate_version import get_coco_version

        data = json.loads(get_coco_version())
        head = _run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
        # 2026-09-27 改：提交号只留在 commit 字段（排查用），不再出现在给经纪人看的 message 里
        assert data["commit"] == head
        assert head not in data["message"], data["message"]


class TestUpdateLock:
    """保险 3：并发跑两次更新会被 flock 挡住"""

    def _make_fake_repo(self, tmp_path):
        root = tmp_path / "fakerepo"
        (root / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPTS / "update.sh", root / "scripts" / "update.sh")
        (root / "VERSION").write_text("0.0.0-1\n", encoding="utf-8")
        return root

    def test_lock_blocks_second_run(self, tmp_path):
        """在测试进程内先持锁（模拟"另一个更新正在跑"），再执行 update.sh → 应当明确退出"""
        import fcntl

        root = self._make_fake_repo(tmp_path)
        lock_path = root / ".coco-update.lock"
        with open(lock_path, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                r = _run(["bash", str(root / "scripts" / "update.sh")], cwd=root)
                assert r.returncode != 0, "拿不到锁时应当退出"
                assert "更新正在进行中" in (r.stdout + r.stderr), r.stdout + r.stderr
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


class TestPushAllRetryAndBackfill:
    """保险 2：推送带重试；推完复核，落后的远程自动补推"""

    def _setup(self, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "master", str(work)], check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "-C", str(work), "config", k, v], check=True)
        (work / "scripts").mkdir()
        shutil.copy(SCRIPTS / "push_all.sh", work / "scripts" / "push_all.sh")
        (work / "VERSION").write_text("0.0.0-1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), "commit", "-qm", "init"], check=True)
        for name in ("gitee.git", "github.git"):
            subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / name)], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(tmp_path / "gitee.git")], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "github", str(tmp_path / "github.git")], check=True)
        return work

    def _env(self):
        return {**os.environ, "PUSH_RETRY_SLEEP": "0"}

    def _remote_head(self, repo):
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "master"],
                              capture_output=True, text=True).stdout.strip()

    def test_pushes_both_and_reports_sync(self, tmp_path):
        work = self._setup(tmp_path)
        r = _run(["bash", "scripts/push_all.sh"], cwd=work, env=self._env())
        assert r.returncode == 0, r.stdout + r.stderr
        local = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                               capture_output=True, text=True).stdout.strip()
        assert self._remote_head(tmp_path / "gitee.git") == local
        assert self._remote_head(tmp_path / "github.git") == local
        assert "两仓库同步完成" in r.stdout

    def test_backfills_a_lagging_remote(self, tmp_path):
        work = self._setup(tmp_path)
        _run(["bash", "scripts/push_all.sh"], cwd=work, env=self._env())
        # 制造"一个远程落后"：把 GitHub 的 master 回退一格
        subprocess.run(["git", "-C", str(tmp_path / "github.git"), "update-ref", "refs/heads/master",
                        self._remote_head(tmp_path / "github.git")], check=True)
        (work / "new.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), "commit", "-qm", "second"], check=True)
        # 手工把 Gitee 推上去、GitHub 故意留旧（模拟"上次一个远程失败"）
        subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "master"], check=True)
        r = _run(["bash", "scripts/push_all.sh"], cwd=work, env=self._env())
        assert r.returncode == 0, r.stdout + r.stderr
        local = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                               capture_output=True, text=True).stdout.strip()
        assert self._remote_head(tmp_path / "github.git") == local, "落后的远程没有被补推"

    def test_reports_failure_when_remote_unreachable(self, tmp_path):
        work = self._setup(tmp_path)
        subprocess.run(["git", "-C", str(work), "remote", "set-url", "github",
                        str(tmp_path / "nonexistent.git")], check=True)
        r = _run(["bash", "scripts/push_all.sh"], cwd=work, env=self._env())
        assert r.returncode != 0
        assert "重试" in (r.stdout + r.stderr), r.stdout + r.stderr


class TestPushAllNonCurrentBranch:
    """推"非当前分支"时的校验（真 bug 记录）：

    在 next 分支上执行 `push_all.sh master` 时，旧版拿本地 HEAD（next）去比远程 master，
    结果**误报 SHA 不一致**。现在按"被推的分支"的 SHA 校验。
    """

    def _setup(self, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "master", str(work)], check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "-C", str(work), "config", k, v], check=True)
        (work / "scripts").mkdir()
        shutil.copy(SCRIPTS / "push_all.sh", work / "scripts" / "push_all.sh")
        (work / "VERSION").write_text("0.0.0-1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), "commit", "-qm", "init"], check=True)
        for name in ("gitee.git", "github.git"):
            subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / name)], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(tmp_path / "gitee.git")], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "github", str(tmp_path / "github.git")], check=True)
        return work

    def test_push_master_while_on_next_branch(self, tmp_path):
        work = self._setup(tmp_path)
        # 造一个 ahead 的 next 分支并切过去（本地 HEAD ≠ master）
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "next"], check=True)
        (work / "next.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), "commit", "-qm", "next work"], check=True)

        env = {**os.environ, "PUSH_RETRY_SLEEP": "0"}
        r = subprocess.run(["bash", "scripts/push_all.sh", "master"], cwd=work,
                           capture_output=True, text=True, env=env)
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert "两仓库同步完成" in out, out
        master_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "master"],
                                    capture_output=True, text=True).stdout.strip()
        for remote in ("gitee.git", "github.git"):
            got = subprocess.run(["git", "-C", str(tmp_path / remote), "rev-parse", "master"],
                                 capture_output=True, text=True).stdout.strip()
            assert got == master_sha, f"{remote} 的 master 应为本地 master（{master_sha[:7]}），实际 {got[:7]}"


class TestPushAllTwoRepos:
    """双仓库（2026-09-28 起）：master → 正式仓，next → 开发仓；推错仓库要能挡住。

    正式仓里出现 next（= 把未验收代码公开）、开发仓里出现 master（= 两条线互相打架）都会被
    channel-guard 工作流拦下；这里守的是**推送脚本自己的路由**：推 next 时压根不该碰正式仓的远程。
    """

    def _setup(self, tmp_path, with_dev_remotes=True):
        work = tmp_path / "work"
        work.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "master", str(work)], check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "-C", str(work), "config", k, v], check=True)
        (work / "scripts").mkdir()
        shutil.copy(SCRIPTS / "push_all.sh", work / "scripts" / "push_all.sh")
        (work / "VERSION").write_text("0.0.0-1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), "commit", "-qm", "init"], check=True)
        for name in ("gitee.git", "github.git", "dev-gitee.git", "dev-gh.git"):
            subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / name)], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(tmp_path / "gitee.git")], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "github", str(tmp_path / "github.git")], check=True)
        if with_dev_remotes:
            subprocess.run(["git", "-C", str(work), "remote", "add", "dev-gitee", str(tmp_path / "dev-gitee.git")], check=True)
            subprocess.run(["git", "-C", str(work), "remote", "add", "dev-gh", str(tmp_path / "dev-gh.git")], check=True)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "next"], check=True)
        (work / "dev.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), "commit", "-qm", "dev work"], check=True)
        return work

    def _env(self):
        return {**os.environ, "PUSH_RETRY_SLEEP": "0"}

    def _remote_head(self, repo, branch):
        # --verify --quiet：分支不存在时输出为空（直接 rev-parse <name> 会把名字回显出来，误判成"存在"）
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                              capture_output=True, text=True).stdout.strip()

    def test_next_goes_to_dev_repo_only(self, tmp_path):
        work = self._setup(tmp_path)
        r = _run(["bash", "scripts/push_all.sh", "next"], cwd=work, env=self._env())
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert "开发仓" in out, out
        next_sha = subprocess.run(["git", "-C", str(work), "rev-parse", "next"],
                                  capture_output=True, text=True).stdout.strip()
        for remote in ("dev-gitee.git", "dev-gh.git"):
            assert self._remote_head(tmp_path / remote, "next") == next_sha, f"{remote} 没拿到 next"
        # 正式仓的两个远程不该出现 next 分支
        for remote in ("gitee.git", "github.git"):
            assert self._remote_head(tmp_path / remote, "next") == "", f"{remote} 上不该有 next"

    def test_missing_dev_remote_prints_setup_commands(self, tmp_path):
        work = self._setup(tmp_path, with_dev_remotes=False)
        r = _run(["bash", "scripts/push_all.sh", "next"], cwd=work, env=self._env())
        out = r.stdout + r.stderr
        assert r.returncode != 0, out
        assert "缺少远程" in out, out
        assert "git remote add dev-gitee" in out, out



class TestPromoteAndVerifyRouting:
    """通道路由（2026-09-28 仓库拆分后）：**开发线的产物只进开发仓**

    真实踩坑（本轮实测）：`promote_release.sh` 原来只有一组 `REMOTES=("origin" "github")`，
    "把测试通道推齐"那一步把 `next` 推到了**正式仓**（Gitee/GitHub 都出现了 next 分支）——
    与 `channel-guard`「正式仓只放 master、别人只能拿到正式版」直接冲突。
    本类钉住：next 分支与验收标签走**开发仓**（dev-gitee / dev-gh）；master 与正式标签走**正式仓**。
    """

    def _setup(self, tmp_path, with_verified_tag=True):
        work = tmp_path / "work"
        work.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "master", str(work)], check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "-C", str(work), "config", k, v], check=True)
        (work / "scripts").mkdir()
        for s in ("promote_release.sh", "mark_verified.sh"):
            shutil.copy(SCRIPTS / s, work / "scripts" / s)
        (work / "VERSION").write_text("0.21.5-9\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), "commit", "-qm", "init"], check=True)
        for name in ("gitee.git", "github.git", "dev-gitee.git", "dev-gh.git"):
            subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / name)], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(tmp_path / "gitee.git")], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "github", str(tmp_path / "github.git")], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "dev-gitee", str(tmp_path / "dev-gitee.git")], check=True)
        subprocess.run(["git", "-C", str(work), "remote", "add", "dev-gh", str(tmp_path / "dev-gh.git")], check=True)
        subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "master"], check=True)
        subprocess.run(["git", "-C", str(work), "push", "-q", "github", "master"], check=True)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "next"], check=True)
        (work / "dev.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), "commit", "-qm", "dev work"], check=True)
        sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
        if with_verified_tag:
            subprocess.run(["git", "-C", str(work), "tag", "-a", f"verified/v0.21.5-9-{sha[:7]}",
                            "-m", "v0.21.5-9 已验收", sha], check=True)
        return work, sha

    def _head(self, repo, branch):
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet",
                               f"refs/heads/{branch}"],
                              capture_output=True, text=True).stdout.strip()

    def _has_tag(self, repo, tag):
        return bool(subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet",
                                    f"refs/tags/{tag}"],
                                   capture_output=True, text=True).stdout.strip())

    def test_promote_dry_run_syncs_next_to_dev_repo_only(self, tmp_path):
        """晋升前的"推齐测试通道"只许碰开发仓，正式仓不许出现 next"""
        work, sha = self._setup(tmp_path)
        r = _run(["bash", "scripts/promote_release.sh", "--tag", "v0.21.5-9", "--dry-run"], cwd=work)
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        for remote in ("dev-gitee.git", "dev-gh.git"):
            assert self._head(tmp_path / remote, "next") == sha, f"{remote} 没拿到 next：{out}"
        for remote in ("gitee.git", "github.git"):
            assert self._head(tmp_path / remote, "next") == "", f"{remote} 上不该有 next：{out}"

    def test_mark_verified_tag_goes_to_dev_repo(self, tmp_path):
        """验收标签钉的是测试通道的提交 → 只进开发仓"""
        work, sha = self._setup(tmp_path, with_verified_tag=False)
        r = _run(["bash", "scripts/mark_verified.sh", "--note", "验收通过：版本/更新口径"], cwd=work)
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        tag = f"verified/v0.21.5-9-{sha[:7]}"
        for remote in ("dev-gitee.git", "dev-gh.git"):
            assert self._has_tag(tmp_path / remote, tag), f"{remote} 没拿到验收标签：{out}"
        for remote in ("gitee.git", "github.git"):
            assert not self._has_tag(tmp_path / remote, tag), f"{remote} 上不该有开发线的验收标签：{out}"
