"""add_property 入口归一/校验、判重的类型隔离、业主认人（2026-09-24）

覆盖三处修复：
① 入口兜底：经纪人原话（"185万"/"一百二十平"）换算成元/㎡；认不出、空标题、非正面积 → 中文提示且不落库；非法类型按二手房记并提示。
② 判重加类型维度：出租与出售、一手与二手互不判重（同一套房可既卖又租），同类型仍严格判重；
   批量去重（find_duplicate_properties）同一口径，不把跨类型的同房号当重复。
③ 业主认人只认手机号：同名不同号不再误合并，新号另建并给提示；同号复用且不误报。
"""
import json

import pytest
from conftest import make_property  # noqa: F401

from tools.real_estate_property import add_property


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
    return db


def call_add(**kwargs):
    return json.loads(add_property(**kwargs))


# ---------- 入口归一 ----------
@pytest.mark.parametrize('raw,expected', [
    ('185万', 1850000),
    ('1,850,000元', 1850000),
    ('一百五十万', 1500000),
    ('2200', 2200),
    ('2200元/月', 2200),
    (4000000, 4000000),
])
def test_price_normalized_to_yuan(tool_db, raw, expected):
    """经纪人怎么说价都要换算成元"""
    r = call_add(title=f'归一小区 {abs(hash(raw)) % 97}号楼101', price=raw, area=100)
    assert r['success'] is True, r
    assert r['property']['price'] == expected


@pytest.mark.parametrize('raw,expected', [('128.5㎡', 128.5), ('一百二十平', 120.0), ('约95平', 95.0), (88, 88.0)])
def test_area_normalized_to_sqm(tool_db, raw, expected):
    r = call_add(title=f'面积归一 {abs(hash(raw)) % 97}号楼201', price=1000000, area=raw)
    assert r['success'] is True, r
    assert r['property']['area'] == expected


def test_unrecognized_price_returns_chinese_hint_not_crash(tool_db):
    r = call_add(title='认不出 1号楼101', price='很多钱', area=100)
    assert r['success'] is False and '价格' in r['error']
    assert tool_db.count_available_properties() == 0


@pytest.mark.parametrize('area', ['很大', 0, -10])
def test_invalid_area_rejected(tool_db, area):
    r = call_add(title=f'脏面积{area} 3号楼301', price=1000000, area=area)
    assert r['success'] is False and '面积' in r['error']
    assert tool_db.count_available_properties() == 0


def test_empty_title_rejected(tool_db):
    r = call_add(title='   ', price=1000000, area=100)
    assert r['success'] is False and '标题' in r['error']
    assert tool_db.count_available_properties() == 0


def test_unknown_property_type_falls_back_to_second_hand_with_note(tool_db):
    r = call_add(title='类型小区 5号楼501', price=1000000, area=100, property_type='house')
    assert r['property']['property_type'] == 'second_hand'
    assert 'property_type' in r['normalized']


# ---------- 判重：类型隔离（正向 + 反向）----------
def test_same_type_same_room_still_deduplicated(tool_db):
    call_add(title='同类型 6号楼601', price=1000000, area=100)
    r = call_add(title='同类型 6号楼601', price=1050000, area=100)
    assert r['duplicate'] is True and r['success'] is False
    assert tool_db.count_available_properties() == 1


def test_sell_and_rent_same_room_allowed(tool_db):
    """同一套房可以既卖又租 → 出租照常录入，并带"同房号另一用途"提示"""
    call_add(title='跨用途 7号楼701', price=1500000, area=100, property_type='second_hand')
    r = call_add(title='跨用途 7号楼701', price=3000, area=100, property_type='rental')
    assert r['success'] is True, r
    assert r.get('suspected_duplicate', {}).get('reason')
    assert tool_db.count_available_properties() == 2


def test_new_and_second_hand_same_room_still_deduplicated(tool_db):
    """同一套房不可能既是新房又是二手房 → 一手房撞上同房号二手房仍算重复，不落库"""
    call_add(title='新旧 7号楼702', price=1500000, area=100, property_type='second_hand')
    r = call_add(title='新旧 7号楼702', price=1600000, area=100, property_type='new')
    assert r['duplicate'] is True and r['success'] is False
    assert tool_db.count_available_properties() == 1


def test_batch_dedup_keeps_cross_use_records(tool_db):
    """批量去重不能把「同房号的出售 + 出租」当重复建议删掉"""
    call_add(title='批量 8号楼801', price=1500000, area=100, property_type='second_hand')
    call_add(title='批量 8号楼801', price=3000, area=100, property_type='rental')
    assert tool_db.find_duplicate_properties() == []


def test_batch_dedup_groups_new_and_second_hand(tool_db):
    """同房号的一手房 + 二手房是同一套 → 批量去重必须认出来（可合并）"""
    a = call_add(title='批量 8号楼802', price=1500000, area=100, property_type='second_hand')
    b = call_add(title='批量 8号楼802', price=1500000, area=100, property_type='new', force=True)
    groups = tool_db.find_duplicate_properties()
    assert len(groups) == 1 and set(groups[0]) == {a['property']['id'], b['property']['id']}, groups


# ---------- 业主认人：只认手机号 ----------
def test_same_name_different_phone_creates_new_owner(tool_db):
    r1 = call_add(title='业主 9号楼901', price=1000000, area=90, owner_name='张伟', owner_phone='13900000001')
    r2 = call_add(title='业主 9号楼902', price=1000000, area=90, owner_name='张伟', owner_phone='13900000002')
    assert r1['owner']['id'] != r2['owner']['id']
    assert r2['owner']['phone'] == '13900000002'
    assert r2.get('owner_note'), '同名不同号必须给提示，不能静默另建'


def test_same_phone_reuses_owner(tool_db):
    r1 = call_add(title='业主 9号楼903', price=1000000, area=90, owner_name='张伟', owner_phone='13900000001')
    r2 = call_add(title='业主 9号楼904', price=1000000, area=90, owner_name='张伟他老婆', owner_phone='13900000001')
    assert r1['owner']['id'] == r2['owner']['id']
    assert not r2.get('owner_note')
