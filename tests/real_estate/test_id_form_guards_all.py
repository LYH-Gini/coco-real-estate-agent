"""全库「编号形态」回归（2026-09-25）：编号传非数字文本要给中文提示，不许假称"对象不存在"

背景（F91 同族，同族收口批任务 1）：框架层只拦 bool、把数字串转整数，**非数字文本不拦**
（要保住 `limit` 类"非数字按默认"的口径）。于是 `get_customer('abc')` 一路走到工具里，
被工具当成"这个 id 库里没有" → **假称"客户不存在"**，经纪人以为客户被删了。
实测 38 个带编号参数的工具几乎全中（只有房东批修过的 4 个是对的）。

本文件覆盖：
① 38 个工具 × 非数字文本 → 「X编号没能识别…编号是数字」；
② 数字字符串仍要认（不许把老调用改坏）；
③ `add_followup(customer_id='abc')` 原先**写库成功**（落一条 customer_id='abc' 的孤儿跟进）—— 必须不落库；
④ `generate_poster_grid` 的 schema 声明 `property_ids` 是数组，历史实现按逗号串 `.split(',')`，
   模型照 schema 传数组直接崩 —— 数组与逗号串都要认。
"""
import json

import pytest

from tools import (real_estate_birthday as m_birthday, real_estate_customer as m_customer,
                   real_estate_deal as m_deal, real_estate_followup as m_followup,
                   real_estate_images as m_images, real_estate_intent as m_intent,
                   real_estate_listing as m_listing, real_estate_owner as m_owner,
                   real_estate_poster as m_poster, real_estate_property as m_property,
                   real_estate_purge as m_purge, real_estate_scripts as m_scripts,
                   real_estate_video as m_video, real_estate_viewing as m_viewing)

MODULES = [m_birthday, m_customer, m_deal, m_followup, m_images, m_intent, m_listing, m_owner,
           m_poster, m_property, m_purge, m_scripts, m_video, m_viewing]


@pytest.fixture
def wired(db, monkeypatch):
    """让所有房产工具模块都指向同一个临时库"""
    for mod in MODULES:
        monkeypatch.setattr(mod, "_get_db", lambda _db=db: _db)
    return db


@pytest.fixture
def fixtures(wired):
    """把每一类对象都造一条，编号一律用 1（真实路径建，回头断言"真的落地了"）"""
    c = wired.add_customer(name="编号客户", tier="C", customer_type="buy_second_hand")
    p = wired.add_property(title="编号房源", price=1_500_000, area=80.0,
                           property_type="second_hand", status="available")
    # 第二套：成交单会把第一套标记成已售，海报/文案/图片这类"只要在售"的工具要用这一套
    p2 = wired.add_property(title="编号房源B（在售）", price=1_650_000, area=85.0,
                            property_type="second_hand", status="available")
    from datetime import datetime
    v = wired.add_viewing(customer_id=c["id"], property_id=p["id"],
                          viewing_time=datetime(2026, 10, 1, 10, 0))
    d = wired.add_deal(customer_id=c["id"], property_id=p["id"], stage="deposit")
    s = wired.add_script(name="编号话术", content="内容", scenario="custom")
    f = wired.add_followup(customer_id=c["id"], type="note", content="编号跟进")
    ids = {"customer": c["id"], "property": p["id"], "property_available": p2["id"],
           "viewing": v["id"], "deal": d["id"], "script": s["id"], "followup": f["id"]}
    assert all(ids.values()), ids
    return ids


# ==================== ① 38 个工具：非数字文本 → 中文提示 ====================
# (工具函数, 参数名, 编号中文名, 其它固定参数, 夹具键)
ID_CASES = [
    (m_customer.get_customer, "customer_id", "客户编号", {}, "customer"),
    (m_customer.update_customer, "customer_id", "客户编号", {"notes": "x"}, "customer"),
    (m_customer.update_tier, "customer_id", "客户编号", {"tier": "A"}, "customer"),
    (m_customer.update_customer_stage, "customer_id", "客户编号", {"stage": "strong"}, "customer"),
    (m_customer.add_customer_tag, "customer_id", "客户编号", {"tag": "急售"}, "customer"),
    (m_customer.remove_customer_tag, "customer_id", "客户编号", {"tag": "急售"}, "customer"),
    (m_customer.list_customer_tags, "customer_id", "客户编号", {}, "customer"),
    (m_customer.customer_change_history, "customer_id", "客户编号", {}, "customer"),
    (m_customer.add_referral, "referrer_customer_id", "客户编号", {"referred_name": "被介绍人"},
     "customer"),
    (m_birthday.update_birthday, "customer_id", "客户编号", {"birthday": "1990-01-01"}, "customer"),
    (m_followup.get_followups, "customer_id", "客户编号", {}, "customer"),
    (m_followup.add_followup, "customer_id", "客户编号", {"content": "跟进"}, "customer"),
    (m_followup.schedule_reminder, "customer_id", "客户编号",
     {"date": "2026-10-05", "time": "09:00"}, "customer"),
    (m_viewing.schedule_viewing, "customer_id", "客户编号",
     {"property_id": 1, "viewing_time": "2026-10-05 10:00"}, "customer"),
    (m_deal.start_deal, "customer_id", "客户编号", {"property_id": 1}, "customer"),
    (m_intent.intent_score, "customer_id", "客户编号", {}, "customer"),
    (m_property.match_property, "customer_id", "客户编号", {}, "customer"),
    (m_purge.delete_customer, "customer_id", "客户编号", {"dry_run": True}, "customer"),

    (m_property.get_property_detail, "property_id", "房源编号", {}, "property"),
    (m_property.update_property, "property_id", "房源编号", {"district": "x"}, "property"),
    (m_property.price_history, "property_id", "房源编号", {}, "property"),
    (m_property.find_alternatives, "property_id", "房源编号", {}, "property"),
    (m_intent.compare_property, "property_id", "房源编号", {}, "property"),
    (m_images.list_property_images, "property_id", "房源编号", {}, "property"),
    (m_images.add_property_images, "property_id", "房源编号", {"images": "a.jpg"}, "property"),
    (m_listing.generate_listing_copy, "property_id", "房源编号", {"platform": "friends"},
     "property"),
    (m_video.generate_short_video_script, "property_id", "房源编号", {}, "property"),
    (m_poster.suggest_poster_titles, "property_id", "房源编号", {}, "property"),
    (m_poster.generate_property_poster, "property_id", "房源编号", {"allow_missing": True},
     "property"),
    (m_viewing.clear_defect_tag, "property_id", "房源编号", {"tag": "采光差"}, "property"),
    (m_purge.delete_property, "property_id", "房源编号", {"dry_run": True}, "property"),
    (m_property.deduplicate_properties, "keep_id", "房源编号", {"dry_run": True}, "property"),
    (m_viewing.schedule_viewing, "property_id", "房源编号",
     {"customer_id": 1, "viewing_time": "2026-10-05 10:00"}, "property"),

    (m_viewing.get_viewing, "viewing_id", "带看编号", {}, "viewing"),
    (m_viewing.record_viewing, "viewing_id", "带看编号", {"status": "done"}, "viewing"),
    (m_viewing.list_viewings, "customer_id", "客户编号", {}, "customer"),
    (m_deal.get_deal, "deal_id", "成交单编号", {}, "deal"),
    (m_deal.advance_deal, "deal_id", "成交单编号", {"stage": "signing"}, "deal"),
    (m_scripts.delete_script, "script_id", "话术编号", {}, "script"),
]


@pytest.mark.parametrize("func,param,label,extra,key",
                         ID_CASES,
                         ids=[f"{f.__name__}.{p}" for f, p, _, _, _ in ID_CASES])
def test_nonnumeric_id_gets_chinese_hint(fixtures, func, param, label, extra, key):
    """编号传 'abc' → 中文提示，且**不许**说"不存在"（那是"库里没有"的意思，两回事）"""
    kwargs = dict(extra)
    kwargs[param] = "abc"
    out = json.loads(func(**kwargs))
    err = out.get("error") or ""
    assert out.get("success") is not True, out
    assert f"{label}没能识别" in err, err
    assert "编号是数字" in err and "abc" in err, err
    assert "不存在" not in err, f"不许假称对象不存在：{err}"
    assert "Tool execution failed" not in json.dumps(out, ensure_ascii=False)


@pytest.mark.parametrize("func,param,label,extra,key",
                         ID_CASES,
                         ids=[f"{f.__name__}.{p}" for f, p, _, _, _ in ID_CASES])
def test_numeric_string_id_still_accepted(fixtures, func, param, label, extra, key):
    """数字字符串（模型常这么传）必须照旧认得出真实编号，不能一改就全拦"""
    kwargs = dict(extra)
    other = [k for k, v in kwargs.items() if isinstance(v, int) and k != param]
    for k in other:
        kwargs[k] = 1
    kwargs[param] = str(fixtures[key])
    out = json.loads(func(**kwargs))
    err = out.get("error") or ""
    assert f"{label}没能识别" not in err, err


def test_followup_with_garbage_customer_id_does_not_write(fixtures, wired):
    """A 级：原先 add_followup(customer_id='abc') 会真的写库（落一条孤儿跟进）"""
    before = wired.count_followups() if hasattr(wired, "count_followups") else None
    out = json.loads(m_followup.add_followup(customer_id="abc", content="垃圾编号"))
    assert out.get("success") is not True and "客户编号没能识别" in (out.get("error") or ""), out
    rows = wired.get_followups(fixtures["customer"], limit=100)
    assert all(str(r.get("customer_id")) != "abc" for r in rows), rows
    if before is not None:
        assert wired.count_followups() == before


# ==================== ② 九宫格：数组与逗号串都认 ====================
def test_poster_grid_accepts_array_and_comma_string(fixtures, wired, tmp_path, monkeypatch):
    """schema 说 property_ids 是数组 → 传数组不能再崩；逗号串仍兼容"""
    monkeypatch.setattr(m_poster, "_poster_dir", lambda: str(tmp_path))
    monkeypatch.setattr(m_poster, "_agent_card", lambda: {"name": "Coco", "phone": "13800000000"})
    pid = fixtures["property_available"]
    for raw in ([pid], str(pid), [str(pid)]):
        out = json.loads(m_poster.generate_poster_grid(property_ids=raw))
        assert "Tool execution failed" not in json.dumps(out, ensure_ascii=False), (raw, out)
        assert out.get("success") is True and out.get("property_ids") == [pid], (raw, out)


@pytest.mark.parametrize("raw", [["abc"], "abc", ["abc", "def"]])
def test_poster_grid_bad_id_gets_hint(fixtures, wired, raw):
    out = json.loads(m_poster.generate_poster_grid(property_ids=raw))
    assert out.get("success") is not True and "房源编号没能识别" in (out.get("error") or ""), out
