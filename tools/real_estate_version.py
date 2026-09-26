"""Coco 自身版本信息工具（经纪人问"你是什么版本"时用）

版本号存在仓库根 VERSION（形如 0.21.3-7：前半段是官方 Hermes 版本，后半段是本仓库第 N 次发行）。
官方 `hermes --version` 显示的是底座版本，不是 Coco 版本——所以这个信息必须由 Coco 自己如实回答，
不能让模型猜。
"""
import json
from pathlib import Path

from tools.registry import registry

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_first_line(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _git_branch() -> str:
    """当前分支（用于区分稳定通道 / 测试通道）。"""
    import subprocess

    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "branch", "--show-current"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except Exception:
        return ""


def _channel_label() -> str:
    branch = _git_branch()
    if branch == "master":
        return "稳定通道"
    if branch == "next":
        return "测试通道"
    return "自定义通道" if branch else "未知通道"


def _test_tag() -> str:
    """测试号：测试通道上离当前提交最近的测试标签（如 v0.21.3-67-test1）。"""
    import subprocess

    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "describe", "--tags",
                              "--match", "v*-test*", "--abbrev=0"],
                             capture_output=True, text=True, timeout=5)
        tag = out.stdout.strip()
        if not tag:
            return ""
        ahead = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-list", "--count", f"{tag}..HEAD"],
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return f"{tag} +{ahead} 提交" if ahead not in ("", "0") else tag
    except Exception:
        return ""


def _git_short_commit() -> str:
    """当前代码的提交短哈希（对上"到底是哪一次提交"，排查时最有用）。"""
    import subprocess

    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "未知"
    except Exception:
        return "未知"


def get_coco_version(task_id: str = None) -> str:
    """查询 Coco 当前版本号（含所基于的官方 Hermes 版本）"""
    ver = _read_first_line(REPO_ROOT / "VERSION") or "未知"
    base = ver.split("-")[0] if "-" in ver else ver
    upstream = _read_first_line(REPO_ROOT / "UPSTREAM_VERSION").splitlines()
    commit = _git_short_commit()
    channel = _channel_label()
    test_tag = _test_tag() if channel == "测试通道" else ""
    version_line = f"Coco v{ver} · 提交 {commit} · {channel}"
    if test_tag:
        version_line += f" · 测试号 {test_tag}"
    return json.dumps({
        "success": True,
        "coco_version": ver,
        "hermes_base": base,
        "commit": commit,
        "channel": channel,
        "test_tag": test_tag,
        "branch": _git_branch(),
        "upstream_tag": upstream[0] if upstream else None,
        "message": version_line,
    }, ensure_ascii=False)


registry.register(
    name="get_coco_version",
    toolset="real_estate",
    schema={"name": "get_coco_version", "description": "查询 Coco 自身的版本号（含所基于的官方 Hermes 版本）。经纪人问'你是什么版本/系统版本号是多少/是不是最新版/该不该更新'时调用本工具如实回答。注意：`hermes --version` 显示的是底座（官方 Hermes）版本，不是 Coco 版本，不要拿它当答案。", "parameters": {
        "type": "object",
        "properties": {},
    }},
    handler=lambda args, **kw: get_coco_version(),
)
