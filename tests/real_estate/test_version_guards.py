"""get_coco_version 的口径守护（2026-09-27 第十二组 F438/F439）

改前实测证据：/root/coco-tool-audit/results/raw/t96.before.log
  · message 是机器腔「Coco v0.21.5-1 · 提交 a3f018a6 · 测试通道」；
  · 工具描述承诺回答"是不是最新版 / 该不该更新"，返回里却没有任何依据 —— 模型只能猜。

老板 2026-09-27 定的口径：
  · 正式版 message = 「Coco v0.21.5-1（正式版）。想知道有没有新版：在服务器上执行 coco update（会检查并更新）。」
  · 测试版**不对外**：message 里不出现"测试版"这类字样，通道只留在结构字段里；
  · 找不到版本号 = 「说不准：这台机器上没找到版本号文件。」
  · `channel` 值只用 正式版 / 测试版 / 未知；commit、branch、upstream_tag 保留（诊断用，不进 message）。
"""
import json

import pytest

import tools.real_estate_version as mod


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """搭一个假安装目录：VERSION / UPSTREAM_VERSION + 假分支"""

    def _setup(version="0.21.5-1", branch="master", upstream="v2026.9.24\n"):
        if version is not None:
            (tmp_path / "VERSION").write_text(version + "\n", encoding="utf-8")
        if upstream is not None:
            (tmp_path / "UPSTREAM_VERSION").write_text(upstream, encoding="utf-8")
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(mod, "_git_branch", lambda: branch or "")
        monkeypatch.setattr(mod, "_git_short_commit", lambda: "abc1234")
        monkeypatch.setattr(mod, "_test_tag", lambda: "")
        return json.loads(mod.get_coco_version())

    return _setup


class TestMessageWording:
    """F438/F439：message 给经纪人一句人话，并且真的能回答"该不该更新\""""

    def test_stable_message(self, fake_env):
        data = fake_env(branch="master")
        assert data["message"] == (
            "Coco v0.21.5-1（正式版）。"
            "想知道有没有新版：在服务器上执行 coco update（会检查并更新）。"), data["message"]

    def test_next_message_is_not_outward_facing(self, fake_env):
        """测试版是我自己用的，不要对外说（2026-09-27 老板定）"""
        data = fake_env(branch="next")
        assert "测试版" not in data["message"], data["message"]
        assert "测试通道" not in data["message"], data["message"]
        assert data["message"].startswith("Coco v0.21.5-1"), data["message"]
        assert "coco update" in data["message"], data["message"]

    def test_message_has_no_internal_jargon(self, fake_env):
        for branch in ("master", "next"):
            data = fake_env(branch=branch)
            for word in ("提交", "通道", "abc1234", "master", "next", "branch", "HEAD"):
                assert word not in data["message"], f"{branch} 的 message 里还有「{word}」：{data['message']}"

    def test_message_tells_how_to_update_not_a_guess(self, fake_env):
        """工具不联网 → 不许说自己是最新版，只能给可执行的下一步"""
        data = fake_env(branch="master")
        assert "coco update" in data["message"], data["message"]
        assert "已经是最新" not in data["message"], data["message"]

    def test_unknown_install_message(self, fake_env):
        data = fake_env(version=None, branch=None)
        assert data["message"] == "说不准：这台机器上没找到版本号文件。", data["message"]
        assert data["coco_version"] in ("未知", None, ""), data


class TestStructureFields:
    """结构字段：通道用普通话词、诊断字段保留"""

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
        assert data["coco_version"] == "0.21.5-1"

    def test_success_true_even_when_unknown(self, fake_env):
        assert fake_env(version=None, branch=None)["success"] is True

    def test_version_with_whitespace_normalized(self, fake_env):
        assert fake_env(version="  0.21.5-1  ")["coco_version"] == "0.21.5-1"
