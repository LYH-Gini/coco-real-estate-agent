"""核心工具回归：计算器、报告与统计、意向评分、竞品对比、政策与定时任务开关（2026-09-18 补测）"""
import json

from conftest import make_customer, make_property


def _dispatch(db, monkeypatch, name, args):
    import tools.real_estate_calculator  # noqa: F401
    import tools.real_estate_report  # noqa: F401
    import tools.real_estate_analytics  # noqa: F401
    import tools.real_estate_intent  # noqa: F401
    import tools.real_estate_policy  # noqa: F401
    import tools.real_estate_cron_tools  # noqa: F401
    import tools.real_estate_customer  # noqa: F401
    import tools.real_estate_property  # noqa: F401
    from tools.registry import registry
    import tools.real_estate_calculator as tcal
    import tools.real_estate_report as trp
    import tools.real_estate_analytics as ta
    import tools.real_estate_intent as tin
    import tools.real_estate_customer as tc
    import tools.real_estate_property as tp
    for m in (tcal, trp, ta, tin, tc, tp):
        if hasattr(m, "_get_db"):      # 纯计算器不带数据库依赖
            monkeypatch.setattr(m, "_get_db", lambda: db)
    return json.loads(registry.dispatch(name, args))


class TestCalculators:
    def test_mortgage_tax_roi(self, db, monkeypatch):
        m = _dispatch(db, monkeypatch, "mortgage_calculator",
                      {"price": 3_000_000, "down_payment_ratio": 0.3, "loan_years": 30, "interest_rate": 4.5})
        assert m.get("success") is True, m
        t = _dispatch(db, monkeypatch, "tax_calculator", {"price": 3_000_000, "area": 100, "is_first_home": True, "hold_years": 2})
        assert t.get("success") is True, t
        r = _dispatch(db, monkeypatch, "roi_calculator",
                      {"price": 2_000_000, "monthly_rent": 5000, "hold_years": 5, "expected_appreciation": 0.03})
        assert r.get("success") is True, r

    def test_loan_compare_and_tax_breakdown(self, db, monkeypatch):
        lc = _dispatch(db, monkeypatch, "loan_compare", {"price": 1_500_000})
        assert lc.get("success") is True, lc
        tb = _dispatch(db, monkeypatch, "tax_breakdown_report", {"price": 1_500_000, "area": 100.0})
        assert tb.get("success") is True, tb


class TestReports:
    def test_reports_run_with_data(self, db, monkeypatch):
        c = make_customer(db, name="报告客户", source="抖音", tier="S")
        p = make_property(db, title="报告房源", price=1_500_000, area=100.0)
        db.add_deal(customer_id=c["id"], property_id=p["id"], price=1_400_000)
        for name, args in (("generate_report", {"period": "week"}),
                           ("performance_dashboard", {"period": "week"}),
                           ("market_brief", {"district": "美兰"}),
                           ("channel_stats", {})):
            out = _dispatch(db, monkeypatch, name, args)
            assert out.get("success") is True, f"{name}: {out}"

    def test_market_brief_counts_own_inventory(self, db, monkeypatch):
        for i in range(3):
            make_property(db, title=f"简报房源{i}", price=1_500_000, area=100.0)
        out = _dispatch(db, monkeypatch, "market_brief", {})
        text = json.dumps(out, ensure_ascii=False)
        assert "3" in text and out.get("success") is True, out


class TestAnalyticsAndIntent:
    def test_intent_score_and_list(self, db, monkeypatch):
        c = make_customer(db, name="意向客户", tier="A")
        p = make_property(db, title="意向房源", price=1_500_000, area=100.0)
        s = _dispatch(db, monkeypatch, "intent_score", {"customer_id": c["id"]})
        assert s.get("success") is True, s
        lst = _dispatch(db, monkeypatch, "list_intent_scores", {})
        assert lst.get("success") is True, lst

    def test_compare_property(self, db, monkeypatch):
        make_property(db, title="对比房源A", price=1_500_000, area=100.0)
        p2 = make_property(db, title="对比房源B", price=1_600_000, area=110.0)
        out = _dispatch(db, monkeypatch, "compare_property", {"property_id": p2["id"]})
        assert out.get("success") is True, out


class TestPolicyAndCron:
    def test_policy_library_disabled_returns_guidance(self, db, monkeypatch):
        """政策库已停用：必须给出可执行指引（联网查 / 咨询官方），而不是编造政策数字

        2026-09-27 更新口径（第十二组 F401–F403）：指引里**不许出现内部工具名**（如 web_search），
        改成"跟我说一声，我联网查给你"；`list_policy_cities` 同样如实说停用（不再 success=true + 空列表）。
        """
        out = _dispatch(db, monkeypatch, "get_loan_policy", {"city": "海口", "policy_type": "首付比例"})
        assert out.get("success") is False
        text = out.get("error", "")
        assert "联网" in text or "咨询" in text, out
        assert "web_search" not in text, "对外文案不许出现内部工具名"
        assert "「海口」" in text, "问的是哪个城市要回显出来"
        cities = _dispatch(db, monkeypatch, "list_policy_cities", {})
        assert cities.get("success") is False, cities
        assert "停用" in cities.get("error", ""), cities

    def test_cron_toggle_reports_json(self, db, monkeypatch):
        """定时任务开关：无论能否注册（依赖 croniter）都必须返回结构化结果，不抛异常"""
        out = _dispatch(db, monkeypatch, "enable_cron", {"chat_id": "oc_test"})
        assert "success" in out, out
        out2 = _dispatch(db, monkeypatch, "disable_cron", {})
        assert "success" in out2, out2
