"""get_script 内置话术：认中文说法、给经纪人念的中文清单、不崩（2026-09-26，第十一组第 73 项）

F361–F367 的回归（改前：`t76a` 该行 8 项全 ⚠️、`t77` 48 例 12 红）：
① F361 乱值提示原先直接念英文枚举键（`['greeting', 'objection_handling', …]`）；
② F362 只认英文、区分大小写，「开场白」「首次联系」「Greeting」全被拒；
③ F363 返回体只有英文子场景键，没有一句给经纪人念的话（`message` 缺失）；
④ F364 场景传数组/字典直接抛 `unhashable type`（回给模型的是英文栈）；
⑤ F365 描述 14 字、参数说明 2 字；
⑥ F366 内置话术与自建话术库是两个数据源 —— 拍板 A 方案：只回内置，但要说清分工；
⑦ F367 内置话术里的数字/规划/学区说法查不到依据 —— 拍板保留原文 + 返回里提醒按实际情况替换。

**老字段 `scripts` 一个字不改**（消费方兼容），新增字段都是加出来的。
"""
import json

import pytest

from tools.real_estate_communication import SCRIPTS, get_script

_SUBS = {
    "greeting": ["first_contact", "follow_up", "after_viewing"],
    "objection_handling": ["price_too_high", "need_to_consider", "location_not_satisfied"],
    "closing": ["create_urgency", "offer_incentive", "final_reminder"],
    "follow_up": ["weekly_check", "holiday_greeting", "price_drop"],
}


def call(**kwargs):
    return json.loads(get_script(**kwargs))


def hint_of(**kwargs):
    r = call(**kwargs)
    assert r.get("success") is not True, r
    return r["error"]


# ---------- ① 认中文说法 / 大小写 ----------

@pytest.mark.parametrize("raw,expected", [
    ("greeting", "greeting"),
    ("Greeting", "greeting"),
    ("开场白", "greeting"),
    ("开场", "greeting"),
    ("  开场白  ", "greeting"),
    ("异议处理", "objection_handling"),
    ("处理异议", "objection_handling"),
    ("逼定", "closing"),
    ("促单", "closing"),
    ("跟进维护", "follow_up"),
    ("跟进", "follow_up"),
])
def test_scenario_written_in_chinese_or_any_case_is_accepted(raw, expected):
    r = call(scenario=raw)
    assert r["success"] is True, r
    assert r["scenario"] == expected
    assert r["scenario_label"] == {"greeting": "开场白", "objection_handling": "异议处理",
                                   "closing": "逼定成交", "follow_up": "跟进维护"}[expected]


@pytest.mark.parametrize("raw,expected", [
    ("首次联系", "first_contact"),
    ("第一次联系", "first_contact"),
    ("再次跟进", "follow_up"),
    ("after_viewing", "after_viewing"),
    ("带看回访", "after_viewing"),
])
def test_sub_scenario_written_in_chinese_is_accepted(raw, expected):
    r = call(scenario="greeting", sub_scenario=raw)
    assert r["success"] is True, r
    assert r["sub_scenario"] == expected
    assert r["scenario"] == "greeting"


def test_sub_scenario_can_use_its_own_scenario_aliases():
    """「嫌贵」只在异议处理下有；给别的场景用要如实说不存在，别串场景"""
    r = call(scenario="异议处理", sub_scenario="嫌贵")
    assert r["success"] is True and r["sub_scenario"] == "price_too_high", r
    bad = hint_of(scenario="开场白", sub_scenario="嫌贵")
    assert "没有「嫌贵」这个子场景" in bad and "①首次联系" in bad, bad


# ---------- ② 提示给中文清单（不许念英文键）----------

def test_unknown_scenario_hint_lists_chinese_names():
    err = hint_of(scenario="xyz")
    assert err == ("未知场景「xyz」。可用场景：①开场白（greeting）②异议处理（objection_handling）"
                   "③逼定成交（closing）④跟进维护（follow_up）")


def test_unknown_scenario_hint_never_offers_custom():
    """『自定义』是 save_script 的档位，get_script 没有这种话术，别把它列进可用场景"""
    err = hint_of(scenario="自定义")
    assert "自定义" not in err.split("可用场景：")[1], err


def test_unknown_sub_scenario_hint_names_scenario_and_options():
    err = hint_of(scenario="greeting", sub_scenario="xxx")
    assert err.startswith("「开场白」下没有「xxx」这个子场景。")
    assert "①首次联系（first_contact）②再次跟进（follow_up）③带看后回访（after_viewing）" in err


def test_hint_keeps_internal_keys_only_inside_parentheses():
    """给经纪人念的话里英文键只能出现在括号对照里（F122 那一族）"""
    for err in (hint_of(scenario="xyz"),
                hint_of(scenario="greeting", sub_scenario="xyz")):
        outside = err.split("（")[0] + "".join(
            part.split("）")[-1] for part in err.split("（")[1:])
        assert "greeting" not in outside and "first_contact" not in outside, err


# ---------- ③ 非文字输入不崩 ----------

@pytest.mark.parametrize("bad", [["greeting"], {"a": 1}, 12, True])
def test_non_text_scenario_gives_chinese_hint_instead_of_crashing(bad):
    err = hint_of(scenario=bad)
    assert "要是文字" in err and "①开场白（greeting）" in err, err


@pytest.mark.parametrize("bad", [["first_contact"], {"a": 1}, 12, True])
def test_non_text_sub_scenario_gives_chinese_hint_instead_of_crashing(bad):
    err = hint_of(scenario="greeting", sub_scenario=bad)
    assert "要是文字" in err and "「开场白」下" in err, err


# ---------- ④⑤ message 与中文标签 ----------

def test_whole_scenario_message_lists_every_sub_scenario():
    r = call(scenario="开场白")
    assert r["message"] == "「开场白」共 3 条：①首次联系②再次跟进③带看后回访。要哪一条跟我说一声，我把原文给你。"
    assert [i["sub_scenario_label"] for i in r["script_list"]] == ["首次联系", "再次跟进", "带看后回访"]
    assert [i["sub_scenario"] for i in r["script_list"]] == _SUBS["greeting"]


def test_single_script_message_carries_the_body():
    r = call(scenario="greeting", sub_scenario="first_contact")
    assert r["message"] == f"「开场白 · 首次联系」：{r['script']}"
    assert r["sub_scenario_label"] == "首次联系"


def test_every_scenario_has_a_chinese_label_and_readable_message():
    for key, subs in _SUBS.items():
        r = call(scenario=key)
        assert r["scenario_label"] and r["scenario_label"] not in (key,), r
        assert len(r["script_list"]) == len(subs)
        for item in r["script_list"]:
            assert item["sub_scenario_label"] not in (item["sub_scenario"],), item
            one = call(scenario=key, sub_scenario=item["sub_scenario"])
            assert item["script"] == one["script"]
            assert one["message"].endswith(one["script"])


# ---------- ⑥ 老字段兼容 + 两个数据源分工 ----------

def test_legacy_scripts_field_still_returns_the_same_bodies():
    """老字段 `scripts` 原样保留 —— 改了会打破消费方"""
    for key, subs in _SUBS.items():
        r = call(scenario=key)
        assert r["scripts"] == SCRIPTS[key]
        assert list(r["scripts"]) == subs


def test_note_explains_builtin_scripts_cannot_be_edited(tmp_path):
    """F366：内置话术改不了，自己攒的话术去话术库那几个工具取"""
    r = call(scenario="greeting")
    assert "内置" in r["note"] and "话术库" in r["note"], r["note"]
    assert r["note"] == call(scenario="greeting", sub_scenario="first_contact")["note"]


# ---------- ⑦ 内置话术内容提醒 ----------

def test_content_note_asks_broker_to_replace_unverifiable_claims():
    """F367：原文保留，但必须提醒里面的数字/规划/学区说法要按实际情况替换"""
    r = call(scenario="异议处理", sub_scenario="位置不满意")
    assert "替换" in r["content_note"] and "20%" in r["content_note"], r["content_note"]
    assert r["content_note"] == call(scenario="greeting")["content_note"]


# ---------- ⑧ 描述与参数说明 ----------

def test_schema_description_and_param_docs_are_substantial():
    from tools.registry import registry

    schema = registry._tools["get_script"].schema
    desc = schema["description"]
    assert "12 条" in desc and "话术库" in desc, desc
    props = schema["parameters"]["properties"]
    for name in ("scenario", "sub_scenario"):
        assert len(props[name]["description"]) >= 12, props[name]
    assert props["scenario"]["enum"] == ["greeting", "objection_handling", "closing", "follow_up"]


# ---------- ⑨ 只读：不许写库 ----------

def test_get_script_never_writes_anything(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_scripts._get_db", lambda: db)
    from tools.real_estate_scripts import save_script

    json.loads(save_script(name="只读探针", content="这条不该被 get_script 动", scenario="greeting"))
    before = [(s["id"], s["name"], s["content"]) for s in db.list_scripts()]
    for kwargs in ({"scenario": "greeting"}, {"scenario": "开场白", "sub_scenario": "首次联系"},
                   {"scenario": "xyz"}, {"scenario": ["greeting"]}):
        call(**kwargs)
    assert [(s["id"], s["name"], s["content"]) for s in db.list_scripts()] == before
