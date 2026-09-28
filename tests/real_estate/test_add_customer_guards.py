"""add_customer / update_customer 入口归一与校验、判重认人、未细分客户筛得到、自动匹配措辞（2026-09-24）

覆盖 9 处修复：
① F31 微信判重误报：库里已有微信客户后，换微信的新客户不再被"密钥不一致"拦死；
   同一个微信仍判重；真密钥不一致（读出来是密文）仍拒绝并提示。
② F32 预算原话归一（"300万"/"五百万" → 3000000/5000000），不再把文本存进库；
   历史脏数据（库里已是文本预算）也不再让匹配/批量匹配崩。
③ F33 客户类型：漏传 → unspecified（不猜成买二手房），乱值给中文提示；
   unspecified 与历史 'buy' 都能按"未细分"筛到（原先在筛选口径里凭空消失）。
④ F34 等级乱值给中文提示，不再崩到数据库 CHECK 约束。
⑤ F35 空姓名拒绝。
⑥ F36 负数预算拒绝；下限大于上限给 warnings，不擅自交换。
⑦ F37 生日写法归一（1990/5/20、5月20日），乱值拒绝；MM-DD 也能被生日提醒查到。
⑧ F38 手机号写法归一（空格/横线/+86），同一人只落一条。
⑨ F39 自动匹配措辞：全够不着时说"暂无符合需求的房源…仅供参考"，不说过度承诺的话。
"""
import json

import pytest
from conftest import make_property  # noqa: F401

from tools.real_estate_customer import add_customer, list_customers, update_customer
from tools.real_estate_property import batch_match_report, match_property


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
    monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
    return db


def call_add(**kwargs):
    return json.loads(add_customer(**kwargs))


def count_customers(db):
    """库里客户条数（含已关闭，避免口径歧义）"""
    return len(db.list_customers(limit=1000))


def call_update(**kwargs):
    return json.loads(update_customer(**kwargs))


# ---------- ① 微信判重：不再被自造的"密文"启发式拦死 ----------
def test_wechat_duplicate_check_does_not_block_new_wechat_customer(tool_db):
    first = call_add(name='微信客一', wechat='wx_one', customer_type='rent')
    second = call_add(name='微信客二', wechat='wx_two', customer_type='rent')
    assert first['success'] is True, first
    assert second['success'] is True, second


def test_same_wechat_is_still_a_duplicate(tool_db):
    call_add(name='微信客一', wechat='wx_one', customer_type='rent')
    dup = call_add(name='微信客三', wechat='wx_one', customer_type='rent')
    assert dup['duplicate'] is True and dup['success'] is False, dup


def test_real_ciphertext_contact_still_refuses_to_deduplicate(tool_db):
    """密钥不一致时读出来是乱码串：不强行判重，对经纪人只说人话（2026-09-28 口径）

    原先 error 写的是「请先检查加密密钥（COCO_ENC_KEY）」—— COCO_ENC_KEY 是服务器上的
    环境变量，经纪人拿到也做不了什么，等于把系统内部的东西念给他听。
    """
    tool_db.add_customer(name='密钥坏了的客户', phone='gAAAAA' + 'x' * 40, tier='C',
                         customer_type='unspecified', status='active')
    r = call_add(name='正常新客', phone='13900008000', customer_type='buy_second_hand')
    assert r['success'] is False and '先不给你判重结果' in r['error'], r
    assert 'COCO_ENC_KEY' not in r['error'], '对外文案不提服务器上的密钥变量'
    assert 'COCO_ENC_KEY' in r['note_for_model'], '系统口径进给模型看的字段'
    assert r['key_mismatch'] is True, r
    assert "Ava" not in r['error'], r


# ---------- ② 预算原话归一 ----------
@pytest.mark.parametrize('raw_min,raw_max,expected', [
    ('300万', '五百万', (3_000_000, 5_000_000)),
    ('180万', '2,000,000元', (1_800_000, 2_000_000)),
    (2_500_000, 3_500_000, (2_500_000, 3_500_000)),
])
def test_budget_normalized_to_yuan(tool_db, raw_min, raw_max, expected):
    r = call_add(name=f'预算归一{raw_min}', phone='1380000' + str(abs(hash(raw_min)) % 10000).zfill(4),
                 budget_min=raw_min, budget_max=raw_max)
    assert r['success'] is True, r
    assert (r['customer']['budget_min'], r['customer']['budget_max']) == expected


def test_unrecognized_budget_returns_chinese_hint_and_stores_nothing(tool_db):
    r = call_add(name='预算认不出', phone='13800009999', budget_max='看情况')
    assert r['success'] is False and '预算' in r['error'], r
    assert count_customers(tool_db) == 0


def test_negative_budget_rejected(tool_db):
    r = call_add(name='负预算', phone='13800009998', budget_min=-100, budget_max=-50)
    assert r['success'] is False and '预算' in r['error'], r
    assert count_customers(tool_db) == 0


def test_reversed_budget_warns_without_swapping(tool_db):
    r = call_add(name='预算颠倒', phone='13800009997', budget_min=5_000_000, budget_max=3_000_000)
    assert r['success'] is True and r['warnings'], r
    assert r['customer']['budget_min'] == 5_000_000 and r['customer']['budget_max'] == 3_000_000


def test_legacy_text_budget_does_not_break_matching(tool_db):
    """历史脏数据（库内预算是文本）不再让匹配与批量匹配崩"""
    make_property(tool_db, title='脏预算小区 1号楼101', price=3_000_000, area=100)
    legacy = tool_db.add_customer(name='历史脏预算客', tier='C', customer_type='buy_second_hand',
                                  budget_min='300万', budget_max='五百万', status='active')
    assert json.loads(match_property(customer_id=legacy['id'])).get('success') is True
    assert json.loads(batch_match_report(top_n=1)).get('success') is True


# ---------- ③ 客户类型：未细分可筛、乱值给提示 ----------
def test_missing_customer_type_stored_as_unspecified(tool_db):
    r = call_add(name='没说类型', phone='13700001111')
    assert r['success'] is True and r['customer']['customer_type'] == 'unspecified', r


def test_unspecified_customers_are_findable_by_filter(tool_db):
    call_add(name='没说类型', phone='13700001112')
    listed = json.loads(list_customers(customer_type='unspecified'))
    assert '没说类型' in [c['name'] for c in listed['customers']], listed


def test_legacy_buy_customers_count_as_unspecified(tool_db):
    """老默认值 'buy' 的客户也要能被"未细分"筛到，否则等于凭空消失"""
    tool_db.add_customer(name='历史默认值客户', tier='C', customer_type='buy', status='active')
    listed = json.loads(list_customers(customer_type='unspecified'))
    assert '历史默认值客户' in [c['name'] for c in listed['customers']], listed


def test_unknown_customer_type_returns_chinese_hint(tool_db):
    r = call_add(name='类型乱值', phone='13700001113', customer_type='xyz')
    assert r['success'] is False and '客户类型' in r['error'], r
    assert count_customers(tool_db) == 0


def test_customer_type_synonyms_normalized(tool_db):
    r = call_add(name='类型别名', phone='13700001114', customer_type='买二手房')
    assert r['success'] is True and r['customer']['customer_type'] == 'buy_second_hand', r


# ---------- ④⑤ 等级与姓名校验 ----------
@pytest.mark.parametrize('tier', ['X', 's级', '1'])
def test_invalid_tier_returns_hint_not_integrity_error(tool_db, tier):
    r = call_add(name=f'等级{tier}', phone='1370000222' + str(abs(hash(tier)) % 10), tier=tier)
    assert r['success'] is False and '等级' in r['error'], r
    assert 'IntegrityError' not in json.dumps(r, ensure_ascii=False)


@pytest.mark.parametrize('name', ['', '   ', None])
def test_empty_name_rejected(tool_db, name):
    r = call_add(name=name, phone='13700003333')
    assert r['success'] is False and '姓名' in r['error'], r
    assert count_customers(tool_db) == 0


# ---------- ⑦ 生日 ----------
@pytest.mark.parametrize('raw,expected', [('5月20日', '05-20'), ('05-20', '05-20'),
                                          ('1990/5/20', '1990-05-20'), ('1990-05-20', '1990-05-20')])
def test_birthday_normalized(tool_db, raw, expected):
    r = call_add(name=f'生日{raw}', phone='1370000444' + str(abs(hash(raw)) % 10), birthday=raw)
    assert r['success'] is True and r['customer']['birthday'] == expected, r


def test_unrecognized_birthday_rejected(tool_db):
    r = call_add(name='生日乱填', phone='13700004445', birthday='明天')
    assert r['success'] is False and '生日' in r['error'], r
    assert count_customers(tool_db) == 0


def test_mm_dd_birthday_is_found_by_reminder_query(tool_db):
    """只写月日的生日也要能被生日提醒查到（原先静默漏人）"""
    call_add(name='生日短写', phone='13700004446', birthday='5月20日')
    call_add(name='生日全写', phone='13700004447', birthday='1990-05-20')
    names = [c['name'] for c in tool_db.get_birthday_customers(month=5, day=20)]
    assert '生日短写' in names and '生日全写' in names, names


# ---------- ⑧ 手机号写法归一 ----------
def test_phone_writing_variants_are_same_person(tool_db):
    first = call_add(name='写法归一客', phone='13999990000', customer_type='rent')
    second = call_add(name='写法归一客', phone='139-9999-0000', customer_type='rent')
    third = call_add(name='写法归一客', phone='+8613999990000', customer_type='rent')
    assert first['success'] is True, first
    assert second['duplicate'] is True and third['duplicate'] is True, (second, third)
    assert count_customers(tool_db) == 1


def test_phone_stored_normalized(tool_db):
    r = call_add(name='写法归一客2', phone='139 8888 7777', customer_type='rent')
    assert r['customer']['phone'] == '13988887777', r


# ---------- ⑨ 自动匹配措辞 ----------
def test_match_message_says_qualified_when_affordable(tool_db):
    make_property(tool_db, title='够得着小区 1号楼101', price=3_000_000, area=100)
    r = call_add(name='预算够客', phone='13600001111', budget_min=2_500_000,
                 budget_max=3_500_000, customer_type='buy_second_hand')
    assert '符合需求' in r['message'] and '暂无' not in r['message'], r['message']


def test_match_message_does_not_overpromise_when_out_of_budget(tool_db):
    make_property(tool_db, title='够不着小区 1号楼101', price=3_000_000, area=100)
    r = call_add(name='预算不够客', phone='13600002222', budget_max=100_000,
                 customer_type='buy_second_hand')
    assert '暂无符合需求的房源' in r['message'], r['message']
    assert '超出预算' in r['message'], r['message']


# ---------- update_customer 同一套口径 ----------
def test_update_customer_normalizes_budget_and_birthday(tool_db):
    cid = call_add(name='更新归一客', phone='13600003333', customer_type='rent')['customer']['id']
    r = call_update(customer_id=cid, budget_max='200万', birthday='6月1日')
    assert r['success'] is True, r
    assert r['customer']['budget_max'] == 2_000_000
    assert r['customer']['birthday'] == '06-01'


def test_update_customer_rejects_bad_tier_and_budget(tool_db):
    cid = call_add(name='更新校验客', phone='13600004444', customer_type='rent')['customer']['id']
    assert json.loads(update_customer(customer_id=cid, tier='Z'))['success'] is False
    assert json.loads(update_customer(customer_id=cid, budget_max='看情况'))['success'] is False
    assert tool_db.get_customer(cid)['tier'] == 'C'


def test_update_customer_budget_drift_still_works(tool_db):
    """漂移预警在归一后仍然生效（原先 float() 遇到历史文本预算会崩）"""
    cid = call_add(name='漂移客', phone='13600005555', budget_max=5_000_000,
                   customer_type='buy_second_hand')['customer']['id']
    r = call_update(customer_id=cid, budget_max=2_000_000)
    assert r['success'] is True and r.get('alerts'), r
    assert r['alerts'][0]['type'] == 'budget_drift'
