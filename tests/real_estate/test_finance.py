"""贷款方案对比器 + 税费明细单测试（功能8+9，批次一）"""
import json

import pytest

from tools.real_estate_calculator import loan_compare, tax_breakdown_report


def parse(result):
    return json.loads(result)


# ==================== 贷款方案对比器 ====================

class TestLoanCompare:
    def test_default_plans(self):
        """默认输出：20年/30年等额本息 + 等额本金 = 3 方案"""
        d = parse(loan_compare(price=3_000_000))
        plans = d["loan_compare"]["方案对比"]
        assert len(plans) == 3

    def test_combo_loan_when_pf_amount_given(self):
        """传公积金额度 → 出组合贷方案且排最前"""
        d = parse(loan_compare(price=4_000_000, provident_fund_loan_amount=600_000))
        plans = d["loan_compare"]["方案对比"]
        assert len(plans) == 4
        assert "组合贷" in plans[0]["方案"]

    def test_monthly_payment_formula(self):
        """月供公式对齐手工算例：100万/30年/3.6% 等额本息 ≈ 4,546元"""
        d = parse(loan_compare(price=1_000_000 / 0.7, loan_years_list="30", commercial_rate=3.6))
        plans = [p for p in d["loan_compare"]["方案对比"] if "30年 等额本息" in p["方案"]]
        mp = float(plans[0]["月供"].replace("元", "").replace(",", ""))
        assert abs(mp - 4546) < 10  # 标准等额本息公式验证

    def test_equal_principal_interest_less(self):
        """等额本金总利息 < 同年限等额本息"""
        d = parse(loan_compare(price=3_000_000, loan_years_list="30", commercial_rate=3.6))
        plans = {p["方案"]: p for p in d["loan_compare"]["方案对比"]}
        # 2026-09-26（F247）：金额口径统一成两位小数「133.71万元」（原先是「133.7万」）——
        # 先把「万元」剥掉再剥「万」，否则会剩下一个「元」把 float() 卡住
        def _wan(text):
            return float(text.replace("万元", "").replace("万", ""))

        ei = _wan(plans["纯商贷 30年 等额本息"]["总利息"])
        ep = _wan(plans["纯商贷 30年 等额本金"]["总利息"])
        assert ep < ei

    def test_invalid_price_rejected(self):
        d = parse(loan_compare(price=0))
        assert d["success"] is False

    def test_invalid_down_payment_rejected(self):
        d = parse(loan_compare(price=3_000_000, down_payment_ratio=1.0))
        assert d["success"] is False


# ==================== 税费明细单 ====================

class TestTaxBreakdown:
    def test_full_five_only_exempt(self):
        """满五唯一：个税免征"""
        d = parse(tax_breakdown_report(price=2_000_000, area=89, hold_years=6, is_only_home=True))
        items = {i["税目"]: i for i in d["tax_breakdown"]["items"]}
        assert items["个人所得税"]["金额"] == "免征"

    def test_full_five_not_only_taxed(self):
        """满五但不唯一：个税不免"""
        d = parse(tax_breakdown_report(price=2_000_000, area=89, hold_years=6, is_only_home=False))
        items = {i["税目"]: i for i in d["tax_breakdown"]["items"]}
        assert items["个人所得税"]["金额"] != "免征"

    def test_full_two_ordinary_vat_exempt(self):
        """满2年普宅：增值税免征"""
        d = parse(tax_breakdown_report(price=2_000_000, area=89, hold_years=3))
        items = {i["税目"]: i for i in d["tax_breakdown"]["items"]}
        assert items["增值税及附加"]["金额"] == "免征"

    def test_under_two_years_vat_charged(self):
        """未满2年：增值税征收"""
        d = parse(tax_breakdown_report(price=2_000_000, area=89, hold_years=1))
        items = {i["税目"]: i for i in d["tax_breakdown"]["items"]}
        assert items["增值税及附加"]["金额"] != "免征"

    def test_second_home_deed_tax_three_percent(self):
        """二套：契税3%"""
        d = parse(tax_breakdown_report(price=2_000_000, area=89, hold_years=6, is_first_home=False))
        items = {i["税目"]: i for i in d["tax_breakdown"]["items"]}
        assert "3.0%" in items["契税"]["金额"]

    def test_first_home_small_area_one_percent(self):
        """首套+90平以下：契税1%"""
        d = parse(tax_breakdown_report(price=1_000_000, area=80, hold_years=6))
        items = {i["税目"]: i for i in d["tax_breakdown"]["items"]}
        assert "1.0%" in items["契税"]["金额"]

    def test_report_has_disclaimer(self):
        """客户版清单必须带免责声明"""
        d = parse(tax_breakdown_report(price=2_000_000, area=89, hold_years=6))
        assert "仅供参考" in d["tax_breakdown"]["report_text"]

    def test_invalid_input_rejected(self):
        d = parse(tax_breakdown_report(price=-1, area=89))
        assert d["success"] is False
