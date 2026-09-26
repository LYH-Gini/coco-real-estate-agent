"""Coco 定时任务脚本共用工具（仓库内为源，安装时同步到 HERMES_HOME/scripts/）

为什么放在这里：cron 脚本只允许放在 HERMES_HOME/scripts/ 下执行（官方调度器有路径护栏），
所以仓库里的 scripts/coco_cron_*.py 才是源文件，安装时生成同名 shim 指向仓库。
本模块只做三件事：定位 HERMES_HOME、读写去重状态、拿数据库。
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path


def repo_root() -> Path:
    """仓库根目录（本文件在 scripts/ 下）"""
    return Path(__file__).resolve().parents[1]


def ensure_import_path() -> Path:
    """把仓库根与 scripts/ 放进 sys.path，保证 import agent.* / coco_cron_* 可用"""
    root = repo_root()
    for path in (str(root), str(root / "scripts")):
        if path not in sys.path:
            sys.path.insert(0, path)
    return root


def hermes_home() -> Path:
    """HERMES_HOME（与 cron 存储同目录）；取不到就用环境变量兜底"""
    try:
        from hermes_constants import get_hermes_home
        return Path(str(get_hermes_home()))
    except Exception:
        return Path(os.getenv("HERMES_HOME") or os.path.expanduser("~/.hermes"))


def state_path(name: str) -> Path:
    """去重状态文件路径（cron 目录下，和数据一起被备份/清理）"""
    return hermes_home() / "cron" / f"coco_state_{name}.json"


def load_state(name: str) -> dict:
    """读状态；文件不存在/损坏一律当空状态（哨兵宁可多提醒一次也不能崩）"""
    path = state_path(name)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(name: str, state: dict) -> None:
    """写状态（原子替换，失败不抛：状态丢了最多重复提醒一次）"""
    path = state_path(name)
    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass


def today_str(now: datetime = None) -> str:
    return (now or datetime.now()).date().isoformat()


def get_db():
    """房产数据库实例（与工具层同一个入口）"""
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


def clip(text, limit: int = 20) -> str:
    """把上次跟进内容压成一行短句，超长截断加省略号"""
    if not text:
        return ""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def parse_dt(value):
    """把 to_dict() 出来的 iso 字符串还原成 datetime；解析不了返回 None"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except Exception:
        return None
