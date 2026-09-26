"""
Coco 房产工具 - 跟进管理
"""
import json
import re
from datetime import datetime
from tools.registry import registry
from agent.real_estate_display import attach_key_warning, mask_contacts
from agent.real_estate_input import (STAGE_LABELS, clamp_limit, clean_text, clip_text,
                                       norm_date, norm_id)

# 列表分页口径（与 list_customers/list_owners 同一套：默认 20、上限 200、≤0 与非数字按默认）
_LIST_LIMIT_DEFAULT = 20
_LIST_LIMIT_MAX = 200

# re_followups 的列宽：PostgreSQL 上 varchar 超长会让整单失败（sqlite 只是照存）
AGENT_ID_MAX = 100

# 逾期清单：默认在对话里列 50 条；limit 不设上限（2026-09-25：要看全部走文档，别塞对话）
OVERDUE_LIMIT_DEFAULT = 50
# 流失预警的风险分阈值：默认 40（中危起步）；传非数字/None/≤0 按这个默认值
CHURN_MIN_RISK_DEFAULT = 40

# 本模块"名单类"返回的默认展示条数（流失名单、阶段滞留名单；2026-09-25 定 20：
# 这类清单要能一眼看完，总数另给 total / stale_total）—— 改它等于同时改所有名单的上限
LIST_SHOW_LIMIT = 20
# 生成的清单文档只保留最近 N 份（每天早报都可能生成，不清理会越堆越多）
OVERDUE_DOC_KEEP = 20


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


# 跟进类型：内部存英文枚举（schema 里那 5 档），对外一律说中文
FOLLOWUP_TYPE_LABELS = {
    'call': '电话', 'visit': '带看', 'deal': '成交', 'note': '备注', 'reminder': '提醒',
}
_TYPE_ALIASES = {
    '电话': 'call', '打电话': 'call', '去电': 'call', '回访': 'call', 'phone': 'call',
    '带看': 'visit', '看房': 'visit', '到访': 'visit',
    '成交': 'deal', '签约': 'deal',
    '备注': 'note', '记录': 'note', '其他': 'note',
    '提醒': 'reminder', '待办': 'reminder',
}


def norm_followup_type(value):
    """跟进类型归一 → (枚举值, 提示或 None)。认英文枚举与常见中文说法，认不出不猜"""
    if value is None or str(value).strip() == '':
        return 'note', None
    text = str(value).strip().lower()
    if text in FOLLOWUP_TYPE_LABELS:
        return text, None
    if text in _TYPE_ALIASES:
        return _TYPE_ALIASES[text], None
    options = "、".join(f"{k}（{v}）" for k, v in FOLLOWUP_TYPE_LABELS.items())
    return None, f"跟进类型没能识别：收到的是「{value}」。可用：{options}"


def norm_followup_time(value, label='下次跟进时间'):
    """跟进时间归一 → ('HH:MM' 或 None, 提示或 None)。认 9:00 / 09:00 / 9点30 / 9点 / 0930 / 09:30:00"""
    if value is None:
        return None, None
    text = str(value).strip().replace('：', ':').replace('点', ':').replace('分', '').rstrip(':')
    if not text:
        return None, None
    if text.isdigit() and len(text) == 4:
        text = text[:2] + ':' + text[2:]
    if text.isdigit():
        text = text + ':00'
    matched = re.match(r'^(\d{1,2})(?::(\d{1,2}))?', text)
    if not matched or (':' in text and not matched.group(2)):
        return None, _time_problem(value, label)
    hour, minute = int(matched.group(1)), int(matched.group(2) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None, _time_problem(value, label)
    return f"{hour:02d}:{minute:02d}", None


def _time_problem(value, label='下次跟进时间'):
    return f"{label}没能识别：收到的是「{value}」。请用 09:30 这类写法"


def split_time_part(value):
    """从日期参数里拆出可能夹带的时刻 → (日期部分, 时刻部分或 None)

    模型常把整个 datetime 塞进 next_date（2026-09-28T14:30:00 / 2026-09-28 14:30），
    原先走 fromisoformat 能认，归一后必须照样认，不然后退成"日期格式错误"。
    带看侧（`real_estate_viewing.norm_viewing_when`）也用它 —— 「日期+时刻」的拆分只留这一处。
    """
    if value is None:
        return None, None
    if isinstance(value, datetime):
        has_time = bool(value.hour or value.minute)
        return value.date().isoformat(), (value.strftime('%H:%M:%S') if has_time else None)
    text = str(value).strip()
    for sep in ('T', 't', ' '):
        head, found, tail = text.partition(sep)
        if found and tail.strip():
            return head, tail
    return text, None


def norm_followup_when(date_value, time_value, date_label='下次跟进日期', time_label='下次跟进时间',
                       date_sample='2026-12-31'):
    """下次跟进时间归一 → (datetime 或 None, 'HH:MM' 或 None, 提示或 None)

    日期认 2026-12-31 / 2026/12/31 / 2026.12.31 / 2026年12月31日 / 12月31日，
    也认日期里夹带时刻的写法；显式传的 time 优先于日期里带的时刻。
    给了时刻就并进 datetime —— 与「设置提醒」「带看后自动提醒」同一口径。
    """
    date_part, embedded = split_time_part(date_value)
    day, problem = norm_date(date_part, date_label, date_sample)
    if problem:
        return None, None, problem
    time_text, problem = norm_followup_time(
        time_value if str(time_value or '').strip() else embedded, time_label)
    if problem:
        return None, None, problem
    if day and time_text:
        hour, minute = (int(part) for part in time_text.split(':'))
        day = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return day, time_text, None


def add_followup(
    customer_id: int, content: str, property_id: int = None,
    type: str = 'note', next_date: str = None, next_time: str = None,
    agent_id: str = None, task_id: str = None,
) -> str:
    """添加客户跟进记录

    写入前先认人认房：客户或房源不存在就如实说明，不写孤儿记录（孤儿跟进会被
    逾期列表/午间检查当成真客户报出来，经纪人只看到一个查不到的编号）。
    下次跟进日期支持 2026-12-31 / 2026/12/31 / 2026年12月31日 / 12月31日 等写法，也认 明天/后天/周三/下周三/3天后；
    给了时间就把时刻并进 next_date（与设置提醒、带看后自动提醒同一口径）。
    """
    customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if property_id is not None:
        property_id, problem = norm_id(property_id, '房源编号')
        if problem:
            return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    content = clean_text(content)
    if not content:
        return json.dumps({"success": False, "error": "跟进内容不能为空，请写一句这次沟通的情况"}, ensure_ascii=False)
    type_value, problem = norm_followup_type(type)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    next_date_dt, next_time_text, problem = norm_followup_when(next_date, next_time)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    warnings = []
    agent_id, clipped = clip_text(clean_text(agent_id), AGENT_ID_MAX)
    if clipped:
        warnings.append("经纪人编号" + clipped + "。")

    db = _get_db()
    customer = db.get_customer(customer_id)
    if not customer:
        return json.dumps({"success": False, "error": "客户不存在，请先在客户列表里核对编号"}, ensure_ascii=False)
    if property_id is not None and not db.get_property(property_id):
        return json.dumps({"success": False, "error": "房源不存在，请核对房源编号"}, ensure_ascii=False)
    result = db.add_followup(
        customer_id=customer_id, property_id=property_id, type=type_value,
        content=content, next_date=next_date_dt, next_time=next_time_text,
        agent_id=agent_id,
    )
    label = FOLLOWUP_TYPE_LABELS.get(type_value, '跟进')
    tail = ""
    if next_date_dt:
        when = next_date_dt.strftime('%Y-%m-%d %H:%M') if next_time_text else next_date_dt.strftime('%Y-%m-%d')
        tail = f"，下次跟进 {when}"
    payload = {"success": True,
               "message": f"已记录「{customer.get('name')}」的{label}跟进{tail}",
               "followup": result}
    if warnings:
        payload["warnings"] = warnings
    return json.dumps(payload, ensure_ascii=False)


def get_followups(customer_id: int, limit: int = _LIST_LIMIT_DEFAULT, task_id: str = None) -> str:
    """查看某位客户的跟进历史（最新的在前；默认 20 条、最多 200）

    返回 total=该客户跟进总条数、count=本次返回条数、truncated；被截断时给一句说明。
    "客户不存在"与"客户存在但没有跟进"分开说 —— 前者多半是编号打错了。
    每条带 type_label 中文类型与 property_title 关联房源标题（房源已删则给可读标注）。
    """
    limit = clamp_limit(limit, _LIST_LIMIT_DEFAULT, _LIST_LIMIT_MAX)
    customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    db = _get_db()
    if not db.get_customer(customer_id):
        return json.dumps({"success": False, "error": "客户不存在，请先在客户列表里核对编号"},
                          ensure_ascii=False)
    result, total = db.get_followups(customer_id, limit=limit, with_total=True)
    titles = db.get_property_titles([f.get('property_id') for f in result])
    items = []
    for row in result:
        item = dict(row)
        item['type_label'] = FOLLOWUP_TYPE_LABELS.get(row.get('type'), row.get('type'))
        pid = row.get('property_id')
        if pid is not None:
            item['property_title'] = titles.get(pid) or f"已删除房源（id={pid}）"
        items.append(item)
    payload = {"success": True, "followups": items, "count": len(items), "total": total,
               "truncated": bool(total and total > len(items))}
    if not total:
        payload["message"] = "这位客户还没有跟进记录"
    elif payload["truncated"]:
        payload["message"] = (f"共 {total} 条跟进，这里列最近 {len(items)} 条（最新在前）。"
                              f"要我列全就说一声。")
    return json.dumps(payload, ensure_ascii=False)


def _write_overdue_document(rows, labels, total):
    """把逾期清单写成一份完整 Markdown 文档 → 返回文件绝对路径（写不出返回 None）

    条数多时（>50）由 Coco 用 `MEDIA:路径` 把这份文档发给经纪人 —— 工具本身不发送，
    只为默认保留最近 20 份，避免每天早报都在磁盘上堆文件。
    """
    try:
        from hermes_constants import get_hermes_home
        doc_dir = get_hermes_home() / "doc_cache"
        doc_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now()
        path = doc_dir / f"逾期跟进_{stamp.strftime('%Y%m%d_%H%M%S')}.md"
        lines = [f"# 逾期跟进清单（共 {total} 条）", "",
                 f"生成时间：{stamp.strftime('%Y-%m-%d %H:%M')}｜按逾期最久排序", ""]
        for row in rows:
            label = labels.get(row.get('customer_id')) or {}
            name = label.get('name') or f"已删除客户（id={row.get('customer_id')}）"
            type_label = FOLLOWUP_TYPE_LABELS.get(row.get('type'), row.get('type'))
            content = (row.get('content') or '').replace('\n', ' ')
            if len(content) > 200:
                content = content[:200] + '…'
            when = ''
            if row.get('next_date'):
                try:
                    days = (stamp - datetime.fromisoformat(row['next_date'])).days
                    when = f"逾期 {days} 天" if days > 0 else "今天到期"
                except ValueError:
                    when = ''
            tail = f"｜{content}" if content else ""
            lines.append(f"- **{name}**（{label.get('tier') or '-'}级）{when}｜{type_label}{tail}")
        path.write_text("\n".join(lines) + "\n", encoding='utf-8')
        for old_doc in sorted(doc_dir.glob('逾期跟进_*.md'))[:-OVERDUE_DOC_KEEP]:
            try:
                old_doc.unlink()
            except OSError:
                pass
        return str(path)
    except Exception:
        return None


def get_overdue(limit: int = OVERDUE_LIMIT_DEFAULT, document: bool = False,
                task_id: str = None) -> str:
    """查看逾期跟进清单（按「每位客户最新一条跟进」的下次跟进时间是否已过判定）

    逾期最久在前；默认在对话里列 50 条（`limit` 不设上限，但对话里不宜塞太多）。
    条数多于 50 时：`document=true` 生成含**全部条目**的 Markdown 清单 → 返回 document_path，
    由 Coco 用 `MEDIA:路径` 发给经纪人（工具本身不发送）。
    返回 total=逾期总条数、count=本次返回条数、truncated。每条带客户名与中文类型；
    客户已被删（存量孤儿）给"已删除客户（id=N）"标注而不是裸编号。
    本工具还会顺手把长期无互动的客户**自动降一级**（S→A→B→C，同一天最多降一次），
    降级名单在 downgrades 字段里如实说明。
    """
    limit = clamp_limit(limit, OVERDUE_LIMIT_DEFAULT, None)
    db = _get_db()
    rows = db.get_overdue()
    total = len(rows)
    # 流失预警：先取一次快照，降级复用同一份（原先这里全库扫描两遍）
    stale = db.get_stale_customers()
    downgrade = db.auto_downgrade_stale_customers(stale=stale)
    wanted = rows if document else rows[:limit]
    labels = db.get_customer_labels([r.get('customer_id') for r in wanted])
    page = []
    for row in rows[:limit]:
        item = dict(row)
        item['type_label'] = FOLLOWUP_TYPE_LABELS.get(row.get('type'), row.get('type'))
        cid = row.get('customer_id')
        label = labels.get(cid)
        if label:
            item['customer_name'] = label['name']
            item['customer_tier'] = label['tier']
        else:
            item['customer_name'] = f"已删除客户（id={cid}）"
            item['customer_missing'] = True
        page.append(item)
    if total:
        message = f"有 {total} 条跟进已逾期（逾期最久在前）"
    elif db.count_customers():
        message = "暂无逾期跟进"
    else:
        message = "库里还没有客户，先登记客户再设跟进"
    response = {"success": True, "overdue": page, "count": len(page), "total": total,
                "truncated": bool(total > len(page)), "message": message}
    if document and total:
        doc_path = _write_overdue_document(rows, labels, total)
        if doc_path:
            response["document_path"] = doc_path
            response["message"] = f"完整清单已生成（共 {total} 条），我把它发给你"
        else:
            response["message"] = message + "（清单文档没生成成功，先按上面这些跟进吧）"
    elif response["truncated"]:
        response["message"] = (f"共 {total} 条逾期跟进，这里先列逾期最久的 {len(page)} 条。"
                              f"要看全部跟我说一声，我会生成一份 Markdown 清单（含全部条目）发给你")
    if downgrade.get('downgrades'):
        response['downgrades'] = downgrade['downgrades']
        response['message'] = message + f"；{len(downgrade['downgrades'])} 位客户长期无互动已自动降级"
    if stale:
        response['stale_customers'] = stale
    return json.dumps(response, ensure_ascii=False)


def stale_check(task_id: str = None) -> str:
    """流失预警检查：自动降级长期无互动客户并返回预警名单

    S级>5天无互动→降A，A级>10天→降B，B级>30天→降C（**同一天最多降一级**）；降级写入变更历史，
    名单里带降级依据（该等级的阈值）。预警名单默认只列最久的 `LIST_SHOW_LIMIT` 位，
    总数在 `total`（老字段 `still_stale_count` 保留 = 总数），被截断时 `truncated` 为真。
    空态分三种说法：没有客户 / 没有需要降级的 / 降了但没有仍超期的。
    """
    db = _get_db()
    # 先算一份流失快照，降级复用同一份（原先各扫一遍全库）
    stale = db.get_stale_customers()
    downgrade = db.auto_downgrade_stale_customers(stale=stale)
    downgraded = len(downgrade.get('downgrades') or [])
    total = len(stale)
    shown = stale[:LIST_SHOW_LIMIT]
    if not db.count_customers():
        message = "库里还没有客户，先登记客户再看流失情况"
    elif downgraded:
        message = f"本次自动降级 {downgraded} 位客户"
        if total:
            message += f"；还有 {total} 位仍长期无互动"
    else:
        message = f"本次没有需要降级的客户；仍有 {total} 位长期无互动" if total else "没有客户需要降级"
    payload = {
        "success": True,
        "downgrades": downgrade.get('downgrades') or [],
        "still_stale": shown,
        "count": len(shown),
        "total": total,
        "truncated": total > len(shown),
        "still_stale_count": total,       # 老字段保留：口径 = 流失客户总数
        "message": message,
    }
    if payload["truncated"]:
        payload["message"] += (f"。共 {total} 位客户长期无互动，这里列最久的 {len(shown)} 位"
                               f"（要我列全就说一声）")
    return json.dumps(payload, ensure_ascii=False)


def schedule_reminder(customer_id: int, date: str, time: str = None, content: str = None,
                      task_id: str = None) -> str:
    """设置客户跟进提醒（到点会出现在逾期提醒与早报里）

    日期/时间复用 add_followup 那套归一（`norm_followup_when`）：认 2026-12-31 / 2026/12/31 /
    2026年12月31日 / 12月31日（也认 明天/后天/周三/下周三/3天后），时间认 09:30 / 9点30 / 0930，
    不传时间按 09:00；认不出分别提示日期/时间。
    同一客户同一时刻的提醒不重复建（内容变了就把那条改掉）。返回 `reminder` 结构化记录供回显核对。
    """
    customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if not str(date or '').strip():
        return json.dumps({"success": False, "error": "请说个提醒日期（如 2026-12-31 或 12月31日）"},
                          ensure_ascii=False)
    next_date_dt, next_time_text, problem = norm_followup_when(
        date, time, '提醒日期', '提醒时间', '2026-12-31 或 12月31日')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if next_date_dt is None:
        return json.dumps({"success": False, "error": "请说个提醒日期（如 2026-12-31 或 12月31日）"},
                          ensure_ascii=False)
    if next_time_text is None:      # 没给时间 → 09:00（口径写进描述与文案，别落空串）
        next_time_text = '09:00'
        next_date_dt = next_date_dt.replace(hour=9, minute=0, second=0, microsecond=0)
    db = _get_db()
    customer = db.get_customer(customer_id)
    if not customer:
        return json.dumps({"success": False, "error": "客户不存在，请先在客户列表里核对编号"},
                          ensure_ascii=False)
    reminder_content = clean_text(content) or f"跟进客户 {customer.get('name')}"
    when = next_date_dt.strftime('%Y-%m-%d %H:%M')
    warnings = []
    if next_date_dt < datetime.now():
        warnings.append("提醒时间已经过了，会立刻出现在逾期提醒里")
    # 设提醒会取代该客户原来那条逾期提醒（逾期按"每位客户最新一条跟进"判定）—— 如实说明，别静默
    previous = db.get_latest_followup(customer_id)
    if previous and previous.get('next_date'):
        try:
            if datetime.fromisoformat(previous['next_date']) < datetime.now():
                warnings.append("这位客户之前那条逾期提醒不再出现了（系统按最新一条人工跟进算逾期）")
        except ValueError:
            pass

    existing = db.find_reminder(customer_id, next_date_dt)
    duplicate = bool(existing)
    if existing:
        if existing.get('content') != reminder_content:
            reminder = db.update_followup_content(existing['id'], reminder_content) or existing
            message = f"已把这条提醒的内容改成「{reminder_content}」"
        else:
            reminder = existing
            message = f"这位客户 {when} 的提醒已经在（第 {existing['id']} 条），没有重复加"
    else:
        reminder = db.add_followup(customer_id=customer_id, type='reminder',
                                   content=reminder_content, next_date=next_date_dt,
                                   next_time=next_time_text)
        message = f"已设置「{customer.get('name')}」{when} 的跟进提醒"
    payload = {"success": True, "message": message, "reminder": reminder}
    if duplicate:
        payload["duplicate"] = True
    if warnings:
        payload["warnings"] = warnings
    return json.dumps(payload, ensure_ascii=False)


def daily_report(task_id: str = None) -> str:
    """生成每日早报（今天要跟进的客户 / 今天到期与已逾期的跟进 / 客户与房源概览 / 流失预警）

    会顺手把长期无互动的客户自动降一级（S→A→B→C，同一天最多降一级），降级名单在 `report.downgrades` 里如实说明
    （含降级前后的等级、多久没互动、该等级的阈值）。流失名单只列最久的 `LIST_SHOW_LIMIT` 位，
    总数在 `report.stale_total`；今天要跟进的只列前 `TODAY_TASK_LIMIT` 位，总数在 `report.today_followups`。
    """
    db = _get_db()
    report = db.daily_report()
    # 先算一份流失快照，降级复用同一份（原先各扫一遍全库）
    stale = db.get_stale_customers()
    downgrade = db.auto_downgrade_stale_customers(stale=stale)
    if downgrade.get('downgrades'):
        report['downgrades'] = downgrade['downgrades']
    notes = []
    if not report.get('total_customers'):
        notes.append("库里还没有客户，先登记客户再看早报")
    else:
        shown_tasks = len(report.get('today_tasks') or [])
        if (report.get('today_followups') or 0) > shown_tasks:
            notes.append(f"今天要跟进 {report['today_followups']} 位，这里列前 {shown_tasks} 位")
    if stale:
        report['stale_total'] = len(stale)
        report['stale_customers'] = stale[:LIST_SHOW_LIMIT]
        if len(stale) > LIST_SHOW_LIMIT:
            notes.append(f"共 {len(stale)} 位客户长期无互动，这里列最久的 {LIST_SHOW_LIMIT} 位"
                         f"（要我列全就说一声）")
    payload = {"success": True, "report": report}
    if notes:
        payload["message"] = "；".join(notes)
    return json.dumps(payload, ensure_ascii=False)


def midday_check(task_id: str = None) -> str:
    """午间检查（今天的到期跟进与逾期情况 / 客户与房源概览 / 流失预警）

    逾期明细带客户名与等级（客户已被删的给「已删除客户（id=N）」标注）；只列最急的 5 条，总数在
    `check.overdue_count`。会顺手把长期无互动的客户自动降一级（S→A→B→C，同一天最多降一级），
    降级名单在 `check.downgrades` 里如实说明；流失名单只列最久的 `LIST_SHOW_LIMIT` 位，
    总数在 `check.stale_total`。
    """
    db = _get_db()
    check = db.midday_check()
    # 先算一份流失快照，降级复用同一份（原先各扫一遍全库）
    stale = db.get_stale_customers()
    downgrade = db.auto_downgrade_stale_customers(stale=stale)
    rows = check.get('overdue_customers') or []
    labels = db.get_customer_labels([r.get('customer_id') for r in rows])
    for row in rows:
        label = labels.get(row.get('customer_id'))
        if label:
            row['customer_name'] = label['name']
            row['customer_tier'] = label['tier']
        else:
            row['customer_name'] = f"已删除客户（id={row.get('customer_id')}）"
            row['customer_missing'] = True
    notes = []
    if not check.get('total_customers'):
        notes.append("库里还没有客户，先登记客户再看午间情况")
    elif (check.get('overdue_count') or 0) > len(rows):
        notes.append(f"当前有 {check['overdue_count']} 条逾期跟进，这里列最急的 {len(rows)} 条")
    if downgrade.get('downgrades'):
        check['downgrades'] = downgrade['downgrades']
    if stale:
        check['stale_total'] = len(stale)
        check['stale_customers'] = stale[:LIST_SHOW_LIMIT]
        if len(stale) > LIST_SHOW_LIMIT:
            notes.append(f"共 {len(stale)} 位客户长期无互动，这里列最久的 {LIST_SHOW_LIMIT} 位"
                         f"（要我列全就说一声）")
    payload = {"success": True, "check": check}
    if notes:
        payload["message"] = "；".join(notes)
    return json.dumps(payload, ensure_ascii=False)


TOOLS = [
    {"name": "add_followup", "description": "添加客户跟进记录（电话/带看/成交/备注/提醒），可关联房源、设下次跟进日期与时间；写入前会核对客户与房源是否存在", "parameters": {
        "type": "object", "properties": {
            "customer_id": {"type": "integer", "description": "客户ID"},
            "content": {"type": "string", "description": "跟进内容"},
            "property_id": {"type": "integer", "description": "关联房源ID"},
            "type": {"type": "string", "enum": ["call", "visit", "deal", "note", "reminder"],
                     "description": "跟进类型（call 电话 / visit 带看 / deal 成交 / note 备注 / reminder 提醒，也认中文写法）"},
            "next_date": {"type": "string",
                          "description": "下次跟进日期（2026-12-31 / 2026/12/31 / 2026年12月31日；也认 明天/后天/周三/下周三/3天后 这类相对说法；给了时间会并到这一刻）"},
            "next_time": {"type": "string", "description": "下次跟进时间，如 09:30（也认 9点30 / 0930）"},
        }, "required": ["customer_id", "content"],
    }, "handler": lambda args, **kw: add_followup(**args)},
    {"name": "get_followups", "description": "查看某位客户的跟进历史（最新的在前；默认返回 20 条、最多 200）。返回 total=该客户跟进总条数、count=本次返回条数、truncated；客户不存在会如实说明", "parameters": {
        "type": "object", "properties": {
            "customer_id": {"type": "integer", "description": "客户ID"},
            "limit": {"type": "integer", "description": "本次返回条数（默认 20，最多 200；传 0/负数/非数字按默认 20）"},
        }, "required": ["customer_id"],
    }, "handler": lambda args, **kw: get_followups(**args)},
    {"name": "get_overdue", "description": "查看逾期跟进清单（按「每位客户最新一条跟进」的下次跟进时间是否已过判定；逾期最久在前；默认在对话里列 50 条，limit 不设上限）。返回 total=逾期总条数、count=本次返回条数、truncated。条数多于 50 时用 document=true 生成完整 Markdown 清单（返回 document_path，用 MEDIA:路径 发给经纪人）。本工具会顺手把长期无互动的客户自动降一级（S→A→B→C，同一天只降一级），降级名单在 downgrades 里如实说明。", "parameters": {
        "type": "object", "properties": {
            "limit": {"type": "integer", "description": "本次返回条数（默认 50，不设上限；传 0/负数/非数字按默认 50）"},
            "document": {"type": "boolean", "description": "要不要生成完整清单文档（含全部条目，不受条数限制）；返回 document_path，用 MEDIA:路径 发给经纪人。默认 false"},
        },
    }, "handler": lambda args, **kw: get_overdue(**args)},
    {"name": "schedule_reminder", "description": "设置客户跟进提醒（到点会出现在逾期提醒与早报里）。日期认 2026-12-31 / 2026/12/31 / 2026年12月31日 / 12月31日（也认 明天/后天/周三/下周三/3天后），时间认 09:30 / 9点30 / 0930（不传时间按 09:00）。返回 reminder=新建（或已有）的提醒记录；同一客户同一时间的提醒不重复建。", "parameters": {
        "type": "object", "properties": {
            "customer_id": {"type": "integer", "description": "客户ID"},
            "date": {"type": "string",
                     "description": "提醒日期（2026-12-31 / 2026/12/31 / 2026年12月31日 / 12月31日；也认 明天/后天/周三/下周三/3天后 这类相对说法）"},
            "time": {"type": "string", "description": "提醒时间（如 09:30；也认 9点30 / 0930；不传按 09:00）"},
            "content": {"type": "string", "description": "提醒内容（不传默认「跟进客户 X」）"},
        }, "required": ["customer_id", "date"],
    }, "handler": lambda args, **kw: schedule_reminder(**args)},
    {"name": "daily_report", "description": "生成每日早报（今天要跟进的客户 / 今天到期与已逾期的跟进 / 客户与房源概览 / 流失预警）。会顺手把长期无互动的客户自动降一级（S→A→B→C，同一天最多降一级），降级名单在 report.downgrades 里如实说明；report.stale_total 是流失客户总数，report.stale_customers 只列最久的 20 位。", "parameters": {
        "type": "object", "properties": {},
    }, "handler": lambda args, **kw: daily_report()},
    {"name": "midday_check", "description": "午间检查（今天的到期跟进与逾期情况 / 客户与房源概览 / 流失预警）。会顺手把长期无互动的客户自动降一级（S→A→B→C，同一天最多降一级），降级名单在 check.downgrades 里如实说明；check.stale_total 是流失客户总数，check.stale_customers 只列最久的 20 位。", "parameters": {
        "type": "object", "properties": {},
    }, "handler": lambda args, **kw: midday_check()},
    {"name": "stale_check", "description": "流失预警检查：自动降级长期无互动客户（S级>5天→A，A级>10天→B，B级>30天→C，同一天最多降一级）并返回预警名单。降级名单在 downgrades 里（含降级前后的等级、多久没互动、该等级的阈值）；报告 total=流失客户总数、count=本次返回条数、truncated，名单默认只列最久的 20 位。", "parameters": {
        "type": "object", "properties": {},
    }, "handler": lambda args, **kw: stale_check()},
]

registry.register(
    name="add_followup",
    toolset="real_estate",
    schema={"name": "add_followup", "description": "添加客户跟进记录（电话/带看/成交/备注/提醒），可关联房源、设下次跟进日期与时间；写入前会核对客户与房源是否存在", "parameters": TOOLS[0]["parameters"]},
    handler=TOOLS[0]["handler"],
)
registry.register(
    name="get_followups",
    toolset="real_estate",
    schema={"name": "get_followups", "description": "查看某位客户的跟进历史（最新的在前；默认返回 20 条、最多 200）。返回 total=该客户跟进总条数、count=本次返回条数、truncated；客户不存在会如实说明", "parameters": TOOLS[1]["parameters"]},
    handler=TOOLS[1]["handler"],
)
registry.register(
    name="get_overdue",
    toolset="real_estate",
    schema={"name": "get_overdue", "description": "查看逾期跟进清单（按「每位客户最新一条跟进」的下次跟进时间是否已过判定；逾期最久在前；默认在对话里列 50 条，limit 不设上限）。返回 total=逾期总条数、count=本次返回条数、truncated。条数多于 50 时用 document=true 生成完整 Markdown 清单（返回 document_path，用 MEDIA:路径 发给经纪人）。本工具会顺手把长期无互动的客户自动降一级（S→A→B→C，同一天只降一级），降级名单在 downgrades 里如实说明。", "parameters": TOOLS[2]["parameters"]},
    handler=TOOLS[2]["handler"],
)
registry.register(
    name="schedule_reminder",
    toolset="real_estate",
    schema={"name": "schedule_reminder", "description": "设置客户跟进提醒（到点会出现在逾期提醒与早报里）。日期认 2026-12-31 / 2026/12/31 / 2026年12月31日 / 12月31日（也认 明天/后天/周三/下周三/3天后），时间认 09:30 / 9点30 / 0930（不传时间按 09:00）。返回 reminder=新建（或已有）的提醒记录；同一客户同一时间的提醒不重复建。", "parameters": TOOLS[3]["parameters"]},
    handler=TOOLS[3]["handler"],
)
registry.register(
    name="daily_report",
    toolset="real_estate",
    schema={"name": "daily_report", "description": "生成每日早报（今天要跟进的客户 / 今天到期与已逾期的跟进 / 客户与房源概览 / 流失预警）。会顺手把长期无互动的客户自动降一级（S→A→B→C，同一天最多降一级），降级名单在 report.downgrades 里如实说明；report.stale_total 是流失客户总数，report.stale_customers 只列最久的 20 位。", "parameters": TOOLS[4]["parameters"]},
    handler=TOOLS[4]["handler"],
)
registry.register(
    name="midday_check",
    toolset="real_estate",
    schema={"name": "midday_check", "description": "午间检查（今天的到期跟进与逾期情况 / 客户与房源概览 / 流失预警）。会顺手把长期无互动的客户自动降一级（S→A→B→C，同一天最多降一级），降级名单在 check.downgrades 里如实说明；check.stale_total 是流失客户总数，check.stale_customers 只列最久的 20 位。", "parameters": TOOLS[5]["parameters"]},
    handler=TOOLS[5]["handler"],
)
registry.register(
    name="stale_check",
    toolset="real_estate",
    schema={"name": "stale_check", "description": "流失预警检查：自动降级长期无互动客户（S级>5天→A，A级>10天→B，B级>30天→C，同一天最多降一级）并返回预警名单。降级名单在 downgrades 里（含降级前后的等级、多久没互动、该等级的阈值）；报告 total=流失客户总数、count=本次返回条数、truncated，名单默认只列最久的 20 位。", "parameters": TOOLS[6]["parameters"]},
    handler=TOOLS[6]["handler"],
)


# 挽回话术模板的中文说法（对外只说人话，内部模板键留在 customers[] 里给 Coco 用）
_WINBACK_LABELS = {"winback_long_absence": "长期未联系", "winback_after_viewing": "看房后没下文"}


def _norm_min_risk(value, default=CHURN_MIN_RISK_DEFAULT):
    """风险分阈值归一 → 正整数：非数字/None/≤0 一律按默认（与 `limit` 类"非数字按默认"同一套口径）

    2026-09-25（F176）：原先 `min_risk='abc'`/`None` 会一路走到 `score < min_risk` 抛 TypeError
    （被框架兜成"执行失败"），传 0/负数又会把全部有信号的客户倒出来。
    """
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def churn_warning(min_risk: int = CHURN_MIN_RISK_DEFAULT, task_id: str = None) -> str:
    """流失预警：找出"快凉了但还能救"的客户，附挽回建议

    返回 `summary.total`=风险客户总数、`summary.high_risk`=高危人数，`customers` 只列风险最高的
    `LIST_SHOW_LIMIT` 位（`count`/`total`/`truncated` 三件齐）；空库与"有客户但没风险"分开说。
    """
    min_risk = _norm_min_risk(min_risk)
    db = _get_db()
    rows = db.churn_risk_customers(min_risk=min_risk)
    # 联系方式展示防御（2026-09-25）：customers[] 里带着 phone，密钥不一致时是密文（原先直出）
    masked_fields = []
    for r in rows:
        _, m = mask_contacts(r)
        masked_fields.extend(m)
    high = [r for r in rows if r["risk_level"] == "高危"]
    total = len(rows)
    shown = rows[:LIST_SHOW_LIMIT]
    if not total:
        message = ("库里还没有客户，先登记客户再看流失预警" if not db.count_customers()
                   else "当前无流失风险客户，保持节奏")
    else:
        lines = [f"⚠️ 流失预警：{total} 位客户有流失风险（高危 {len(high)} 位）"]
        for r in shown[:10]:
            lines.append(f"\n· {r['name']}（{r['tier']}级，风险{r['risk_score']}分[{r['risk_level']}]）")
            lines.append(f"  信号: {'、'.join(r['signals'])}")
            label = _WINBACK_LABELS.get(r["winback_script"], "挽回")
            lines.append(f"  建议: 用「{label}」挽回模板生成话术")
        if total > 10:
            lines.append(f"\n共 {total} 位客户有流失风险，这里列风险最高的 {min(10, len(shown))} 位"
                         f"（要我列全就说一声）")
        message = "\n".join(lines)
    return json.dumps(attach_key_warning({
        "success": True,
        "summary": {"total": total, "high_risk": len(high)},
        "customers": shown,
        "count": len(shown),
        "total": total,
        "truncated": total > len(shown),
        "message": message,
    }, masked_fields), ensure_ascii=False)


registry.register(
    name="churn_warning",
    toolset="real_estate",
    schema={"name": "churn_warning", "description": "客户流失预警：综合最后跟进时间/带看后沉默/等级加权评分，点名高危客户并给挽回建议。返回 summary.total=风险客户总数、summary.high_risk=高危人数，customers 只列风险最高的 20 位（truncated 标记是否被截断）。", "parameters": {
        "type": "object",
        "properties": {
            "min_risk": {"type": "integer", "description": "最低风险分（默认 40，中危起步；传非数字/0/负数按默认 40）"},
        },
    }},
    handler=lambda args, **kw: churn_warning(**args),
)


def stage_stagnation(task_id: str = None) -> str:
    """阶段滞留清单：强意向超7天 / 已看房超14天 / 谈判超7天无推进的客户（按停留天数，最久在前）

    阶段一律给中文名（条目里 `stage` 保留原值、`stage_label` 是中文）；
    默认只列最久的 `LIST_SHOW_LIMIT` 位，总数在 `total`；空态区分"没有客户"与"没有滞留"。
    """
    db = _get_db()
    alerts = db.stage_stagnation_report()
    total = len(alerts)
    shown = alerts[:LIST_SHOW_LIMIT]
    if not db.count_customers():
        message = "库里还没有客户，先登记客户再看阶段滞留"
    elif not total:
        message = "无阶段滞留客户，节奏健康"
    else:
        lines = [f"阶段滞留提醒：{total} 位客户停留超时"]
        for a in shown:
            label = STAGE_LABELS.get(a['stage'], a['stage'])
            lines.append(f"\n· {a['name']}（{a['tier']}级）在「{label}」已停留 {a['days_in_stage']} 天")
            lines.append(f"  建议: {a['hint']}")
        message = "\n".join(lines)
    items = []
    for a in shown:
        row = dict(a)
        row['stage_label'] = STAGE_LABELS.get(a['stage'], a['stage'])
        items.append(row)
    payload = {"success": True, "alerts": items, "count": len(items), "total": total,
               "truncated": total > len(items), "message": message}
    if payload["truncated"]:
        payload["message"] += (f"\n共 {total} 位客户阶段停留超时，这里列最久的 {len(items)} 位"
                              f"（要我列全就说一声）")
    return json.dumps(payload, ensure_ascii=False)


registry.register(
    name="stage_stagnation",
    toolset="real_estate",
    schema={"name": "stage_stagnation", "description": "阶段滞留清单：强意向超7天/已看房超14天/谈判超7天无推进的客户（按停留天数，最久在前）。返回 total=滞留客户总数、count=本次返回条数、truncated，名单默认只列最久的 20 位。", "parameters": {
        "type": "object",
        "properties": {},
    }},
    handler=lambda args, **kw: stage_stagnation(**args),
)
