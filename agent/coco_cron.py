"""
Coco 房产助理 - 定时任务注册与对齐

任务表（2026-09-23 拍板重新设计，替换原来的「早报 + 午间检查 + 每 30 分钟逾期检查」）：
  1. coco_daily_report    09:00 每天    上班早报：今天要跟进谁 / 今天带看 / 生日 / 成交节点
  2. coco_overdue_sentinel 10:00、17:00 逾期哨兵（纯脚本，不烧 token；同一客户当天只提醒一次）
  3. coco_opportunity     12:30 每天    机会提醒（降价捞回 / 新上房源匹配，没机会不吭声）
  4. coco_day_end         20:30 每天    收工小结：今天做了什么 / 该做没做 / 明天第一件事
  5. coco_weekly_report   08:30 周一    周报：上周活动量、渠道、该盯的人、本周重点

设计取向（为什么是这几条）：
- 每条消息都要回答"今天做什么"，不做数据播报，不发"今日无异常"这类占屏话；
- 频率越高的检查越要"只在有事时说话"，并且尽量脚本化（不叫模型、不烧 token）；
- 同一件事当天只提醒一次，逾期客户催过就不再重复念；
- 只用经纪人自己录入的房源/客户数据，不编造（尤其是生日：只对已录入生日的客户提醒）。

开启方式：经纪人一句话（工具 enable_cron）或 .env.db 里设 COCO_ENABLE_CRON=1。
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# 标记文件：防止重复注册（放在 HERMES_HOME 下，与 cron 存储一致）
_MARKER = ".coco_cron_registered"
_CRON_JOBS = ()

# ==================== 任务提示词（改这里就等于改经纪人机器上的行为） ====================
_DAILY_PROMPT = (
    "你是Coco房产助理。系统会在下面附上刚查好的今日数据（Script Output）。\n"
    "请按数据的板块顺序写成一条给经纪人的早报，用「老板早，今天的情况：」开头：\n"
    "· 每个板块用【】标题 + 「· 」条目，一条一行；\n"
    "· 数据里写「无」的板块就照实写一句「今天没有…」，不要省略、不要编；\n"
    "· 只能用数据里出现过的客户名、房源、数字，不许添加、不许推测；\n"
    "· 生日板块只列数据里给出的客户（库里没录入生日的客户不会出现在数据里）；\n"
    "· 不要写「数据要点」这类小结，不要加「需要我做什么」清单，不要重复已列出的数字。"
)

_OPPORTUNITY_PROMPT = (
    "你是Coco房产助理。系统会在下面附上刚筛出来的机会数据（Script Output）。\n"
    "请写成一条给经纪人的机会提醒：用「📣 今天的机会」开头，逐条列房源与可联系的客户，\n"
    "保留编号、价格、客户等级等原始信息，不许编造、不许增删数据；\n"
    "末尾加一句建议动作（例如「优先联系 X」）。全文不超过 8 行，数据里没有的一律不写。"
)

_DAYEND_PROMPT = (
    "你是Coco房产助理。系统会在下面附上今天的收尾数据（Script Output）。\n"
    "请按板块写成一条收工小结，用「老板，今天的收尾：」开头，每个板块用【】标题 + 「· 」条目；\n"
    "数字照抄数据，不要口算、不要编造；数据里写「无」的板块照实写一句；\n"
    "不要加「需要我做什么」清单，不要写客套话。"
)

_WEEKLY_PROMPT = (
    "你是Coco房产助理。系统会在下面附上近 7 天的数据（Script Output）。\n"
    "请写成一条周报，用「老板，上周情况（区间见数据）：」开头，按数据的板块顺序写，数字照抄；\n"
    "最后加【本周重点】2-3 条**具体**建议：要点名到人、房源或渠道"
    "（例如「周三前联系王芳，S级已逾期1天」），\n"
    "禁止「关注S级客户跟进」「及时录入新房源」这类放谁身上都行的空话；不许编造数据。"
)

# 任务定义清单（4 元组：(任务名, cron 表达式, 注册名, 提示词或脚本参数 dict)）
_AVAILABLE_JOBS = (
    ("coco_daily_report", "0 9 * * *", "coco_daily_report", _DAILY_PROMPT),
    ("coco_overdue_sentinel", "0 10,17 * * *", "coco_overdue_sentinel",
     {"script": "coco_cron_overdue.py", "no_agent": True}),
    ("coco_opportunity", "30 12 * * *", "coco_opportunity",
     {"script": "coco_cron_opportunity.py", "prompt": _OPPORTUNITY_PROMPT}),
    ("coco_day_end", "30 20 * * *", "coco_day_end",
     {"script": "coco_cron_dayend.py", "prompt": _DAYEND_PROMPT}),
    ("coco_weekly_report", "30 8 * * 1", "coco_weekly_report",
     {"script": "coco_cron_weekly.py", "prompt": _WEEKLY_PROMPT}),
)

# 任务的中文名与「什么时候发」：工具说明、开启回执都从这里取，
# 改任务表时只改这张表，话术不会跟真实时间表脱节（2026-09-24 修：曾写死旧时间表）。
_JOB_LABELS = {
    "coco_daily_report": ("上班早报", "09:00 每天"),
    "coco_overdue_sentinel": ("逾期提醒", "10:00、17:00"),
    "coco_opportunity": ("机会提醒", "12:30 每天"),
    "coco_day_end": ("收工小结", "20:30 每天"),
    "coco_weekly_report": ("周报", "周一 08:30"),
}


def job_label(name: str) -> str:
    """任务名 → 给经纪人看的名字（认不出就原样返回，不编）"""
    return _JOB_LABELS.get(name, (name, ""))[0]


def job_schedule_summary() -> str:
    """按 _AVAILABLE_JOBS 顺序生成「09:00 每天 上班早报 / 10:00、17:00 逾期提醒 / …」"""
    parts = []
    for item in _AVAILABLE_JOBS:
        label, when = _JOB_LABELS.get(item[2], (item[2], ""))
        parts.append(f"{when} {label}".strip())
    return " / ".join(parts)


# 已废弃任务：注册/同步时若发现残留就删掉（否则新旧一起发，经纪人被轰）
# 2026-09-23 取消：coco_midday_check（与早报重叠，且没异常也发"今日无异常"）、
# coco_overdue_check（每 30 分钟重复念同一批客户）；更早取消的生日/看门狗任务一并清掉。
_DEPRECATED_JOB_NAMES = (
    "coco_midday_check", "coco_overdue_check", "coco_birthday_check", "coco_watchdog",
)

# 需要安装到 HERMES_HOME/scripts/ 的脚本（官方调度器只允许在那里执行 cron 脚本）
_SCRIPT_FILES = (
    "coco_cron_overdue.py", "coco_cron_opportunity.py", "coco_cron_daily.py",
    "coco_cron_dayend.py", "coco_cron_weekly.py",
)

_SHIM_TEMPLATE = '''"""Coco cron 脚本入口（自动生成，勿手改；改仓库里的同名脚本）

cron 脚本只允许放在 HERMES_HOME/scripts/ 下执行（官方调度器有路径护栏），
所以这里只做转发：把仓库里的同名脚本跑起来。
"""
import runpy
import sys

_REPO = {repo!r}
_TARGET = {target!r}
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
runpy.run_path(_TARGET, run_name="__main__")
'''


def _marker_path() -> str:
    """标记文件路径：跟随 HERMES_HOME（与 cron 存储同目录）"""
    try:
        from hermes_constants import get_hermes_home
        home = get_hermes_home()
        return os.path.join(str(home), _MARKER)
    except Exception:
        # 兜底：环境变量
        home = os.getenv("HERMES_HOME") or os.path.expanduser("~/.hermes")
        return os.path.join(home, _MARKER)


def _cron_store_ready() -> bool:
    """确认 cron 存储可用"""
    try:
        from cron.jobs import get_cron_output_dir
        get_cron_output_dir()
        return True
    except Exception:
        return False


def _job_exists(job_name: str) -> bool:
    """通过官方 list_jobs 接口检查同名任务是否已注册（不猜存储路径）"""
    try:
        from cron.jobs import list_jobs
        jobs = list_jobs(include_disabled=True)
        for job in jobs:
            if job.get("name") == job_name:
                return True
    except Exception as e:
        logger.warning("[Coco] list_jobs failed: %s", e)
    return False


def _job_spec_kwargs(item) -> dict:
    """把清单条目展开成 create_job/update_job 的参数字典"""
    kwargs = {"schedule": item[1], "name": item[2]}
    spec = item[3]
    if isinstance(spec, dict):  # 脚本型任务或"脚本 + 提示词"任务
        kwargs.update(spec)
    else:
        kwargs["prompt"] = spec
    return kwargs


def _scripts_dir() -> Path:
    """cron 脚本目录（必须与官方调度器的解析规则一致：HERMES_HOME/scripts）"""
    return Path(_marker_path()).parent / "scripts"


def _install_cron_scripts() -> list:
    """把仓库里的 Coco cron 脚本装到 HERMES_HOME/scripts/（内容一致就不重写）

    官方调度器只执行 HERMES_HOME/scripts/ 下的脚本（绝对路径越界会被拒），
    所以仓库里的是源文件，这里生成同名转发入口。返回本次写入的脚本名。
    """
    installed = []
    try:
        repo = Path(__file__).resolve().parents[1]
        target_dir = _scripts_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in _SCRIPT_FILES:
            source = repo / "scripts" / name
            if not source.exists():
                logger.warning("[Coco] cron script source missing: %s", source)
                continue
            content = _SHIM_TEMPLATE.format(repo=str(repo), target=str(source))
            dest = target_dir / name
            try:
                if dest.exists() and dest.read_text(encoding="utf-8") == content:
                    continue
                dest.write_text(content, encoding="utf-8")
                installed.append(name)
                logger.info("[Coco] cron script installed: %s", dest)
            except Exception as e:
                logger.warning("[Coco] cron script %s install failed: %s", name, e)
    except Exception as e:
        logger.warning("[Coco] install coco cron scripts failed: %s", e)
    return installed


def _sync_coco_jobs() -> dict:
    """把已注册任务对齐到代码里的最新定义（提示词/时间/脚本）+ 清理废弃任务

    为什么要有这一步：任务定义是**注册时写进任务记录**的（cron/jobs.json），
    代码里改文案/改时间**不会**自动覆盖已经存在的任务——注册路径遇到同名任务一律跳过，
    结果就是"改了代码，早报还按旧口径报"。真实教训：早报指令原文只点"S/A级客户状态"，
    改成四级全列后，经纪人机器上那条老任务仍是旧文案，早报继续漏 B 级。

    只动名字与 _AVAILABLE_JOBS 完全一致的任务；不相干的任务（经纪人自己建的）不碰。
    返回 {"prompts": [...], "schedules": [...], "scripts": [...], "removed": [...], "installed": [...]}。
    """
    result = {"prompts": [], "schedules": [], "scripts": [], "removed": [], "installed": []}
    result["installed"] = _install_cron_scripts()
    try:
        from cron.jobs import list_jobs, remove_job, update_job

        jobs = list_jobs(include_disabled=True)
        existing = {}
        for job in jobs:
            name = job.get("name") or ""
            if name and name not in existing:
                existing[name] = job

        for job in jobs:  # 1) 清掉废弃任务
            name = job.get("name") or ""
            if name in _DEPRECATED_JOB_NAMES and name not in result["removed"]:
                remove_job(job.get("id"))
                result["removed"].append(name)
                logger.info("[Coco] deprecated cron job removed: %s", name)

        for item in _AVAILABLE_JOBS:  # 2) 对齐提示词 / 时间 / 脚本
            job = existing.get(item[2])
            if not job:
                continue
            want = _job_spec_kwargs(item)
            updates = {}
            if "prompt" in want and (job.get("prompt") or "") != want["prompt"]:
                updates["prompt"] = want["prompt"]
            expr = (job.get("schedule") or {}).get("expr")
            if expr and expr != want.get("schedule"):
                updates["schedule"] = want["schedule"]
            if "script" in want and (job.get("script") or "") != want["script"]:
                updates["script"] = want["script"]
            if "no_agent" in want and bool(job.get("no_agent")) != bool(want["no_agent"]):
                updates["no_agent"] = want["no_agent"]
            if not updates:
                continue
            if update_job(job.get("id"), updates) is None:
                logger.warning("[Coco] cron job sync skipped (job gone): %s", item[2])
                continue
            for key, bucket in (("prompt", "prompts"), ("schedule", "schedules"),
                               ("script", "scripts"), ("no_agent", "scripts")):
                if key in updates and item[2] not in result[bucket]:
                    result[bucket].append(item[2])
            logger.info("[Coco] cron job synced: %s (%s)", item[2], ",".join(sorted(updates)))
    except Exception as e:
        logger.warning("[Coco] sync coco cron jobs failed: %s", e)
    return result


def _create_job(item, chat_id: str) -> None:
    """按清单条目建任务（脚本型任务不传工具集；create_job 的 prompt 是必填位置参数）"""
    from cron.jobs import create_job
    kwargs = _job_spec_kwargs(item)
    prompt = kwargs.pop("prompt", None)
    if prompt is not None:
        kwargs["enabled_toolsets"] = ["real_estate"]
    create_job(prompt=prompt, deliver=f"feishu:{chat_id}", **kwargs)


def register_coco_cron_jobs(chat_id: str) -> dict:
    """注册 Coco 定时任务到指定飞书会话（默认关闭，2026-08-12 定）

    默认不注册任何定时任务（消耗 token）。如需开启：在 .env.db 设置
    COCO_ENABLE_CRON=1 并重启服务，此函数才会注册 _AVAILABLE_JOBS 中的任务。

    无论开关是否打开，都会先把已注册任务的提示词/时间/脚本对齐到代码最新定义，
    并清掉废弃任务（对齐只改任务记录，不新增任务，不消耗额外 token）。

    Args:
        chat_id: 飞书会话 ID

    Returns:
        dict: {"registered": [...], "skipped": [...], "synced": {...}}
    """
    result = {"registered": [], "skipped": []}

    synced = _sync_coco_jobs()
    if any(synced.values()):
        result["synced"] = synced

    # 开关：默认关闭；COCO_ENABLE_CRON=1 才注册
    if os.getenv('COCO_ENABLE_CRON', '0') != '1':
        logger.info("[Coco] 定时任务默认关闭（COCO_ENABLE_CRON 未设为 1），跳过注册")
        return result

    marker = _marker_path()
    all_jobs = list(_AVAILABLE_JOBS)

    # 标记存在则跳过（幂等）；缺失任务由 _job_exists 兜底补注册
    if os.path.exists(marker):
        missing = [item[2] for item in all_jobs if not _job_exists(item[2])]
        if not missing:
            return result
        logger.info("[Coco] missing jobs to re-register: %s", missing)

    if not _cron_store_ready():
        logger.warning("[Coco] cron store not ready, skip cron registration")
        return result

    for item in all_jobs:
        name = item[2]
        if _job_exists(name):
            result["skipped"].append(name)
            continue
        try:
            _create_job(item, chat_id)
            result["registered"].append(name)
            logger.info("[Coco] cron job registered: %s -> %s", name, chat_id)
        except Exception as e:
            logger.warning("[Coco] cron job %s registration failed: %s", name, e)
            result["skipped"].append(f"{name}(error)")

    # 写标记
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as f:
            f.write("1")
    except Exception:
        pass

    return result


def enable_coco_cron_jobs(chat_id: str) -> dict:
    """经纪人自助开启定时任务（2026-08-12 加）：注册 _AVAILABLE_JOBS 全部任务

    供工具 enable_cron 调用。与 register_coco_cron_jobs 不同：不受 COCO_ENABLE_CRON
    环境变量限制，经纪人一句话即可开启。
    """
    result = {"registered": [], "skipped": []}
    if not _cron_store_ready():
        return {"registered": [], "skipped": [], "error": "定时任务服务暂时不可用，请稍后再试。"}
    synced = _sync_coco_jobs()
    if any(synced.values()):
        result["synced"] = synced
    for item in _AVAILABLE_JOBS:
        name = item[2]
        if _job_exists(name):
            result["skipped"].append(name)
            continue
        try:
            _create_job(item, chat_id)
            result["registered"].append(name)
            logger.info("[Coco] cron job enabled: %s -> %s", name, chat_id)
        except Exception as e:
            logger.warning("[Coco] cron job %s enable failed: %s", name, e)
            result["skipped"].append(f"{name}(error)")
    return result


def disable_coco_cron_jobs() -> dict:
    """经纪人自助关闭定时任务（2026-08-12 加）：删除已注册的 Coco 任务

    连废弃任务一起删（老版本注册的午间检查/30 分钟检查也得清干净）。
    """
    removed = []
    try:
        from cron.jobs import list_jobs, remove_job
        known = {item[2] for item in _AVAILABLE_JOBS} | set(_DEPRECATED_JOB_NAMES)
        for job in list_jobs(include_disabled=True):
            name = job.get("name") or ""
            if name in known:
                remove_job(job.get("id"))
                removed.append(name)
                logger.info("[Coco] cron job disabled: %s", name)
    except Exception as e:
        logger.warning("[Coco] disable_coco_cron_jobs failed: %s", e)
        return {"removed": removed, "error": str(e)}
    return {"removed": removed}


def remove_duplicate_coco_jobs() -> int:
    """清理重复注册的 Coco 任务，保留每个任务第一个，返回删除数"""
    removed = 0
    try:
        from cron.jobs import list_jobs, remove_job
        seen = set()
        for job in list_jobs(include_disabled=True):
            name = job.get("name") or ""
            if name.startswith("coco_"):
                if name in seen:
                    try:
                        remove_job(job.get("id"))
                        removed += 1
                        logger.info("[Coco] removed duplicate job: %s (%s)", name, job.get("id"))
                    except Exception as e:
                        logger.warning("[Coco] failed to remove duplicate %s: %s", name, e)
                else:
                    seen.add(name)
    except Exception as e:
        logger.warning("[Coco] remove_duplicate_coco_jobs failed: %s", e)
    return removed
