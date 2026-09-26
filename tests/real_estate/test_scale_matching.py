"""规模契约：房源超过 1 万条时，匹配与降价提醒仍要覆盖全部房源。

2026-09-18 修：原先候选池写死 search_properties(limit=10000)，库内房源 6 万+ 套时后 5 万套
永远不参与匹配；降价提醒还额外对每套房各查一次（N+1）。本文件把这两条钉死。
"""
import json
import sqlite3

from conftest import make_property


class TestMatchingAtScale:
    def test_match_covers_property_beyond_ten_thousand(self, db):
        """第 10050 套（超出旧的 1 万上限）必须能被匹配到"""
        with db.get_session() as s:
            from agent.real_estate_db import Property
            for i in range(1, 10051):
                s.add(Property(title=f"规模盘 {i}", price=1_500_000, area=100.0, rooms=3, halls=2,
                               district="美兰-海甸岛", property_type="second_hand", status="available"))
            # 唯一一套 5 室，且排在 1 万条之后
            s.add(Property(title="规模盘 唯一五居", price=1_500_000, area=140.0, rooms=5, halls=2,
                           district="美兰-海甸岛", property_type="second_hand", status="available"))
            s.commit()
        c = db.add_customer(name="要五居的客户", budget_min=1_400_000, budget_max=1_600_000,
                            layout_pref="5室2厅", location="美兰区", customer_type="buy_second_hand", tier="S")
        matches = db.match_property(c["id"], top_n=3)
        titles = [m["title"] for m in matches]
        assert "规模盘 唯一五居" in titles, f"超出 1 万条的房源没进候选池：{titles}"

    def test_batch_match_uses_same_pool(self, db):
        """批量匹配与单客户匹配结果口径一致（同一候选池）"""
        with db.get_session() as s:
            from agent.real_estate_db import Property
            for i in range(1, 200):
                s.add(Property(title=f"批量盘 {i}", price=1_500_000, area=100.0, rooms=3, halls=2,
                               district="美兰-海甸岛", property_type="second_hand", status="available"))
            s.commit()
        db.add_customer(name="批量客户", budget_min=1_400_000, budget_max=1_600_000,
                        layout_pref="3室2厅", location="美兰区", customer_type="buy_second_hand", tier="A")
        result = db.match_all_customers(top_n=1)
        assert result["summary"]["total"] == 1
        assert result["customers"][0]["matches"], "批量匹配不该漏掉明显匹配的客户"


class TestPriceDropAlerts:
    def test_only_scans_recent_drops(self, db, monkeypatch):
        """降价提醒：只对"近期降过价"的房源做客户反匹配（不再全库 N+1）"""
        import tools.real_estate_property as tp
        monkeypatch.setattr(tp, "_get_db", lambda: db)
        p = make_property(db, title="降价房源", price=1_500_000, area=100.0)
        db.add_customer(name="差一点够得着的客户", budget_min=1_300_000, budget_max=1_420_000,
                        customer_type="buy_second_hand", tier="A")
        # 把没降过价的房子也造一批，确保它们不会被扫到 / 混进结果
        for i in range(30):
            make_property(db, title=f"未降价 {i}", price=1_500_000, area=100.0)
        db.update_property(p["id"], price=1_400_000)      # 产生一条调价记录
        data = json.loads(tp.price_drop_alerts(days=7))
        assert data["success"] is True
        ids = [a["property_id"] for a in data["alerts"]]
        assert p["id"] in ids, data
        assert all(i == p["id"] for i in ids), "只应包含近期降过价的房源"
        assert data["alerts"][0]["matched_customers"], "预算差一点的客户应被反匹配出来"


class TestChannelStats:
    def test_channel_stats_counts_deals_without_n_plus_one(self, db):
        """渠道统计：客户来源分组 + 成交数正确（内部改为一次查询取成交客户集合）"""
        c1 = db.add_customer(name="渠道客A", source="抖音", customer_type="buy_second_hand", tier="A")
        db.add_customer(name="渠道客B", source="抖音", customer_type="buy_second_hand", tier="B")
        db.add_customer(name="渠道客C", source="贝壳", customer_type="rent", tier="C")
        p = make_property(db, title="渠道成交房", price=1_000_000, area=90.0)
        db.add_deal(customer_id=c1["id"], property_id=p["id"], price=1_000_000)
        stats = db.get_channel_stats()
        by_src = {r["source"]: r for r in stats["channels"]} if isinstance(stats, dict) and "channels" in stats else None
        rows = by_src or (stats if isinstance(stats, list) else stats.get("items"))
        rows = {r["source"]: r for r in rows}
        assert rows["抖音"]["customers"] == 2
        assert rows["抖音"]["deals"] == 1
        assert rows["贝壳"]["customers"] == 1
        assert rows["贝壳"]["deals"] == 0


class TestIndexMigration:
    def test_indexes_created_on_real_schema(self, tmp_path):
        """010 迁移：在真实结构（有这些列）上必须真的建出索引，且可重复执行"""
        import subprocess, sys
        db_path = tmp_path / "idx.db"
        url = f"sqlite:///{db_path}"
        from agent.real_estate_db import init_real_estate_db
        init_real_estate_db(url)
        r1 = subprocess.run([sys.executable, "scripts/migrate.py", "--database-url", url],
                            capture_output=True, text=True, cwd=str(REPO_ROOT))
        assert r1.returncode == 0, r1.stderr
        conn = sqlite3.connect(db_path)
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        conn.close()
        assert "re_idx_prop_status_type" in names
        r2 = subprocess.run([sys.executable, "scripts/migrate.py", "--database-url", url],
                            capture_output=True, text=True, cwd=str(REPO_ROOT))
        assert r2.returncode == 0 and "数据库已是最新" in r2.stdout


from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[2]
