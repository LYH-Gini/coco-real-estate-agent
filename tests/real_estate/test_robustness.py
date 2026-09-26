"""健壮性契约：模型漏传参数要得到可读提示；工具内部失败不能静默吞掉。

2026-09-18 加：①网关层必填参数校验（原先 handler 直接抛 TypeError，模型看到
"Tool execution failed" 转而自己编答案）；②add_property/update_property 里
"业主登记失败""自动匹配失败"原先静默吞掉，经纪人会以为登记好了。
"""
import json

from conftest import make_property


class TestRequiredParamValidation:
    def test_missing_required_returns_readable_error(self, db, monkeypatch):
        """漏传必填参数 → 返回"缺少必填参数：X"，而不是 TypeError 崩到模型面前"""
        import tools.real_estate_customer  # noqa: F401 —— 触发注册
        from tools.registry import registry
        out = json.loads(registry.dispatch("add_customer", {}))
        assert "error" in out
        assert "缺少必填参数" in out["error"]
        assert "name" in out["error"]
        assert "TypeError" not in out["error"]

    def test_no_validation_when_params_present(self, db, monkeypatch):
        """参数给全时正常执行（校验不误伤）"""
        import tools.real_estate_customer  # noqa: F401
        from tools.registry import registry
        monkeypatch.setattr("tools.real_estate_customer._get_db", lambda: db)
        out = json.loads(registry.dispatch("add_customer", {"name": "校验用客户"}))
        assert out.get("success") is True, out


class TestFailuresAreNotSwallowed:
    def test_owner_link_failure_is_surfaced(self, db, monkeypatch):
        """业主登记失败必须在返回里带 warning_owner（原先是静默吞掉）"""
        import tools.real_estate_property as tp
        monkeypatch.setattr(tp, "_get_db", lambda: db)
        def boom(*a, **kw):
            raise RuntimeError("模拟业主库写入失败")
        monkeypatch.setattr(db, "link_owner_to_property", boom)
        out = json.loads(tp.add_property(title="健壮性房源", price=1_000_000, area=90.0,
                                         owner_name="某业主", owner_phone="13800138000"))
        assert out["success"] is True
        assert "warning_owner" in out, out
        assert "业主信息没登记上" in out["warning_owner"]

    def test_match_failure_is_surfaced(self, db, monkeypatch):
        """自动匹配失败也要带 warning_match，而不是悄悄返回"无客户" """
        import tools.real_estate_property as tp
        monkeypatch.setattr(tp, "_get_db", lambda: db)
        def boom(*a, **kw):
            raise RuntimeError("模拟匹配失败")
        monkeypatch.setattr(db, "match_customers_for_property", boom)
        out = json.loads(tp.add_property(title="健壮性房源2", price=1_000_000, area=90.0))
        assert out["success"] is True
        assert "warning_match" in out, out
        assert "自动匹配客户失败" in out["warning_match"]

    def test_update_property_owner_failure_surfaced(self, db, monkeypatch):
        """更新房源时业主登记失败同样要带 warning_owner"""
        import tools.real_estate_property as tp
        monkeypatch.setattr(tp, "_get_db", lambda: db)
        p = make_property(db, title="待更新房源", price=1_000_000, area=90.0)
        def boom(*a, **kw):
            raise RuntimeError("模拟业主库写入失败")
        monkeypatch.setattr(db, "link_owner_to_property", boom)
        out = json.loads(tp.update_property(property_id=p["id"], owner_name="某业主"))
        assert out["success"] is True
        assert "warning_owner" in out, out


class TestWarningsOnPartialFailure:
    """统计/匹配类失败必须出现在返回里（不能静默当成 0 或无声跳过）"""

    def test_add_customer_match_failure_warns(self, db, monkeypatch):
        import tools.real_estate_customer as tc
        monkeypatch.setattr(tc, "_get_db", lambda: db)
        def boom(*a, **kw):
            raise RuntimeError("模拟匹配失败")
        monkeypatch.setattr(db, "match_property", boom)
        out = json.loads(tc.add_customer(name="警告客户", customer_type="buy_second_hand"))
        assert out["success"] is True
        assert "warning_match" in out and "自动匹配房源失败" in out["warning_match"], out

    def test_report_warns_when_stats_fail(self, db, monkeypatch):
        import tools.real_estate_report as trp
        monkeypatch.setattr(trp, "_get_db", lambda: db)
        def boom(*a, **kw):
            raise RuntimeError("模拟统计失败")
        monkeypatch.setattr(db, "viewing_stats", boom)
        out = json.loads(trp.generate_report(period="week"))
        assert out["success"] is True
        assert "warning_stats" in out, out
        assert "带看统计获取失败" in out["warning_stats"]

    def test_market_brief_warns_when_stats_fail(self, db, monkeypatch):
        """自家盘况统计失败时：标注"统计失败"、不基于假 0 给建议、返回带 warning_stats"""
        import tools.real_estate_analytics as ta
        import agent.real_estate_db as redb
        monkeypatch.setattr(ta, "_get_db", lambda: db)

        class _Boom:
            def __ge__(self, other):
                raise RuntimeError("模拟统计失败")

        class _FakeViewing:
            viewing_time = _Boom()

        monkeypatch.setattr(redb, "Viewing", _FakeViewing)
        out = json.loads(ta.market_brief())
        assert out["success"] is True, out
        assert "warning_stats" in out, out
        assert "统计失败" in out["message"]

    def test_intent_ranking_warns_on_partial_failure(self, db, monkeypatch):
        """单个客户算分失败不能悄悄跳过（否则排名少人，经纪人以为这些客户不在库里）

        2026-09-26（第八组）：计分从 `db.customer_intent_score`（逐客户查库）改成
        `tools.real_estate_intent._score_intent`（一处定义、排名与详情同源）→ 打点位置跟着挪到
        这个纯函数上；契约本身不变（失败要点名、不静默）。
        """
        import tools.real_estate_intent as tin
        monkeypatch.setattr(tin, "_get_db", lambda: db)
        c1 = db.add_customer(name="能算分的", customer_type="buy_second_hand", tier="A")
        c2 = db.add_customer(name="算分失败的", customer_type="buy_second_hand", tier="A")
        real = tin._score_intent

        def flaky(comp):
            if comp.get("customer_id") == c2["id"]:
                raise RuntimeError("模拟算分失败")
            return real(comp)

        monkeypatch.setattr(tin, "_score_intent", flaky)
        out = json.loads(tin.list_intent_scores())
        assert out["success"] is True
        assert "warning_scores" in out, out
        assert "算分失败的" in out["warning_scores"]
        assert [row["customer_id"] for row in out["rankings"]] == [c1["id"]], out
