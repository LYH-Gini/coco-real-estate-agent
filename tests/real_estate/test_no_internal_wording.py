"""对外说法回归：经纪人会读到的字里不许出现内部构造（2026-09-28 加）

真实教训：经纪人问「你收录了哪些城市的政策？」，Coco 把工具的说明书与报错原文
（「本地政策库已停用…本工具只会返回…」）转述给他，读起来像提示词。修法是
①提示词加【对外说话规则】；②工具对外只给产品语言、系统口径进 `note_for_model`。

本文件把「对外字段里不许出现内部构造」钉成可复跑的检查（关键路径抽样）：
- 不许出现任何一个 real_estate 工具名（名单从注册表实时取，不写死）；
- 不许出现 `xxx=yyy` / `xxx: yyy` 这类内部键值写法（`id=` 与 `MEDIA:` 例外，
  它们本来就是给经纪人对账用的）；
- 不许出现一批内部口径词（本工具 / 知识库 / 提示词 / 停用 / 数据库表…）。

扫描范围只含**经纪人会读到的字段**（message / error / warnings）；
`note_for_model` 与 `ask` 是按约定给模型看的（见【对外说话规则】第 4 条），不在本扫描内。

**已知待办（不在本文件断言范围，属海报那一轮）**：海报工具 `generate_property_poster` /
`suggest_poster_titles` 的 `ask` 里夹带工具名与参数名、`notes` 里有「已回落到旧引擎」「（不臆造）」
这类内部口径（台账 F442/F443）。等做海报那轮时一并改掉、并把它们加进这里的 CASES。
"""
import json
import re

import pytest
from conftest import make_customer, make_property

INTERNAL_WORDS = ("本工具", "知识库", "提示词", "已停用", "数据库表", "字段名", "参数名",
                  "Tool execution failed", "gAAAA")
# `xxx=yyy` 或 `xxx: yyy` 的内部键值写法（id / MEDIA / http 放行）
_KV = re.compile(r"\b([a-z][a-z_]{2,})\s*[=:]\s*\S")
_KV_ALLOWED = {"id", "media", "http", "https"}


def _tool_names():
    """从注册表实时取 real_estate 工具名（改工具集时这里自动跟上，不写死名单）"""
    from toolsets import get_toolset_info
    return sorted(get_toolset_info("real_estate")["resolved_tools"])


def _visible(payload):
    """经纪人会读到的那些字段拼成一段文本"""
    out = []
    for key in ("message", "error"):
        v = payload.get(key)
        if isinstance(v, str):
            out.append(v)
    for key in ("warnings", "notes"):
        v = payload.get(key)
        if isinstance(v, list):
            out.extend(x for x in v if isinstance(x, str))
        elif isinstance(v, str):
            out.append(v)
    return "\n".join(out)


def _dispatch(db, monkeypatch, name, args):
    import tools.real_estate_policy  # noqa: F401
    import tools.real_estate_customer  # noqa: F401
    import tools.real_estate_property  # noqa: F401
    import tools.real_estate_followup  # noqa: F401
    import tools.real_estate_deal  # noqa: F401
    import tools.real_estate_viewing  # noqa: F401
    import tools.real_estate_owner  # noqa: F401
    import tools.real_estate_customer as tc
    import tools.real_estate_property as tp
    import tools.real_estate_followup as tf
    import tools.real_estate_deal as td
    import tools.real_estate_viewing as tv
    import tools.real_estate_owner as to
    from tools.registry import registry
    for m in (tc, tp, tf, td, tv, to):
        if hasattr(m, "_get_db"):
            monkeypatch.setattr(m, "_get_db", lambda: db)
    return json.loads(registry.dispatch(name, args))


def _cases(db):
    """关键路径抽样：读类 + 写入类命中分支（判重/查重）各来几条"""
    from datetime import date

    c = make_customer(db, name="说法客户", phone="13900009001", tier="A")
    p = make_property(db, title="说法房源 1号楼1单元101", price=1_500_000, area=100.0)
    db.add_followup(customer_id=c["id"], type="phone", content="打了个电话",
                    next_date=date(2020, 1, 1))
    return [
        ("get_loan_policy", {"city": "海口"}),
        ("list_policy_cities", {}),
        ("add_customer", {"name": "重复客户", "phone": "13900009001"}),          # 判重命中分支
        ("get_customer", {"customer_id": c["id"]}),
        ("list_customers", {}),
        ("add_property", {"title": "说法房源 1号楼1单元101", "price": 1_500_000, "area": 100.0}),  # 判重命中
        ("get_property_detail", {"property_id": p["id"]}),
        ("search_property", {"district": "美兰"}),
        ("match_property", {"customer_id": c["id"]}),
        ("get_overdue", {}),
        ("list_deals", {}),
        ("list_viewings", {}),
        ("get_owner", {"owner_id": 999999}),                                     # 不存在的编号分支
    ]


@pytest.fixture
def cases(db, monkeypatch):
    out = []
    for name, args in _cases(db):
        out.append((name, args, _dispatch(db, monkeypatch, name, args)))
    return out


def test_visible_text_never_names_a_tool(cases):
    names = _tool_names()
    hits = []
    for name, _args, payload in cases:
        text = _visible(payload)
        for tool in names:
            if tool in text:
                hits.append(f"{name}: 出现了工具名 {tool} → {text[:160]}")
    assert not hits, "经纪人会读到的字里不许出现工具名：\n" + "\n".join(hits)


def test_visible_text_has_no_internal_key_value_writing(cases):
    hits = []
    for name, _args, payload in cases:
        for m in _KV.finditer(_visible(payload)):
            if m.group(1).lower() not in _KV_ALLOWED:
                hits.append(f"{name}: {m.group(0)!r} → {_visible(payload)[:160]}")
    assert not hits, "不许出现内部键值写法（status=sold / perfect_match=true 这类）：\n" + "\n".join(hits)


def test_visible_text_has_no_internal_mechanism_words(cases):
    hits = []
    for name, _args, payload in cases:
        text = _visible(payload)
        for word in INTERNAL_WORDS:
            if word in text:
                hits.append(f"{name}: 出现了 {word!r} → {text[:160]}")
    assert not hits, "不许把系统机制讲给经纪人：\n" + "\n".join(hits)


def test_key_mismatch_payload_is_broker_facing(db, monkeypatch):
    """判重遇到密钥不一致：对外只说人话，系统口径进 note_for_model（同族四个出口之一）"""
    make_customer(db, name="密钥坏了的客户", phone="gAAAAA" + "x" * 40)
    r = _dispatch(db, monkeypatch, "add_customer",
                  {"name": "正常新客", "phone": "13900009002"})
    text = _visible(r)
    assert "先不给你判重结果" in text, r
    assert not [w for w in INTERNAL_WORDS if w in text], r
    assert "COCO_ENC_KEY" not in text, "对外文案不提服务器上的密钥变量"
    assert "COCO_ENC_KEY" in r.get("note_for_model", ""), r
