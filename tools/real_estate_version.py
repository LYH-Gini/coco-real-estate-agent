"""Coco 自身版本信息工具（经纪人问"你是什么版本 / 是不是最新版"时用）

版本号存在仓库根 VERSION（形如 0.21.5-3：前半段是官方 Hermes 版本，后半段是本仓库第 N 次发行）。
官方 `hermes --version` 显示的是底座版本，不是 Coco 版本——所以这个信息必须由 Coco 自己如实回答，
不能让模型猜。

"是不是最新版"（2026-09-28 加）：**只读**查一次远端有没有新提交 —— `git ls-remote` 拿当前分支的
上游分支头与本地 HEAD 比对。**绝不 fetch / pull / checkout / reset**（与 tools/real_estate_update_guard.py
的口径一致：只拦写操作、只读放行），也不执行任何更新命令。三种情形如实分开：
查到有新版 / 查过就是最新 / 没连上网（查不到就直说，禁止说"已经是最新版"）。
"""
import json
from pathlib import Path

from tools.registry import registry

REPO_ROOT = Path(__file__).resolve().parents[1]

# 只读检查的缓存（同一会话反复问版本不该反复联网）
_CHECK_TTL_SECONDS = 600
_CHECK_CACHE: dict = {}

MSG_UNKNOWN_VERSION = "说不准：这台机器上没找到版本号文件。"
NOTE_FOR_MODEL = (
    "只把 message 那一句说给经纪人，不要另起一张「版本信息」表格，也不要出现通道（正式版／测试版）、"
    "底座版本、提交号、测试号、上游标签这些词（排查用，经纪人看不懂也用不上）。"
    "update_available=true 才可以说有新版；=false 才可以说「就是最新版本」（这是查过之后才允许说的话）；"
    "=null（没连上网）禁止说「已经是最新版」，只能给 coco update 这一步。")


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
    """通道：正式版 / 测试版 / 未知（2026-09-27 定：只用普通话词，且**不进 message**）

    测试版是自用通道，不对外说（2026-09-27）；
    非 master/next 的分支（自定义开发分支）与拿不到分支都算「未知」，具体分支名留在 branch 字段里备查。
    """
    branch = _git_branch()
    if branch == "master":
        return "正式版"
    if branch == "next":
        return "测试版"
    return "未知"


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


def _local_head() -> str:
    import subprocess

    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except Exception:
        return ""


def _upstream():
    """当前分支的上游跟踪：返回 (remote, ref)；拿不到返回 ('', '')

    **远程名不能硬编码**（实例上可能只有一个 `origin`，而我们本地才有 dev-gitee / dev-gh），
    一律按实例自己的 `@{u}` 推。
    """
    import subprocess

    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--abbrev-ref",
                              "--symbolic-full-name", "@{u}"],
                             capture_output=True, text=True, timeout=5)
        full = out.stdout.strip()
    except Exception:
        return "", ""
    if "/" not in full:
        return "", ""
    remote, _, ref = full.partition("/")
    return remote, ref


def _candidate_refs():
    """要查的 (remote, ref) 候选：优先上游跟踪分支，其次远程里的同名分支（最多 2 个）

    实例是 install.sh 装的 → `branch.<通道>.remote/merge` 都配好了（`@{u}` 一定有）；
    开发机上的克隆可能没配上游，这里按远程名兜底试一次（仍然只读）。
    """
    import subprocess

    remote, ref = _upstream()
    if remote and ref:
        return [(remote, ref)]
    branch = _git_branch()
    if not branch:
        return []
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "remote"],
                             capture_output=True, text=True, timeout=5)
        remotes = [r.strip() for r in out.stdout.splitlines() if r.strip()]
    except Exception:
        return []
    return [(r, branch) for r in remotes[:2]]


def _remote_head(timeout: int = 15):
    """只读查远端分支头（`git ls-remote --heads`，不改本地任何东西）；拿不到返回 None

    `GIT_TERMINAL_PROMPT=0` + ssh BatchMode：凭据缺失时**立刻失败**，不弹交互提示
    （否则工具调用会卡在等密码上）。查不到就是查不到，交给上层如实说。
    """
    import os
    import subprocess

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    for remote, ref in _candidate_refs():
        try:
            out = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-remote", "--heads", remote, ref],
                                 capture_output=True, text=True, timeout=timeout, env=env)
        except Exception:
            continue
        for line in out.stdout.splitlines():
            sha, _, name = line.partition("\t")
            if name.strip().endswith(ref):
                return sha.strip()
    return None


def check_update(use_cache: bool = True) -> dict:
    """有没有新版本 → {"available": True/False/None, "remote_head": ..., "local_head": ...}

    只读：远端分支头与本地 HEAD 比对，不一致即认为实例落后、有更新（"本地领先远端"这种开发机
    场景也会算 true，正式实例不会出现）。拿不到（网络不通 / 没有上游 / 没有 git）一律
    available=None —— 查不到就说查不到，绝不猜。
    """
    import time

    now = time.time()
    if use_cache and _CHECK_CACHE.get("at") and now - _CHECK_CACHE["at"] < _CHECK_TTL_SECONDS:
        return dict(_CHECK_CACHE["result"])

    local = _local_head()
    remote = _remote_head()
    if local and remote:
        result = {"available": remote != local, "remote_head": remote[:7], "local_head": local[:7]}
    else:
        result = {"available": None, "remote_head": None, "local_head": local[:7] or None}

    _CHECK_CACHE.update({"at": now, "result": result})
    return dict(result)


def _version_message(ver: str, label: str) -> str:
    """给经纪人的那一句（三种情形分开说；2026-09-28 定的原文，别改回机器腔）"""
    chk = check_update()
    if chk["available"] is True:
        tail = ("我这边查到已经有新版本了 —— 在服务器上执行 coco update 就能更新"
                "（这条我不能替你执行）。")
    elif chk["available"] is False:
        tail = "我刚查过，这台机器现在就是最新版本。"
    else:
        tail = ("我刚才没连上网，这次查不到有没有新版 —— 你可以在服务器上执行 coco update，"
                "它会自己检查并更新。")
    return f"Coco v{ver}{label}。{tail}"


def get_coco_version(task_id: str = None) -> str:
    """查询 Coco 当前版本号，并只读地查一次有没有新版本"""
    ver = _read_first_line(REPO_ROOT / "VERSION") or "未知"
    base = ver.split("-")[0] if "-" in ver else ver
    upstream = _read_first_line(REPO_ROOT / "UPSTREAM_VERSION").splitlines()
    commit = _git_short_commit()
    channel = _channel_label()
    test_tag = _test_tag() if channel == "测试版" else ""
    if ver == "未知":
        # 版本号文件都没有 → 不猜、不编，直接说清（2026-09-27 定文案）
        version_line = MSG_UNKNOWN_VERSION
        chk = {"available": None, "remote_head": None}
    else:
        # 给经纪人的一句话：只有正式版才点明"正式版"，测试版不对外说通道（他自己用的）
        label = "（正式版）" if channel == "正式版" else ""
        version_line = _version_message(ver, label)
        chk = check_update()
    return json.dumps({
        "success": True,
        "coco_version": ver,
        "hermes_base": base,
        "commit": commit,
        "channel": channel,
        "test_tag": test_tag,
        "branch": _git_branch(),
        "upstream_tag": upstream[0] if upstream else None,
        "update_available": chk.get("available"),
        "update_remote_head": chk.get("remote_head"),
        "note_for_model": NOTE_FOR_MODEL,
        "message": version_line,
    }, ensure_ascii=False)


registry.register(
    name="get_coco_version",
    toolset="real_estate",
    schema={"name": "get_coco_version", "description": (
        "查询 Coco 自身的版本号，并**只读**查一次有没有新版本（`git ls-remote` 比对上游分支，"
        "不执行更新、不改动本地代码）。经纪人问'你是什么版本/版本号是多少/是不是最新版/该不该更新'时调用。"
        "把返回的 message 那一句如实告诉经纪人即可；查到有新版、查过就是最新、没连上网三种情形都要如实说，"
        "查不到时禁止说'已经是最新版'。注意：`hermes --version` 显示的是底座（官方 Hermes）版本，"
        "不是 Coco 版本，不要拿它当答案。"), "parameters": {
        "type": "object",
        "properties": {},
    }},
    handler=lambda args, **kw: get_coco_version(),
)
