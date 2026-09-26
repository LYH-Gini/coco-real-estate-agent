"""Coco 版本查询工具 get_coco_version 测试（2026-09-18 加）

版本号来自仓库根 VERSION；官方 `hermes --version` 只显示底座版本，所以必须由工具如实回答。
"""
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestCocoVersion:
    def test_returns_repo_version(self):
        """返回值与仓库根 VERSION 一致，并推导出底座版本"""
        import tools.real_estate_version as vmod
        data = json.loads(vmod.get_coco_version())
        assert data["success"] is True
        ver = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        assert data["coco_version"] == ver
        assert data["hermes_base"] == ver.split("-")[0]

    def test_message_is_user_facing_wording(self):
        """对外措辞为「Coco vX-Y · 提交 … · 通道」，并附提交号（便于对上"哪一次提交"）"""
        import tools.real_estate_version as vmod
        data = json.loads(vmod.get_coco_version())
        assert re.match(r"^Coco v[\d.]+-\d+ · 提交 [0-9a-f]{6,} · \S+通道( · 测试号 .+)?$", data["message"]), data["message"]

    def test_upstream_tag_reported(self):
        """同时回报所基于的官方 tag（供排查用）"""
        import tools.real_estate_version as vmod
        data = json.loads(vmod.get_coco_version())
        assert data["upstream_tag"] and data["upstream_tag"].startswith("v")

    def test_registry_dispatch_ignores_runtime_kwargs(self):
        """走注册表真实分发：框架注入的 session_id/task_id 不能把工具调用打崩"""
        import tools.real_estate_version  # noqa: F401 —— 触发注册
        import model_tools  # noqa: F401
        from tools.registry import registry
        entry = registry.get_entry("get_coco_version")
        assert entry is not None
        data = json.loads(entry.handler({}, session_id="agent:main:feishu:dm:oc_x", task_id="t1"))
        assert data["success"] is True

    def test_exposed_in_real_estate_toolset(self):
        """必须在 real_estate 工具集清单里，否则模型看不到这个工具"""
        from toolsets import TOOLSETS
        assert "get_coco_version" in TOOLSETS["real_estate"]["tools"]


class TestReleaseVersionConsistency:
    """发版一致性：仓库根 VERSION 与两个 README 的版本行必须同步。

    为什么要有：README 的当前版本行与徽章要跟着 VERSION 走，发版时容易漏改。
    """

    def test_readmes_advertise_the_repo_version(self):
        ver = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        for name in ("README.md", "README.zh-CN.md"):
            text = (REPO_ROOT / name).read_text(encoding="utf-8")
            assert f"v{ver}" in text, f"{name} 里没有当前版本 v{ver}（发版时漏改）"
