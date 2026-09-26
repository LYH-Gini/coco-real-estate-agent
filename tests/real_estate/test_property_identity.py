"""房源判重（按身份要素）回归 —— 2026-09-21 重做

真实事故：同一套房换个写法再录一次就会重复建档。旧规则只比"标题归一化后是否一字不差"，
实测 9 种常见写法里 7 种漏判。本文件把两批用例固化：

* 批 A：同一套房的不同写法 → **必须**判重（旧规则下 7/9 漏判 → 改前必红）
* 批 B：不同房源 → **必须**放行（防误拦：同小区不同房号/楼栋/单元、面积差 2㎡、二期、不同小区）
* 疑似重复：同小区同面积但房号不全 → 照常录入 + 返回 suspected_duplicate 供 Coco 提示
* 批量扫描：deduplicate_properties 也要认出写法不同的同一套房
"""
import json

import pytest

from conftest import make_property

NEW_TITLE = "海口美兰区海甸岛恒大美丽沙 3 号楼 1 单元 1602"
NEW_AREA = 128.0

# (说明, 标题, 面积) —— 都是同一套房的写法差异
SAME_PROPERTY_VARIANTS = [
    ("完全相同", NEW_TITLE, 128),
    ("去掉片区词（海甸岛）", "海口美兰区恒大美丽沙 3 号楼 1 单元 1602", 128),
    ("只有小区+房号", "恒大美丽沙3号楼1单元1602", 128),
    ("小区简称「美丽沙」", "海甸岛美丽沙 3号楼1单元1602", 128),
    ("楼栋写成「3栋」", "海口美兰区海甸岛恒大美丽沙 3栋1单元1602", 128),
    ("房号带「室」", "海甸岛恒大美丽沙3号楼1单元1602室", 128),
    ("楼栋带「#」", "海甸岛恒大美丽沙 3#1单元1602", 128),
    ("标题里带楼层「16层1602」", "海甸岛恒大美丽沙 3号楼1单元16层1602", 128),
    ("面积口径差 0.5 ㎡", "海甸岛恒大美丽沙3号楼1单元1602", 128.5),
]

# (说明, 标题, 面积) —— 都是不同的房源，必须放行
DIFFERENT_PROPERTY_CASES = [
    ("同小区不同房号", "海甸岛恒大美丽沙 3号楼1单元1601", 128),
    ("同小区不同楼栋", "海甸岛恒大美丽沙 5号楼1单元1602", 128),
    ("同小区不同单元", "海甸岛恒大美丽沙 3号楼2单元1602", 128),
    ("同小区同房号、面积差 2 ㎡", "海甸岛恒大美丽沙 3号楼1单元1602", 130),
    ("同小区但属「二期」", "海甸岛恒大美丽沙二期3号楼1单元1602", 128),
    ("不同小区、房号与面积都相同", "保利中央海岸 3号楼1单元1602", 128),
    ("不同小区（名不相近）", "雅居乐金沙湾 8号楼2单元301", 128),
    ("房号带字母后缀（1602A ≠ 1602）", "海甸岛恒大美丽沙 3号楼1单元1602A", 128),
]


@pytest.mark.parametrize("label,old_title,old_area", SAME_PROPERTY_VARIANTS,
                         ids=[c[0] for c in SAME_PROPERTY_VARIANTS])
def test_same_property_different_wording_blocks(db, label, old_title, old_area):
    """同一套房换写法 → 判重（这是本次修的核心：旧规则漏判）"""
    make_property(db, title=old_title, area=old_area)
    dup = db.find_duplicate_property(title=NEW_TITLE, area=NEW_AREA)
    assert dup is not None, f"{label}：{old_title} 与 {NEW_TITLE} 应判为同一套"


@pytest.mark.parametrize("label,other_title,other_area", DIFFERENT_PROPERTY_CASES,
                         ids=[c[0] for c in DIFFERENT_PROPERTY_CASES])
def test_different_property_passes(db, label, other_title, other_area):
    """不同房源 → 放行（判重变严后最容易误拦的就是这些）"""
    make_property(db, title=other_title, area=other_area)
    dup = db.find_duplicate_property(title=NEW_TITLE, area=NEW_AREA)
    assert dup is None, f"{label}：{other_title} 是不同房源，不该被判重"


def test_tool_blocks_before_insert_and_force_still_works(db, monkeypatch):
    """工具层：判重命中 → 不落库；force=true 仍可强录（保留原行为）"""
    import tools.real_estate_property as t
    monkeypatch.setattr(t, "_get_db", lambda: db)
    make_property(db, title="恒大美丽沙3号楼1单元1602", area=128.0, price=2_100_000)
    before = db.get_stats().get("available_properties", 0)

    out = json.loads(t.add_property(title=NEW_TITLE, price=2_200_000, area=128.0))
    assert out["success"] is False and out["duplicate"] is True, out
    assert out["existing_property"]["title"] == "恒大美丽沙3号楼1单元1602"
    assert db.get_stats().get("available_properties", 0) == before, "命中重复不得落库"

    forced = json.loads(t.add_property(title=NEW_TITLE, price=2_200_000, area=128.0, force=True))
    assert forced["success"] is True


def test_sold_property_still_ignored(db):
    """已售房源不参与判重（只认在售，保留原行为）"""
    make_property(db, title="恒大美丽沙3号楼1单元1602", area=128.0, status="sold")
    assert db.find_duplicate_property(title=NEW_TITLE, area=128.0) is None


def test_exclude_id_keeps_update_path_clean(db):
    """更新场景排除自身 id（保留原行为）"""
    p = make_property(db, title="恒大美丽沙3号楼1单元1602", area=128.0)
    assert db.find_duplicate_property(title=NEW_TITLE, area=128.0, exclude_id=p["id"]) is None


def test_suspected_duplicate_hints_without_blocking(db, monkeypatch):
    """房号不全时只提示不拦：照常录入 + 返回 suspected_duplicate"""
    import tools.real_estate_property as t
    monkeypatch.setattr(t, "_get_db", lambda: db)
    # 库里这条没写房号 → 新的这条带房号，无法确定是不是同一套 → 只提示
    make_property(db, title="海甸岛恒大美丽沙", area=128.0, price=2_100_000)

    out = json.loads(t.add_property(title=NEW_TITLE, price=2_200_000, area=128.0))
    assert out["success"] is True, out
    assert out["suspected_duplicate"]["area"] == 128.0
    assert "hint" in out["suspected_duplicate"]


def test_suspected_not_triggered_for_different_area_or_community(db):
    """疑似判定的边界：面积不同 / 小区不同 → 不给提示（少打扰）"""
    make_property(db, title="海甸岛恒大美丽沙 3号楼1单元", area=128.0)
    assert db.find_suspected_property(title="海甸岛恒大美丽沙 5号楼2单元", area=130.0) is None
    assert db.find_suspected_property(title="保利中央海岸 5号楼2单元", area=128.0) is None


def test_batch_scan_finds_variation_duplicates(db):
    """批量扫描：写法不同的同一套房也要认出来（旧实现按标题+面积+价格完全相同，抓不到）"""
    make_property(db, title="恒大美丽沙3号楼1单元1602", area=128.0, price=2_100_000)
    make_property(db, title=NEW_TITLE, area=128.5, price=2_200_000)
    make_property(db, title="保利中央海岸 3号楼1单元1602", area=128.0, price=2_300_000)

    groups = db.find_duplicate_properties()
    assert len(groups) == 1, groups
    assert len(groups[0]) == 2, groups
    # 保留最早录入的那条
    assert groups[0][0] < groups[0][1]


def test_deduplicate_tool_reports_and_cleans(db, monkeypatch):
    """deduplicate_properties：dry_run 先报明细，确认后只删重复项、保留最早那条"""
    import tools.real_estate_property as t
    monkeypatch.setattr(t, "_get_db", lambda: db)
    keep = make_property(db, title="恒大美丽沙3号楼1单元1602", area=128.0)
    dup = make_property(db, title=NEW_TITLE, area=128.5)

    dry = json.loads(t.deduplicate_properties(dry_run=True))
    assert dry["result"]["duplicate_groups"] == 1, dry
    assert dry["result"]["groups"][0]["keep_id"] == keep["id"]
    assert dry["result"]["groups"][0]["duplicate_ids"] == [dup["id"]]
    assert db.get_stats().get("available_properties", 0) == 2, "dry_run 不得真的删"

    done = json.loads(t.deduplicate_properties(dry_run=False))
    assert done["result"]["removable"] == [dup["id"]]
    assert db.get_stats().get("available_properties", 0) == 1


def test_keeper_prefers_owner_record(db, monkeypatch):
    """带业主信息的那条优先保留，光版那条被删（优先级 + 保护规则共同作用的结果）"""
    import tools.real_estate_property as t
    monkeypatch.setattr(t, "_get_db", lambda: db)
    plain = make_property(db, title="恒大美丽沙3号楼1单元1602", area=128.0)
    owned = make_property(db, title=NEW_TITLE, area=128.5)
    db.link_owner_to_property(owned["id"], name="陈志强", phone="13800000000")

    out = json.loads(t.deduplicate_properties(dry_run=False))
    assert out["result"]["groups"][0]["keep_id"] == owned["id"], out["result"]
    assert out["result"]["removable"] == [plain["id"]], out["result"]
    assert db.get_property(owned["id"]) is not None


def test_keeper_prefers_record_with_images(db, monkeypatch):
    """带图片的那条优先保留"""
    import tools.real_estate_property as t
    monkeypatch.setattr(t, "_get_db", lambda: db)
    plain = make_property(db, title="恒大美丽沙3号楼1单元1602", area=128.0)
    with_img = make_property(db, title=NEW_TITLE, area=128.5, images="/tmp/a.jpg")

    out = json.loads(t.deduplicate_properties(dry_run=False))
    assert out["result"]["groups"][0]["keep_id"] == with_img["id"], out["result"]
    assert out["result"]["removable"] == [plain["id"]]


def test_protection_skips_when_drop_carries_the_only_owner(db, monkeypatch):
    """被删项带业主、保留项没有（保留项靠"在售"胜出）→ 不合并时跳过，开 merge 才并进保留项"""
    import tools.real_estate_property as t
    monkeypatch.setattr(t, "_get_db", lambda: db)
    sold_with_owner = make_property(db, title=NEW_TITLE, area=128.0, status="sold")
    db.link_owner_to_property(sold_with_owner["id"], name="陈志强", phone="13800000000")
    live_plain = make_property(db, title="恒大美丽沙3号楼1单元1602", area=128.0, status="available")

    no_merge = json.loads(t.deduplicate_properties(dry_run=True))
    skipped = no_merge["result"]["skipped"]
    assert skipped and skipped[0]["id"] == sold_with_owner["id"], no_merge["result"]
    assert "合并" in skipped[0]["reason"]

    with_merge = json.loads(t.deduplicate_properties(dry_run=False, merge=True))
    assert with_merge["result"]["removable"] == [sold_with_owner["id"]], with_merge["result"]
    assert db.get_property(live_plain["id"])["owner_id"], "业主关联应并到在售那条上"


def test_cleanup_still_removes_plain_duplicate(db, monkeypatch):
    """反向：保留项本身有业主、重复项没有 → 照常清理（保护不能变成一律不动）"""
    import tools.real_estate_property as t
    monkeypatch.setattr(t, "_get_db", lambda: db)
    keep = make_property(db, title="恒大美丽沙3号楼1单元1602", area=128.0)
    db.link_owner_to_property(keep["id"], name="陈志强", phone="13800000000")
    dup = make_property(db, title=NEW_TITLE, area=128.5)

    out = json.loads(t.deduplicate_properties(dry_run=False))
    assert out["result"]["removable"] == [dup["id"]], out
    assert db.get_stats().get("available_properties", 0) == 1


# ---------- 2026-09-21 真实事故：Coco 自己生成的标题把房号解析带偏 ----------
# （同一套出租房被重发 → 又建了一条；根因是解析时先删空格，把「1802 1 室」粘成 18021）
# (说明, 库里已有的标题, 再次录入的标题, 面积)
REAL_TITLE_VARIANTS = [
    ("出租标题带「1 室 1 厅精装出租」尾缀", "国贸京华城 4 号楼 1 单元 1802 1 室 1 厅精装出租",
     "海口龙华区国贸京华城附近 4 号楼 1 单元 1802", 45),
    ("标题带面积 45平", "京华城 1802 45平 精装", "京华城 4号楼1单元1802", 45),
    ("标题带面积 145平（三位数）", "保利中央海岸 7号楼2单元2103 145平", "保利中央海岸 7号楼2单元2103", 145),
    ("标题带租金 月租2200", "京华城 1802 月租2200", "京华城 4号楼1单元1802", 45),
    ("标题带总价 280万", "保利中央海岸 7号楼2单元2103 280万", "保利中央海岸 7号楼2单元2103", 145),
    ("单元写中文「4栋一单元」", "京华城 4栋一单元1802", "京华城 4号楼1单元1802", 45),
]


@pytest.mark.parametrize("label,old_title,new_title,area", REAL_TITLE_VARIANTS,
                         ids=[c[0] for c in REAL_TITLE_VARIANTS])
def test_title_with_extra_numbers_still_blocks(db, label, old_title, new_title, area):
    """标题里带面积/租金/总价/室厅尾缀时，房号不能被带偏 → 仍须判重"""
    make_property(db, title=old_title, area=area)
    assert db.find_duplicate_property(title=new_title, area=area) is not None, f"{label}：{old_title}"


def test_title_with_area_but_different_room_passes(db):
    """反向：标题带面积数字，但房号不同 → 必须放行（别把 45 平当房号）"""
    make_property(db, title="京华城 1803 45平 精装", area=45.0)
    assert db.find_duplicate_property(title="京华城 4号楼1单元1802", area=45.0) is None


# ---------- 疑似重复：不拦，但要让经纪人看得见 ----------
def test_suspected_when_existing_record_is_off_market(db, monkeypatch):
    """库里那条已售（同类型）→ 不拦，但返回疑似提示（原因写明状态）"""
    import tools.real_estate_property as t
    monkeypatch.setattr(t, "_get_db", lambda: db)
    make_property(db, title="京华城 4号楼1单元1802", area=45.0, status="sold")

    out = json.loads(t.add_property(title="京华城 4号楼1单元1802", price=2200, area=45.0,
                                    property_type="second_hand"))
    assert out["success"] is True, out
    assert "sold" in out["suspected_duplicate"]["reason"]


def test_suspected_when_area_mismatch(db, monkeypatch):
    """同房号同类型但面积不符（45 vs 48）→ 不拦，但返回疑似提示（原因写明面积）"""
    import tools.real_estate_property as t
    monkeypatch.setattr(t, "_get_db", lambda: db)
    make_property(db, title="京华城 4号楼1单元1802", area=45.0)

    out = json.loads(t.add_property(title="京华城 4号楼1单元1802", price=2200, area=48.0,
                                    property_type="second_hand"))
    assert out["success"] is True, out
    assert "㎡" in out["suspected_duplicate"]["reason"]


# ---------- 真实库样本（两对重复）----------
REAL_LIBRARY = [
    ("海口秀英区西海岸雅居乐金沙湾 6 号楼 1 单元 1202", 110.0, "new"),
    ("西海岸雅居乐金沙湾 6 号楼 1 单元 1202 3 室 2 厅 110㎡", 110.0, "new"),
    ("海口龙华区国贸京华城附近 4 号楼 1 单元 1802", 45.0, "rental"),
    ("国贸京华城 4 号楼 1 单元 1802 1 室 1 厅精装出租", 45.0, "rental"),
]


def test_real_library_duplicate_groups_are_found(db):
    """真实库样本：编号 13/22、17/23 这两组重复，扫库必须认出来"""
    ids = {}
    for title, area, ptype in REAL_LIBRARY:
        ids[title] = make_property(db, title=title, area=area, property_type=ptype)["id"]

    groups = {frozenset(g) for g in db.find_duplicate_properties()}
    assert len(groups) == 2, groups
    assert frozenset({ids[REAL_LIBRARY[0][0]], ids[REAL_LIBRARY[1][0]]}) in groups
    assert frozenset({ids[REAL_LIBRARY[2][0]], ids[REAL_LIBRARY[3][0]]}) in groups


# ---------- 合并式去重（2026-09-21：保留项优先级 + 合并后再删）----------
def test_keeper_prefers_available_over_sold(db):
    """保留项优先级：在售 > 已售（实测第 3 组踩到"留了已售那条"）"""
    sold = make_property(db, title="恒大美丽沙3号楼1单元1602", area=128.0, status="sold")
    live = make_property(db, title=NEW_TITLE, area=128.0, status="available")

    out = db.remove_duplicate_properties(dry_run=True)
    assert out["groups"][0]["keep_id"] == live["id"], out["groups"]
    assert out["groups"][0]["duplicate_ids"] == [sold["id"]]


def test_keeper_prefers_richer_when_same_status(db):
    """同级比信息完整度：带业主/图片/朝向的那条优先（哪怕 id 更大）"""
    thin = make_property(db, title="恒大美丽沙3号楼1单元1602", area=128.0)
    rich = make_property(db, title=NEW_TITLE, area=128.0, orientation="南", images="/tmp/a.jpg")
    db.link_owner_to_property(rich["id"], name="陈志强", phone="13800000000")

    out = db.remove_duplicate_properties(dry_run=True)
    assert out["groups"][0]["keep_id"] == rich["id"], out["groups"]


def test_merge_moves_unique_info_then_deletes(db, monkeypatch):
    """选①合并：保留项靠业主/图片胜出，被删那条的独有字段（朝向/卫数/租客要求）并过去再删"""
    import tools.real_estate_property as t
    monkeypatch.setattr(t, "_get_db", lambda: db)
    keep = make_property(db, title="恒大美丽沙3号楼1单元1602", area=128.0, images="/tmp/k.jpg")
    db.link_owner_to_property(keep["id"], name="陈志强", phone="13800000000")
    drop = make_property(db, title=NEW_TITLE, area=128.0, orientation="南",
                         tenant_requirements="拎包入住", bathrooms=1)

    dry = json.loads(t.deduplicate_properties(dry_run=True))
    group = dry["result"]["groups"][0]
    assert group["keep_id"] == keep["id"], group
    plan = group["merge_plan"]
    assert plan and plan[0]["id"] == drop["id"], plan
    assert {"orientation", "tenant_requirements", "bathrooms"} <= set(plan[0]["unique"]), plan

    done = json.loads(t.deduplicate_properties(dry_run=False, merge=True))
    assert done["result"]["merged_count"] == 1, done["result"]
    assert done["result"]["removable"] == [drop["id"]]

    after = db.get_property(keep["id"])
    assert after["orientation"] == "南", after          # 独有字段补过来了
    assert after["tenant_requirements"] == "拎包入住", after
    assert after["bathrooms"] == 1, after
    assert (after.get("images") or "").strip(), after    # 自己的图片还在
    assert db.get_property(drop["id"]) is None, "被删项应已删除"


def test_merge_keeps_existing_values(db):
    """合并是"只补空缺、不覆盖"：保留项已有的装修值不能被被删项的旧值冲掉"""
    keep = make_property(db, title="恒大美丽沙3号楼1单元1602", area=128.0, renovation="精装")
    drop = make_property(db, title=NEW_TITLE, area=128.0, renovation="毛坯", orientation="南")

    info = db.merge_duplicate_property(keep_id=keep["id"], drop_id=drop["id"], dry_run=False)
    assert info.get("error") is None, info
    after = db.get_property(keep["id"])
    assert after["renovation"] == "精装", after        # 没被覆盖
    assert after["orientation"] == "南", after         # 空缺的补上了


def test_update_property_fill_missing_only(db, monkeypatch):
    """录入命中 ② 只补空缺：库里已有值的字段一律不动，并在返回里说明"""
    import tools.real_estate_property as t
    monkeypatch.setattr(t, "_get_db", lambda: db)
    p = make_property(db, title="恒大美丽沙3号楼1单元1602", area=128.0, renovation="精装")

    out = json.loads(t.update_property(property_id=p["id"], renovation="豪装", orientation="南",
                                       tags="地铁房", fill_missing_only=True))
    assert out["success"] is True, out
    assert out["property"]["renovation"] == "精装"          # 库里已有 → 不动
    assert out["property"]["orientation"] == "南"           # 空缺 → 补上
    assert "renovation" in out["kept_existing"], out         # 回显没动的字段


def test_add_property_duplicate_returns_merge_preview(db, monkeypatch):
    """录入命中重复：返回 merge_preview（库里独有/这次不同）+ 三档选项"""
    import tools.real_estate_property as t
    monkeypatch.setattr(t, "_get_db", lambda: db)
    make_property(db, title="恒大美丽沙3号楼1单元1602", area=128.0, renovation="精装", orientation="南")

    out = json.loads(t.add_property(title=NEW_TITLE, price=2_150_000, area=128.0, renovation="豪装"))
    assert out["duplicate"] is True, out
    preview = out["merge_preview"]
    assert "朝向" in preview["will_keep"], preview          # 库里独有 → 会保留
    assert preview["will_update"].get("装修"), preview       # 这次与库里不同 → 建议更新
    assert len(out["options"]) == 3, out["options"]
    assert "只补空缺" in out["error"], out["error"]
    # 三档选项也是给人看的：中文说法，不带参数名
    assert len(out["options"]) == 3, out["options"]
    assert all("force" not in opt and "fill_missing_only" not in opt for opt in out["options"]), out["options"]
