"""房源发布文案（generate_listing_copy）回归：文案里的数字与事实必须跟房源对得上（2026-09-26）

本文件钉七件事（对应 FINDINGS 的 F289–F295）：
① F289 面积走房源侧口径，整数不带 `.0`（`90㎡` 不是 `90.0㎡`）；
② F290 出租房的"每平米月租"带 `/月`（`41.67元/㎡/月`），与列表/详情同一口径；
③ F291 平台认中文与常见别名（朋友圈/微信/贝壳/安居客/58同城…），乱值给中文可选值清单，
   **绝不静默按默认平台生成**；
④ F292 没小区/没区域时不出现空的地区行（`📍  `），也不过留空的「地址：」；
⑤ F293 价格是 0（库里"没填价"的实际形态）说「价格待定」，不说「0万」；
⑥ F294 不存在 / 已售 / 已租 **分开说**（原先三种情况同一句「房源不存在或不在售」）；
⑦ F295 描述写清渠道/文体/能不能直接发，参数说明不再只有两个字。
"""
import json
import re

import pytest
from conftest import make_property  # noqa: F401

from tools import real_estate_listing as m_listing

PLATFORMS = ["friends", "beike", "anjuke", "58"]


@pytest.fixture
def wired(db, monkeypatch):
    monkeypatch.setattr(m_listing, "_get_db", lambda: db)
    return db


def _copy(tool_db, pid, platform="friends"):
    out = json.loads(m_listing.generate_listing_copy(property_id=pid, platform=platform))
    assert out["success"] is True, out
    return out["copy"]


def _sale(tool_db, **overrides):
    data = dict(title="海阔天空 7号楼2单元1602", community="海阔天空", district="朝阳区",
                address="朝阳区望京西路88号", price=1_500_000, area=90.0, rooms=3, halls=2,
                bathrooms=2, orientation="南", floor="16层", renovation="精装", year_built=2015,
                has_elevator=1, parking=1, property_type="second_hand", status="available")
    data.update(overrides)
    return make_property(tool_db, **data)


# ---------- ① 面积口径 ----------
@pytest.mark.parametrize("platform", PLATFORMS)
def test_area_has_no_trailing_zero(wired, platform):
    """四个平台都不许把 90.0 写成 90.0㎡（详情/列表就是 90㎡）"""
    p = _sale(wired)
    copy = _copy(wired, p["id"], platform)
    assert "90㎡" in copy, copy
    assert "90.0㎡" not in copy, copy


def test_area_keeps_decimal_when_real(wired):
    p = _sale(wired, area=128.5)
    assert "128.5㎡" in _copy(wired, p["id"])


# ---------- ② 出租单价带 /月 ----------
@pytest.mark.parametrize("platform", PLATFORMS)
def test_rental_unit_price_says_per_month(wired, platform):
    """出租房：2500元/月 60㎡ → 单价 41.67元/㎡/月（原先写 元/㎡，少了个「/月」）"""
    p = make_property(wired, title="望京小筑3号楼501", community="望京小筑", district="朝阳区",
                      price=2_500, area=60.0, rooms=1, halls=1, property_type="rental")
    copy = _copy(wired, p["id"], platform)
    assert "2500元/月" in copy, copy
    assert "41.67元/㎡/月" in copy, copy
    assert "41.67元/㎡）" not in copy and "41.67元/㎡，" not in copy, copy


def test_sale_unit_price_has_no_per_month(wired):
    p = _sale(wired)
    copy = _copy(wired, p["id"])
    assert "16666.67元/㎡" in copy and "元/㎡/月" not in copy, copy


# ---------- ③ 平台写法归一 ----------
@pytest.mark.parametrize("written,expected", [
    ("朋友圈", "friends"), ("微信", "friends"), ("weixin", "friends"), ("moments", "friends"),
    ("friend", "friends"), ("friends", "friends"),
    ("贝壳", "beike"), ("贝壳找房", "beike"), ("beike", "beike"),
    ("安居客", "anjuke"), ("anjuke", "anjuke"),
    ("58", "58"), ("58同城", "58"), ("五八", "58"), ("58.com", "58"),
    (" 贝壳 ", "beike"), ("朋友圈", "friends"),
])
def test_platform_alias(wired, written, expected):
    p = _sale(wired)
    out = json.loads(m_listing.generate_listing_copy(property_id=p["id"], platform=written))
    assert out["success"] is True and out["platform"] == expected, (written, out)


@pytest.mark.parametrize("bad", ["weibo", "微博", "小红书", "抖音", "", None, "abc"])
def test_platform_unknown_gives_chinese_options(wired, bad):
    """乱值/没给：中文可选值清单，且**绝不静默按默认平台生成一份朋友圈文案**"""
    p = _sale(wired)
    out = json.loads(m_listing.generate_listing_copy(property_id=p["id"], platform=bad))
    assert out["success"] is False, out
    assert "copy" not in out, out
    err = out["error"]
    for word in ("朋友圈", "贝壳", "安居客", "58同城"):
        assert word in err, err


# ---------- ④ 缺字段不留空行 ----------
def test_no_empty_region_line(wired):
    """没有小区/区域/地址时，不许出现只有 emoji 的空行"""
    p = make_property(wired, title="极简房源", price=800_000, area=45.0, community=None,
                      district=None, address=None, rooms=None, halls=None, property_type="second_hand")
    for platform in PLATFORMS:
        copy = _copy(wired, p["id"], platform)
        for line in copy.split("\n"):
            assert line.strip() not in ("📍", "地址：", "【位置】"), (platform, copy)
        assert "None" not in copy and "?室" not in copy, copy
        assert "房产" not in copy, copy


def test_missing_fields_do_not_appear(wired):
    """没填的字段（朝向/装修/楼层/年份）不出现在文案里，也不写占位符"""
    p = make_property(wired, title="极简房源 1号楼101", price=800_000, area=45.0,
                      orientation=None, renovation=None, floor=None, year_built=None,
                      has_elevator=None, parking=0)
    copy = _copy(wired, p["id"])
    for label in ("朝向：", "装修：", "楼层：", "建成年份：", "有电梯", "有车位"):
        assert label not in copy, copy


# ---------- ⑤ 价格 0 = 没填价 ----------
def test_zero_price_says_pending(wired):
    p = make_property(wired, title="还没定价的房 2号楼202", price=0, area=30.0)
    for platform in PLATFORMS:
        copy = _copy(wired, p["id"], platform)
        assert "价格待定" in copy, copy
        assert not re.search(r"(?<![\d.])0万", copy), copy


# ---------- ⑥ 不存在 / 已售 / 已租 分开说 ----------
def test_missing_property_is_not_available_property(wired):
    out = json.loads(m_listing.generate_listing_copy(property_id=999999, platform="friends"))
    assert out["success"] is False
    assert "没有编号 999999 的房源" in out["error"], out
    assert "不在售" not in out["error"], out


@pytest.mark.parametrize("status,phrase", [("sold", "已经售出"), ("rented", "已经出租")])
def test_unavailable_states_are_named(wired, status, phrase):
    """已售/已租：说清是哪一套、什么状态，不能跟"不存在"混成一句"""
    p = _sale(wired, status=status, title="已成交探针房源")
    out = json.loads(m_listing.generate_listing_copy(property_id=p["id"], platform="friends"))
    assert out["success"] is False and "copy" not in out, out
    assert phrase in out["error"] and p["title"] in out["error"], out
    assert out["property_status"] in ("已售", "已租"), out


# ---------- 与房源详情同源对账 ----------
def test_copy_numbers_match_property_detail(wired, monkeypatch):
    """文案里的价格/面积/单价必须与 get_property_detail 同源"""
    from tools import real_estate_property as m_property

    monkeypatch.setattr(m_property, "_get_db", lambda: wired)
    p = _sale(wired)
    copy = _copy(wired, p["id"])
    msg = json.loads(m_property.get_property_detail(property_id=p["id"]))["message"]
    assert "150万" in copy and "150万" in msg, (copy, msg)
    assert "90㎡" in copy and "90㎡" in msg, (copy, msg)
    assert "16666.67元/㎡" in copy and "16666.67元/㎡" in msg, (copy, msg)

    rent = make_property(wired, title="望京小筑3号楼502", price=2_500, area=60.0,
                         property_type="rental")
    rcopy = _copy(wired, rent["id"])
    rmsg = json.loads(m_property.get_property_detail(property_id=rent["id"]))["message"]
    assert "2500元/月" in rcopy and "2500元/月" in rmsg, (rcopy, rmsg)
    assert "41.67元/㎡/月" in rcopy and "41.67元/㎡/月" in rmsg, (rcopy, rmsg)


def test_copy_is_stable_between_calls(wired):
    """同一房源两次生成的文案逐字一致（没有随机/时间漂移）"""
    p = _sale(wired)
    assert _copy(wired, p["id"]) == _copy(wired, p["id"])


def test_description_and_params_are_documented():
    """F295：描述要说清渠道/文体/能不能直接发；参数说明不许只有两个字"""
    from tools.registry import registry

    schema = registry.get_entry("generate_listing_copy").schema
    desc = schema["description"]
    assert len(desc) > 60, desc
    for word in ("朋友圈", "贝壳", "安居客", "58", "复制", "在售"):
        assert word in desc, (word, desc)
    props = schema["parameters"]["properties"]
    assert len(props["property_id"]["description"]) > 8, props
    assert "朋友圈" in props["platform"]["description"], props
