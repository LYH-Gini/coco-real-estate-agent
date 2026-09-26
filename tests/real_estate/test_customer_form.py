"""客户录入模板必须覆盖 add_customer 支持的字段（2026-09-24）

原先模板漏了「客户生日」一栏 —— 模板是 Coco 逐项收集客户信息的唯一入口（描述里写着"必须调用此工具，
禁止自行编造录入格式"），模板不问就永远收不到生日，而生日提醒只在"已录入生日"时才触发，功能等于用不上；
另有四处不一致：客户类型没写"不确定就不填（未细分）"、等级栏漏了默认级 C、"客户情况描述"与"备注"
两栏指向同一个 notes 字段、下次回访日期没说它属于跟进记录。

本用例把「模板栏位 == add_customer 可填字段」钉成不变量：以后建档加了字段忘了同步模板会红。
"""
import json

from tools.real_estate_customer import get_customer_form
from tools.registry import registry

# 模板里必须出现的中文关键词（对应 add_customer 的业务字段）
REQUIRED_HINTS = {
    "name": ["姓名"],
    "phone": ["电话"],
    "wechat": ["微信"],
    "customer_type": ["客户类型"],
    "budget_min": ["预算"],
    "budget_max": ["预算"],
    "area_pref": ["面积"],
    "layout_pref": ["户型"],
    "location": ["区域"],
    "renovation": ["装修"],
    "source": ["来源"],
    "tier": ["等级"],
    "birthday": ["生日"],
    "notes": ["备注"],
}


def _form():
    return json.loads(get_customer_form())["form"]


def test_form_covers_every_field_add_customer_can_fill():
    form = _form()
    props = (registry.get_entry("add_customer").schema.get("parameters") or {}).get("properties", {})
    missing = [field for field in set(props) - {"force"}
               if not any(kw in form for kw in REQUIRED_HINTS.get(field, []))]
    assert not missing, f"模板缺这些栏位：{missing}"


def test_form_has_birthday_field():
    """本轮修的关键项：生日栏（生日提醒靠它）"""
    assert "生日" in _form()


def test_customer_type_line_explains_unspecified():
    form = _form()
    assert "未细分" in form and "不确定" in form


def test_tier_line_covers_all_four_levels():
    form = _form()
    assert "S/A/B/C" in form


def test_notes_maps_to_a_single_line():
    """建档只有一个 notes 字段，模板不该出现"情况描述"和"备注"两栏"""
    lines = [l for l in _form().splitlines() if l.strip().startswith("-")]
    notes_lines = [l for l in lines if "备注" in l or "情况描述" in l]
    assert len(notes_lines) == 1, notes_lines


def test_phone_line_mentions_encryption():
    form = _form()
    assert "加密" in form


def test_followup_date_line_explains_it_belongs_to_followups():
    form = _form()
    assert "跟进" in form


def test_form_still_marks_required_field():
    assert "必填" in _form()


def test_form_is_stable_across_calls():
    assert _form() == _form()
