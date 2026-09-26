"""话术场景/子场景的归一与中文名（共用件 agent/real_estate_script.py，2026-09-26）

话术族六个工具（get_script / use_template / save_script / get_script_by_name / list_scripts /
delete_script）从这一轮起共用同一套档位与中文名 —— 本文件钉住这套共用件的契约：
① 中文说法、别名、大小写都能认；
② 认不出给中文清单（圆序号 + 中文名，英文键只放括号里对照），**不猜、不崩**；
③ `allowed` 之外的档位不认（get_script 不认「自定义」、save_script 认）；
④ 顶层场景与子场景是两张表（`follow_up` 两边都有，含义不同）。
"""
import pytest

from agent.real_estate_script import (
    SCENARIO_LABELS,
    SUB_SCENARIO_LABELS,
    label_of,
    norm_scenario,
    norm_sub_scenario,
    options_text,
    type_word,
)

_ALL_SCENARIOS = ["greeting", "objection_handling", "closing", "follow_up", "custom"]


# ---------- ① 场景写法归一 ----------

@pytest.mark.parametrize("raw,expected", [
    ("greeting", "greeting"),
    ("Greeting", "greeting"),
    ("GREETING", "greeting"),
    ("开场白", "greeting"),
    ("开场", "greeting"),
    ("异议处理", "objection_handling"),
    ("处理异议", "objection_handling"),
    ("逼定", "closing"),
    ("促单", "closing"),
    ("跟进维护", "follow_up"),
    ("跟进", "follow_up"),
    ("跟进 维护", "follow_up"),          # 中间空格也认
    ("  开场白  ", "greeting"),           # 首尾空白
    ("自定义", "custom"),
])
def test_scenario_written_in_any_style_is_normalized(raw, expected):
    key, problem = norm_scenario(raw, _ALL_SCENARIOS)
    assert problem is None, problem
    assert key == expected


def test_scenario_outside_allowed_is_rejected_with_chinese_list():
    """get_script 不认「自定义」—— 提示里要列它真正认的那几个，别把 custom 也念出来"""
    key, problem = norm_scenario("自定义", ["greeting", "objection_handling", "closing", "follow_up"])
    assert key is None
    assert "开场白" in problem and "①" in problem
    # 清单部分（可用场景：之后）不许把 custom 也念出来
    assert "自定义" not in problem.split("可用场景：")[1]


def test_unknown_scenario_hint_is_chinese_with_bracketed_keys():
    key, problem = norm_scenario("xyz", ["greeting", "closing"])
    assert key is None
    assert problem == "未知场景「xyz」。可用场景：①开场白（greeting）②逼定成交（closing）"


@pytest.mark.parametrize("bad", [["greeting"], {"a": 1}, 12, True])
def test_non_text_scenario_gives_hint_instead_of_crashing(bad):
    """实测旧实现在这里抛 unhashable type —— 非文字一律中文提示"""
    key, problem = norm_scenario(bad, ["greeting", "closing"])
    assert key is None
    assert "要是文字" in problem and "①开场白（greeting）" in problem


# ---------- ② 子场景写法归一 ----------

@pytest.mark.parametrize("raw,expected", [
    ("first_contact", "first_contact"),
    ("首次联系", "first_contact"),
    ("第一次联系", "first_contact"),
    ("再次跟进", "follow_up"),
    ("带看后回访", "after_viewing"),
    ("看房后回访", "after_viewing"),
    ("嫌贵", "price_too_high"),
    ("再考虑", "need_to_consider"),
    ("最后提醒", "final_reminder"),
    ("降价", "price_drop"),
])
def test_sub_scenario_written_in_any_style_is_normalized(raw, expected):
    allowed = ["first_contact", "follow_up", "after_viewing"]
    key, problem = norm_sub_scenario(raw, allowed)
    # 只有前三个属于 greeting，其余用各自的场景集合再验一遍
    if expected not in allowed:
        key, problem = norm_sub_scenario(raw, [expected])
    assert problem is None, problem
    assert key == expected


def test_sub_scenario_hint_names_the_scenario_and_its_own_options():
    allowed = ["first_contact", "follow_up", "after_viewing"]
    key, problem = norm_sub_scenario("xxx", allowed, scenario_key="greeting")
    assert key is None
    assert problem.startswith("「开场白」下没有「xxx」这个子场景。")
    assert "①首次联系（first_contact）②再次跟进（follow_up）③带看后回访（after_viewing）" in problem


# ---------- ③ 两张表不能混（follow_up 两边同名不同义） ----------

def test_labels_distinguish_top_level_scenario_from_sub_scenario():
    assert label_of("follow_up") == "跟进维护"
    assert label_of("follow_up", "sub") == "再次跟进"
    assert label_of("greeting") == "开场白"
    assert label_of("first_contact", "sub") == "首次联系"
    # 认不出的键回落原样，不臆造中文名
    assert label_of("no_such_key") == "no_such_key"


def test_option_lists_cover_every_declared_key():
    """两张标签表必须覆盖全部档位 —— 漏一个就会在提示里念出英文键"""
    assert set(SCENARIO_LABELS) == {"greeting", "objection_handling", "closing", "follow_up", "custom"}
    assert set(SUB_SCENARIO_LABELS) == {
        "first_contact", "follow_up", "after_viewing",
        "price_too_high", "need_to_consider", "location_not_satisfied",
        "create_urgency", "offer_incentive", "final_reminder",
        "weekly_check", "holiday_greeting", "price_drop",
    }


def test_options_text_keeps_caller_order():
    assert options_text(["closing", "greeting"]) == "①逼定成交（closing）②开场白（greeting）"
    assert options_text(["first_contact"], "sub") == "①首次联系（first_contact）"


# ---------- ④ 类型不对时的大白话类型名（话术族共用，别再各写一份）----------

@pytest.mark.parametrize("value,expected", [
    ("abc", "文字"), (1, "数字"), (1.5, "数字"), (True, "真假值"),
    (["a"], "一串值"), (("a",), "一串值"), ({"a": 1}, "一份表格"),
])
def test_type_word_is_human_readable(value, expected):
    assert type_word(value) == expected


def test_options_text_accepts_a_custom_label_table():
    """模板名清单就是这么来的（use_template），自定义表要能盖掉默认那两张"""
    assert options_text(["custom"], labels={"custom": "自定义"}) == "①自定义（custom）"
