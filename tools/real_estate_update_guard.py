"""Coco 定制：更新类命令拦截（Coco 不要在网关会话里自己执行更新）

为什么需要这条守卫（2026-09-20 拍板 A+B）
------------------------------------------------
Coco 在飞书会话里有 terminal 工具（`hermes-feishu` 工具集包含 core 工具）。如果经纪人一句
"帮我更新"，她自己就敲了更新命令，后果是二选一的坏结果：

* `hermes update`（**官方**更新命令）——不会跑 Coco 自己的 `migrations/`（表结构会落后），
  还会 `git stash` + `git reset --hard`（可能覆盖经纪人手改过的代码）。
* `coco update`（等价于旧写法 `bash <安装目录>/scripts/update.sh`，我们的）——脚本第 7 步 `systemctl --user restart
  hermes-gateway` 会重启网关服务，而脚本与网关同属一个服务 cgroup → **连同脚本自己一起被杀**：
  更新跑成半截（收尾与体检没跑），同时把当前对话打断。

上游已有的 `gateway_lifecycle_block` 只覆盖 `hermes gateway restart|stop|uninstall`、
`launchctl ...`、`systemctl ... hermes-gateway`（且提示是英文、面向开发者），**不覆盖
`hermes update`**。所以这里补一条范围更全、话术给经纪人看的中文拦截：

    更新一律由经纪人在服务器终端执行；Coco 只负责把命令给出去。

本模块是 Coco 自有文件（不属于上游），改动上游的地方在 `tools/terminal_tool.py` 里的
COCO-PATCH 调用点，并由 `scripts/check_coco_hooks.py` 第 16 项守着，防止上游同步把它冲掉。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Optional

# 给经纪人看的话术（连同正确的更新命令一起给出）
# 口径（2026-09-28 定）：**只给 coco update 这一条** —— 不给等价写法、不列别的命令、不解释
# 为什么别的做法不行（经纪人只需要知道敲哪一条）。
REFUSAL_MESSAGE = (
    "【更新要你在服务器终端执行】\n"
    "  coco update\n"
    "我这边执行会重启服务、把我们的对话一起中断，所以这条必须你自己敲。更新只认这一条命令。\n"
    "【服务类命令】只给这几条：coco status（看服务状态）/ coco restart（重启服务）/ coco logs（看日志）。"
)

# 命中即拦截的更新类命令；均为「更新/重装/重启本机 Coco」语义，避免误伤普通命令
_UPDATE_PATTERNS = (
    re.compile(r"(?<![\w.\-/])hermes\s+update\b", re.I),                       # 官方更新
    re.compile(r"(?<![\w.\-/])hermes\s+gateway\s+(?:restart|stop|uninstall)\b", re.I),
    re.compile(r"""(?:^|[\s;&|(`])(?:sudo\s+)?bash\s+\S*install\.sh\b""", re.I),   # 安装脚本（会重建目录）
    re.compile(r"""(?:^|[\s;&|(`])(?:sudo\s+)?(?:bash|sh|source|\.)\s+\S*scripts/update\.sh\b""", re.I),
    re.compile(r"""(?:^|[\s;&|(`])(?:sudo\s+)?\S*scripts/update\.sh\b""", re.I),   # 直接执行 update.sh
    # 2026-09-22：原来只认 hermes-gateway，漏了旧口径 `sudo systemctl restart hermes`（经纪人真收到过这条）
    re.compile(r"systemctl\s+(?:-\S+\s+)*(?:restart|stop|start)\b[^\n]*\bhermes[.\-]?\w*", re.I),
    # coco 侧的写操作（2026-09-21 加）：coco 有了这些命令后，不拦就能绕过上面所有规则
    re.compile(r"(?<![\w.\-/])coco\s+(?:update|start|stop|restart|uninstall|restore)\b", re.I),
    re.compile(r"(?<![\w.\-/])coco\s+gateway\s+(?:install|start|stop|restart|uninstall)\b", re.I),
    re.compile(r"(?<![\w.\-/])coco\s+(?:model|setup|pairing)\b", re.I),   # 配置类要人工交互，别在会话里跑
    re.compile(r"(?<![\w.\-/])coco\s+cli\b", re.I),                       # 逃生口不给会话用（可跑任意官方命令）
    # 直接在我们的安装目录里做 git 变更（会造成"跑着的代码"与磁盘代码错位）
    re.compile(r"git\s+(?:-C\s+\S*(?:hermes-agent|coco-real-estate)\S*\s+)?(?:pull|checkout|reset|clean|stash)\b", re.I),
)

_BLOCKED_STATUS = "blocked"


def _blocked_json(message: str) -> str:
    """与上游守卫一致的返回形状（工具结果里带 error/status），便于模型与用户读懂"""
    return json.dumps({"error": message, "status": _BLOCKED_STATUS}, ensure_ascii=False)


def _normalize(command: str) -> str:
    """去掉换行与多余空白，便于正则匹配（保留 & | ; 等命令连接符）"""
    return " ".join(str(command or "").replace("\r", "\n").split())


def is_update_command(command: str) -> bool:
    """是否属于"更新/重装/重启 Coco"类命令（纯函数，便于单测）"""
    normalized = _normalize(command)
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _UPDATE_PATTERNS)


def coco_update_block(
    *,
    command: str,
    cwd: str | None = None,
    workdir: Optional[str] = None,
    session_key: str | None = None,
    **_ignored,
) -> Optional[str]:
    """更新类命令 → 返回拦截 JSON（字符串）；放行 → None。

    签名与上游守卫保持一致（多收关键字参数也不报错），这样 COCO-PATCH 的调用点可以与
    `gateway_lifecycle_block` 并列写，不必关心上游将来新增什么参数。
    """
    try:
        if is_update_command(command):
            return _blocked_json(REFUSAL_MESSAGE)
    except Exception:  # 守卫本身绝不能因为异常把终端工具弄挂
        return None
    return None


def install_dir() -> str:
    """Coco 安装目录（仅用于话术展示与调试）"""
    return os.environ.get("HERMES_AGENT_DIR") or str(Path(__file__).resolve().parents[1])
