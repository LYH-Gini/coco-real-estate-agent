"""loan_compare 回归（2026-09-26 第七组「贷款税费计算」第 55 项，F242–F247）

背景（实测，见 /root/coco-tool-audit/results/raw/t60.before.log，修前 14/17）：
① 写法全崩：房价「400万」、首付「30%」、利率「4.5%」→ TypeError 执行失败；
② 利率小数写法 `0.045` **静默算错**（按揭侧已定为给提示、不猜）——同族两套标准；
③ 首付 `30` 被拒（按揭侧是归一成 30%）——同族两套标准；
④ 年限列表不校验：`abc` **静默退回默认 20,30**、`0` **除零崩**、`100` 照算、中文逗号静默；
⑤ 公积金额度超过贷款额 → `min()` **静默截断**，一句说明都没有；
⑥ 提示 `房价需>0且首付比例需在(0,1)之间`（两句混一句 + 数学写法）、`商贷利率来源` 值是内部词
   「默认值/手动指定」、月供 `14,187元`（按揭是 `14187.19元`）、金额一位小数（按揭两位）。

本文件钉住修后的行为（含"结果必须等于独立公式"这条硬钉子）。
"""
import json

import pytest

P, RATIO, RATE = 4_000_000, 0.3, 4.5
_LOAN = P * (1 - RATIO)


def _call(**kwargs):
    import tools.real_estate_calculator as m

    return json.loads(m.loan_compare(**kwargs))


def _plans(out):
    return (out.get("loan_compare") or {}).get("方案对比") or []


def _pure(out, years, method="等额本息"):
    return next((p for p in _plans(out)
                 if f"{years}年 {method}" in str(p.get("方案")) and "纯商贷" in str(p.get("方案"))), {})


def _monthly(text):
    return float(str(text).replace("元", "").replace(",", ""))


def _wan(text):
    return float(str(text).replace("万元", "").replace("万", ""))


def _installment(amount, rate, years):
    mr, n = rate / 100 / 12, years * 12
    return amount * mr * (1 + mr) ** n / ((1 + mr) ** n - 1)


# ==================== ① 输入口径归一（F242 / F244） ====================

class TestNormalization:
    @pytest.mark.parametrize("kwargs", [
        {},                                      # 基准：4000000 / 0.3 / 4.5
        {"price": "400万"},
        {"price": "4,000,000"},
        {"down_payment_ratio": 30},
        {"down_payment_ratio": "30%"},
        {"commercial_rate": "4.5%"},
    ])
    def test_writings_give_the_same_numbers(self, kwargs):
        args = {"price": P, "down_payment_ratio": RATIO, "commercial_rate": RATE}
        args.update(kwargs)
        out = _call(**args)
        assert out["success"] is True, out
        assert abs(_monthly(_pure(out, 30)["月供"]) - _installment(_LOAN, RATE, 30)) < 1, out
        assert out["loan_compare"]["首付"].startswith("30%"), out["loan_compare"]["首付"]

    @pytest.mark.parametrize("years_list", ["20,30", "20，30", "20年,30年", "30,20"])
    def test_years_list_writings(self, years_list):
        out = _call(price=P, loan_years_list=years_list)
        assert out["success"] is True, out
        assert _pure(out, 20) and _pure(out, 30), [p["方案"] for p in _plans(out)]

    def test_conversions_are_disclosed_in_note(self):
        out = _call(price=P, down_payment_ratio=30, loan_years_list="20年,30年")
        note = out.get("note") or ""
        assert "首付比例「30」我按 30% 算的" in note, out
        assert "贷款年限列表「20年,30年」我按 [20, 30] 算的" in note, out

    def test_no_note_when_writings_are_clean(self):
        out = _call(price=P, down_payment_ratio=0.3, loan_years_list="20,30")
        assert "note" not in out, out


# ==================== ② 坏值一律中文提示、不许崩或静默（F242–F245） ====================

class TestBadInput:
    @pytest.mark.parametrize("kwargs,keyword", [
        ({"price": "abc"}, "房价没能识别"),
        ({"price": 0}, "房价要大于 0"),
        ({"price": -1}, "房价要大于 0"),
        ({"down_payment_ratio": 0}, "首付比例要在 0 和 1 之间"),
        ({"down_payment_ratio": 1}, "首付比例"),
        ({"down_payment_ratio": 1.5}, "首付比例"),
        ({"commercial_rate": 0.045}, "商贷年利率看着不对"),
        ({"commercial_rate": "abc"}, "商贷年利率没能识别"),
        ({"loan_years_list": "abc"}, "贷款年限没能识别"),
        ({"loan_years_list": "0"}, "贷款年限要在 1 到 40 年之间"),
        ({"loan_years_list": "100"}, "贷款年限要在 1 到 40 年之间"),
    ])
    def test_bad_values(self, kwargs, keyword):
        args = {"price": P, "down_payment_ratio": RATIO}
        args.update(kwargs)
        out = _call(**args)
        assert out.get("success") is not True, out
        assert keyword in out["error"], out
        assert "Tool execution failed" not in json.dumps(out, ensure_ascii=False), out

    def test_bad_years_list_does_not_silently_fall_back(self):
        out = _call(price=P, loan_years_list="abc")
        assert out.get("success") is not True, out
        assert "回到默认" not in json.dumps(out, ensure_ascii=False), out


# ==================== ③ 公积金额度：截断要说明（F246） ====================

class TestProvidentFund:
    def test_over_amount_is_disclosed(self):
        out = _call(price=P, down_payment_ratio=RATIO, provident_fund_loan_amount=10_000_000)
        assert out["success"] is True, out
        note = out.get("note") or ""
        assert "公积金贷款额度" in note and "超过了贷款总额" in note and "我按" in note, out
        combo = next((p for p in _plans(out) if "公积金贷" in str(p["方案"])), {})
        # 额度覆盖了全部贷款额 → 不再叫「组合贷(…+商贷0万)」，改叫「纯公积金贷 N万」
        assert str(combo.get("方案")).startswith("纯公积金贷 280万"), combo
        assert combo.get("年利率") == "公积金2.85%", combo

    def test_normal_amount_has_no_truncation_note(self):
        out = _call(price=P, down_payment_ratio=RATIO, provident_fund_loan_amount=1_000_000)
        assert "超过了贷款总额" not in (out.get("note") or ""), out


# ==================== ④ 算得对：独立公式复核（硬钉子） ====================

class TestMath:
    @pytest.mark.parametrize("years", [20, 30])
    def test_installment_matches_formula(self, years):
        out = _call(price=P, down_payment_ratio=RATIO, commercial_rate=RATE)
        assert abs(_monthly(_pure(out, years)["月供"]) - _installment(_LOAN, RATE, years)) < 1, out

    def test_equal_principal_matches_formula(self):
        out = _call(price=P, down_payment_ratio=RATIO, commercial_rate=RATE)
        ep = _pure(out, 30, "等额本金")
        first = _monthly(str(ep["月供"]).replace("首月", "").replace("逐月递减", ""))
        assert abs(first - (_LOAN / 360 + _LOAN * RATE / 100 / 12)) < 1, ep
        expect_interest = _LOAN * RATE / 100 * (360 + 1) / 2 / 12
        assert abs(_wan(ep["总利息"]) - expect_interest / 10000) < 0.2, ep

    def test_combo_is_sum_of_parts(self):
        out = _call(price=P, down_payment_ratio=RATIO, commercial_rate=RATE,
                    provident_fund_rate=2.85, provident_fund_loan_amount=1_000_000)
        combo = next((p for p in _plans(out) if "组合贷" in str(p["方案"])), {})
        expect = _installment(1_000_000, 2.85, 30) + _installment(_LOAN - 1_000_000, RATE, 30)
        assert abs(_monthly(combo["月供"]) - expect) < 1, combo

    def test_same_scenario_matches_mortgage_calculator(self):
        import tools.real_estate_calculator as m

        out = _call(price=P, down_payment_ratio=RATIO, commercial_rate=RATE)
        single = json.loads(m.mortgage_calculator(price=P, down_payment_ratio=RATIO,
                                                  loan_years=30, interest_rate=RATE))
        assert abs(_monthly(_pure(out, 30)["月供"])
                   - _monthly(single["calculator"]["月供"])) < 1, (out, single)


# ==================== ⑤ 文案与精度（F247） ====================

class TestWordingAndPrecision:
    def test_monthly_payment_has_two_decimals(self):
        out = _call(price=P, down_payment_ratio=RATIO, commercial_rate=RATE)
        assert _pure(out, 30)["月供"] == "14,187.19元", _pure(out, 30)["月供"]

    def test_amounts_use_two_decimal_wan(self):
        out = _call(price=P, down_payment_ratio=RATIO, commercial_rate=RATE)
        assert out["loan_compare"]["贷款总额"] == "280.00万元", out["loan_compare"]
        assert _pure(out, 30)["总利息"].endswith("万元"), _pure(out, 30)

    def test_rate_source_is_human_readable(self):
        out = _call(price=P, down_payment_ratio=RATIO, commercial_rate=RATE)
        assert out["loan_compare"]["商贷利率来源"] == "你说的是 4.5%", out["loan_compare"]
        out2 = _call(price=P, down_payment_ratio=RATIO)
        assert "没给利率" in out2["loan_compare"]["商贷利率来源"], out2["loan_compare"]

    def test_error_message_is_per_field(self):
        out = _call(price=0)
        assert out["error"] == "房价要大于 0：收到的是「0」", out
        out2 = _call(price=P, down_payment_ratio=1.5)
        assert "首付比例要在 0 和 1 之间" in out2["error"] and "房价" not in out2["error"], out2

    def test_description_documents_writings(self):
        from tools.registry import registry

        entry = registry.get_entry("loan_compare")
        desc = entry.schema["description"]
        props = entry.schema["parameters"]["properties"]
        assert "400万" in desc and "4.5% 写 4.5" in desc, desc
        assert "0.3" in props["down_payment_ratio"]["description"], props["down_payment_ratio"]
        assert "20,30" in props["loan_years_list"]["description"], props["loan_years_list"]


# ==================== ⑥ 框架层合约（走真实 dispatch） ====================

def test_dispatch_required_and_unknown(monkeypatch):
    from tools.registry import registry

    out = json.loads(registry.dispatch("loan_compare", {}, session_id="t", task_id="t"))
    assert out.get("success") is not True and "price" in (out.get("error") or ""), out
    out2 = json.loads(registry.dispatch("loan_compare", {"price": P, "period": "month"},
                                        session_id="t", task_id="t"))
    assert out2.get("success") is not True and "period" in (out2.get("error") or ""), out2
