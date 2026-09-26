"""tax_breakdown_report 回归（2026-09-26 第七组「贷款税费计算」第 57 项，F255–F258）

背景（实测，见 /root/coco-tool-audit/results/raw/t62.before.log，修前 14/15）：
① **报错两句混一句**：房价 0 与面积 0 都回「房价和面积需>0」，看不出是哪一个字段；
② **`original_price` 坏值两个工具不一致**：明细单对 `0`/负数静默按"没给"处理（负数还会把差额算得**更大**），
   而税calculator 会拒绝；
③ **客户版清单的精度与写法**：面积显示 `89.0㎡`（应 `89㎡`）；成交价**四舍五入到整数万**
   （3,855,000 → 显示 `386万元`，**丢掉 5 千**）；税目写成 `增值税及附加（卖方(常转嫁)）`（嵌套半角括号）；
④ 描述没说"要一个总额用税费计算器"、`original_price` 何时用。

本文件钉住修后的行为（含"清单文案与结构化字段一致"这条口径钉子）。
"""
import json

import pytest

P, AREA = 4_000_000, 89


def _call(**kwargs):
    import tools.real_estate_calculator as m

    return json.loads(m.tax_breakdown_report(**kwargs))


def _text(**kwargs):
    return (_call(**kwargs).get("tax_breakdown") or {}).get("report_text") or ""


def _items(**kwargs):
    return {i.get("税目"): i for i in ((_call(**kwargs).get("tax_breakdown") or {}).get("items") or [])}


# ==================== ① 报错分字段（F255） ====================

class TestFieldLevelErrors:
    def test_price_and_area_are_reported_separately(self):
        out = _call(price=0, area=AREA)
        assert out.get("success") is not True and out["error"] == "房价要大于 0：收到的是「0」", out
        out2 = _call(price=P, area=0)
        assert out2.get("success") is not True and out2["error"] == "面积要大于 0：收到的是「0」", out2
        for out3 in (out, out2):
            assert "房价和面积" not in out3["error"], out3

    @pytest.mark.parametrize("kwargs,keyword", [
        ({"price": "abc"}, "成交价没能识别"),
        ({"area": "abc"}, "面积没能识别"),
        ({"hold_years": -1}, "持有年限要在 0 到 100 年之间"),
        ({"property_class": "别墅"}, "住宅类型没能识别"),
        ({"is_first_home": "乱写的"}, "买方是否首套没能识别"),
    ])
    def test_bad_values(self, kwargs, keyword):
        args = {"price": P, "area": AREA}
        args.update(kwargs)
        out = _call(**args)
        assert out.get("success") is not True and keyword in out["error"], out
        assert "Tool execution failed" not in json.dumps(out, ensure_ascii=False), out


# ==================== ② 原购入价（F256） ====================

class TestOriginalPrice:
    @pytest.mark.parametrize("value", [0, -1_000_000])
    def test_bad_original_price_is_refused(self, value):
        out = _call(price=P, area=120, property_class="non_ordinary", original_price=value)
        assert out.get("success") is not True, out
        assert "卖方原购入价要大于 0" in out["error"], out

    def test_original_price_writings_and_diff_taxation(self):
        out = _call(price=P, area=120, hold_years=3, property_class="non_ordinary",
                    original_price="300万")
        assert out["success"] is True, out
        assert _items(price=P, area=120, hold_years=3, property_class="non_ordinary",
                      original_price="300万")["增值税及附加"]["金额"] == "4.76万元"
        assert "原价300万元" in _items(price=P, area=120, hold_years=3,
                                    property_class="non_ordinary",
                                    original_price="300万")["增值税及附加"]["依据"]

    def test_same_as_tax_calculator(self):
        import tools.real_estate_calculator as m

        args = dict(price=P, area=120, hold_years=3, property_class="non_ordinary",
                    original_price=3_000_000)
        mine = _items(**args)["增值税及附加"]["金额"]
        single = json.loads(m.tax_calculator(**args))["calculator"]["增值税"]
        assert mine == single, (mine, single)

    def test_missing_original_price_says_so_in_依据(self):
        assert "非普宅需提供原购入价" in _items(price=P, area=120, hold_years=3,
                                        property_class="non_ordinary")["增值税及附加"]["依据"]


# ==================== ③ 客户版清单的精度与写法（F257） ====================

class TestReportText:
    def test_area_has_no_trailing_zero(self):
        assert "89㎡" in _text(price=P, area=89), _text(price=P, area=89)
        assert "89.0㎡" not in _text(price=P, area=89)
        assert "89.5㎡" in _text(price=P, area=89.5), "小数面积照旧保留"

    def test_price_is_not_rounded_to_whole_wan(self):
        """3,855,000 曾是「386万元」（四舍五入丢 5 千）"""
        text = _text(price=3_855_000, area=89)
        assert "385.50万元" in text, text
        assert "386万元" not in text, text

    def test_bearer_uses_chinese_comma(self):
        text = _text(price=P, area=AREA)
        assert "（卖方，常转嫁）" in text, text
        assert "（卖方(常转嫁)）" not in text, text

    def test_report_has_exempt_line_and_disclaimer(self):
        text = _text(price=P, area=AREA)
        assert "免征" in text and "实际以税务局核定为准" in text, text
        assert "签约前请以当地最新政策为准" in text, text

    def test_report_matches_structured_items(self):
        out = _call(price=P, area=AREA)
        text = (out.get("tax_breakdown") or {}).get("report_text") or ""
        for name, item in _items(price=P, area=AREA).items():
            assert item["金额"] in text, (name, item["金额"], text)
        total = (out.get("tax_breakdown") or {}).get("total_tax_yuan")
        assert f"{total/10000:.2f}万元" in text, (total, text)

    def test_no_internal_terms_in_report(self):
        text = _text(price=P, area=AREA)
        for word in ("price", "area", "property_class", "original_price", "items",
                     "total_tax_yuan", "tax_breakdown_report", "参数"):
            assert word not in text, (word, text)


# ==================== ④ 描述与参数说明（F258） ====================

def test_description_documents_the_split_and_original_price():
    from tools.registry import registry

    entry = registry.get_entry("tax_breakdown_report")
    desc = entry.schema["description"]
    props = entry.schema["parameters"]["properties"]
    assert "税费计算器" in desc and "可直接转发客户" in desc, desc
    assert "original_price" in props, props
    assert "差额计税" in props["original_price"]["description"], props["original_price"]


# ==================== ⑤ 只读 + 框架层 ====================

def test_does_not_write(wired_db=None):
    """纯计算：不碰库（这里用一次调用 + 重复一致性代替，与 t62 的 ② 一致）"""
    first = _call(price=P, area=AREA)
    second = _call(price=P, area=AREA)
    assert json.dumps(first, sort_keys=True, ensure_ascii=False) == \
        json.dumps(second, sort_keys=True, ensure_ascii=False)


def test_dispatch_required_and_unknown():
    from tools.registry import registry

    out = json.loads(registry.dispatch("tax_breakdown_report", {}, session_id="t", task_id="t"))
    assert out.get("success") is not True and "price" in (out.get("error") or ""), out
    out2 = json.loads(registry.dispatch("tax_breakdown_report",
                                        {"price": P, "area": AREA, "period": "x"},
                                        session_id="t", task_id="t"))
    assert out2.get("success") is not True and "period" in (out2.get("error") or ""), out2
