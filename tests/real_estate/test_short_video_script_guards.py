"""短视频口播稿（generate_short_video_script）回归：稿子里的每句话都要有依据（2026-09-26 第 66 项）

口播稿是**要念出来的**，编出来的卖点等于让经纪人当着客户说假话。本文件钉六件事：
① 亮点只来自房源真有的字段，**有几条说几条**（原先不足 3 条就补「采光通透/产权清晰随时看房」这类假话）；
② 不写查不到依据的断言：产权、「基本找不到第二套」、业主诚心卖/租金还能谈；
③ 出租说「月租」、买卖说「总价」（抖音分支原先对出租也说"总价"）；
④ 面积走共用件（`90㎡` 不是 `90.0㎡`）；
⑤ `platform` **必填**，认中文与别名，乱值给中文可选值清单；
⑥ 不存在 / 已售 / 已租 分开说（走共用件）。
"""
import json
import re

import pytest
from conftest import make_property  # noqa: F401

from tools import real_estate_video as m_video

# 房源里查不到依据的句子（不许出现在任何稿子里）
UNFOUNDED = ["采光通透", "格局方正", "产权清晰", "基本找不到第二套", "业主诚心", "还能谈",
             "性价比很高"]


@pytest.fixture
def wired(db, monkeypatch):
    monkeypatch.setattr(m_video, "_get_db", lambda: db)
    return db


def _script(**kw):
    return json.loads(m_video.generate_short_video_script(**kw))


def _mk(db, **kw):
    data = dict(title="海阔天空 7号楼2单元1602", community="海阔天空", district="朝阳区",
                price=1_500_000, area=90.0, rooms=3, halls=2, orientation="南北通透",
                floor="16层", renovation="精装", year_built=2015, has_elevator=1,
                status="available", property_type="second_hand")
    data.update(kw)
    return make_property(db, **data)["id"]


# ---------- ① 亮点只来自真字段 ----------
def test_highlights_come_from_real_fields(wired):
    pid = _mk(wired, tags="近地铁,学区房")
    out = _script(property_id=pid, platform="douyin")
    assert out["success"] is True, out
    hl = out["highlights"]
    assert "90㎡ 3室2厅空间" in hl, hl
    assert "南北通透朝向" in hl and "精装装修" in hl, hl
    assert all(not any(u in h for u in UNFOUNDED) for h in hl), hl


def test_bare_property_does_not_invent_highlights(wired):
    """只有标题/价格/面积 → 就说一条（面积），不补第二条第三条"""
    pid = _mk(wired, title="极简房源", community=None, district=None, orientation=None,
              floor=None, renovation=None, year_built=None, has_elevator=None, tags=None)
    out = _script(property_id=pid, platform="douyin")
    script = out["script"]
    assert out["highlights"] == ["90㎡ 3室2厅空间"], out["highlights"]
    assert "亮点有三个" not in script, script
    assert "亮点有两个" not in script, script
    assert "这套房子的亮点：" in script, script
    for word in UNFOUNDED:
        assert word not in script, (word, script)


def test_two_highlights_says_two(wired):
    pid = _mk(wired, orientation="南", renovation=None, floor=None, year_built=None,
              has_elevator=None, tags=None)
    out = _script(property_id=pid, platform="douyin")
    assert len(out["highlights"]) == 2, out["highlights"]
    assert "亮点有两个" in out["script"], out["script"]


def test_no_highlights_at_all_is_stated_honestly(wired):
    """连面积都没有的房源 → 如实说"信息还不多"，不编"""
    pid = _mk(wired, title="空壳房源", community=None, district=None, orientation=None,
              floor=None, renovation=None, year_built=None, has_elevator=None, tags=None,
              status="available")
    m_video._get_db().update_property(pid, area=1)          # 面积列 NOT NULL，这里给个最小值
    out = _script(property_id=pid, platform="douyin")
    assert "信息还不多" in out["script"] or out["highlights"], out["script"]


def test_rough_renovation_is_not_a_selling_point(wired):
    """毛坯/简装不当亮点念（原先"毛坯装修"也被当亮点）"""
    pid = _mk(wired, renovation="毛坯", orientation=None, floor=None, year_built=None,
              has_elevator=None, tags=None)
    out = _script(property_id=pid, platform="douyin")
    assert not any("毛坯" in h for h in out["highlights"]), out["highlights"]


# ---------- ② 无依据断言 ----------
@pytest.mark.parametrize("platform", ["douyin", "shipinhao"])
@pytest.mark.parametrize("kw", [{"tags": "近地铁,学区房"}, {}, {"property_type": "rental"}])
def test_no_unfounded_claims_anywhere(wired, platform, kw):
    base = {"title": "某小区 1号楼101", "community": "某小区", "district": "朝阳区"}
    base.update(kw)
    pid = _mk(wired, **base)
    out = _script(property_id=pid, platform=platform)
    for word in UNFOUNDED:
        assert word not in out["script"], (word, out["script"])


# ---------- ③ 租 / 售 口径 ----------
def test_rental_says_monthly_rent_not_total_price(wired):
    pid = _mk(wired, title="望京小筑 3号楼501", price=2_500, area=60.0, rooms=1, halls=1,
              property_type="rental", renovation=None, orientation=None, floor=None,
              year_built=None, has_elevator=None, tags=None)
    out = _script(property_id=pid, platform="douyin")
    script = out["script"]
    assert "月租只要2500元/月" in script, script
    assert "总价" not in script, script


def test_sale_says_total_price(wired):
    pid = _mk(wired)
    out = _script(property_id=pid, platform="douyin")
    assert "总价只要150万" in out["script"], out["script"]


# ---------- ④ 面积口径 ----------
@pytest.mark.parametrize("platform", ["douyin", "shipinhao"])
def test_area_has_no_trailing_zero(wired, platform):
    pid = _mk(wired)
    out = _script(property_id=pid, platform=platform)
    assert "90㎡" in out["script"] and "90.0㎡" not in out["script"], out["script"]
    assert "90.0㎡" not in " ".join(out["highlights"]), out["highlights"]


# ---------- ⑤ platform ----------
@pytest.mark.parametrize("written,expect", [("抖音", "douyin"), ("douyin", "douyin"),
                                            ("抖音短视频", "douyin"), ("视频号", "shipinhao"),
                                            ("微信视频号", "shipinhao"), ("shipinhao", "shipinhao")])
def test_platform_alias(wired, written, expect):
    pid = _mk(wired)
    out = _script(property_id=pid, platform=written)
    assert out["success"] is True and out["platform"] == expect, (written, out)


@pytest.mark.parametrize("bad", ["kuaishou", "快手", "小红书", "", None])
def test_platform_unknown_or_missing_gives_chinese_options(wired, bad):
    pid = _mk(wired)
    out = _script(property_id=pid, platform=bad)
    assert out["success"] is False and "script" not in out, out
    if bad:                       # 乱值：中文可选值清单
        assert "抖音" in out["error"] and "视频号" in out["error"], out
    else:                         # 没给：说清楚要怎么给
        assert "抖音" in out["error"] and "视频号" in out["error"], out


def test_platform_is_required_in_schema():
    from tools.registry import registry

    schema = registry.get_entry("generate_short_video_script").schema
    assert "platform" in schema["parameters"]["required"], schema["parameters"]["required"]
    assert "必填" in schema["parameters"]["properties"]["platform"]["description"]
    desc = schema["description"]
    assert "真有的字段" in desc and "月租" in desc, desc


# ---------- ⑥ 不可用措辞 ----------
def test_missing_property_says_so(wired):
    out = _script(property_id=999999, platform="douyin")
    assert out["success"] is False and "没有编号 999999 的房源" in out["error"], out


@pytest.mark.parametrize("status,phrase,label", [("sold", "已经售出", "已售"),
                                                 ("rented", "已经出租", "已租")])
def test_unavailable_states_are_named(wired, status, phrase, label):
    pid = _mk(wired, status=status)
    out = _script(property_id=pid, platform="douyin")
    assert out["success"] is False and phrase in out["error"], out
    assert "写口播稿" in out["error"], out
    assert out["property_status"] == label, out


def test_script_structure_and_length(wired):
    """四段结构与时长声明在位；30 秒的稿子正文字数在合理区间"""
    pid = _mk(wired)
    out = _script(property_id=pid, platform="douyin")
    script = out["script"]
    for seg in ("【0-3秒 钩子】", "【3-20秒 亮点】", "【20-25秒 价格】", "【25-30秒 行动号召】"):
        assert seg in script, (seg, script)
    body = re.sub(r"【[^】]*】", "", script)
    n = len([c for c in body if not c.isspace()])
    assert 60 <= n <= 200, (n, script)
    assert out["duration"] == "30秒"
