"""跟进/逾期回归测试（2026-08-29 修 midday_check 对 dict 属性访问的 bug）"""
from datetime import datetime, timedelta

from conftest import make_customer


class TestOverdue:
    def test_get_overdue_returns_dicts(self, db):
        """get_overdue 返回字典列表（每项含 customer_id/content/next_date）"""
        c = make_customer(db, name="张三")
        db.add_followup(customer_id=c["id"], type="call", content="回访",
                        next_date=datetime.now() - timedelta(days=1))
        overdue = db.get_overdue()
        assert len(overdue) == 1
        assert isinstance(overdue[0], dict)
        assert overdue[0]["customer_id"] == c["id"]
        assert overdue[0]["content"] == "回访"

    def test_midday_check_with_overdue_no_crash(self, db):
        """回归：get_overdue 返回 dict，midday_check 必须用字典访问，不再崩溃"""
        c = make_customer(db, name="李四")
        db.add_followup(customer_id=c["id"], type="call", content="回访",
                        next_date=datetime.now() - timedelta(days=2))
        check = db.midday_check()
        assert check["overdue_count"] == 1
        assert check["overdue_customers"][0]["customer_id"] == c["id"]
        assert check["overdue_customers"][0]["content"] == "回访"

    def test_midday_check_no_overdue(self, db):
        """无逾期跟进时 midday_check 正常返回 overdue_count=0"""
        c = make_customer(db, name="王五")
        db.add_followup(customer_id=c["id"], type="call", content="回访",
                        next_date=datetime.now() + timedelta(days=3))
        check = db.midday_check()
        assert check["overdue_count"] == 0
        assert check["overdue_customers"] == []
