"""数据清理测试：彻底删除 / 归档 / 恢复（2026-09-23 新增）

口径：有关联带看/成交/跟进/需求变更/转介绍引用的记录默认不删（宁可留着不误删）；
调价记录属于房源自身明细，随房源一起删，不算"牵挂"。
"""
from datetime import datetime, timedelta

from conftest import make_customer, make_property

from agent import real_estate_db as m


def _row(db, model_name, oid):
    """直接从库里读一行（用于判断是否真的没了）"""
    model = getattr(m, model_name)
    with db.get_session() as s:
        return s.query(model).get(oid)


def _count(db, model_name, **filters):
    model = getattr(m, model_name)
    with db.get_session() as s:
        q = s.query(model)
        for key, value in filters.items():
            q = q.filter(getattr(model, key) == value)
        return q.count()


def _add_price_history(db, property_id, old_price=4_000_000, new_price=3_900_000):
    with db.get_session() as s:
        s.add(m.PriceHistory(property_id=property_id, old_price=old_price, new_price=new_price))
        s.commit()


def _add_customer_change(db, customer_id):
    with db.get_session() as s:
        s.add(m.CustomerChange(customer_id=customer_id, field='budget_max',
                               old_value='4000000', new_value='5000000'))
        s.commit()


def _backdate(db, model_name, oid, days):
    """把创建时间改到 N 天前（用于验证 before 时间过滤）"""
    model = getattr(m, model_name)
    with db.get_session() as s:
        row = s.query(model).get(oid)
        row.created_at = datetime.now() - timedelta(days=days)
        s.commit()


# ==================== 预演 ====================

class TestPurgePreview:
    def test_preview_never_touches_data(self, db):
        p = make_property(db, title="已售房", status="sold")
        result = db.purge_preview(kind="property")
        assert result['matched'] == 1 and result['deletable'] == 1
        assert _row(db, 'Property', p['id']) is not None

    def test_preview_only_picks_marked_properties(self, db):
        make_property(db, title="在售房", status="available")
        make_property(db, title="已租房", status="rented")
        result = db.purge_preview(kind="property")
        assert [e['title'] for e in result['entries']] == ["已租房"]

    def test_preview_only_picks_closed_customers(self, db):
        make_customer(db, name="活跃客户", status="active")
        make_customer(db, name="关闭客户", status="closed")
        result = db.purge_preview(kind="customer")
        assert [e['name'] for e in result['entries']] == ["关闭客户"]

    def test_preview_marks_protected_records_with_reason(self, db):
        c = make_customer(db, name="有跟进的关闭客户", status="closed")
        db.add_followup(customer_id=c['id'], type='phone', content='回访')
        result = db.purge_preview(kind="customer")
        assert result['deletable'] == 0 and result['skipped'] == 1
        assert '跟进1条' in result['skipped_entries'][0]['skip_reason']

    def test_preview_respects_before_date(self, db):
        old = make_customer(db, name="很久以前的关闭客户", status="closed")
        new = make_customer(db, name="昨天的关闭客户", status="closed")
        _backdate(db, 'Customer', old['id'], days=30)
        cutoff = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        result = db.purge_preview(kind="customer", before=cutoff)
        assert [e['name'] for e in result['entries']] == ["很久以前的关闭客户"]

    def test_preview_counts_price_history_as_own_detail(self, db):
        """调价记录不算牵挂：房源仍算可删，条数一并回显"""
        p = make_property(db, title="调过价的已售房", status="sold")
        _add_price_history(db, p['id'])
        entry = db.purge_preview(kind="property")['entries'][0]
        assert entry['skip_reason'] is None
        assert entry['related']['price_history'] == 1 and entry['related_total'] == 1

    def test_bad_before_format_raises(self, db):
        try:
            db.purge_preview(kind="customer", before="9月23日")
        except ValueError as exc:
            assert 'YYYY-MM-DD' in str(exc)
        else:
            raise AssertionError('时间格式错误应当直接报错')


# ==================== 单条彻底删除 ====================

class TestDeleteProperty:
    def test_delete_clean_property_removes_row_and_price_history(self, db):
        p = make_property(db, title="无牵挂的已售房", status="sold")
        _add_price_history(db, p['id'])
        result = db.delete_property(property_id=p['id'])
        assert result['success'] is True
        assert _row(db, 'Property', p['id']) is None
        assert _count(db, 'PriceHistory', property_id=p['id']) == 0

    def test_delete_property_with_viewing_refused_by_default(self, db):
        p = make_property(db, title="带看过的已售房", status="sold")
        c = make_customer(db, name="看过房的客户")
        db.add_viewing(customer_id=c['id'], property_id=p['id'],
                       viewing_time=datetime.now() - timedelta(days=1))
        result = db.delete_property(property_id=p['id'])
        assert result['success'] is False and result['error'] == 'has_history'
        assert _row(db, 'Property', p['id']) is not None

    def test_delete_property_force_removes_related_history(self, db):
        p = make_property(db, title="要连历史删的房", status="sold")
        c = make_customer(db, name="看过房的客户")
        db.add_viewing(customer_id=c['id'], property_id=p['id'], viewing_time=datetime.now())
        db.add_followup(customer_id=c['id'], property_id=p['id'], type='phone', content='约看')
        _add_price_history(db, p['id'])
        result = db.delete_property(property_id=p['id'], force=True)
        assert result['success'] is True and result['deleted_related'] == 3
        assert _row(db, 'Property', p['id']) is None
        assert _count(db, 'Viewing', property_id=p['id']) == 0
        assert _count(db, 'Followup', property_id=p['id']) == 0

    def test_delete_property_dry_run_keeps_row(self, db):
        p = make_property(db, title="预演用的房", status="sold")
        result = db.delete_property(property_id=p['id'], dry_run=True)
        assert result['success'] is True and result['dry_run'] is True
        assert _row(db, 'Property', p['id']) is not None

    def test_delete_property_missing_id_reports_not_found(self, db):
        result = db.delete_property(property_id=99999)
        assert result['success'] is False and result['error'] == 'not_found'

    def test_delete_property_ambiguous_title_asks_for_id(self, db):
        make_property(db, title="同名小区房", status="sold")
        make_property(db, title="同名小区房", status="sold")
        result = db.delete_property(title="同名小区房")
        assert result['success'] is False and result['error'] == 'ambiguous'
        assert len(result['candidates']) == 2


class TestDeleteCustomer:
    def test_delete_clean_customer_removes_row(self, db):
        c = make_customer(db, name="无跟进的关闭客户", status="closed")
        result = db.delete_customer(customer_id=c['id'])
        assert result['success'] is True
        assert _row(db, 'Customer', c['id']) is None

    def test_delete_customer_with_followup_refused_by_default(self, db):
        c = make_customer(db, name="有跟进的关闭客户", status="closed")
        db.add_followup(customer_id=c['id'], type='phone', content='回访')
        result = db.delete_customer(customer_id=c['id'])
        assert result['success'] is False and result['error'] == 'has_history'
        assert _row(db, 'Customer', c['id']) is not None

    def test_delete_customer_force_removes_all_related(self, db):
        c = make_customer(db, name="要连历史删的客户", status="closed")
        p = make_property(db, title="他看过的房")
        db.add_followup(customer_id=c['id'], type='phone', content='回访')
        db.add_viewing(customer_id=c['id'], property_id=p['id'], viewing_time=datetime.now())
        db.add_deal(customer_id=c['id'], property_id=p['id'], stage='deposit')
        _add_customer_change(db, c['id'])
        result = db.delete_customer(customer_id=c['id'], force=True)
        assert result['success'] is True
        assert _row(db, 'Customer', c['id']) is None
        assert _count(db, 'Followup', customer_id=c['id']) == 0
        assert _count(db, 'Viewing', customer_id=c['id']) == 0
        assert _count(db, 'Deal', customer_id=c['id']) == 0
        assert _count(db, 'CustomerChange', customer_id=c['id']) == 0

    def test_referrer_customer_protected_by_default(self, db):
        referrer = make_customer(db, name="转介绍人", status="closed")
        db.add_referral(referrer_customer_id=referrer['id'], referred_name="被介绍的朋友")
        result = db.delete_customer(customer_id=referrer['id'])
        assert result['success'] is False
        assert '转介绍1条' in result['skip_reason']

    def test_force_delete_referrer_keeps_other_deal_intact(self, db):
        """强制删除转介绍人：别人的成交单只摘掉转介绍链接，成交记录本身保留"""
        referrer = make_customer(db, name="转介绍人", status="closed")
        ref = db.add_referral(referrer_customer_id=referrer['id'], referred_name="被介绍的朋友")
        buyer = make_customer(db, name="成交客户")
        p = make_property(db, title="成交房源")
        db.add_deal(customer_id=buyer['id'], property_id=p['id'], stage='deposit',
                    referral_id=ref['id'])
        result = db.delete_customer(customer_id=referrer['id'], force=True)
        assert result['success'] is True
        with db.get_session() as s:
            deals = s.query(m.Deal).filter(m.Deal.customer_id == buyer['id']).all()
        assert len(deals) == 1 and deals[0].referral_id is None
        assert _count(db, 'Referral') == 0

    def test_name_and_phone_together_disambiguate(self, db):
        make_customer(db, name="同名客户", phone="13800000001", status="closed")
        target = make_customer(db, name="同名客户", phone="13800000002", status="closed")
        result = db.delete_customer(name="同名客户", phone="13800000002")
        assert result['success'] is True
        assert _row(db, 'Customer', target['id']) is None
        assert _count(db, 'Customer', name="同名客户") == 1


# ==================== 批量清理 / 归档 / 恢复 ====================

class TestPurgeData:
    def test_purge_dry_run_is_readonly(self, db):
        make_customer(db, name="关闭客户", status="closed")
        result = db.purge_data(kind="customer", dry_run=True)
        assert result['deleted'] == 1 and result['dry_run'] is True
        assert _count(db, 'Customer', status="closed") == 1

    def test_purge_deletes_clean_and_skips_protected(self, db):
        clean = make_customer(db, name="干净的关闭客户", status="closed")
        protected = make_customer(db, name="有跟进的关闭客户", status="closed")
        db.add_followup(customer_id=protected['id'], type='phone', content='回访')
        result = db.purge_data(kind="customer", dry_run=False)
        assert result['deleted'] == 1 and result['skipped_count'] == 1
        assert _row(db, 'Customer', clean['id']) is None
        assert _row(db, 'Customer', protected['id']) is not None

    def test_purge_force_deletes_protected_too(self, db):
        protected = make_customer(db, name="有跟进的关闭客户", status="closed")
        db.add_followup(customer_id=protected['id'], type='phone', content='回访')
        result = db.purge_data(kind="customer", dry_run=False, force=True)
        assert result['deleted'] == 1 and result['skipped_count'] == 0
        assert _row(db, 'Customer', protected['id']) is None

    def test_purge_all_covers_both_kinds(self, db):
        make_customer(db, name="关闭客户", status="closed")
        make_property(db, title="已租房", status="rented")
        result = db.purge_data(kind="all", dry_run=False)
        assert result['deleted'] == 2 and result['matched'] == 2

    def test_archive_marks_instead_of_deleting(self, db):
        c = make_customer(db, name="活跃客户", status="active")
        result = db.purge_data(kind="customer", statuses=['active'],
                               mode='archive', dry_run=False)
        assert result['archived'] == 1
        assert _row(db, 'Customer', c['id']).status == 'closed'

    def test_archive_rental_property_marked_rented(self, db):
        rental = make_property(db, title="出租房", status="available", property_type="rental")
        db.purge_data(kind="property", statuses=['available'], mode='archive', dry_run=False)
        assert _row(db, 'Property', rental['id']).status == 'rented'

    def test_bad_mode_refused(self, db):
        result = db.purge_data(kind="customer", mode='delete-everything')
        assert result['success'] is False and result['error'] == 'bad_mode'

    def test_restore_status_brings_records_back(self, db):
        c = make_customer(db, name="误标的客户", status="closed")
        p = make_property(db, title="误标的房", status="sold")
        result = db.restore_status(kind="all", dry_run=False)
        assert result['restored'] == 2
        assert _row(db, 'Customer', c['id']).status == 'active'
        assert _row(db, 'Property', p['id']).status == 'available'

    def test_restore_dry_run_is_readonly(self, db):
        c = make_customer(db, name="误标的客户", status="closed")
        result = db.restore_status(kind="customer", dry_run=True)
        assert result['restored'] == 1
        assert _row(db, 'Customer', c['id']).status == 'closed'
