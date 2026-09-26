"""use_template 消息模板：占位符必须填满、单位不叠、认中文模板名（2026-09-26，第十一组第 74 项）

F368–F375 的回归（改前：`t76a` 该行 8 项全 ⚠️、`t78` 95 例 51 红）：
① F368 **不给变量时把模板原文当消息吐出来**（`{community}`/`{price}万`…直接发给客户）；
② F369 变量是空值就印进去（`价格：None万`、`小区：` 空行、亮点传列表变 `['近地铁', '精装']`）；
③ F370 缺变量只报第一个、且是内部英文变量名（`缺少变量: 'price'`）；
④ F371 **单位写死在模板里**：`1500000` → `1500000万`、`150万` → `150万万`、`2500元/月` → `2500元/月万`、
   `area='90㎡'` → `90㎡㎡`、`rooms='3室2厅'` → `3室2厅室2厅`、面积 `90.0` → `90.0㎡`；
⑤ F372 模板名只认英文、区分大小写、乱值提示念英文键清单；传列表直接崩 `unhashable type`；
⑥ F373 `variables` 传字符串/列表/数字 → 英文栈 `TypeError: str.format() argument after ** must be a mapping`；
⑦ F374 描述 6 字、参数说明 2 字，**没告诉模型每个模板要传哪些变量**；
⑧ F375 金额 `0`/负数会被当成真值印出「0万」（零价房源的既有口径是按"没填"）。

**2026-09-26 拍板**：缺项就**整条不给**（消息是要发给客户的，半截带 `{price}` 或空行比不给更糟）；
金额 `0`/负数按"没填"；两条挽回模板照批准的原文补上（`churn_warning` 的流失预警一直在引导经纪人用它们，
但这两个键此前根本不存在）。
"""
import json

import pytest

from tools.real_estate_communication import TEMPLATES, use_template

# 每个模板要填哪些格子（这份表就是"模型该传什么"的规格；测试自己持有，不读实现）
EXPECTED_VARS = {
    "property_recommend": ["community", "price", "rooms", "halls", "area", "highlights"],
    "viewing_reminder": ["time", "address", "contact"],
    "follow_up": ["customer", "last_contact", "todo"],
    "price_change": ["title", "old_price", "new_price", "change"],
    "market_report": ["district", "new_listings", "deals", "avg_price"],
    "winback_long_absence": ["customer"],
    "winback_after_viewing": ["customer", "property"],
}
LABELS = {
    "property_recommend": "房源推荐", "viewing_reminder": "看房提醒", "follow_up": "跟进提醒",
    "price_change": "价格变动", "market_report": "市场周报",
    "winback_long_absence": "长期未联系挽回", "winback_after_viewing": "看房后没下文挽回",
}
FULL = {"community": "格子小区", "price": "150", "rooms": "3", "halls": "2",
        "area": "90", "highlights": "近地铁"}
OTHER = {
    "viewing_reminder": {"time": "2026-09-27 10:00", "address": "格子小区 1号楼101", "contact": "王先生 13800001111"},
    "follow_up": {"customer": "张三", "last_contact": "2026-09-20 电话", "todo": "回访看房时间"},
    "price_change": {"title": "格子小区 1号楼101", "old_price": "160", "new_price": "150", "change": "降价"},
    "market_report": {"district": "美兰区", "new_listings": "12", "deals": "3", "avg_price": "1.2"},
    "winback_long_absence": {"customer": "张先生"},
    "winback_after_viewing": {"customer": "张先生", "property": "格子小区 3号楼1602"},
}


def call(**kwargs):
    return json.loads(use_template(**kwargs))


def vars_for(key):
    return FULL if key == "property_recommend" else OTHER[key]


# ---------- ① 占位符必须填满（本族重点）----------

@pytest.mark.parametrize("variables", [None, {}])
def test_no_variables_means_no_message_at_all(variables):
    """整条不给 —— 半截带 {price} 的消息比不给更糟（拍板）"""
    r = call(template_name="property_recommend", variables=variables)
    assert r.get("success") is not True, r
    assert "message" not in r, r
    assert "小区（community）" in r["error"] and "price" in r["error"], r["error"]
    assert "{" not in r["error"], r["error"]


def test_partial_variables_lists_every_missing_item_at_once():
    r = call(template_name="房源推荐", variables={"community": "格子小区"})
    assert r.get("success") is not True and "message" not in r, r
    assert r["missing"] == ["price", "rooms", "halls", "area", "highlights"], r
    for cn in ["价格", "户型-室", "户型-厅", "面积", "亮点"]:
        assert cn in r["error"], r["error"]
    assert r["error"] == ("「房源推荐」还缺 5 项：价格（price）、户型-室（rooms）、户型-厅（halls）、"
                          "面积（area）、亮点（highlights）。补齐后我再生成。")


def test_fully_empty_call_uses_the_needs_wording():
    r = call(template_name="看房提醒")
    assert r["error"] == ("「看房提醒」需要这些内容：时间（time）、地址（address）、联系人（contact）。"
                          "给我我就生成。"), r["error"]


def test_every_template_renders_without_leftover_placeholders():
    """每个模板填全后都不许留 `{xxx}`、不许有 None/空行；模板清单与规格表必须对齐"""
    assert set(TEMPLATES) == set(EXPECTED_VARS), (set(TEMPLATES) ^ set(EXPECTED_VARS))
    for key, fields in EXPECTED_VARS.items():
        r = call(template_name=key, variables=vars_for(key))
        assert r["success"] is True, r
        assert "{" not in r["message"] and "}" not in r["message"], r["message"]
        assert "None" not in r["message"], r["message"]
        for line in r["message"].split("\n")[1:]:
            assert line.split("：", 1)[-1].strip(), r["message"]
        assert r["template"] == key and r["template_label"] == LABELS[key], r
        assert len(fields) >= 1


# ---------- ② 空值与脏值不许直出 ----------

@pytest.mark.parametrize("bad", [None, "", "   "])
def test_empty_values_count_as_not_given(bad):
    r = call(template_name="property_recommend", variables=dict(FULL, community=bad))
    assert r.get("success") is not True and "message" not in r, r
    assert "小区（community）" in r["error"], r["error"]


def test_list_value_is_joined_with_a_chinese_separator():
    r = call(template_name="property_recommend", variables=dict(FULL, highlights=["近地铁", "南北通透"]))
    assert r["success"] is True, r
    assert "亮点：近地铁、南北通透" in r["message"], r["message"]
    assert "[" not in r["message"] and "'" not in r["message"], r["message"]


def test_dict_value_counts_as_not_given():
    r = call(template_name="property_recommend", variables=dict(FULL, community={"a": 1}))
    assert r.get("success") is not True and "小区（community）" in r["error"], r


# ---------- ③ 金额 0 / 负数 ----------

@pytest.mark.parametrize("bad", [0, -5, "0", "0元"])
def test_zero_or_negative_money_is_treated_as_not_filled(bad):
    """沿用零价房源的口径 —— 不给客户发一条「0万」的消息"""
    r = call(template_name="property_recommend", variables=dict(FULL, price=bad))
    assert r.get("success") is not True and "message" not in r, r
    assert "价格（price）" in r["error"], r["error"]


# ---------- ④ 单位不叠、能对账 ----------

@pytest.mark.parametrize("raw,expected", [
    ("150", "150万"),
    ("150万", "150万"),
    ("150万元", "150万"),
    (150, "150万"),
    (1500000, "150万"),          # 纯数字 ≥1 万 → 视作"元"换算成万
    ("1600000", "160万"),
    ("2500元/月", "2500元/月"),   # 月租带单位原样说，不补"万"
    ("2500/月", "2500元/月"),
    ("5000元", "5000元"),         # 不足一万按元说
    (5000, "5000万"),            # 纯数字按"万"理解（老口径）
    ("1.2", "1.2万"),
])
def test_money_normalised_to_one_reading(raw, expected):
    r = call(template_name="property_recommend", variables=dict(FULL, price=raw))
    assert r["success"] is True, r
    assert f"价格：{expected}\n" in r["message"] + "\n", (raw, r["message"])


@pytest.mark.parametrize("raw,expected", [
    ("90", "90㎡"), ("90.0", "90㎡"), (90, "90㎡"), (90.0, "90㎡"),
    ("90㎡", "90㎡"), ("90平", "90㎡"), (90.5, "90.5㎡"),
])
def test_area_has_no_dot_zero_and_no_doubled_unit(raw, expected):
    r = call(template_name="property_recommend", variables=dict(FULL, area=raw))
    assert r["success"] is True, r
    assert f"面积：{expected}\n" in r["message"] + "\n", (raw, r["message"])


def test_room_counts_accept_the_whole_phrase():
    r = call(template_name="property_recommend", variables=dict(FULL, rooms="3室2厅", halls=2))
    assert "户型：3室2厅" in r["message"], r["message"]
    r = call(template_name="property_recommend", variables=dict(FULL, rooms="三", halls="两"))
    assert "户型：3室2厅" in r["message"], r["message"]


def test_count_accepts_unit_suffix():
    r = call(template_name="market_report", variables=dict(OTHER["market_report"], new_listings="12套"))
    assert "新增房源：12套" in r["message"], r["message"]


def test_money_conversion_is_stated_in_the_receipt():
    """换算了什么必须说出来（不静默换算）"""
    r = call(template_name="price_change", variables={"title": "格子小区 1号楼101", "old_price": 1600000,
                                                     "new_price": 1500000, "change": "降价10万"})
    assert "原价：160万" in r["message"] and "现价：150万" in r["message"], r["message"]
    assert r["note"] == "注：原价「1600000」按元换成了「160万」；现价「1500000」按元换成了「150万」。", r.get("note")


def test_no_note_when_nothing_was_converted():
    r = call(template_name="property_recommend", variables=FULL)
    assert "note" not in r, r


# ---------- ⑤ 模板名写法 ----------

@pytest.mark.parametrize("raw,key", [
    ("property_recommend", "property_recommend"), ("Property_Recommend", "property_recommend"),
    ("房源推荐", "property_recommend"), ("房源", "property_recommend"),
    ("看房提醒", "viewing_reminder"), ("带看提醒", "viewing_reminder"),
    ("跟进提醒", "follow_up"), ("价格变动", "price_change"), ("降价通知", "price_change"),
    ("市场周报", "market_report"), ("周报", "market_report"),
    ("长期未联系挽回", "winback_long_absence"), ("长期未联系", "winback_long_absence"),
    ("看房后没下文", "winback_after_viewing"),
])
def test_template_name_written_in_any_style_is_accepted(raw, key):
    r = call(template_name=raw, variables=vars_for(key))
    assert r["success"] is True and r["template"] == key, r


def test_unknown_template_hint_lists_chinese_names():
    r = call(template_name="xyz")
    assert r.get("success") is not True, r
    assert r["error"].startswith("未知模板「xyz」。可用模板：①房源推荐（property_recommend）")
    assert "⑥长期未联系挽回（winback_long_absence）" in r["error"], r["error"]
    assert "⑦看房后没下文挽回（winback_after_viewing）" in r["error"], r["error"]


@pytest.mark.parametrize("bad", [["property_recommend"], {"a": 1}, 123])
def test_non_text_template_name_gives_hint_not_a_crash(bad):
    r = call(template_name=bad, variables=FULL)
    assert r.get("success") is not True, r
    assert r["error"].startswith("模板名要是文字（如「房源推荐」）。可用模板："), r["error"]


# ---------- ⑥ variables 类型错 ----------

@pytest.mark.parametrize("bad", ["a=b", ["a"], 150, True])
def test_non_dict_variables_gives_chinese_hint(bad):
    r = call(template_name="property_recommend", variables=bad)
    assert r.get("success") is not True, r
    assert "模板变量要成对给我" in r["error"] and "没法用" in r["error"], r["error"]
    assert "TypeError" not in json.dumps(r, ensure_ascii=False), r


# ---------- ⑦ 两条挽回模板补齐（跨工具死引用）----------

def test_winback_templates_exist_and_match_the_churn_labels():
    """churn_warning 的流失预警一直说「用「长期未联系」挽回模板生成话术」，这两个键必须真的能取到"""
    from tools.real_estate_followup import _WINBACK_LABELS

    assert set(_WINBACK_LABELS) <= set(TEMPLATES), (set(_WINBACK_LABELS) - set(TEMPLATES))
    for key, cn in _WINBACK_LABELS.items():
        r = call(template_name=key, variables=vars_for(key))
        assert r["success"] is True and r["template_label"].startswith(cn), (key, r)
        r = call(template_name=cn, variables=vars_for(key))
        assert r["success"] is True and r["template"] == key, (cn, r)


def test_winback_bodies_carry_no_unverifiable_claim():
    """第九组口径：库里没有依据的断言一律不写（不编「业主愿意谈价」「已经有人抢」）"""
    for key in ("winback_long_absence", "winback_after_viewing"):
        body = TEMPLATES[key]
        for claim in ("业主愿意", "已经有人", "抢", "降到", "仅此一套", "马上要"):
            assert claim not in body, (key, claim)


# ---------- ⑧ 描述与参数说明 ----------

def test_schema_description_lists_templates_and_units():
    from tools.registry import registry

    schema = registry._tools["use_template"].schema
    desc = schema["description"]
    assert "7 个模板" in desc and "不查库" in desc and "缺哪一项会一次列全" in desc, desc
    assert schema["parameters"]["properties"]["template_name"]["enum"] == list(TEMPLATES), schema
    vars_note = schema["parameters"]["properties"]["variables"]["description"]
    for key, fields in EXPECTED_VARS.items():
        for name in fields:
            assert f"{name} " in vars_note, (key, name, vars_note)


# ---------- ⑨ 只读：不写库、不查库 ----------

def test_use_template_touches_no_database(db, monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    calls = []

    def counter(conn, cursor, stmt, params, ctx, many):
        calls.append(stmt)

    event.listen(Engine, "before_cursor_execute", counter)
    try:
        call(template_name="property_recommend", variables=FULL)
        call(template_name="长期未联系挽回", variables={"customer": "张先生"})
        call(template_name="xyz")
    finally:
        event.remove(Engine, "before_cursor_execute", counter)
    assert calls == [], calls
