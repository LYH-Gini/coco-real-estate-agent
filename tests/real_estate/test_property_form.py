"""房源录入模板必须覆盖 add_property 支持的字段（2026-09-24）

原先模板漏了「业主信息」与「租客要求」两栏 —— Coco 按模板逐项收集时就不会问，经纪人给的业主电话
容易被塞进标题/备注，出租房源的租客要求也会丢（匹配租客依赖它）。
本用例把「模板字段 == add_property 关键字段」钉成不变量，以后加字段忘了同步模板会红。
"""
import json

from tools.real_estate_property import get_property_form


# 模板里必须出现的中文关键词（对应 add_property 的业务字段）
REQUIRED_HINTS = {
    "title": ["标题"],
    "price": ["售价", "月租"],
    "area": ["面积"],
    "community": ["小区"],
    "district": ["区域"],
    "address": ["地址"],
    "rooms": ["户型"],
    "floor": ["楼层"],
    "orientation": ["朝向"],
    "renovation": ["装修"],
    "year_built": ["年份"],
    "has_elevator": ["电梯"],
    "parking": ["车位"],
    "property_type": ["类型"],
    "tags": ["标签"],
    "owner_name": ["业主", "房东"],
    "owner_phone": ["电话"],
    "tenant_requirements": ["租客要求"],
}


def _form():
    return json.loads(get_property_form())["form"]


def test_form_covers_owner_and_tenant_requirements():
    """本轮修复的两栏：业主信息、租客要求"""
    form = _form()
    assert "业主" in form and "电话" in form, form
    assert "租客要求" in form, form


def test_form_covers_every_key_property_field():
    form = _form()
    missing = {f: kws for f, kws in REQUIRED_HINTS.items() if not any(kw in form for kw in kws)}
    assert not missing, f"模板漏了这些字段：{missing}"


def test_form_explains_price_unit_and_required_fields():
    form = _form()
    assert "元" in form and "万" in form, "价格单位要写明（元/万换算）"
    assert form.count("必填") >= 3, "标题/售价/面积要标必填"
