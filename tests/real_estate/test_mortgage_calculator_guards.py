"""mortgage_calculator 回归（2026-09-26 第七组「贷款税费计算」第 54 项，F234–F240）

背景（实测，见 /root/coco-tool-audit/results/raw/t59a.before.log）：
① **常见写法全崩**：`price='400万'`、`'4,000,000'`、`interest_rate='4.5%'`、`loan_years='30年'`、
   `down_payment_ratio='30%'` → 全部 `TypeError` 执行失败；
② **利率小数写法静默算错**：`interest_rate=0.045` → 月供 7830.54 元（正确 14187.19，差近一半）；
③ **首付传百分数静默算错**：`down_payment_ratio=30` → 月供 −587754.96 元（负的）；
④ 边界崩溃：`loan_years=0`、`interest_rate=0` → ZeroDivisionError；
⑤ 静默吃坏值：房价 0/负数、首付比例 1/1.5 → `success=true` 输出 0 或负数；
⑥ 精度：传 0.3 显示「30.0%」；
⑦ 描述写"还款计划"但实现没有；`method` 说明没有中文说法。

本文件钉住修后的行为（含"月供必须等于独立公式"这条硬钉子 —— 计算类工具最怕算错还回成功）。
"""
import json
import re

import pytest

P, RATIO, YEARS, RATE = 4_000_000, 0.3, 30, 4.5
_LOAN = P * (1 - RATIO)
_MR = RATE / 100 / 12
_N = YEARS * 12
_EXPECT_MONTHLY = _LOAN * _MR * (1 + _MR) ** _N / ((1 + _MR) ** _N - 1)


def _call(**kwargs):
    import tools.real_estate_calculator as m

    return json.loads(m.mortgage_calculator(**kwargs))


def _monthly(out):
    return float(str(out["calculator"]["月供"]).replace("元", "").replace(",", ""))


# ==================== ① 输入口径归一（F234 / F235 / F236 的一半） ====================

class TestInputNormalization:
    @pytest.mark.parametrize("price", [4_000_000, "400万", "4,000,000"])
    def test_price_writings(self, price):
        out = _call(price=price)
        assert out["success"] is True, out
        assert abs(_monthly(out) - _EXPECT_MONTHLY) < 1, out

    @pytest.mark.parametrize("rate", [4.5, "4.5%", "4.5％"])
    def test_rate_writings(self, rate):
        out = _call(price=P, interest_rate=rate)
        assert out["success"] is True and abs(_monthly(out) - _EXPECT_MONTHLY) < 1, out

    @pytest.mark.parametrize("ratio", [0.3, 30, "30%"])
    def test_ratio_writings(self, ratio):
        out = _call(price=P, down_payment_ratio=ratio)
        assert out["success"] is True, out
        assert out["calculator"]["首付比例"] == "30%", out
        assert abs(_monthly(out) - _EXPECT_MONTHLY) < 1, out

    @pytest.mark.parametrize("years", [30, "30年"])
    def test_years_writings(self, years):
        out = _call(price=P, loan_years=years)
        assert out["success"] is True and abs(_monthly(out) - _EXPECT_MONTHLY) < 1, out

    def test_normalization_is_disclosed(self):
        out = _call(price=P, down_payment_ratio=30, loan_years="30年", interest_rate="4.5%")
        note = out.get("note") or ""
        assert "首付比例「30」我按 30% 算的" in note, out
        assert "贷款年限「30年」我按 30 年算的" in note, out
        assert "年利率「4.5%」我按 4.5% 算的" in note, out

    def test_no_note_when_writings_are_already_clean(self):
        out = _call(price=P, down_payment_ratio=0.3, loan_years=30, interest_rate=4.5)
        assert "note" not in out, out


# ==================== ② 认不出的写法：给提示、不猜着算（F234 / F235 / F236） ====================

class TestBadInput:
    def test_decimal_rate_is_refused_not_guessed(self):
        """0.045 静默算成一半月供是最坏的一类；现在必须给中文提示"""
        out = _call(price=P, interest_rate=0.045)
        assert out.get("success") is not True, out
        assert "年利率看着不对" in out["error"] and "0.045" in out["error"], out

    def test_ratio_percent_number_does_not_produce_negative(self):
        out = _call(price=P, down_payment_ratio=300)
        assert out.get("success") is not True, out
        assert "首付比例" in out["error"], out

    @pytest.mark.parametrize("kwargs,keyword", [
        ({"price": "abc"}, "房价没能识别"),
        ({"price": ""}, "房价是空的"),
        ({"price": None}, "房价是空的"),
        ({"price": True}, "房价没能识别"),
        ({"interest_rate": "abc"}, "年利率没能识别"),
        ({"interest_rate": 0}, "年利率要大于 0"),
        ({"loan_years": "abc"}, "贷款年限没能识别"),
        ({"loan_years": 0}, "贷款年限要在 1 到 40 年之间"),
        ({"loan_years": 41}, "贷款年限要在 1 到 40 年之间"),
        ({"down_payment_ratio": 0}, "首付比例要在 0 和 1 之间"),
        ({"down_payment_ratio": 1}, "首付比例"),
        ({"down_payment_ratio": 1.5}, "首付比例"),
        ({"method": "乱写的"}, "还款方式没能识别"),
    ])
    def test_bad_values_get_chinese_hint(self, kwargs, keyword):
        args = {"price": P}
        args.update(kwargs)
        out = _call(**args)
        assert out.get("success") is not True, out
        assert keyword in out["error"], out
        assert "Tool execution failed" not in json.dumps(out, ensure_ascii=False), out

    def test_negative_or_zero_price(self):
        for price in (0, -4_000_000):
            out = _call(price=price)
            assert out.get("success") is not True and "房价要大于 0" in out["error"], out


# ==================== ③ 算得对（硬钉子） ====================

class TestMath:
    def test_monthly_payment_matches_independent_formula(self):
        out = _call(price=P, down_payment_ratio=RATIO, loan_years=YEARS, interest_rate=RATE)
        assert abs(_monthly(out) - _EXPECT_MONTHLY) < 1, (out, _EXPECT_MONTHLY)

    def test_internal_consistency(self):
        out = _call(price=P, down_payment_ratio=RATIO, loan_years=YEARS, interest_rate=RATE)
        calc = out["calculator"]
        total_pay = float(calc["总还款额"].replace("万元", ""))
        total_int = float(calc["总利息"].replace("万元", ""))
        loan = float(calc["贷款金额"].replace("万元", ""))
        assert abs(total_pay * 10000 - _monthly(out) * _N) < 100, calc
        assert abs((total_pay - loan) - total_int) < 0.02, calc

    def test_equal_principal_first_and_last(self):
        out = _call(price=P, down_payment_ratio=RATIO, loan_years=YEARS, interest_rate=RATE,
                    method="equal_principal")
        first, last = (float(x) for x in out["calculator"]["月供"].split(" - "))
        assert abs(first - (_LOAN / _N + _LOAN * _MR)) < 0.02, out
        assert abs(last - (_LOAN / _N + (_LOAN / _N) * _MR)) < 0.02, out


# ==================== ④ 精度与描述（F239 / F240） ====================

def test_ratio_formatting_has_no_trailing_zero():
    out = _call(price=P, down_payment_ratio=0.3)
    assert out["calculator"]["首付比例"] == "30%", out


def test_description_matches_implementation():
    from tools.registry import registry

    desc = registry.get_entry("mortgage_calculator").schema["description"]
    assert "还款计划" not in desc, desc
    assert "月供" in desc and "总利息" in desc, desc
    props = registry.get_entry("mortgage_calculator").schema["parameters"]["properties"]
    assert "等额本息" in props["method"]["description"], props["method"]
    assert props["method"]["enum"] == ["equal_installment", "equal_principal"], props["method"]
    assert "0.3" in props["down_payment_ratio"]["description"], props["down_payment_ratio"]


# ==================== ⑤ 框架层契约（走真实 dispatch） ====================

def test_dispatch_required_and_unknown_param(monkeypatch):
    from tools.registry import registry

    out = json.loads(registry.dispatch("mortgage_calculator", {}, session_id="t", task_id="t"))
    assert out.get("success") is not True and "price" in (out.get("error") or ""), out
    out2 = json.loads(registry.dispatch("mortgage_calculator", {"price": P, "period": "month"},
                                        session_id="t", task_id="t"))
    assert out2.get("success") is not True and "period" in (out2.get("error") or ""), out2
