"""roi_calculator 回归（2026-09-26 第七组「贷款税费计算」第 58 项，F259–F264 + 净收益能力）

背景（实测，见 /root/coco-tool-audit/results/raw/t63.before.log，修前 16/18）：
① 写法全崩：购入价「400万」、月租「5000元/月」、增值率文本 → TypeError；
② 除零崩：购入价 0、持有 0 年 → ZeroDivisionError；
③ **增值率口径不明、静默算出荒唐数**：传 `5`（想表达 5%）被当成 500%/年 → 5 年后卖出价
   **3,110,400.00 万元（约 311 亿）**；
④ 边界静默出数：购入价负数、月租负数、持有负数/41 年、增值率 −50%；
⑤ **口径一个字没说**：总收益/总回报率没扣税费与空置；年化回报率是按总回报率折算的近似值（不是 IRR）；
⑥ 精度：购入价 3,855,000 显示成「386万元」（丢 5 千）、年租金显示「59994.0元」（浮点尾巴）；描述 20 字。

本文件钉住修后的行为（含"净口径只在给了空置/物业费/月供时才出现、毛口径字段一个不改"这条契约）。
"""
import json

import pytest

PRICE, RENT, YEARS = 4_000_000, 5000, 5


def _call(**kwargs):
    import tools.real_estate_calculator as m

    return json.loads(m.roi_calculator(**kwargs))


def _num(text):
    return float(str(text).split(' ')[0].replace('万元', '').replace('万', '')
                 .replace('元', '').replace('%', '').replace(',', ''))


# ==================== ① 输入口径与归一（F259 / F261） ====================

class TestNormalization:
    @pytest.mark.parametrize("kwargs", [
        {},
        {"price": "400万"},
        {"price": "4,000,000"},
        {"monthly_rent": "5000元/月"},
        {"expected_appreciation": 5},          # 百分数写法
        {"expected_appreciation": "5%"},       # 带百分号
    ])
    def test_writings_give_the_same_numbers(self, kwargs):
        args = {"price": PRICE, "monthly_rent": RENT, "hold_years": YEARS,
                "expected_appreciation": 0.05}
        args.update(kwargs)
        out = _call(**args)
        assert out["success"] is True, out
        assert _num(out["calculator"]["预期年增值率"]) == 5, out["calculator"]
        assert _num(out["calculator"]["预期卖出价"]) == pytest.approx(510.51, abs=0.01), out["calculator"]

    def test_percent_writing_is_disclosed(self):
        out = _call(price=PRICE, monthly_rent=RENT, expected_appreciation=5)
        assert "预期年增值率「5」我按 5% 算的" in (out.get("note") or ""), out

    def test_decimal_ratio_does_not_get_a_bogus_note(self):
        out = _call(price=PRICE, monthly_rent=RENT, expected_appreciation=0.05)
        assert "预期年增值率" not in (out.get("note") or ""), out

    @pytest.mark.parametrize("kwargs,keyword", [
        ({"price": "abc"}, "购入价没能识别"),
        ({"price": 0}, "购入价要大于 0"),
        ({"price": -PRICE}, "购入价要大于 0"),
        ({"monthly_rent": "abc"}, "月租金没能识别"),
        ({"monthly_rent": -5000}, "月租金不能是负数"),
        ({"hold_years": 0}, "持有年限要在 1 到 40 年之间"),
        ({"hold_years": 41}, "持有年限要在 1 到 40 年之间"),
        ({"expected_appreciation": "abc"}, "预期年增值率没能识别"),
        ({"expected_appreciation": 200}, "预期年增值率要在"),
        ({"expected_appreciation": -2}, "预期年增值率要在"),
        ({"vacancy_months": 13}, "每年空置月数要在 0 到 12 年之间"),
        ({"property_fee_monthly": -1}, "每月物业费不能是负数"),
        ({"loan_monthly_payment": -1}, "每月还贷额不能是负数"),
    ])
    def test_bad_values_get_chinese_hint(self, kwargs, keyword):
        args = {"price": PRICE, "monthly_rent": RENT}
        args.update(kwargs)
        out = _call(**args)
        assert out.get("success") is not True, out
        assert keyword in out["error"], out
        assert "Tool execution failed" not in json.dumps(out, ensure_ascii=False), out

    def test_zero_rent_is_allowed_but_no_crash(self):
        out = _call(price=PRICE, monthly_rent=0)
        assert out["success"] is True and _num(out["calculator"]["毛租金回报率"]) == 0, out


# ==================== ② 算得对（硬钉子） ====================

class TestMath:
    def test_all_formulas(self):
        out = _call(price=PRICE, monthly_rent=RENT, hold_years=YEARS, expected_appreciation=0.05)
        c = out["calculator"]
        annual_rent, appr, n = RENT * 12, 0.05, YEARS
        future = PRICE * (1 + appr) ** n
        gain = future - PRICE
        total = annual_rent * n + gain
        total_roi = total / PRICE * 100
        assert _num(c["毛租金回报率"]) == pytest.approx(annual_rent / PRICE * 100, abs=0.01), c
        assert _num(c["预期卖出价"]) == pytest.approx(future / 10000, abs=0.01), c
        assert _num(c["升值收益"]) == pytest.approx(gain / 10000, abs=0.01), c
        assert _num(c["总收益"]) == pytest.approx(total / 10000, abs=0.01), c
        assert _num(c["总回报率"]) == pytest.approx(total_roi, abs=0.01), c
        assert _num(c["年化回报率"]) == pytest.approx(
            ((1 + total_roi / 100) ** (1 / n) - 1) * 100, abs=0.01), c


# ==================== ③ 口径说明（F263） ====================

def test_note_states_the_scope():
    note = _call(price=PRICE, monthly_rent=RENT).get("note") or ""
    assert "只算租金收入 + 房价增值" in note, note
    assert "未扣税费" in note, note
    assert "不是 IRR" in note, note


# ==================== ④ 净收益能力（拍板新增） ====================

class TestNetYield:
    def test_net_fields_only_appear_when_inputs_given(self):
        plain = _call(price=PRICE, monthly_rent=RENT)["calculator"]
        assert not any(k.startswith("净") or k in ("有效年租金", "年持有成本") for k in plain), plain
        net = _call(price=PRICE, monthly_rent=RENT, vacancy_months=1,
                    property_fee_monthly=300, loan_monthly_payment=8000)["calculator"]
        for key in ("有效年租金", "年持有成本", "净收益", "净租金回报率", "净总回报率", "净年化回报率"):
            assert key in net, (key, net)
        # 毛口径字段一个没改
        for key in ("毛租金回报率", "总收益", "总回报率", "年化回报率"):
            assert net[key] == plain[key], (key, net[key], plain[key])

    def test_net_numbers(self):
        out = _call(price=PRICE, monthly_rent=RENT, hold_years=5, expected_appreciation=0.05,
                    vacancy_months=1, property_fee_monthly=300, loan_monthly_payment=8000)
        c = out["calculator"]
        effective_rent = RENT * (12 - 1)
        cost = (300 + 8000) * 12
        future = PRICE * 1.05 ** 5
        net_gain = effective_rent * 5 + (future - PRICE) - cost * 5
        assert _num(c["有效年租金"]) == effective_rent, c
        assert _num(c["年持有成本"]) == cost, c
        assert _num(c["净收益"]) == pytest.approx(net_gain / 10000, abs=0.01), c
        assert _num(c["净租金回报率"]) == pytest.approx(effective_rent / PRICE * 100, abs=0.01), c
        assert "净口径" in (out.get("note") or ""), out

    def test_net_note_uses_readable_units(self):
        note = _call(price=PRICE, monthly_rent=RENT, property_fee_monthly=300,
                     loan_monthly_payment=8000).get("note") or ""
        assert "每月物业费 300元" in note and "每月还贷 8000元" in note, note
        assert "0.03万" not in note, note


# ==================== ⑤ 精度与描述（F264） ====================

class TestFormatting:
    def test_precision_and_units(self):
        out = _call(price=3_855_000, monthly_rent=4999, hold_years=5,
                    expected_appreciation=0.045)["calculator"]
        assert out["购入价"] == "385.50万元", out
        assert out["月租金"] == "4999元/月", out
        assert out["年租金收入"] == "59988元", out
        assert out["预期年增值率"] == "4.5%", out

    def test_description_documents_scope_and_new_params(self):
        from tools.registry import registry

        entry = registry.get_entry("roi_calculator")
        desc = entry.schema["description"]
        props = entry.schema["parameters"]["properties"]
        assert "未扣税费" in desc and "净收益" in desc, desc
        assert set(props) >= {"price", "monthly_rent", "hold_years", "expected_appreciation",
                              "vacancy_months", "property_fee_monthly",
                              "loan_monthly_payment"}, props
        assert "百分数" in props["expected_appreciation"]["description"], props["expected_appreciation"]


# ==================== ⑥ 框架层（走真实 dispatch） ====================

def test_dispatch_required_and_unknown():
    from tools.registry import registry

    out = json.loads(registry.dispatch("roi_calculator", {}, session_id="t", task_id="t"))
    assert out.get("success") is not True and "price" in (out.get("error") or ""), out
    out2 = json.loads(registry.dispatch("roi_calculator",
                                        {"price": PRICE, "monthly_rent": RENT, "period": "x"},
                                        session_id="t", task_id="t"))
    assert out2.get("success") is not True and "period" in (out2.get("error") or ""), out2
