"""
Coco 房产工具 - 话术库沉淀
经纪人可自定义话术并复用
"""
import json
from tools.registry import registry
from agent.real_estate_input import clamp_limit, clean_text, clip_text, norm_id
from agent.real_estate_script import (
    SCENARIO_ALIASES, label_of, norm_scenario, norm_sub_scenario, type_word,
)

# 列表类统一口径（契约 8）：默认 50、上限 200；回执里最多列 5 条名字
_LIST_LIMIT_DEFAULT = 50
_LIST_LIMIT_MAX = 200
_LIST_MSG_MAX = 5
_LIST_CIRCLED = "①②③④⑤"

# 话术库认的场景（顺序即提示里给经纪人念的清单顺序）—— 与内置话术同一套档位
SCRIPT_SCENARIOS = ["greeting", "objection_handling", "closing", "follow_up", "custom"]
SCRIPT_NAME_MAX = 100        # 与 re_scripts.name 的列宽一致（PostgreSQL 上超长会让整次保存失败）


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


def _script_field(value, label, hint):
    """文本项归一 → (文本 或 None, 中文提示 或 None)

    非文字（列表/字典）不许落到数据库层：那是 sqlite/PG 的绑定错误，报错里连表名列名
    与要写入的参数都会带出来，等于把内部结构念给模型（2026-09-26）。
    """
    if value is not None and not isinstance(value, str):
        return None, f"{label}要给{hint}。这次收到的是{type_word(value)}，我没法用。"
    return clean_text(value), None


def _truthy(value):
    """force 类开关：认 true/1/yes/是/覆盖（模型有时把布尔写成文字）"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y", "是", "要", "覆盖"}


def save_script(name: str, content: str, scenario: str = "custom",
                force: bool = False, task_id: str = None) -> str:
    """保存自定义话术到话术库

    scenario: greeting(开场白) / objection_handling(异议处理) / closing(逼定成交) / follow_up(跟进维护) / custom(自定义)
    force: 同名时是否覆盖原来那条（不填＝不覆盖，先提醒）
    """
    name, problem = _script_field(name, "话术名称", "一个名字（如「议价话术」）")
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if not name:
        return json.dumps({"success": False, "error": "话术名称不能为空，给一个名字就行（如「议价话术」）。"},
                          ensure_ascii=False)
    name, clip_hint = clip_text(name, SCRIPT_NAME_MAX)

    content, problem = _script_field(content, "话术内容", "一段文字")
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if not content:
        return json.dumps({"success": False, "error": "话术内容不能为空，把要存的那段话发给我。"},
                          ensure_ascii=False)

    if isinstance(scenario, str):
        key, problem = (norm_scenario(scenario, SCRIPT_SCENARIOS) if scenario.strip() else ("custom", None))
    elif scenario is None:
        key, problem = "custom", None
    else:
        key, problem = None, f"话术场景要给一个说法（如「开场白」）。这次收到的是{type_word(scenario)}，我没法用。"
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)

    db = _get_db()
    existing = db.get_script_by_name(name)
    if existing and not _truthy(force):
        return json.dumps({"success": False, "error": (
            f"话术库里已经有一份叫「{name}」的（编号 {existing['id']}）。"
            f"要我用这版覆盖它，就跟我说一声；要另存一份就把名字改一下。")}, ensure_ascii=False)

    if existing:                                  # 覆盖：还是同一条，编号不变
        result = db.update_script(existing["id"], content=content, scenario=key)
        updated = True
        message = f"已更新同名话术「{name}」（编号 {existing['id']}，场景：{label_of(key)}）。"
    else:
        result = db.add_script(name=name, content=content, scenario=key)
        updated = False
        message = f"话术已保存：「{name}」（编号 {result['id']}，场景：{label_of(key)}）。"
    result["scenario_label"] = label_of(key)

    payload = {"success": True, "script": result, "updated": updated, "message": message}
    if clip_hint:
        payload["truncated"] = True
        payload["note"] = f"名称{clip_hint}。"
    return json.dumps(payload, ensure_ascii=False)


def _builtin_script_hint(name):
    """名字不在话术库里时，看它是不是**内置话术**的场景名/子场景名 → 说清两个库的分工

    内置话术（get_script 那 12 条）与话术库是两个数据源（2026-09-26 拍板 A 方案保持这样），
    但读取侧要能说清"你问的这个名字属于哪一半、下一步该怎么问"，不能都说"话术不存在"。
    """
    from tools.real_estate_communication import SCRIPTS, SCENARIO_ORDER

    key, _ = norm_scenario(name, SCENARIO_ORDER)
    if key:
        return (f"「{name}」不在话术库里 —— 它是内置话术的场景名。"
                f"要看内置话术，跟我说「{label_of(key)}的话术」；要看你自己存的话术，就说「列一下话术库」。")
    for sc, subs in SCRIPTS.items():
        sub, _ = norm_sub_scenario(name, list(subs), scenario_key=sc)
        if sub:
            return (f"「{name}」不在话术库里 —— 它是内置话术「{label_of(sc)}」里的子场景。"
                    f"要看那条话术，跟我说「{label_of(sc)}的{label_of(sub, 'sub')}」。")
    return None


def get_script_by_name(name: str, task_id: str = None) -> str:
    """按名称获取话术（话术库里的；内置话术用 get_script 按场景取）"""
    name, problem = _script_field(name, "话术名称", "一个名字（如「议价话术」）")
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if not name:
        return json.dumps({"success": False, "error": "话术名称不能为空，告诉我名字我帮你取。"}, ensure_ascii=False)

    db = _get_db()
    rows = db.list_scripts_by_name(name)
    if rows:
        result = rows[0]                       # 存量重名时给编号最小那条（与 force 覆盖的是同一条）
        result["scenario_label"] = label_of(result["scenario"])
        message = f"「{name}」（编号 {result['id']}，场景：{result['scenario_label']}）：{result['content']}"
        payload = {"success": True, "script": result, "message": message}
        if len(rows) > 1:
            ids = "、".join(str(r["id"]) for r in rows)
            payload["duplicate_count"] = len(rows)
            payload["message"] = (message + f"\n话术库里有 {len(rows)} 份叫「{name}」的（编号 {ids}），"
                                             f"这里给你编号 {result['id']} 那份；"
                                             f"要合成一份就说「用这版覆盖{name}」。")
        return json.dumps(payload, ensure_ascii=False)

    hint = _builtin_script_hint(name)
    if hint:
        return json.dumps({"success": False, "error": hint}, ensure_ascii=False)
    if not db.list_scripts(limit=1):
        return json.dumps({"success": False, "error": (
            "话术库里还一条话术都没存过。先存一条（跟我说「存一条话术」），再按名字取。")}, ensure_ascii=False)
    return json.dumps({"success": False, "error": (
        f"话术库里没有叫「{name}」的。要看库里都有什么，跟我说「列一下话术库」。")}, ensure_ascii=False)


def _scenario_spellings(key):
    """该档位在库里的**全部历史写法** → 供筛选用

    契约：筛选要把历史值归到同一档（改前 `save_script` 把「逼定」「开场白」原样存进库过，
    按档位筛的时候不能把它们落下）；「自定义」档还要算上改前存进去的空串与 NULL。
    """
    values = [k for k, v in SCENARIO_ALIASES.items() if v == key]
    if key == "custom":
        values += ["", None]
    return values


def list_scripts(scenario: str = None, limit: int = _LIST_LIMIT_DEFAULT, task_id: str = None) -> str:
    """列出话术库（可按场景筛选）

    参数:
        scenario: 按场景筛（认中文说法与英文键；不填就列全部）
        limit: 最多列几条（默认 50、最多 200）
    """
    scenario_key = None
    if isinstance(scenario, str):
        if scenario.strip():
            scenario_key, problem = norm_scenario(scenario, SCRIPT_SCENARIOS)
            if problem:
                return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    elif scenario is not None:
        return json.dumps({"success": False, "error": (
            f"话术场景要给一个说法（如「开场白」）。这次收到的是{type_word(scenario)}，我没法用。")},
            ensure_ascii=False)

    limit = clamp_limit(limit, _LIST_LIMIT_DEFAULT, _LIST_LIMIT_MAX)
    db = _get_db()
    rows, total = db.list_scripts(scenario_values=_scenario_spellings(scenario_key) if scenario_key else None,
                                  limit=limit, with_total=True)
    for item in rows:
        item["scenario_label"] = label_of(item["scenario"])

    payload = {"success": True, "scripts": rows, "count": len(rows), "total": total,
               "truncated": bool(total > len(rows))}
    scope = f"「{label_of(scenario_key)}」下共 {total} 条" if scenario_key else f"话术库共 {total} 条"
    if not total:
        if scenario_key:
            _, overall = db.list_scripts(limit=1, with_total=True)
            payload["message"] = f"「{label_of(scenario_key)}」下还没有话术（话术库共 {overall} 条）。"
        else:
            payload["message"] = "话术库里还一条话术都没存过。跟我说「存一条话术」，把要记的话发我。"
    elif payload["truncated"]:
        payload["message"] = f"{scope}，这里列最近 {len(rows)} 条（最新登记优先）。要我多列就说一声。"
    else:
        shown = rows[:_LIST_MSG_MAX]
        items = "".join(f"{_LIST_CIRCLED[i]}「{s['name']}」（编号 {s['id']}）" for i, s in enumerate(shown))
        if len(rows) > _LIST_MSG_MAX:
            payload["message"] = (f"{scope}（最新的在前），这里列最新的 {_LIST_MSG_MAX} 条：{items}；"
                                  f"要全列就说一声。")
        else:
            payload["message"] = f"{scope}（最新的在前）：{items}。要说哪条的原文，跟我说名字。"
    return json.dumps(payload, ensure_ascii=False)


def delete_script(script_id: int, task_id: str = None) -> str:
    """删除话术（**彻底删除**：删了就取不回来，回执会带上删的是哪一条）"""
    script_id, problem = norm_id(script_id, '话术编号', '，可在话术列表里查')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if script_id <= 0:
        return json.dumps({"success": False, "error": (
            f"话术编号要是正整数（如 12），收到的是「{script_id}」。可在话术列表里查。")}, ensure_ascii=False)

    db = _get_db()
    target = db.get_script(script_id)
    if not target:
        if not db.list_scripts(limit=1):
            return json.dumps({"success": False, "error": (
                "话术库里现在一条话术都没有，没有可删的。要存话术跟我说一声。")}, ensure_ascii=False)
        return json.dumps({"success": False, "error": (
            f"话术库里没有编号 {script_id} 的话术。要看库里都有什么，跟我说「列一下话术库」。")},
            ensure_ascii=False)

    db.delete_script(script_id)
    label = label_of(target["scenario"])
    return json.dumps({
        "success": True,
        "deleted": {**target, "scenario_label": label},
        "message": f"已删除话术「{target['name']}」（编号 {script_id}，场景：{label}）。删了就取不回来了。",
    }, ensure_ascii=False)


registry.register(
    name="save_script",
    toolset="real_estate",
    schema={"name": "save_script", "description": "保存一条自定义话术到话术库（经纪人自己攒的话术，随时能按名字取回来）。同名会先提醒你 —— 要覆盖原来那条就说一声，要另存一份就改个名字；场景认中文说法；名称最长 100 字，超了会截断并告诉你。这是\"存话术\"，跟内置话术（get_script 那 12 条）不是一回事。", "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "话术名称（最长 100 字，如「议价话术」）。同名会先提醒你，不会静默覆盖"},
            "content": {"type": "string", "description": "话术内容（要发给客户的那段原话）"},
            "scenario": {"type": "string", "enum": ["greeting", "objection_handling", "closing", "follow_up", "custom"], "description": "话术场景：开场白 / 异议处理 / 逼定成交 / 跟进维护 / 自定义（认这几种中文说法，也认英文；不填按「自定义」）"},
            "force": {"type": "boolean", "description": "同名时是否覆盖原来那条：不填 = 不覆盖（先提醒你已有同名）；填 true = 用这次的内容覆盖它，编号不变"},
        },
        "required": ["name", "content"],
    }},
    handler=lambda args, **kw: save_script(**args),
)

registry.register(
    name="get_script_by_name",
    toolset="real_estate",
    schema={"name": "get_script_by_name", "description": "按名字取一条自己存的话术（话术库里的那些）。名字前后空白会自动忽略；库里有几份同名会告诉你；这是查\"自己的话术库\"，内置那 12 条标准话术用 get_script 按场景取。", "parameters": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "话术名称（原样匹配，但首尾空白会忽略；不确定叫什么就先让我列一下话术库）"}},
        "required": ["name"],
    }},
    handler=lambda args, **kw: get_script_by_name(**args),
)

registry.register(
    name="list_scripts",
    toolset="real_estate",
    schema={"name": "list_scripts", "description": "列出话术库（经纪人自己攒的那些），最新的在前；可按场景筛。返回条数与总数、被截断会说清；要看某条的原文按名字取。内置那 12 条标准话术不在这个库里（用 get_script 按场景取）。", "parameters": {
        "type": "object",
        "properties": {
            "scenario": {"type": "string", "enum": ["greeting", "objection_handling", "closing", "follow_up", "custom"], "description": "按场景筛（可选）：开场白 / 异议处理 / 逼定成交 / 跟进维护 / 自定义（认这几种中文说法，也认英文；不填就列全部）"},
            "limit": {"type": "integer", "description": "最多列几条（可选）：默认 50、最多 200；不填或填 0 按默认"},
        },
    }},
    handler=lambda args, **kw: list_scripts(**args),
)

registry.register(
    name="delete_script",
    toolset="real_estate",
    schema={"name": "delete_script", "description": "彻底删除一条自己存的话术（删了就取不回来，要留就先把内容记下来）。编号在话术列表里能查到；内置那 12 条标准话术不在这里面、删不掉。", "parameters": {
        "type": "object",
        "properties": {"script_id": {"type": "integer", "description": "话术编号（正整数，在话术列表里能查到；不确定就先让我列一下话术库）"}},
        "required": ["script_id"],
    }},
    handler=lambda args, **kw: delete_script(**args),
)
