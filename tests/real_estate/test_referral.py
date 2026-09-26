"""转介绍经营测试（功能6，批次八）"""
import json

from conftest import make_customer, make_property

from tools.real_estate_customer import add_referral, referral_stats


class TestReferralDB:
    def test_add_referral_auto_creates_customer(self, db):
        """登记转介绍自动建新客户档案，来源=转介绍"""
        referrer = make_customer(db, name="老客户")
        r = db.add_referral(referrer_customer_id=referrer["id"],
                            referred_name="新客小王", referred_phone="13811112222")
        assert r["referrer_name"] == "老客户"
        assert r["referred_customer_id"] is not None
        new_c = db.get_customer(r["referred_customer_id"])
        assert new_c["name"] == "新客小王"
        assert new_c["source"] == "转介绍"
        assert new_c["phone"] == "13811112222"  # 加密往返

    def test_referral_phone_encrypted(self, db, tmp_path, monkeypatch):
        """转介绍人手机号在加密模式下密文存储"""
        import base64
        key = base64.urlsafe_b64encode(b"coco-test-key-32-bytes-exactly!!").decode()
        monkeypatch.setenv("COCO_ENC_KEY", key)
        referrer = make_customer(db)
        r = db.add_referral(referrer_customer_id=referrer["id"],
                            referred_name="加密客", referred_phone="13700003333")
        got = db.get_customer(r["referred_customer_id"])
        assert got["phone"] == "13700003333"

    def test_referral_stats_ranking(self, db):
        """贡献榜按介绍人数排序"""
        referrer_top = make_customer(db, name="介绍3人的")
        referrer_1 = make_customer(db, name="介绍1人的")
        for name in ("被介A", "被介B", "被介C"):
            db.add_referral(referrer_customer_id=referrer_top["id"], referred_name=name)
        db.add_referral(referrer_customer_id=referrer_1["id"], referred_name="被介D")
        board = db.referral_stats()
        assert board[0]["referrer_name"] == "介绍3人的"
        assert board[0]["referrals"] == 3


class TestReferralTool:
    def test_add_referral_tool(self, db, monkeypatch):
        monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
        referrer = make_customer(db, name="热心老客")
        r = json.loads(add_referral(referrer_customer_id=referrer["id"],
                                    referred_name="小李", referred_phone="13600004444"))
        assert r["success"] is True
        assert "小李" in r["message"]
        assert "热心老客" in r["message"]

    def test_add_referral_nonexistent_referrer(self, db, monkeypatch):
        monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
        r = json.loads(add_referral(referrer_customer_id=99999, referred_name="某"))
        assert r["success"] is False

    def test_referral_stats_tool(self, db, monkeypatch):
        monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
        referrer = make_customer(db, name="金主")
        db.add_referral(referrer_customer_id=referrer["id"], referred_name="客A")
        db.add_referral(referrer_customer_id=referrer["id"], referred_name="客B")
        r = json.loads(referral_stats())
        assert r["success"] is True
        assert r["leaderboard"][0]["referrals"] == 2
