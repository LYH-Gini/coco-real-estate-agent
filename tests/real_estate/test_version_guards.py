"""get_coco_version 的口径守护（2026-09-27 F438/F439；2026-09-28 扩"能不能自己查最新版"）

改前实测证据：/root/coco-tool-audit/results/raw/t96.before.log
  · message 是机器腔「Coco v0.21.5-1 · 提交 a3f018a6 · 测试通道」；
  · 工具描述承诺回答"是不是最新版 / 该不该更新"，返回里却没有任何依据 —— 模型只能猜。

2026-09-28 定的三句话（逐字落地，别改回机器腔；也别说"仓库"这类词）：
  · 查到有新版：`Coco v0.21.5-3。我这边查到已经有新版本了 —— 在服务器上执行 coco update 就能更新（这条我不能替你执行）。`
  · 查过是最新：`Coco v0.21.5-3。我刚查过，这台机器现在就是最新版本。`
  · 没连上网：  `Coco v0.21.5-3。我刚才没连上网，这次查不到有没有新版 —— 你可以在服务器上执行 coco update，它会自己检查并更新。`

其余口径（2026-09-27 定，继续有效）：
  · 正式版 message 带「（正式版）」；测试版**不对外**说通道（自用通道），通道只留结构字段；
  · 找不到版本号 = 「说不准：这台机器上没找到版本号文件。」
  · message 里不出现通道/底座版本/提交号/测试号/分支这些词（模型会照念给经纪人）。
"""
import json

import pytest

import tools.real_estate_version as mod


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """搭一个假安装目录：VERSION / UPSTREAM_VERSION + 假分支 + 假的"有没有新版"结论"""

    def _setup(version="0.21.5-3", branch="master", upstream="v2026.9.24\n", update=None):
        if version is not None:
            (tmp_path / "VERSION").write_text(version + "\n", encoding="utf-8")
        if upstream is not None:
            (tmp_path / "UPSTREAM_VERSION").write_text(upstream, encoding="utf-8")
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(mod, "_git_branch", lambda: branch or "")
        monkeypatch.setattr(mod, "_git_short_commit", lambda: "abc1234")
        monkeypatch.setattr(mod, "_test_tag", lambda: "")
        monkeypatch.setattr(mod, "check_update",
                            lambda use_cache=True: {"available": update, "remote_head": "def5678",
                                                    "local_head": "abc1234"})
        return json.loads(mod.get_coco_version())

    return _setup


class TestMessageWording:
    """message 给经纪人一句人话，并且真的能回答"是不是最新版" """

    def test_update_available(self, fake_env):
        data = fake_env(branch="master", update=True)
        assert data["message"] == (
            "Coco v0.21.5-3（正式版）。我这边查到已经有新版本了 —— 在服务器上执行 coco update "
            "就能更新（这条我不能替你执行）。"), data["message"]

    def test_up_to_date(self, fake_env):
        data = fake_env(branch="master", update=False)
        assert data["message"] == (
            "Coco v0.21.5-3（正式版）。我刚查过，这台机器现在就是最新版本。"), data["message"]

    def test_no_network(self, fake_env):
        data = fake_env(branch="master", update=None)
        assert data["message"] == (
            "Coco v0.21.5-3（正式版）。我刚才没连上网，这次查不到有没有新版 —— "
            "你可以在服务器上执行 coco update，它会自己检查并更新。"), data["message"]

    def test_next_message_is_not_outward_facing(self, fake_env):
        """测试版是我自己用的，不要对外说通道（2026-09-27 定）"""
        for update in (True, False, None):
            data = fake_env(branch="next", update=update)
            assert "测试版" not in data["message"], data["message"]
            assert "测试通道" not in data["message"], data["message"]
            assert data["message"].startswith("Coco v0.21.5-3。"), data["message"]

    def test_message_has_no_internal_jargon(self, fake_env):
        for branch in ("master", "next"):
            for update in (True, False, None):
                data = fake_env(branch=branch, update=update)
                for word in ("提交", "通道", "abc1234", "def5678", "master", "next", "branch",
                             "HEAD", "仓库", "底座", "测试号"):
                    assert word not in data["message"], \
                        f"{branch}/{update} 的 message 里还有「{word}」：{data['message']}"

    def test_no_network_message_never_claims_up_to_date(self, fake_env):
        """查不到 → 不许说"已经是最新版"（2026-09-27 口径不变，2026-09-28 也只是放开"查过才可以说"）"""
        msg = fake_env(update=None)["message"]
        assert "已经是最新版" not in msg and "就是最新版本" not in msg, msg
        assert "coco update" in msg, msg

    def test_unknown_install_message(self, fake_env):
        data = fake_env(version=None, branch=None, update=None)
        assert data["message"] == "说不准：这台机器上没找到版本号文件。", data["message"]
        assert data["coco_version"] in ("未知", None, ""), data


class TestStructureFields:
    """结构字段：通道用普通话词、诊断字段保留、新版结论用机器可读字段"""

    def test_channel_values_are_plain_words(self, fake_env):
        assert fake_env(branch="master")["channel"] == "正式版"
        assert fake_env(branch="next")["channel"] == "测试版"
        assert fake_env(branch="feature/x")["channel"] == "未知"
        assert fake_env(branch=None)["channel"] == "未知"

    def test_diagnostic_fields_kept(self, fake_env):
        data = fake_env(branch="next")
        assert data["commit"] == "abc1234"
        assert data["branch"] == "next"
        assert data["upstream_tag"] == "v2026.9.24"
        assert data["hermes_base"] == "0.21.5"
        assert data["coco_version"] == "0.21.5-3"

    def test_update_fields_are_machine_readable(self, fake_env):
        assert fake_env(update=True)["update_available"] is True
        assert fake_env(update=False)["update_available"] is False
        assert fake_env(update=None)["update_available"] is None

    def test_note_for_model_carries_the_rule(self, fake_env):
        """给模型的那句话要说清：查不到不许说已是最新、不要列通道/底座那张表"""
        note = fake_env(update=None)["note_for_model"]
        assert "已经是最新版" in note and "通道" in note, note

    def test_success_true_even_when_unknown(self, fake_env):
        assert fake_env(version=None, branch=None)["success"] is True

    def test_version_with_whitespace_normalized(self, fake_env):
        assert fake_env(version="  0.21.5-3  ")["coco_version"] == "0.21.5-3"


class TestUpdateCheckIsReadOnlyAndHonest:
    """只读检查本身：不比不看、比了才敢下结论、拿不到就说拿不到、同一会话不反复联网"""

    def _patch_git(self, monkeypatch, local, remote):
        monkeypatch.setattr(mod, "_local_head", lambda: local)
        monkeypatch.setattr(mod, "_remote_head", lambda timeout=15: remote)
        mod._CHECK_CACHE.clear()

    def test_same_sha_means_up_to_date(self, monkeypatch):
        self._patch_git(monkeypatch, "a" * 40, "a" * 40)
        assert mod.check_update(use_cache=False)["available"] is False

    def test_different_sha_means_update_available(self, monkeypatch):
        self._patch_git(monkeypatch, "a" * 40, "b" * 40)
        assert mod.check_update(use_cache=False)["available"] is True

    def test_remote_unreachable_is_unknown_not_false(self, monkeypatch):
        """拿不到远端 → None（查不到），**绝不能**当成"已是最新" """
        self._patch_git(monkeypatch, "a" * 40, None)
        result = mod.check_update(use_cache=False)
        assert result["available"] is None and result["remote_head"] is None

    def test_no_git_repo_is_unknown(self, monkeypatch):
        self._patch_git(monkeypatch, "", None)
        assert mod.check_update(use_cache=False)["available"] is None

    def test_result_is_cached_so_repeat_questions_dont_hit_network(self, monkeypatch):
        calls = {"n": 0}

        def _counting_remote(timeout=15):
            calls["n"] += 1
            return "b" * 40

        monkeypatch.setattr(mod, "_local_head", lambda: "a" * 40)
        monkeypatch.setattr(mod, "_remote_head", _counting_remote)
        mod._CHECK_CACHE.clear()
        mod.check_update()
        mod.check_update()
        assert calls["n"] == 1, calls

    def test_remote_head_uses_ls_remote_only(self, monkeypatch):
        """拿远端头只用 `git ls-remote`（只读），且命令形态正确"""
        import subprocess as sp
        seen = []

        def _fake_run(cmd, **kwargs):
            seen.append(cmd)

            class R:
                stdout = "b" * 40 + "\trefs/heads/next\n"
                returncode = 0
            return R()

        monkeypatch.setattr(sp, "run", _fake_run)
        monkeypatch.setattr(mod, "_candidate_refs", lambda: [("origin", "next")])
        assert mod._remote_head() == "b" * 40
        assert seen == [["git", "-C", str(mod.REPO_ROOT), "ls-remote", "--heads", "origin", "next"]], seen

    def test_check_never_uses_write_verbs(self, monkeypatch):
        """只读性（行为判据）：整条检查链只跑 git 的只读子命令"""
        import subprocess as sp
        seen = []

        def _fake_run(cmd, **kwargs):
            seen.append(cmd)
            out = ""
            if isinstance(cmd, list) and len(cmd) > 3:
                verb = cmd[3]
                if verb == "rev-parse" and "HEAD" in cmd:
                    out = "a" * 40
                elif verb == "rev-parse":
                    out = "origin/next"
                elif verb == "ls-remote":
                    out = "b" * 40 + "\trefs/heads/next"

            class R:
                stdout = out
                returncode = 0
            return R()

        monkeypatch.setattr(sp, "run", _fake_run)
        mod._CHECK_CACHE.clear()
        result = mod.check_update(use_cache=False)
        verbs = [c[3] for c in seen if isinstance(c, list) and c[0] == "git" and len(c) > 3]
        assert verbs, seen
        assert set(verbs) <= {"rev-parse", "ls-remote", "describe", "branch", "remote"}, verbs
        assert "ls-remote" in verbs, f"必须用 ls-remote 拿远端头：{verbs}"
        assert result["available"] is True, result
