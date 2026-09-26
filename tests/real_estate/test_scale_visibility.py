"""房源规模契约：房源超过默认条数上限时，按编号仍要能取到那一套。

2026-09-18 实测复现：造 12000 套房源后，给第 11500 套做海报/文案/短视频/图片会报"房源不存在或不在售"
（这些工具原先在 search_properties() 的前 50 条里遍历找编号）。本测试用第 115 套把这条链钉死。
"""
import json

from conftest import make_property


def _seed_many(db, n=120):
    ids = []
    for i in range(1, n + 1):
        p = make_property(db, title=f"规模测试 {i}号楼{i}单元101", area=100.0, price=1_500_000)
        ids.append(p["id"])
    return ids


def _patch_all(monkeypatch, db):
    import tools.real_estate_images as im
    import tools.real_estate_listing as li
    import tools.real_estate_poster as po
    import tools.real_estate_video as vi
    for mod in (im, li, po, vi):
        monkeypatch.setattr(mod, "_get_db", lambda: db)


def test_poster_finds_property_beyond_default_limit(db, monkeypatch):
    """第 115 套房源能生成海报（按编号），不再报"房源不存在" """
    import tools.real_estate_poster as po
    _patch_all(monkeypatch, db)
    monkeypatch.setenv("COCO_BRAND", "测试品牌")   # 海报需要品牌，环境变量兜底
    ids = _seed_many(db)
    target = ids[114]
    assert target > 50, "目标房源必须在默认 50 条之外，否则测不出问题"
    # 2026-09-19 新流程：主标题/信息齐全才出图，这里允许缺项直出，只验证"能按编号取到房源"
    data = json.loads(po.generate_property_poster(property_id=target, poster_title="今日主推", template="A", show_room_no="full",
                                                 allow_missing=True))
    assert data.get("error") != "房源不存在或不在售", data
    assert data["success"] is True, data


def test_poster_finds_by_title_beyond_default_limit(db, monkeypatch):
    """按标题也能找到靠后的房源"""
    import tools.real_estate_poster as po
    _patch_all(monkeypatch, db)
    monkeypatch.setenv("COCO_BRAND", "测试品牌")
    _seed_many(db)
    data = json.loads(po.generate_property_poster(title="规模测试 115号楼115单元101", template="A", show_room_no="full",
                                                 poster_title="今日主推", allow_missing=True))
    assert data.get("error") != "房源不存在或不在售", data


def test_listing_and_video_and_images_find_late_property(db, monkeypatch):
    """发布文案 / 短视频脚本 / 图片列表 都能取到靠后的房源"""
    import tools.real_estate_images as im
    import tools.real_estate_listing as li
    import tools.real_estate_video as vi
    _patch_all(monkeypatch, db)
    ids = _seed_many(db)
    target = ids[114]
    for fn, kwargs in ((li.generate_listing_copy, {"property_id": target, "platform": "friends"}),
                       (vi.generate_short_video_script, {"property_id": target, "platform": "douyin"}),
                       (im.list_property_images, {"property_id": target}),
                       (im.add_property_images, {"property_id": target, "images": "a.jpg"})):
        data = json.loads(fn(**kwargs))
        assert data.get("error") != "房源不存在或不在售", f"{fn.__name__}: {data}"
        assert data["success"] is True, f"{fn.__name__}: {data}"


def test_sold_property_still_reports_not_available(db, monkeypatch):
    """非在售房源仍然如实报（2026-09-26 口径细化：已售/已租点到状态，不再与"不存在"同一句）"""
    import tools.real_estate_listing as li
    _patch_all(monkeypatch, db)
    p = make_property(db, title="已售房源", status="sold")
    data = json.loads(li.generate_listing_copy(property_id=p["id"]))
    assert data["success"] is False
    assert "已经售出" in data["error"] and "不能发在售文案" in data["error"], data
    assert data["property_status"] == "已售", data


def test_total_properties_numbers_are_real(db, monkeypatch):
    """匹配类工具报的"房源总数"必须是真实在售数（不是 50/10000 这种截断值）"""
    import tools.real_estate_property as tp
    monkeypatch.setattr(tp, "_get_db", lambda: db)
    _seed_many(db, n=60)          # 超过默认 50 条，专门验证不会被截断
    db.add_customer(name="数字口径客户", budget_min=1_000_000, budget_max=2_000_000,
                    customer_type="buy_second_hand", tier="A")
    out = json.loads(tp.match_property(customer_id=1, top_n=1))
    assert out["total_properties"] == db.get_stats()["available_properties"] == 60
    out2 = json.loads(tp.batch_match_report(top_n=1))
    assert out2["total_properties"] == 60
