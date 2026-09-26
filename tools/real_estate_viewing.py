"""
Coco 房产工具 - 带看管理
"""
import json
from datetime import datetime, timedelta
from tools.registry import registry
from agent.real_estate_input import (STAGE_LABELS, clamp_limit, clean_text, norm_date, norm_id)
from tools.real_estate_followup import norm_followup_time, split_time_part
from tools.real_estate_property import _STATUS_LABELS

# 列表分页口径（与 list_customers/list_owners 同一套：默认 20、上限 200、≤0 与非数字按默认）
_LIST_LIMIT_DEFAULT = 20
_LIST_LIMIT_MAX = 200

# 只给日期、没说时刻时按上午 10:00 记（回执必须说明这是默认值，别让经纪人以为他说过）
_DEFAULT_VIEWING_HOUR = 10

# 带看统计里「看房最多的房源」列几套（2026-09-25 拍板：5 套）
_TOP_PROPERTY_LIMIT = 5

# 带看状态与客户意向：存英文枚举、说中文（提示语与读回来都用这张表）
_VIEWING_STATUS_LABELS = {'scheduled': '待带看', 'done': '已完成', 'cancelled': '已取消'}
_VIEWING_RESULT_LABELS = {'interested': '感兴趣', 'not_interested': '不感兴趣', 'pending': '再考虑'}
# 经纪人嘴里的说法（与「记跟进」认中文类型同一套口径）
_VIEWING_STATUS_ALIASES = {
    '待带看': 'scheduled', '待看': 'scheduled', '还没看': 'scheduled', '未看': 'scheduled',
    '计划中': 'scheduled', '约好了': 'scheduled',
    '已完成': 'done', '已看': 'done', '看完': 'done', '看完了': 'done', '看过': 'done',
    '看过了': 'done', '完成': 'done',
    '已取消': 'cancelled', '取消': 'cancelled', '不来了': 'cancelled', '没去': 'cancelled',
}
_VIEWING_RESULT_ALIASES = {
    '感兴趣': 'interested', '有意向': 'interested', '满意': 'interested', '喜欢': 'interested',
    '不感兴趣': 'not_interested', '没兴趣': 'not_interested', '不满意': 'not_interested',
    '不喜欢': 'not_interested',
    '再考虑': 'pending', '待定': 'pending', '再看看': 'pending', '再想想': 'pending', '犹豫': 'pending',
}

# 带看真的完成后，哪些阶段档位可以自动挪到「已看房」（2026-09-25 拍板）：
# 潜在/意向/强意向 都还没到"看过房"，挪过去是事实；**标着「流失」的也挪**（他又带看了，说明没流失）；
# 已看房及更靠后的档位（谈判/成交中/售后维护）一律不动 —— 机器不替经纪人往回拉。
_STAGE_ADVANCE_FROM = ('lead', 'interested', 'strong', 'lost', None, '')

# 记完结果后给经纪人的下一步建议（只说不做，不自动调别的工具）
_NEXT_STEP_ADVICE = {
    'interested': '下一步要不要我找几套同小区/同户型的给他对比？',
    'not_interested': '要不要我按他的要求换几套给他看？',
    'pending': '要不要过两天提醒你跟一下？',
}


def _next_step_advice(result, status):
    if status == 'cancelled':
        return '要不要重新约个时间？'
    return _NEXT_STEP_ADVICE.get(result or '')


def _has_overdue_followup(db, customer_id):
    """这位客户当前是否有逾期提醒（按"每客户最新一条**人为**跟进"的口径，与 get_overdue 一致）

    2026-09-26（F215）：带看档 3 自动写的两条跟进不算「人为跟进」—— 记完这次带看也不会
    改变逾期状态，所以下面那句回执说的是"原来那条提醒仍然有效"。
    """
    latest = db.get_latest_followup(customer_id)
    if not latest or not latest.get('next_date'):
        return False
    try:
        when = datetime.fromisoformat(str(latest['next_date']).replace(' ', 'T')[:19])
    except ValueError:
        return False
    return when < datetime.now()


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


def norm_viewing_when(value, label='带看时间', sample='2026-12-31 10:00'):
    """带看时间归一 → (datetime 或 None, 是否用了默认时刻, 提示或 None)

    认 `2026-12-31 10:00` / `2026-12-31`（按 10:00）/ `2026/12/31 10:00` / `2026年12月31日 10:00` /
    `9点30` / `明天 10:00` / `周三 10:00` / `3天后 10:00` / 夹带时刻的 `2026-12-31T10:00` ——
    日期与时刻复用跟进侧同一套归一（`norm_date` / `norm_followup_time` / `split_time_part`），
    别再各写一份解析器（原先只认 `YYYY-MM-DD HH:MM`，经纪人怎么说都被拒）。
    提示文案按"日期+时刻挤在一个参数里"的场景自己给：认不出时引的是**经纪人原话**，不是拆出来的半截。
    """
    date_part, embedded = split_time_part(value)
    if not str(date_part or '').strip():
        return None, False, f"{label}不能为空，请说一下哪天几点（如 {sample}、明天 10:00）"
    day, problem = norm_date(date_part, label, sample)
    if problem:
        return None, False, f"{label}没能识别：收到的是「{value}」。可以说 {sample}，也可以说 明天 10:00"
    time_text = None
    if embedded:
        time_text, problem = norm_followup_time(embedded, label)
        if problem:
            return None, False, f"{label}没能识别：收到的是「{value}」。时间请用 9:30 或 9点30 这类写法"
    if not time_text:
        return day.replace(hour=_DEFAULT_VIEWING_HOUR, minute=0), True, None
    hour, minute = (int(part) for part in time_text.split(':'))
    return day.replace(hour=hour, minute=minute), False, None


def schedule_viewing(customer_id: int, property_id: int, viewing_time: str, task_id: str = None) -> str:
    """预约带看：为指定客户安排指定房源的带看时间

    写入前先认人认房（客户或房源不存在就如实说明，不写孤儿带看 —— 孤儿会在带看列表、
    统计里冒出来，经纪人只看到一个查不到的编号）。同一客户同一房源同一时间已有未完成的
    带看时不再重复登记，返回既有那条。已售/已租的房源、已关闭的客户、已经过去的时间都只给
    提醒不拦（补录历史带看是真实需求）。回执里给带看编号，记结果、查详情都用它。
    """
    customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    property_id, problem = norm_id(property_id, '房源编号')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    dt, used_default, problem = norm_viewing_when(viewing_time)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    db = _get_db()
    customer = db.get_customer(customer_id)
    if not customer:
        return json.dumps({"success": False, "error": "客户不存在，请先在客户列表里核对编号"},
                          ensure_ascii=False)
    prop = db.get_property(property_id)
    if not prop:
        return json.dumps({"success": False, "error": "房源不存在，请核对房源编号"}, ensure_ascii=False)

    stamped = dt.strftime('%Y-%m-%d %H:%M')
    existing = db.find_scheduled_viewing(customer_id, property_id, dt)
    if existing:
        return json.dumps({
            "success": True, "viewing": existing, "already_scheduled": True,
            "message": f"这位客户已经约了这个房源 {stamped} 的带看（带看编号 {existing['id']}），没有重复登记",
        }, ensure_ascii=False)

    warnings = []
    if dt < datetime.now():
        warnings.append(f"这个时间已经过去了（{stamped}）。我按补录记下了；"
                        f"如果是要新约带看，请给我一个将来的时间")
    if (prop.get('status') or '') in ('sold', 'rented'):
        warnings.append(f"该房源状态是{_STATUS_LABELS.get(prop['status'], prop['status'])}，"
                        f"约带看前先确认一下")
    if (customer.get('status') or '') == 'closed':
        warnings.append("这位客户已经标记为已关闭，确认还要约带看吗")

    result = db.add_viewing(customer_id=customer_id, property_id=property_id, viewing_time=dt)
    note = f"；你没说具体时间，我按 {_DEFAULT_VIEWING_HOUR:02d}:00 记的" if used_default else ""
    payload = {
        "success": True,
        "viewing": result,
        "message": (f"已预约 {stamped} 带看（带看编号 {result['id']}{note}），"
                    f"客户: {customer.get('name')}，房源: {result.get('property_title')}"),
    }
    if warnings:
        payload["warnings"] = warnings
    return json.dumps(payload, ensure_ascii=False)


def _norm_viewing_enum(value, labels, aliases, label):
    """带看枚举归一 → (规范值或 None, 提示或 None)。没给/空串按"没要求改"处理（返回 None 不报错）"""
    if value is None:
        return None, None
    text = str(value).strip()
    if not text:
        return None, None
    key = text.lower()
    if key in labels:
        return key, None
    if text in aliases:
        return aliases[text], None
    options = "、".join(f"{cn}（{en}）" for en, cn in labels.items())
    return None, f"{label}没能识别：收到的是「{value}」。可用：{options}"


def norm_viewing_status(value):
    """带看状态归一（待带看/已完成/已取消，认中文说法）"""
    return _norm_viewing_enum(value, _VIEWING_STATUS_LABELS, _VIEWING_STATUS_ALIASES, '带看状态')


def norm_viewing_result(value):
    """客户意向归一（感兴趣/不感兴趣/再考虑，认中文说法）"""
    return _norm_viewing_enum(value, _VIEWING_RESULT_LABELS, _VIEWING_RESULT_ALIASES, '客户意向')


def record_viewing(viewing_id: int, status: str = None, result: str = None, feedback: str = None, task_id: str = None) -> str:
    """记录带看结果：状态（待带看/已完成/已取消）、客户意向（感兴趣/不感兴趣/再考虑）、客户反馈

    状态与意向认中文说法（已完成、已看、客户不感兴趣…）也认英文值；**什么都没说就给一句提示**，
    不回一个"成功"却什么也没记。带看真的完成时，Coco 会连带做四件事（都按"同一条带看只做一次"）：
    ① 把这次带看记到那位客户的跟进里（含结果与客户反馈）② 客户阶段在"潜在/意向/强意向/流失"时挪到「已看房」
    （已看房及更靠后的档位、已关闭的客户一律不动）③ 安排 1 小时后回访提醒（并关联房源）
    ④ 有客户反馈或"不感兴趣"时重扫该房源缺陷标签。回执里给下一步建议；改回其它状态只提醒不拦。
    """
    viewing_id, problem = norm_id(viewing_id, '带看编号', '，可在带看记录列表里查')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    status_value, problem = norm_viewing_status(status)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    result_value, problem = norm_viewing_result(result)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    feedback = clean_text(feedback)          # 空串按"未填"存，别让空串与未填两种形态并存
    if status_value is None and result_value is None and not feedback:
        return json.dumps({"success": False, "error":
                           "没说记什么 —— 说说这次带看怎么样（看完了 / 客户取消了 / 客户什么意向），"
                           "或者补一句客户反馈"}, ensure_ascii=False)
    db = _get_db()
    before = db.get_viewing(viewing_id)
    if not before:
        return json.dumps({"success": False, "error": "带看记录不存在，请在带看记录列表里核对编号"},
                          ensure_ascii=False)
    kwargs = {}
    if status_value:
        kwargs['status'] = status_value
    if result_value:
        kwargs['result'] = result_value
    if feedback:
        kwargs['feedback'] = feedback
    updated = db.update_viewing(viewing_id, **kwargs)

    warnings = []
    if status_value and before.get('status') == 'done' and status_value != 'done':
        warnings.append(f"这条带看原来记的是已完成，现在改成了{_VIEWING_STATUS_LABELS[status_value]}"
                        f" —— 如果不是笔误就不用管")

    # 带看真的完成了 → 三件连带（都是同一条带看只做一次：命中就更新/不动，不重复建）
    #   ① 写一条「带看」跟进，把这次带看沉淀到客户时间线上
    #   ② 按事实把客户阶段推进到「已看房」（只在档位早于它时推，不回退）
    #   ③ 回访提醒（1 小时后）
    followup, followup_reused = None, False
    stage_change = None
    reminder, reminder_reused = None, False
    reminder_error = None
    if updated.get('status') == 'done':
        customer = db.get_customer(updated['customer_id'])
        customer_name = customer.get('name') if customer else '客户'
        title = updated.get('property_title') or '这套房源'
        # 写这次带看之前，先看这位客户原来是不是有逾期提醒（写入后"最新一条跟进"就变了）
        prior_overdue = _has_overdue_followup(db, updated['customer_id'])

        # ① 带看跟进
        try:
            parts = [f"带看 {title}"]
            if updated.get('result'):
                parts.append(f"，客户{_VIEWING_RESULT_LABELS.get(updated['result'], updated['result'])}")
            if updated.get('feedback'):
                parts.append(f"；客户反馈：{updated['feedback']}")
            if not updated.get('result') and not updated.get('feedback'):
                parts.append("（已完成）")
            content = "".join(parts)
            existing_visit = db.find_followup_by_viewing(viewing_id, followup_type='visit')
            if existing_visit:
                followup = db.update_followup(existing_visit['id'], content=content)
                followup_reused = True
            else:
                followup = db.add_followup(
                    customer_id=updated['customer_id'], property_id=updated.get('property_id'),
                    type='visit', content=content, source_viewing_id=viewing_id,
                )
        except Exception as exc:
            followup = None
            warnings.append("这次带看没能记到客户的跟进里 —— 你手动记一条也行")

        # ② 阶段推进（事实是他看过房了；已在更靠后的档位不动，客户已关闭不动）
        stage_from = (customer or {}).get('stage')
        if _STAGE_ADVANCE_FROM and (customer or {}).get('status') != 'closed' \
                and stage_from in _STAGE_ADVANCE_FROM:
            try:
                moved = db.update_stage(updated['customer_id'], 'viewed')
                if moved:
                    stage_change = {'from': stage_from or None, 'to': 'viewed',
                                    'from_label': STAGE_LABELS.get(stage_from, stage_from or '未设'),
                                    'to_label': STAGE_LABELS['viewed']}
            except Exception:
                stage_change = None

        # ③ 回访提醒
        try:
            when = datetime.now() + timedelta(hours=1)
            content = (f"带看后回访：{customer_name} 看完 {updated.get('property_title')} 已 1 小时，"
                       f"主动跟进了解意向")
            existing = db.find_followup_by_viewing(viewing_id, followup_type='reminder')
            if existing:
                reminder = db.update_followup(existing['id'], content=content,
                                              property_id=updated.get('property_id'))
                reminder_reused = True
            else:
                reminder = db.add_followup(
                    customer_id=updated['customer_id'],
                    property_id=updated.get('property_id'),
                    type='reminder', content=content, next_date=when,
                    next_time=when.strftime('%H:%M'), source_viewing_id=viewing_id,
                )
        except Exception as exc:
            # 不许静默兜底：提醒没建起来要如实说（带看结果本身已经记下了）
            reminder = None
            reminder_error = type(exc).__name__
            warnings.append("回访提醒这次没建起来 —— 你手动记一条也行，或者再说一次我重试")

        # 带看跟进不算"人工联系"（F215 起逾期只看人为跟进）→ 记完这次带看，原来那条逾期提醒**依然有效**
        if followup and prior_overdue:
            warnings.append("这位客户原来那条提醒仍然有效（带看记录不算人工跟进），回访后记得处理它")

    # 缺陷标签反哺（2026-08-28 功能3）：记录带看结果后自动重扫该房缺陷
    defect_refreshed = None
    defect_error = None
    if updated.get('property_id') and (feedback or updated.get('result') == 'not_interested'):
        try:
            defect_refreshed = db.refresh_defect_tags(updated['property_id'])
        except Exception as exc:
            defect_refreshed = None
            defect_error = type(exc).__name__
            warnings.append("房源缺陷标签这次没能重新统计（不影响这条带看结果）")

    labels = []
    if status_value:
        labels.append(_VIEWING_STATUS_LABELS[status_value])
    if result_value:
        labels.append(f"客户{_VIEWING_RESULT_LABELS[result_value]}")
    if labels:
        message = f"带看记录：{'、'.join(labels)}（带看编号 {viewing_id}）"
        if feedback:
            message += "，客户反馈也记下了"
    else:
        message = f"已记下客户反馈（带看编号 {viewing_id}）"
    if followup:
        message += "，已记到这位客户的跟进里"
    if stage_change:
        message += (f"，客户阶段也从「{stage_change['from_label']}」挪到「{stage_change['to_label']}」")
    if reminder_reused:
        message += "；这条带看的回访提醒已经在（时间不变），我把它一起更新了"
    elif reminder:
        message += "，已安排 1 小时后回访提醒"
    if defect_refreshed:
        message += f"；检测到共性差评，已更新房源缺陷标签: {'、'.join(defect_refreshed)}"
    advice = _next_step_advice(updated.get('result'), updated.get('status'))
    if advice:
        message += f"。{advice}"

    payload = {"success": True, "viewing": _viewing_display(updated), "message": message}
    if followup:
        payload["followup"] = followup
    if stage_change:
        payload["stage_change"] = stage_change
    if reminder:
        payload["reminder"] = reminder
    if defect_refreshed:
        payload["defect_tags_updated"] = defect_refreshed
    if reminder_error:
        payload["reminder_error"] = reminder_error
    if defect_error:
        payload["defect_rescan_error"] = defect_error
    if warnings:
        payload["warnings"] = warnings
    return json.dumps(payload, ensure_ascii=False)


def _readable_time(value):
    """ISO/库值 → `2026-09-26 01:40`（认不出就原样返回，绝不返回 None 字面量）"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace(' ', 'T')[:19]).strftime('%Y-%m-%d %H:%M')
    except ValueError:
        return str(value)


def _viewing_display(row):
    """带看记录 → 给经纪人看的形态：状态/意向中文、时间可读、孤儿客户与房源给可读标注

    读路径统一走这一处（详情、列表都复用），别各拼一遍（2026-09-25 F195）。
    原值一律保留（机器读），只**补**给人看的字段。
    """
    if not row:
        return row
    item = dict(row)
    status = item.get('status')
    result = item.get('result')
    item['status_label'] = _VIEWING_STATUS_LABELS.get(status, status) if status else None
    item['result_label'] = _VIEWING_RESULT_LABELS.get(result, result) if result else None
    item['viewing_time_label'] = _readable_time(item.get('viewing_time'))
    if not item.get('customer_name'):
        item['customer_name'] = f"已删除客户（id={item.get('customer_id')}）"
    if not item.get('property_title'):
        item['property_title'] = f"已删除房源（id={item.get('property_id')}）"
    return item


def get_viewing(viewing_id: int, task_id: str = None) -> str:
    """查看一次带看的详情：客户与房源、带看时间、状态与客户意向（中文说法）、客户反馈、记录时间

    用于记完结果后核对，或经纪人问"这套房谁看过、看得怎么样"；按带看编号查（编号来自预约回执或带看列表）。
    状态与意向同时给原值与中文（`status_label`/`result_label`），时间给可读的 `viewing_time_label`；
    客户或房源已被删掉的存量记录给可读标注，不回 null。
    带看完成后自动安排过回访提醒的，还会给出 `reminder`（提醒本身）与 `reminder_label`
    （如「已安排回访提醒（2026-09-26 01:40）」）—— 那句话有据可查，不是空承诺。
    """
    viewing_id, problem = norm_id(viewing_id, '带看编号', '，可在带看记录列表里查')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    db = _get_db()
    result = db.get_viewing(viewing_id)
    if result:
        item = _viewing_display(result)
        reminder = db.find_followup_by_viewing(viewing_id, followup_type='reminder')
        if reminder:
            item['reminder'] = reminder
            item['reminder_label'] = f"已安排回访提醒（{_readable_time(reminder.get('next_date'))}）"
        return json.dumps({"success": True, "viewing": item}, ensure_ascii=False)
    return json.dumps({"success": False, "error": "带看记录不存在，请在带看记录列表里核对编号"},
                      ensure_ascii=False)


def list_viewings(customer_id: int = None, property_id: int = None, status: str = None,
                  limit: int = _LIST_LIMIT_DEFAULT, task_id: str = None) -> str:
    """列出带看记录：谁看了哪套房、什么时候、状态与客户意向（中文）、客户反馈、有没有安排回访提醒

    默认按带看时间倒序（最近约的在前），可按客户/房源/状态筛（状态认 待带看/已完成/已取消 与英文值），
    默认列 20 条（最多 200）。返回 total=符合条件的总数、count=本次返回条数、truncated；被截断时给一句说明。
    "客户不存在"、"房源不存在"与"有客户但还没带看"分开说 —— 前者多半是编号打错了。
    """
    limit = clamp_limit(limit, _LIST_LIMIT_DEFAULT, _LIST_LIMIT_MAX)
    if customer_id is not None:
        customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
        if problem:
            return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if property_id is not None:
        property_id, problem = norm_id(property_id, '房源编号')
        if problem:
            return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    status_value, problem = norm_viewing_status(status)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    db = _get_db()
    if customer_id is not None and not db.get_customer(customer_id):
        return json.dumps({"success": False, "error": "客户不存在，请先在客户列表里核对编号"},
                          ensure_ascii=False)
    if property_id is not None and not db.get_property(property_id):
        return json.dumps({"success": False, "error": "房源不存在，请核对房源编号"},
                          ensure_ascii=False)
    rows, total = db.list_viewings(customer_id=customer_id, property_id=property_id,
                                   status=status_value, limit=limit, with_total=True)
    viewings = [_viewing_display(row) for row in rows]
    reminders = db.find_followups_by_viewings([x['id'] for x in viewings])
    for item in viewings:
        reminder = reminders.get(item['id'])
        if reminder:
            item['reminder'] = reminder
            item['reminder_label'] = f"已安排回访提醒（{_readable_time(reminder.get('next_date'))}）"
    payload = {"success": True, "viewings": viewings, "count": len(viewings), "total": total,
               "truncated": bool(total and total > len(viewings))}
    if not total:
        if status_value:
            payload["message"] = "没有符合这个条件的带看记录"
        elif customer_id is not None:
            payload["message"] = "这位客户还没有带看记录"
        elif property_id is not None:
            payload["message"] = "这套房源还没有带看记录"
        else:
            payload["message"] = "还没有任何带看记录"
    elif payload["truncated"]:
        payload["message"] = f"共 {total} 条带看，这里列最近 {len(viewings)} 条（要我多列就说一声）"
    return json.dumps(payload, ensure_ascii=False)


def viewing_stats(task_id: str = None) -> str:
    """带看统计（全部历史）：总次数、待带看/已看/已取消、客户意向分布、感兴趣占比、看房最多的房源

    感兴趣占比按**已看**算（interested / done），不是按总带看算；"还没有已看"与"占比 0%"是两回事，
    所以说明里会分开讲清。客户意向分布只统计已看（感兴趣/不感兴趣/再考虑/未记意向，四项之和 = 已看）。
    看房最多的房源列前 5 套（按带看次数降序），被截断时说明一句。数字口径与带看列表完全对得上。
    """
    db = _get_db()
    stats = db.viewing_stats() or {}
    total = stats.get('total_viewings') or 0
    scheduled = stats.get('scheduled') or 0
    done = stats.get('done') or 0
    cancelled = stats.get('cancelled') or 0
    interested = stats.get('interested') or 0
    rate = stats.get('interest_rate')
    intent = stats.get('intent_breakdown') or {}
    top, top_total = db.viewing_top_properties(_TOP_PROPERTY_LIMIT)
    summary = {'总带看': total, '待带看': scheduled, '已看': done, '已取消': cancelled,
               '感兴趣的客户': interested, '感兴趣占比': f'{rate}%' if done else None,
               '客户意向': {'感兴趣': intent.get('interested', 0),
                        '不感兴趣': intent.get('not_interested', 0),
                        '再考虑': intent.get('pending', 0),
                        '没记意向': intent.get('unknown', 0)},
               '看房最多的房源': [{'房源': x['property_title'], '带看次数': x['viewings'],
                              '感兴趣': x['interested']} for x in top]}
    if not total:
        message = "还没有带看记录 —— 先约一次带看再看统计"
    elif not done:
        message = (f"已经有 {total} 次带看，但还没记过带看结果 —— "
                   f"感兴趣占比要等有「已看」才有意义")
    elif not interested:
        message = f"已看 {done} 次，暂时没有客户感兴趣（占比 0%）"
    else:
        message = (f"共 {total} 次带看：待带看 {scheduled} 次、已看 {done} 次"
                   f"（其中 {interested} 位客户感兴趣）、已取消 {cancelled} 次；"
                   f"感兴趣占比 {rate}%（按已看算）")
    if done:
        line = (f"客户意向：感兴趣 {intent.get('interested', 0)} 位、"
                f"不感兴趣 {intent.get('not_interested', 0)} 位、"
                f"再考虑 {intent.get('pending', 0)} 位")
        if intent.get('unknown'):
            line += f"、另外 {intent['unknown']} 次没记意向"
        message += f"；{line}"
    if top:
        first = top[0]
        message += (f"；看房最多的是 {first['property_title']}"
                    f"（{first['viewings']} 次，其中 {first['interested']} 位感兴趣）")
        if top_total > len(top):
            message += f"。这里列看房最多的 {_TOP_PROPERTY_LIMIT} 套（要我列全就说一声）"
    payload = {"success": True, "stats": stats, "summary": summary, "message": message,
               "intent": intent, "top_properties": top, "top_properties_total": top_total,
               "top_properties_truncated": bool(top_total > len(top))}
    return json.dumps(payload, ensure_ascii=False)


registry.register(
    name="schedule_viewing",
    toolset="real_estate",
    schema={"name": "schedule_viewing", "description":
            "预约带看：给指定客户安排看某个房源的带看时间，时间支持 2026-12-31 10:00、明天 10:00、周三 10:00、"
            "3天后 10:00（只给日期就按当天 10:00 记，回执会说明）。返回带看编号（记结果、查详情都用它）；"
            "客户或房源编号查不到会如实说明，不建空记录；同一客户同一房源同一时间不会重复登记。"
            "已售/已租的房源、已关闭的客户、已经过去的时间只给提醒不拦。", "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "integer", "description": "客户编号（数字，来自建档或客户列表）"},
            "property_id": {"type": "integer", "description": "房源编号（数字，来自建档或房源列表）"},
            "viewing_time": {"type": "string", "description":
                             "带看时间：2026-12-31 10:00 / 2026/12/31 10:00 / 2026年12月31日 10:00 / "
                             "明天 10:00 / 周三 10:00 / 3天后 10:00；只给日期（如 2026-12-31）就按 10:00 记"},
        },
        "required": ["customer_id", "property_id", "viewing_time"],
    }},
    handler=lambda args, **kw: schedule_viewing(**args),
)

registry.register(
    name="record_viewing",
    toolset="real_estate",
    schema={"name": "record_viewing", "description":
            "记录带看结果：带看状态（待带看/已完成/已取消）、客户意向（感兴趣/不感兴趣/再考虑）、客户反馈；"
            "认中文说法也认英文值，也可以只补一句反馈。带看真的完成时会连带做四件事：把这次带看记到客户的跟进里、"
            "客户阶段在「潜在/意向/强意向/流失」时挪到「已看房」（更靠后的档位与已关闭客户不动）、"
            "安排 1 小时后回访提醒（同一条带看只留一条）、按反馈重扫房源缺陷标签；回执里给下一步建议。"
            "把已完成的带看改回其它状态只提醒不拦。", "parameters": {
        "type": "object",
        "properties": {
            "viewing_id": {"type": "integer", "description": "带看编号（数字，来自预约带看的回执或带看列表）"},
            "status": {"type": "string", "enum": ["scheduled", "done", "cancelled"],
                       "description": "带看状态：scheduled待带看 / done已完成 / cancelled已取消"
                                      "（也可以直接说 已完成、看完了、已取消）"},
            "result": {"type": "string", "enum": ["interested", "not_interested", "pending"],
                       "description": "客户意向：interested感兴趣 / not_interested不感兴趣 / pending再考虑"
                                      "（也可以直接说 感兴趣、不感兴趣、再看看）"},
            "feedback": {"type": "string", "description":
                         "客户反馈原话（如 采光差、户型还行）；给了反馈会顺手重扫该房源缺陷标签"},
        },
        "required": ["viewing_id"],
    }},
    handler=lambda args, **kw: record_viewing(**args),
)

registry.register(
    name="get_viewing",
    toolset="real_estate",
    schema={"name": "get_viewing", "description":
            "查看一次带看的详情：客户、房源、带看时间（还给了给人看的 viewing_time_label）、"
            "带看状态与客户意向（原值 + 中文 status_label/result_label）、客户反馈、记录时间；"
            "带看完成后安排过回访提醒的会给出 reminder_label（如「已安排回访提醒（2026-09-26 01:40）」）。"
            "用于记完结果后核对，或经纪人问「这套房谁看过、看得怎么样」；按带看编号查（编号来自预约回执或带看列表）。",
            "parameters": {
        "type": "object",
        "properties": {"viewing_id": {"type": "integer",
                                      "description": "带看编号（数字，来自预约带看的回执或带看列表）"}},
        "required": ["viewing_id"],
    }},
    handler=lambda args, **kw: get_viewing(**args),
)

registry.register(
    name="list_viewings",
    toolset="real_estate",
    schema={"name": "list_viewings", "description":
            "列出带看记录：谁看了哪套房、带看时间、状态与客户意向（中文 status_label/result_label）、"
            "客户反馈；带看完成后安排过回访提醒的会给 reminder_label。默认按带看时间倒序（最近约的在前），"
            "可按客户/房源/状态筛（状态认 待带看/已完成/已取消，也认 scheduled/done/cancelled），默认列 20 条（最多 200）。"
            "返回 total=符合条件的总数、count=本次返回条数、truncated。经纪人问「这位客户都看过哪些房」"
            "「这套房谁看过」时用它。", "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "integer", "description": "客户编号（数字，来自建档或客户列表）"},
            "property_id": {"type": "integer", "description": "房源编号（数字，来自建档或房源列表）"},
            "status": {"type": "string", "enum": ["scheduled", "done", "cancelled"],
                       "description": "带看状态：scheduled待带看 / done已完成 / cancelled已取消"
                                      "（也可以直接说 已完成、看完了、已取消）"},
            "limit": {"type": "integer", "description": "返回条数（默认 20，最多 200；传 0/负数/非数字按默认 20）"},
        },
    }},
    handler=lambda args, **kw: list_viewings(**args),
)

registry.register(
    name="viewing_stats",
    toolset="real_estate",
    schema={"name": "viewing_stats", "description":
            "带看统计（全部历史，不按月）：总带看次数、待带看/已看/已取消次数、客户意向分布"
            "（感兴趣/不感兴趣/再考虑/没记意向，只统计已看）、感兴趣占比（按「已看」算，不是按总带看）、"
            "以及看房最多的前 5 套房源（按带看次数排）。还没有已看时会说明「要等有已看才有意义」，"
            "库里没有带看与「还没记过结果」分开说。返回 stats（原字段）、summary（中文键名）、"
            "intent、top_properties 与 message（一句可直接复述的中文）。经纪人问「最近带看情况怎么样」时用它。", "parameters": {
        "type": "object",
        "properties": {},
    }},
    handler=lambda args, **kw: viewing_stats(),
)


def clear_defect_tag(property_id: int, tag: str, task_id: str = None) -> str:
    """房东整改后手动清除某缺陷标签（清完记下整改时间，整改前的旧差评不会再打回来）

    清除成功后：这条房源**整改之前**的旧差评不再把该标签打回来；整改之后的新反馈仍按
    「≥2 位不同客户提及」判定 —— 真出新问题照样会重新标上。标签名去首尾空白；空标签给提示；
    "房源不存在"与"这套房源没有这个标签"分开说。返回剩余标签，方便经纪人核对。
    """
    property_id, problem = norm_id(property_id, '房源编号')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    tag = clean_text(tag)
    if not tag:
        return json.dumps({"success": False, "error":
                           "标签不能为空，说一下要清哪个标签（如 采光差）"}, ensure_ascii=False)
    db = _get_db()
    result = db.clear_defect_tag(property_id, tag)
    if result is None:
        return json.dumps({"success": False, "error": "房源不存在，请核对房源编号"},
                          ensure_ascii=False)
    if not result.get('cleared'):
        payload = {"success": False, "error": f"这套房源没有「{tag}」这个标签"}
        if result.get('remaining'):
            payload["defect_tags"] = result['remaining']
        return json.dumps(payload, ensure_ascii=False)
    return json.dumps({
        "success": True, "defect_tags": result.get('remaining') or [],
        "message": f"已清除缺陷标签：{tag}（已记下整改时间，整改前的旧差评不会再把这个标签打回来）",
    }, ensure_ascii=False)


registry.register(
    name="clear_defect_tag",
    toolset="real_estate",
    schema={"name": "clear_defect_tag", "description":
            "清除房源缺陷标签（房东整改后由经纪人手动操作）：清完会记下这条房源的整改时间，"
            "整改之前的旧差评不会再把这个标签打回来；整改之后的新反馈仍按「≥2 位不同客户提及」判定 —— "
            "真出新问题照样会标上。标签名认库里已有的中文标签（如 采光差、临街吵、漏水）。"
            "返回剩余标签便于核对；「房源不存在」与「这套房源没有这个标签」分开说。", "parameters": {
        "type": "object",
        "properties": {
            "property_id": {"type": "integer", "description": "房源ID"},
            "tag": {"type": "string", "description": "要清除的标签名（如 采光差）"},
        },
        "required": ["property_id", "tag"],
    }},
    handler=lambda args, **kw: clear_defect_tag(**args),
)
