"""海报主标题候选（suggest_poster_titles）回归：候选必须"有依据"（2026-09-26 第 65 项）

本文件钉五件事：
① **标签与装修必须真的进候选**（原先硬编码的三个套话排在最前、`[:3]` 截断，标签从来没进过候选）；
② **无依据的断言一律不出现**：业主诚售 / 仅此一套 / 开发商直售 / 拎包入住 / 今日可看；
③ 每个候选在 `candidates_detail` 里带 `why`（依据），老字段 `candidates` 原样保留；
④ 有条件的卖点只在条件满足时出现：「随时可看」要有看房方式、「新上房源」要近 7 天录入、
   「南北通透好房」要朝向含南北通透、标签超过 6 个字不做候选；
⑤ 不存在 / 已售 / 已租 分开说；按标题命中多套不猜；两个参数都没给要说"请给编号或标题"。
"""
import json
from datetime import datetime, timedelta

import pytest
from conftest import make_property  # noqa: F401

from tools import real_estate_poster as m_poster

BANNED = ["业主诚售", "仅此一套", "开发商直售", "拎包入住", "今日可看"]


@pytest.fixture
def wired(db, monkeypatch):
    monkeypatch.setattr(m_poster, "_get_db", lambda: db)
    return db


def _suggest(**kw):
    return json.loads(m_poster.suggest_poster_titles(**kw))


def _mk(db, **kw):
    data = dict(title="海阔天空 7号楼2单元1602", community="海阔天空", district="朝阳区",
                price=1_500_000, area=90.0, status="available", property_type="second_hand")
    data.update(kw)
    return make_property(db, **data)["id"]


# ---------- ① 有依据的卖点要进候选 ----------
def test_tags_and_renovation_reach_the_candidates(wired):
    pid = _mk(wired, tags="近地铁,学区房", renovation="精装")
    out = _suggest(property_id=pid)
    assert out["success"] is True, out
    cands = out["candidates"]
    assert "近地铁好房" in cands and "学区房好房" in cands, cands
    assert any(c.endswith("好房") for c in cands), cands


def test_tag_order_respected_and_capped_at_three(wired):
    pid = _mk(wired, tags="近地铁,学区房,电梯房", renovation="精装")
    out = _suggest(property_id=pid)
    assert out["candidates"] == ["近地铁好房", "学区房好房", "精装好房"], out


def test_no_data_property_gets_no_invented_selling_points(wired):
    """什么字段都没有 → 只有中性营销词（加"近 7 天新上"这条事实），不编卖点"""
    pid = _mk(wired, title="极简房源", tags=None, renovation=None, orientation=None,
              property_type="second_hand")
    out = _suggest(property_id=pid)
    cands = out["candidates"]
    assert set(cands) <= {"今日主推", "好房推荐", "新上房源"}, cands
    assert not [c for c in cands if c.endswith("好房") and c != "好房推荐"], cands


def test_old_property_without_data_gets_exactly_the_two_marketing_words(wired):
    """字段全空 + 不是近 7 天新上 → 就两个营销词（不假装有卖点）"""
    from sqlalchemy import text

    pid = _mk(wired, title="老房源 8号楼808", tags=None, renovation=None, orientation=None)
    with wired.engine.begin() as conn:
        conn.execute(text("update re_properties set created_at=:t where id=:i"),
                     {"t": (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S"),
                      "i": pid})
    out = _suggest(property_id=pid)
    assert out["candidates"] == ["今日主推", "好房推荐"], out


@pytest.mark.parametrize("ptype,expect", [("new", "新盘在售"), ("rental", "月租好房"),
                                          ("second_hand", "今日主推")])
def test_marketing_words_follow_type(wired, ptype, expect):
    pid = _mk(wired, title=f"某小区 {ptype}", property_type=ptype, tags=None, renovation=None)
    out = _suggest(property_id=pid)
    assert expect in out["candidates"], out


# ---------- ② 无依据的断言一律不出现 ----------
@pytest.mark.parametrize("kw", [
    {"tags": "近地铁,学区房", "renovation": "精装"},
    {"tags": None, "renovation": None},
    {"property_type": "new"},
    {"property_type": "rental"},
])
def test_banned_claims_never_appear(wired, kw):
    pid = _mk(wired, **kw)
    out = _suggest(property_id=pid)
    hit = [c for c in out["candidates"] if c in BANNED]
    assert not hit, (hit, out)


def test_banned_claims_not_in_source_db_anywhere(wired):
    """同一套房源在"有看房方式/近 7 天新上"时也不该冒出这些词"""
    pid = _mk(wired, tags="近地铁", renovation="豪装", orientation="南北通透",
              viewing_note="钥匙在门店")
    out = _suggest(property_id=pid)
    assert not [c for c in out["candidates"] if c in BANNED], out


# ---------- ③ 结构化依据 ----------
def test_candidates_detail_explains_each_item(wired):
    pid = _mk(wired, tags="近地铁,学区房", renovation="精装")
    out = _suggest(property_id=pid)
    detail = out.get("candidates_detail")
    assert detail and len(detail) == len(out["candidates"]), out
    assert [d["title"] for d in detail] == out["candidates"], out
    for d in detail:
        assert d.get("why"), d
    whys = " ".join(d["why"] for d in detail)
    assert "近地铁" in whys and "精装" in whys, whys


# ---------- ④ 条件性卖点 ----------
def test_viewing_note_enables_ready_to_view(wired):
    pid_yes = _mk(wired, title="有钥匙 1号楼101", viewing_note="钥匙在门店")
    pid_no = _mk(wired, title="没钥匙 2号楼202")
    assert "随时可看" in _suggest(property_id=pid_yes)["candidates"]
    assert "随时可看" not in _suggest(property_id=pid_no)["candidates"]


def test_recent_listing_marker_only_for_new_ones(wired):
    """「新上房源」只在近 7 天录入时出现"""
    pid_new = _mk(wired, title="今天录的 3号楼303")
    assert "新上房源" in _suggest(property_id=pid_new)["candidates"]
    old = _mk(wired, title="三个月前录的 4号楼404")
    with wired.engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text("update re_properties set created_at=:t where id=:i"),
                     {"t": (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S"),
                      "i": old})
    assert "新上房源" not in _suggest(property_id=old)["candidates"]


def test_north_south_orientation_gives_candidate(wired):
    pid = _mk(wired, orientation="南北通透")
    assert "南北通透好房" in _suggest(property_id=pid)["candidates"]
    other = _mk(wired, title="朝东的房 5号楼505", orientation="东")
    assert "南北通透好房" not in _suggest(property_id=other)["candidates"]


def test_too_long_tag_is_skipped(wired):
    """标签太长不做候选（海报大字放不下），也不编一个短词顶上"""
    pid = _mk(wired, tags="地铁1号线步行3分钟,近地铁", renovation=None)
    cands = _suggest(property_id=pid)["candidates"]
    assert "地铁1号线步行3分钟好房" not in cands, cands
    assert "近地铁好房" in cands, cands


# ---------- ⑤ 措辞与选房 ----------
def test_missing_id_says_so(wired):
    out = _suggest(property_id=999999)
    assert out["success"] is False and "没有编号 999999 的房源" in out["error"], out


@pytest.mark.parametrize("status,phrase,label", [("sold", "已经售出", "已售"),
                                                 ("rented", "已经出租", "已租")])
def test_unavailable_states_are_named(wired, status, phrase, label):
    pid = _mk(wired, status=status)
    out = _suggest(property_id=pid)
    assert out["success"] is False and phrase in out["error"], out
    assert "给海报起标题" in out["error"], out
    assert out["property_status"] == label, out


def test_title_hitting_several_properties_asks(wired):
    _mk(wired, title="海阔天空 7号楼2单元1602")
    _mk(wired, title="海阔天空 9号楼1单元901")
    out = _suggest(title="海阔天空")
    assert out["success"] is False and out.get("ambiguous") is True, out
    assert len(out.get("properties") or []) >= 2, out
    assert "把编号给我" in out["error"], out


def test_title_hitting_one_returns_property_id(wired):
    pid = _mk(wired, title="独一份的房 6号楼606")
    out = _suggest(title="独一份的房 6号楼606")
    assert out["success"] is True and out["property_id"] == pid, out


def test_no_arguments_asks_for_an_id(wired):
    out = _suggest()
    assert out["success"] is False and "请给房源编号或标题" in out["error"], out
    out2 = _suggest(title="")
    assert out2["success"] is False and "请给房源编号或标题" in out2["error"], out2


def test_description_names_the_basis_rule():
    from tools.registry import registry

    desc = registry.get_entry("suggest_poster_titles").schema["description"]
    assert "真有的卖点" in desc and "查不到的断言" in desc, desc
