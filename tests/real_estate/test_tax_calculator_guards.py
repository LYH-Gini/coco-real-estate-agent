"""tax_calculator 回归（2026-09-26 第七组「贷款税费计算」第 56 项，F248–F254）

背景（实测，见 /root/coco-tool-audit/results/raw/t61.before.log，修前 13/17）——**同一个场景两个工具两个数**：
① 拿"买方首套"当"卖方唯一"判免个税：买方二套 + 卖方唯一 + 满 5 年 → 它征 4.00 万，明细单免征；
② 增值税口径不同：未满 2 年 → 它按全额 5.6%（22.40 万），明细单按价税分离 5%（19.05 万）；
③ 漏了"非普通住宅"档：120㎡ 首套非普宅 → 它 1.5%（6 万），明细单 3%（12 万）；
④ `is_first_home='否'` 被当成"是"（Python 真值）→ 契税按 1% 而不是 3%，**静默算错**；
⑤ 房价「400万」/面积「89平」/持有年限「两年」→ 崩；房价 0/负、面积 0/负、持有年限 −1 → 静默出数；
⑥ 单位写法「4.00万元」vs 明细单「4.00万」；描述 19 字。

修法核心：三档税率与判定抽成**一个共用函数** `_tax_assessment`，两个工具都调它（拍板以明细单为准）。
"""
import json

import pytest

P, AREA = 4_000_000, 89


def _call(**kwargs):
    import tools.real_estate_calculator as m

    return json.loads(m.tax_calculator(**kwargs))


def _breakdown(**kwargs):
    import tools.real_estate_calculator as m

    out = json.loads(m.tax_breakdown_report(**kwargs))
    items = (out.get("tax_breakdown") or {}).get("items") or []
    return {i.get("税目"): i.get("金额") for i in items}


def _amount(text):
    """「4.00万元 (1.0%)」→ 4.0（契税那格带税率后缀，先取空格前的金额部分）"""
    head = str(text).split(" ")[0]
    return float(head.replace("万元", "").replace("万", "").replace("元", ""))


# ==================== ① 满五唯一口径（F248） ====================

class TestFullFiveOnly:
    @pytest.mark.parametrize("first_home,only_home,expect_zero", [
        (True, True, True),      # 首套 + 卖方唯一 → 免征
        (False, True, True),     # 二套 + 卖方唯一 → **也免征**（旧实现这里征 4 万）
        (True, False, False),    # 首套 + 卖方不唯一 → 征 1%
        (False, False, False),
    ])
    def test_personal_tax_uses_seller_uniqueness(self, first_home, only_home, expect_zero):
        out = _call(price=P, area=AREA, hold_years=5, is_first_home=first_home,
                    is_only_home=only_home)
        tax = out["calculator"]["个人所得税"]
        if expect_zero:
            assert tax == "免征", out["calculator"]
        else:
            assert _amount(tax) == pytest.approx(4.0, abs=0.01), out["calculator"]

    def test_matches_breakdown_report(self):
        mine = _call(price=P, area=AREA, hold_years=5, is_first_home=False, is_only_home=True)
        theirs = _breakdown(price=P, area=AREA, hold_years=5, is_first_home=False, is_only_home=True)
        assert mine["calculator"]["个人所得税"] == "免征" == theirs["个人所得税"], (mine, theirs)


# ==================== ② 增值税口径（F249） ====================

class TestVAT:
    def test_under_two_years_uses_price_tax_separation(self):
        out = _call(price=P, area=AREA, hold_years=1)
        assert out["calculator"]["增值税"] == "19.05万元", out["calculator"]
        assert _amount(_breakdown(price=P, area=AREA, hold_years=1)["增值税及附加"]) == \
            pytest.approx(19.05, abs=0.01)

    def test_two_years_ordinary_is_exempt(self):
        out = _call(price=P, area=AREA, hold_years=2)
        assert out["calculator"]["增值税"] == "免征", out["calculator"]

    def test_two_years_non_ordinary_notes_the_gap(self):
        out = _call(price=P, area=120, hold_years=3, property_class="non_ordinary")
        assert "非普通住宅满 2 年按差额计税" in (out.get("note") or ""), out


# ==================== ③ 契税三档（F250） ====================

class TestDeedTax:
    @pytest.mark.parametrize("area,first_home,pclass,expect", [
        (89, True, "ordinary", 4.00),          # 1%
        (120, True, "ordinary", 6.00),         # 1.5%
        (120, True, "non_ordinary", 12.00),    # 3%（非普宅档，旧实现漏了）
        (89, False, "ordinary", 12.00),        # 二套 3%
        (120, False, "non_ordinary", 12.00),
    ])
    def test_rates(self, area, first_home, pclass, expect):
        out = _call(price=P, area=area, is_first_home=first_home, property_class=pclass)
        assert _amount(out["calculator"]["契税"]) == pytest.approx(expect, abs=0.01), out["calculator"]

    def test_matches_breakdown_report_non_ordinary(self):
        mine = _call(price=P, area=120, property_class="non_ordinary")["calculator"]["契税"]
        theirs = _breakdown(price=P, area=120, property_class="non_ordinary")["契税"]
        assert mine.split(" ")[0] == theirs.split(" ")[0], (mine, theirs)


# ==================== ④ 是/否类参数归一（F251） ====================

class TestBooleanNormalization:
    def test_chinese_no_is_not_treated_as_true(self):
        """旧实现：'否' 在 Python 里是真值 → 按首套 1% 算（静默算错）"""
        out = _call(price=P, area=AREA, is_first_home="否")
        assert out["calculator"]["是否首套"] == "否", out["calculator"]
        assert _amount(out["calculator"]["契税"]) == pytest.approx(12.0, abs=0.01), out["calculator"]

    @pytest.mark.parametrize("value,expect", [("是", "是"), ("首套", "是"), (True, "是"),
                                              ("二套", "否"), ("不是", "否"), (False, "否")])
    def test_first_home_forms(self, value, expect):
        out = _call(price=P, area=AREA, is_first_home=value)
        assert out["calculator"]["是否首套"] == expect, out["calculator"]

    @pytest.mark.parametrize("value,expect", [("唯一", "是"), ("不唯一", "否"), (False, "否")])
    def test_only_home_forms(self, value, expect):
        out = _call(price=P, area=AREA, is_only_home=value)
        assert out["calculator"]["是否唯一"] == expect, out["calculator"]

    def test_bad_boolean_gets_hint(self):
        out = _call(price=P, area=AREA, is_first_home="乱写的")
        assert out.get("success") is not True and "买方是否首套没能识别" in out["error"], out

    @pytest.mark.parametrize("value,expect", [("ordinary", "普通住宅"), ("非普宅", "非普通住宅"),
                                              ("non_ordinary", "非普通住宅"), ("普通住宅", "普通住宅")])
    def test_property_class_forms(self, value, expect):
        out = _call(price=P, area=AREA, property_class=value)
        assert out["calculator"]["住宅类型"] == expect, out["calculator"]

    def test_bad_property_class_gets_hint(self):
        out = _call(price=P, area=AREA, property_class="别墅")
        assert out.get("success") is not True and "住宅类型没能识别" in out["error"], out


# ==================== ⑤ 输入口径与边界（F252 / F253） ====================

class TestInputAndBoundaries:
    def test_price_and_area_writings(self):
        base = _call(price=P, area=AREA)["calculator"]
        for price, area in (("400万", "89平"), ("4,000,000", 89), (P, "89㎡")):
            out = _call(price=price, area=area)
            assert out["success"] is True, out
            assert out["calculator"]["契税"] == base["契税"], (out, base)

    @pytest.mark.parametrize("kwargs,keyword", [
        ({"price": "abc"}, "房价没能识别"),
        ({"price": 0}, "房价要大于 0"),
        ({"price": -P}, "房价要大于 0"),
        ({"area": "abc"}, "面积没能识别"),
        ({"area": 0}, "面积要大于 0"),
        ({"area": -89}, "面积要大于 0"),
        ({"hold_years": "乱写的"}, "持有年限没能识别"),
        ({"hold_years": -1}, "持有年限要在 0 到 100 年之间"),
        ({"hold_years": 101}, "持有年限要在 0 到 100 年之间"),
    ])
    def test_bad_values(self, kwargs, keyword):
        args = {"price": P, "area": AREA}
        args.update(kwargs)
        out = _call(**args)
        assert out.get("success") is not True, out
        assert keyword in out["error"], out
        assert "Tool execution failed" not in json.dumps(out, ensure_ascii=False), out

    def test_hold_years_chinese_and_decimal(self):
        assert _call(price=P, area=AREA, hold_years="两年")["calculator"]["持有年限"] == "2年"
        assert _call(price=P, area=AREA, hold_years=1.5)["calculator"]["持有年限"] == "1.5年"


# ==================== ⑥ 单位与描述（F254） ====================

def test_units_are_unified_with_breakdown_report():
    mine = _call(price=P, area=AREA)["calculator"]
    theirs = _breakdown(price=P, area=AREA)
    assert mine["契税"].endswith("(1.0%)") and "万元" in mine["契税"], mine
    assert "万元" in theirs["契税"], theirs
    assert mine["税费合计"].endswith("万元"), mine


def test_description_and_params_document_the_rule():
    from tools.registry import registry

    entry = registry.get_entry("tax_calculator")
    desc = entry.schema["description"]
    props = entry.schema["parameters"]["properties"]
    assert "税费明细单" in desc and "400万" in desc and "89平" in desc, desc
    assert set(props) >= {"price", "area", "is_first_home", "is_only_home", "hold_years",
                          "property_class"}, props
    assert "卖方是否唯一" in props["is_only_home"]["description"], props["is_only_home"]
    assert "满五年且唯一" in props["is_only_home"]["description"], props["is_only_home"]


# ==================== ⑥b 卖方原购入价（非普宅满 2 年差额计税） ====================

class TestOriginalPrice:
    def test_diff_taxation_with_original_price(self):
        """非普宅满 2 年：增值税 = （现价 − 原价）÷ 1.05 × 5%"""
        out = _call(price=P, area=120, hold_years=3, property_class="non_ordinary",
                    original_price=3_000_000)
        assert out["calculator"]["增值税"] == "4.76万元", out["calculator"]
        assert out["calculator"]["卖方原购入价"] == "300.00万元", out["calculator"]
        assert "原价300万元" in (out.get("note") or ""), out

    def test_matches_breakdown_report_with_original_price(self):
        mine = _call(price=P, area=120, hold_years=3, property_class="non_ordinary",
                     original_price=3_000_000)["calculator"]["增值税"]
        theirs = _breakdown(price=P, area=120, hold_years=3, property_class="non_ordinary",
                            original_price=3_000_000)["增值税及附加"]
        assert mine == theirs, (mine, theirs)

    def test_missing_original_price_is_disclosed(self):
        out = _call(price=P, area=120, hold_years=3, property_class="non_ordinary")
        assert "没给卖方原购入价" in (out.get("note") or ""), out
        assert out["calculator"]["增值税"] == "19.05万元", out["calculator"]
        assert "卖方原购入价" not in out["calculator"], out["calculator"]

    @pytest.mark.parametrize("value", [0, -1_000_000])
    def test_bad_original_price_gets_hint(self, value):
        out = _call(price=P, area=120, property_class="non_ordinary", original_price=value)
        assert out.get("success") is not True and "卖方原购入价要大于 0" in out["error"], out

    def test_original_price_writings(self):
        for raw in ("300万", "3,000,000", 3_000_000):
            out = _call(price=P, area=120, hold_years=3, property_class="non_ordinary",
                        original_price=raw)
            assert out["calculator"]["卖方原购入价"] == "300.00万元", (raw, out)

    def test_description_documents_original_price(self):
        from tools.registry import registry

        props = registry.get_entry("tax_calculator").schema["parameters"]["properties"]
        assert "original_price" in props, props
        assert "差额计税" in props["original_price"]["description"], props["original_price"]


# ==================== ⑦ 框架层（走真实 dispatch） ====================

def test_dispatch_required_and_unknown(monkeypatch):
    from tools.registry import registry

    out = json.loads(registry.dispatch("tax_calculator", {}, session_id="t", task_id="t"))
    assert out.get("success") is not True and "price" in (out.get("error") or ""), out
    out2 = json.loads(registry.dispatch("tax_calculator", {"price": P, "area": AREA, "period": "x"},
                                        session_id="t", task_id="t"))
    assert out2.get("success") is not True and "period" in (out2.get("error") or ""), out2
