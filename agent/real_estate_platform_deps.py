"""平台适配器的依赖体检（2026-09-27 加）。

背景：Coco 的安装只装「项目基础依赖 + 房产专用包」，而平台适配器各有自己的第三方包
（多数是 aiohttp）。`aiohttp` 只写在 pyproject 的可选 extras 里，基础依赖不含它，于是任何
Coco 实例都没有 aiohttp —— 配微信时被 `check_weixin_requirements()` 拦下（现场报
"Missing dependencies: Weixin needs aiohttp and cryptography."）。

这里只做一件事：**列出已配置的平台，挨个调用它自己的依赖检查函数**，供 `coco check` 给出
可执行结论。平台清单与主密钥名从官方向导表取，不在本文件里另写一份，避免两处漂移。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

# 补装命令里要用到的包（版本与 pyproject 一致；install.sh / scripts/update.sh 装的就是这两个）
DEP_PIP_SPEC = '"aiohttp==3.14.3" "cryptography==50.0.0"'


def env_value(path: str, key: str) -> str:
    """从 .env 里取一个键的值（只读，不解析 shell 语法以外的东西）。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if name.strip() == key:
                    return value.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _platform_table() -> Optional[List[dict]]:
    """平台清单，与 `coco gateway setup` 菜单同源：内置表 + 插件注册表。

    只看静态 `_PLATFORMS` 会漏掉插件平台（如飞书）—— 那些是通过平台注册表挂上来的，
    官方向导用 `_all_platforms()` 做合并，这里照用。读不到就返回 None。
    """
    try:
        from hermes_cli.gateway_setup_wizard import _all_platforms

        return list(_all_platforms())
    except Exception:
        pass
    try:
        from hermes_cli.gateway_setup_wizard import _PLATFORMS

        return list(_PLATFORMS)
    except Exception:
        return None


def configured_platforms(env_path: str) -> Optional[List[Tuple[str, str]]]:
    """已配置的平台：[(key, label)]。

    判断依据是该平台声明的主密钥（token_var）是否已在 .env 里填了值。
    返回 None 表示**读不到平台清单**（与"一个平台都没配"是两回事，调用方要分开报）。
    """
    table = _platform_table()
    if table is None:
        return None
    found: List[Tuple[str, str]] = []
    for entry in table:
        key = entry.get("key")
        token_var = entry.get("token_var")
        if key and token_var and env_value(env_path, token_var):
            found.append((key, entry.get("label") or key))
    return found


def platform_dep_state(key: str) -> Tuple[Optional[bool], str]:
    """查一个平台的适配器依赖。

    返回 (状态, 说明)：True = 依赖齐全；False = 缺依赖（这个通道起不来）；None = 判断不了
    （模块导入失败或没有依赖检查函数，多半是版本差异，不当作故障）。
    """
    for mod_path in (f"plugins.platforms.{key}.adapter", f"gateway.platforms.{key}"):
        try:
            mod = __import__(mod_path, fromlist=["*"])
        except Exception:
            continue
        for fn_name in (f"check_{key}_requirements", "check_requirements"):
            checker = getattr(mod, fn_name, None)
            if callable(checker):
                try:
                    return bool(checker()), f"{mod_path}:{fn_name}"
                except Exception as exc:  # 适配器自己的检查函数抛错
                    return None, f"{mod_path}:{fn_name} 异常（{type(exc).__name__}）"

    # 兜底：平台注册表里登记的依赖门。适配器模块导入失败（最常见的就是 SDK 没装）时用得上——
    # 注册表的 check_fn 不需要装好 SDK 就能调用，正好回答"缺不缺依赖"。
    try:
        from gateway.platform_registry import platform_registry

        entry = platform_registry.get(key)
    except Exception:
        entry = None
    check_fn = getattr(entry, "check_fn", None)
    if callable(check_fn):
        try:
            return bool(check_fn()), f"platform_registry:{key}:check_fn"
        except Exception as exc:
            return None, f"platform_registry:{key}:check_fn 异常（{type(exc).__name__}）"
    return None, "没有可用的依赖检查函数"
