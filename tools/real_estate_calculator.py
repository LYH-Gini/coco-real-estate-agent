"""
Coco 房产工具 - 金融计算器
贷款计算、税费计算、投资回报计算、贷款方案对比、税费明细单

金额单位说明（2026-09-25 同族收口批逐处判断过）：这里的"万"是**金融表格口径**，
与"房源价格怎么说"（`agent/real_estate_money.py`）不是一回事 —— 贷款额/利息/税额
同一个函数里精度本就分档（本金 .1f、税费 .2f、房价 .0f 带"元"字），且必须逐项对齐
表格列，所以**刻意不**复用 `fmt_price`/`fmt_wan`。
"""
import json
import re
from agent.real_estate_input import norm_money
from agent.real_estate_money import fmt_budget, fmt_wan
from tools.registry import registry


# ==================== 计算族共用的入参归一与校验（2026-09-26，第 54 项）====================
# 计算器没有数据库、没有列表，风险全在"**算错还回成功**"上：模型把 4.5% 写成 0.045、
# 首付把 30% 写成 30、金额写「400万」，旧实现里前两种**静默算出错答案**、后一种直接崩。
# 所以这一族统一：**认得出就归一（并在 note 里说明换算了什么），认不出就给中文提示，绝不猜着算**。

def _norm_money_arg(value, label='房价', sample='400万 记作 4000000'):
    """金额入参归一 → (元 或 None, 中文提示 或 None)；复用共用的 `norm_money`"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, f"{label}是空的：请给一个数字（如 {sample}）"
    if isinstance(value, bool):
        return None, f"{label}没能识别：收到的是「{value}」。请按元给数字（如 {sample}）"
    amount = norm_money(value)
    if amount is None:
        return None, f"{label}没能识别：收到的是「{value}」。请按元给数字（如 {sample}）"
    return amount, None


def _norm_rate_arg(value, label='年利率'):
    """年利率归一 → (百分数 或 None, 中文提示 或 None, 是否换算过)

    认 `4.5` 与 `4.5%`（都当百分数）。**`0.045` 这种小数写法给提示、不猜** ——
    真实贷款没有 0.045% 的利率，猜错一次就是给客户算错月供。
    """
    if value is None:
        return None, None, False
    text = str(value).strip().replace('％', '%')
    converted = False
    if text.endswith('%'):
        text, converted = text[:-1].strip(), True
    try:
        rate = float(text)
    except (TypeError, ValueError):
        return None, (f"{label}没能识别：收到的是「{value}」。"
                      f"请按百分数给（4.5% 写 4.5）"), False
    if rate <= 0:
        return None, f"{label}要大于 0：收到的是「{value}」", False
    if not converted and rate <= 0.5:
        return None, (f"{label}看着不对：收到的是「{value}」。"
                      f"请按百分数给（4.5% 写 4.5，别写 0.045）"), False
    return rate, None, converted


def _norm_ratio_arg(value, label='首付比例'):
    """首付比例归一 → (0-1 的比例 或 None, 中文提示 或 None, 是否换算过)

    认 `0.3`（比例）、`30`（百分数）、`30%`（带符号）。`30` 无歧义，归一成 30% 并在 note 里说明。
    """
    if value is None or value == '':
        return None, None, False
    text = str(value).strip().replace('％', '%')
    converted = False
    if text.endswith('%'):
        text, converted = text[:-1].strip(), True
    try:
        ratio = float(text)
    except (TypeError, ValueError):
        return None, (f"{label}没能识别：收到的是「{value}」。"
                      f"请写 0.3（=30%）或直接写 30"), False
    if converted or ratio > 1:
        # 百分数写法：30 → 30%；但首付低于 5% 不现实，多半是把别的数写进来了
        if not 5 <= ratio <= 100:
            return None, (f"{label}要在 0 和 1 之间：收到的是「{value}」"
                          f"（30% 写 0.3，也可直接写 30）"), False
        ratio, converted = ratio / 100, True
    if not 0.05 <= ratio < 1:
        return None, (f"{label}要在 0 和 1 之间：收到的是「{value}」"
                      f"（30% 写 0.3，也可直接写 30）"), False
    return ratio, None, converted


def _tax_assessment(price_yuan, area, hold_years, is_first_home, is_only_home, property_class,
                    original_price=None):
    """契税 / 增值税（及附加）/ 个人所得税 的**权威口径**（一处定义，两个工具共用）

    `tax_breakdown_report`（客户版税费明细单）与 `tax_calculator` 都调它 —— 免得"同一个场景两个工具两个数"
    （2026-09-26 F248–F250 就是这么来的：一个拿"买方首套"当"卖方唯一"、一个漏了非普宅档、一个按 5.6% 全额算增值税）。
    """
    ordinary = (property_class != "non_ordinary")
    full_two = hold_years >= 2
    full_five_only = hold_years >= 5 and is_only_home
    if is_first_home:
        deed_rate = 0.01 if area <= 90 else (0.015 if ordinary else 0.03)
    else:
        deed_rate = 0.03
    deed = price_yuan * deed_rate
    if full_two:
        if ordinary:
            vat, vat_note = 0.0, "满2年普通住宅免征"
        else:
            base = price_yuan - (original_price or 0)
            vat = base * 0.05 / 1.05
            vat_note = (f"满2年非普宅按差额(现价-原价{original_price/10000:.0f}万元)5%"
                        if original_price else "非普宅需提供原购入价按差额计税")
    else:
        vat, vat_note = price_yuan / 1.05 * 0.05, "未满2年全额5%（增值税及附加约5.6%口径内）"
    if full_five_only:
        personal, personal_note = 0.0, "满五唯一免征"
    elif hold_years >= 5:
        personal, personal_note = price_yuan * 0.01, "满五不唯一 → 差额20%或核定1%（本单按1%）"
    else:
        personal, personal_note = price_yuan * 0.01, "未满五年 → 差额20%或核定1%（本单按1%）"
    return {
        "ordinary": ordinary, "full_two": full_two, "full_five_only": full_five_only,
        "deed": deed, "deed_rate": deed_rate,
        "vat": vat, "vat_note": vat_note,
        "personal": personal, "personal_note": personal_note,
    }


def _norm_area_arg(value, label='面积'):
    """面积归一 → (㎡ 或 None, 中文提示 或 None)；认 `89` / `89.5` / `89平` / `89㎡` / `89平方米`"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, f"{label}是空的：请给平方米数字（如 89 或 89平）"
    text = str(value).strip().replace('平方米', '').replace('平米', '').replace('㎡', '') \
        .replace('平', '').replace('m2', '').replace('M2', '').replace(' ', '')
    try:
        area = float(text)
    except (TypeError, ValueError):
        return None, f"{label}没能识别：收到的是「{value}」。请给平方米数字（如 89 或 89平）"
    if area <= 0:
        return None, f"{label}要大于 0：收到的是「{value}」"
    return area, None


def _norm_years_value(value, label='持有年限', low=0, high=100):
    """年限归一（可带小数）→ (年 或 None, 中文提示 或 None)；认 `2` / `2.5` / `2年` / `两年`"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    raw = str(value).strip()
    cn = {'零': 0, '一': 1, '两': 2, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7,
          '八': 8, '九': 9, '十': 10}
    text = raw.replace('年', '').strip()
    try:
        years = float(text)
    except (TypeError, ValueError):
        years = float(cn.get(text, -1)) if text in cn else None
        if years is None or years < 0:
            return None, f"{label}没能识别：收到的是「{value}」。请给年数（如 2 或 2.5）"
    if not low <= years <= high:
        return None, f"{label}要在 {low} 到 {high} 年之间：收到的是「{value}」"
    return years, None


def _norm_bool_arg(value, label, true_words, false_words):
    """是/否类参数归一 → (bool 或 None, 中文提示 或 None)

    必须归一：`is_first_home='否'` 在 Python 里是"真"，旧实现会把它当**首套**算（契税按 1% 而不是 3%）。
    """
    if value is None:
        return None, None
    if isinstance(value, bool):
        return value, None
    text = str(value).strip().lower()
    if text in ('true', '1', 'yes', 'y'):
        return True, None
    if text in ('false', '0', 'no', 'n'):
        return False, None
    if text in true_words:
        return True, None
    if text in false_words:
        return False, None
    return None, (f"{label}没能识别：收到的是「{value}」。请说 是 或 否"
                  f"（也可以说 {'/'.join(true_words[:2])} / {'/'.join(false_words[:2])}）")


def _norm_property_class(value, label='住宅类型'):
    """普通/非普通住宅归一 → ('ordinary'/'non_ordinary' 或 None, 中文提示 或 None)"""
    if value is None:
        return None, None
    text = str(value).strip().lower()
    if text in ('ordinary', 'common', '普通', '普通住宅', '普宅', '是'):
        return 'ordinary', None
    if text in ('non_ordinary', 'non-ordinary', 'nonordinary', '非普通', '非普通住宅', '非普宅', '否'):
        return 'non_ordinary', None
    return None, f"{label}没能识别：收到的是「{value}」。请说 普通住宅(ordinary) 或 非普通住宅(non_ordinary)"


def _norm_growth_rate(value, label='预期年增值率', default=0.05):
    """预期年增值率归一 → (小数比例 或 None, 中文提示 或 None, 是否换写过)

    这个参数最容易被写错：按揭/税费那边利率是**百分数**（4.5 表示 4.5%），这里历史默认是**比例**（0.05）。
    实测传 `5`（想表达 5%）会被当成 500%/年 → 5 年后算出 311 亿的卖出价。规则：
    `|值| ≤ 1` 按比例、`1 < 值 ≤ 100` 按百分数（并在回执里说明换写了什么），超出范围给中文提示。
    """
    if value is None:
        return default, None, False
    text = str(value).strip().replace('％', '%')
    converted = False
    if text.endswith('%'):
        text, converted = text[:-1].strip(), True
    try:
        raw = float(text)
    except (TypeError, ValueError):
        return None, (f"{label}没能识别：收到的是「{value}」。"
                      f"请按百分数给（5% 写 5，也可写 0.05）"), False
    if converted or raw > 1:
        rate = raw / 100
        changed = True
    else:
        rate = raw
        changed = abs(raw - default) > 1e-9
    if not -1 < rate <= 1:
        return None, (f"{label}要在 −100% 到 100% 之间：收到的是「{value}」"), False
    return rate, None, changed


def _norm_years_list(value, label='贷款年限'):
    """年限列表归一 → (年限列表 或 None, 中文提示 或 None, 是否需要说明)

    认 `20,30` / `20，30`（中文逗号）/ `20年,30年` / `20、30`；每个年限都要在 1–40 年之间。
    乱值**不许静默退回默认**（原先 `abc` 会被悄悄换成 20,30）。
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return [20, 30], None, False
    raw = str(value)
    text = (raw.replace('，', ',').replace('、', ',').replace(';', ',').replace('；', ',')
               .replace('年', '').replace(' ', ''))
    years = []
    for part in text.split(','):
        if not part.strip():
            continue
        try:
            years.append(int(float(part.strip())))
        except ValueError:
            return None, (f"{label}没能识别：收到的是「{value}」。"
                          f"请写 20,30 这样的年限列表（每个 1–40 年）"), False
    if not years:
        return None, (f"{label}没能识别：收到的是「{value}」。"
                      f"请写 20,30 这样的年限列表（每个 1–40 年）"), False
    if any(not 1 <= y <= 40 for y in years):
        return None, f"{label}要在 1 到 40 年之间：收到的是「{value}」", False
    years = sorted(set(years))
    converted = raw.strip() != ','.join(str(y) for y in years)
    return years, None, converted


def _norm_years_arg(value, label='贷款年限'):
    """贷款年限归一 → (年 或 None, 中文提示 或 None, 是否换算过)；认 `30` 与 `30年`"""
    if value is None or value == '':
        return None, None, False
    raw = str(value).strip()
    converted = '年' in raw
    try:
        years = int(float(raw.replace('年', '')))
    except (TypeError, ValueError):
        return None, f"{label}没能识别：收到的是「{value}」。请写 30 或 30年", False
    if not 1 <= years <= 40:
        return None, f"{label}要在 1 到 40 年之间：收到的是「{value}」", False
    return years, None, converted


def mortgage_calculator(
    price: float,
    down_payment_ratio: float = 0.3,
    loan_years: int = 30,
    interest_rate: float = 4.5,
    method: str = "equal_installment",
    task_id: str = None,
) -> str:
    """
    贷款计算器

    参数:
        price: 房价（元，如 400万=4000000；也认「400万」「4,000,000」）
        down_payment_ratio: 首付比例（0.3 = 30%；也可直接写 30 或「30%」）
        loan_years: 贷款年限（30 或「30年」，1–40 年）
        interest_rate: 年利率（百分数，4.5 表示 4.5%；也可写「4.5%」）
        method: 还款方式 equal_installment(等额本息) / equal_principal(等额本金)

    认得出的写法会归一并在 `note` 里说明；认不出（或越界）一律中文提示、**不猜着算**。
    """
    price_yuan, problem = _norm_money_arg(price, '房价')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if price_yuan <= 0:
        return json.dumps({"success": False, "error": (
            f"房价要大于 0：收到的是「{price}」")}, ensure_ascii=False)
    ratio, problem, ratio_converted = _norm_ratio_arg(down_payment_ratio)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if ratio is None:
        ratio = 0.3
    years, problem, years_converted = _norm_years_arg(loan_years)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if years is None:
        years = 30
    rate, problem, rate_converted = _norm_rate_arg(interest_rate)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if rate is None:
        rate = 4.5
    if method not in ("equal_installment", "equal_principal"):
        return json.dumps({"success": False, "error": (
            f"还款方式没能识别：收到的是「{method}」。可以说 等额本息(equal_installment) 或 "
            f"等额本金(equal_principal)")}, ensure_ascii=False)

    down_payment = price_yuan * ratio
    loan_amount = price_yuan - down_payment
    if loan_amount <= 0:
        return json.dumps({"success": False, "error": (
            f"首付已经覆盖整套房价（首付 {ratio * 100:g}%），没有贷款可算："
            f"首付比例要在 0 和 1 之间")}, ensure_ascii=False)

    # 月利率
    monthly_rate = rate / 100 / 12
    total_months = years * 12

    if method == "equal_installment":
        # 等额本息
        monthly_payment = loan_amount * monthly_rate * (1 + monthly_rate) ** total_months / ((1 + monthly_rate) ** total_months - 1)
        total_payment = monthly_payment * total_months
        total_interest = total_payment - loan_amount
    else:
        # 等额本金
        monthly_principal = loan_amount / total_months
        first_month_payment = monthly_principal + loan_amount * monthly_rate
        last_month_payment = monthly_principal + monthly_principal * monthly_rate
        total_payment = (first_month_payment + last_month_payment) * total_months / 2
        total_interest = total_payment - loan_amount
        monthly_payment = f"{first_month_payment:.2f} - {last_month_payment:.2f}"

    result = {
        "房价": f"{price_yuan/10000:.0f}万元",
        "首付比例": f"{ratio*100:g}%",
        "首付金额": f"{down_payment/10000:.2f}万元",
        "贷款金额": f"{loan_amount/10000:.2f}万元",
        "贷款年限": f"{years}年",
        "年利率": f"{rate}%",
        "还款方式": "等额本息" if method == "equal_installment" else "等额本金",
        "月供": f"{monthly_payment:.2f}元" if isinstance(monthly_payment, float) else monthly_payment,
        "总还款额": f"{total_payment/10000:.2f}万元",
        "总利息": f"{total_interest/10000:.2f}万元",
    }

    notes = []
    if ratio_converted:
        notes.append(f"首付比例「{down_payment_ratio}」我按 {ratio*100:g}% 算的")
    if years_converted:
        notes.append(f"贷款年限「{loan_years}」我按 {years} 年算的")
    if rate_converted:
        notes.append(f"年利率「{interest_rate}」我按 {rate}% 算的")
    payload = {"success": True, "calculator": result}
    if notes:
        payload["note"] = "；".join(notes)
    return json.dumps(payload, ensure_ascii=False)


def tax_calculator(
    price: float,
    area: float,
    is_first_home: bool = True,
    is_only_home: bool = True,
    hold_years: float = 2,
    property_class: str = "ordinary",
    original_price: float = None,
    task_id: str = None,
) -> str:
    """
    税费计算器

    参数:
        price: 房价（元，如 400万=4000000；也认「400万」）
        area: 面积（㎡，如 89 或「89平」）
        is_first_home: 买方是否首套（是/否）
        is_only_home: 卖方是否唯一住房（满五唯一免个税的关键）
        hold_years: 房产证持有年限（年，可小数如 1.5）
        property_class: ordinary(普通住宅) / non_ordinary(非普通住宅)
        original_price: 卖方原购入价（元），非普通住宅满 2 年按差额计税要用；不给就按现价估算并说明

    口径与「税费明细单」（客户版）**共用一处**（`_tax_assessment`），两个出口不会给出两个数。
    """
    price_yuan, problem = _norm_money_arg(price, '房价')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if price_yuan <= 0:
        return json.dumps({"success": False, "error": (
            f"房价要大于 0：收到的是「{price}」")}, ensure_ascii=False)
    area_value, problem = _norm_area_arg(area)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    hold_years_value, problem = _norm_years_value(hold_years)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if hold_years_value is None:
        hold_years_value = 2
    first_home, problem = _norm_bool_arg(is_first_home, '买方是否首套',
                                         ('是', '首套', '首套房', '首套住房'),
                                         ('否', '不是', '二套', '二套房'))
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if first_home is None:
        first_home = True
    only_home, problem = _norm_bool_arg(is_only_home, '卖方是否唯一住房',
                                        ('是', '唯一', '唯一住房'), ('否', '不唯一', '非唯一'))
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if only_home is None:
        only_home = True
    pclass, problem = _norm_property_class(property_class)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if pclass is None:
        pclass = 'ordinary'
    # 可选参数：没给就是"没给"（`_norm_money_arg` 对必填项才把 None 当"空值"报错）
    original_value = None
    if original_price is not None and str(original_price).strip() != '':
        original_value, problem = _norm_money_arg(original_price, '卖方原购入价')
        if problem:
            return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if original_value is not None and original_value <= 0:
        return json.dumps({"success": False, "error": (
            f"卖方原购入价要大于 0：收到的是「{original_price}」"
            f"（不给就按现价估算，回执里会说明）")}, ensure_ascii=False)

    rules = _tax_assessment(price_yuan, area_value, hold_years_value, first_home, only_home, pclass,
                            original_price=original_value)
    total_tax = rules["deed"] + rules["vat"] + rules["personal"]

    result = {
        "房价": f"{price_yuan/10000:.2f}万元",
        "面积": f"{area_value:g}㎡",
        "是否首套": "是" if first_home else "否",
        "是否唯一": "是" if only_home else "否",
        "持有年限": f"{hold_years_value:g}年",
        "住宅类型": "普通住宅" if rules["ordinary"] else "非普通住宅",
        "契税": f"{rules['deed']/10000:.2f}万元 ({rules['deed_rate']*100:.1f}%)",
        "增值税": f"{rules['vat']/10000:.2f}万元" if rules["vat"] > 0 else "免征",
        "个人所得税": f"{rules['personal']/10000:.2f}万元" if rules["personal"] > 0 else "免征",
        "税费合计": f"{total_tax/10000:.2f}万元",
    }
    notes = []
    if rules["vat"] <= 0:
        notes.append(f"增值税：{rules['vat_note']}")
    else:
        notes.append(f"增值税：{rules['vat_note']}")
    notes.append(f"个人所得税：{rules['personal_note']}")
    if original_value is not None:
        result["卖方原购入价"] = f"{original_value/10000:.2f}万元"
    if not rules["ordinary"] and rules["full_two"] and original_value is None:
        notes.append("非普通住宅满 2 年按差额计税，这里没给卖方原购入价、按现价全额估算；"
                     "要精确就给我原购入价，或用税费明细单")
    payload = {"success": True, "calculator": result, "note": "；".join(notes)}
    return json.dumps(payload, ensure_ascii=False)


def loan_compare(
    price: float,
    down_payment_ratio: float = 0.3,
    loan_years_list: str = "20,30",
    commercial_rate: float = None,
    provident_fund_rate: float = 2.85,
    provident_fund_loan_amount: float = None,
    city: str = None,
    task_id: str = None,
) -> str:
    """
    贷款方案对比器：一次输出 商贷/组合贷/等额本金 多方案对比表

    参数:
        price: 房价（元，如 400万=4000000）
        down_payment_ratio: 首付比例（默认0.3）
        loan_years_list: 商贷年限列表，逗号分隔（默认"20,30"）
        commercial_rate: 商贷年利率%（不传则按 city 查政策库，查不到用 3.6%）
        provident_fund_rate: 公积金年利率%（默认2.85%）
        provident_fund_loan_amount: 公积金贷款额度（元），传了才出组合贷方案
        city: 城市名（用于查询当地政策利率）
    """
    price_yuan, problem = _norm_money_arg(price, '房价')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if price_yuan <= 0:
        return json.dumps({"success": False, "error": (
            f"房价要大于 0：收到的是「{price}」")}, ensure_ascii=False)
    ratio, problem, ratio_converted = _norm_ratio_arg(down_payment_ratio)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if ratio is None:
        ratio = 0.3
    years, problem, years_converted = _norm_years_list(loan_years_list)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    notes = []
    if ratio_converted:
        notes.append(f"首付比例「{down_payment_ratio}」我按 {ratio*100:g}% 算的")
    if years_converted:
        notes.append(f"贷款年限列表「{loan_years_list}」我按 {years} 算的")

    down_payment = price_yuan * ratio
    loan_amount = price_yuan - down_payment
    if loan_amount <= 0:
        return json.dumps({"success": False, "error": (
            f"首付已经覆盖整套房价（首付 {ratio * 100:g}%），没有贷款可算："
            f"首付比例要在 0 和 1 之间")}, ensure_ascii=False)

    # 利率：显式传入 > 政策库查询 > 默认值（都过同一套归一，小数写法一律给提示）
    if commercial_rate is None:
        commercial_rate, rate_source = 3.6, "没给利率，按常见商贷 3.6% 算的"
        if city:
            try:
                from tools.real_estate_policy import get_loan_policy
                raw = get_loan_policy(city=city, policy_type="贷款利率")
                data = json.loads(raw)
                text = json.dumps(data, ensure_ascii=False)
                m = re.search(r"(\d+\.?\d*)\s*%", text.replace("：", ":"))
                if m:
                    commercial_rate = float(m.group(1))
                    rate_source = f"按{city}的政策库查到 {commercial_rate}%"
            except Exception:
                pass
    else:
        commercial_rate, problem, _c = _norm_rate_arg(commercial_rate, '商贷年利率')
        if problem:
            return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
        rate_source = f"你说的是 {commercial_rate}%"
    provident_fund_rate, problem, _c = _norm_rate_arg(provident_fund_rate, '公积金年利率')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if provident_fund_rate is None:
        provident_fund_rate = 2.85
    if provident_fund_loan_amount is not None:
        provident_fund_loan_amount, problem = _norm_money_arg(provident_fund_loan_amount,
                                                              '公积金贷款额度')
        if problem:
            return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
        if provident_fund_loan_amount > loan_amount:
            notes.append(f"公积金贷款额度 {fmt_wan(provident_fund_loan_amount)} 超过了贷款总额 "
                         f"{fmt_wan(loan_amount)}，我按 {fmt_wan(loan_amount)} 算的")

    def installment_monthly(amount, annual_rate, months):
        mr = annual_rate / 100 / 12
        return amount * mr * (1 + mr) ** months / ((1 + mr) ** months - 1)

    def principal_monthly_first(amount, annual_rate, months):
        mr = annual_rate / 100 / 12
        return amount / months + amount * mr

    plans = []
    # 方案1..N：等额本息 各年限
    for y in years:
        months = y * 12
        mp = installment_monthly(loan_amount, commercial_rate, months)
        total_interest = mp * months - loan_amount
        plans.append({
            "方案": f"纯商贷 {y}年 等额本息",
            "贷款额": f"{loan_amount/10000:.2f}万元",
            "年利率": f"{commercial_rate}%",
            "月供": f"{mp:,.2f}元",
            "总利息": f"{total_interest/10000:.2f}万元",
            "适合人群": f"月供压力要小、打算长期还款" if y >= 30 else "利息总额与月供的平衡",
        })

    # 组合贷方案（按最长年限算）
    if provident_fund_loan_amount and provident_fund_loan_amount > 0:
        gf = min(provident_fund_loan_amount, loan_amount)
        comm = loan_amount - gf
        y = max(years)
        months = y * 12
        mp = installment_monthly(gf, provident_fund_rate, months) + \
            installment_monthly(comm, commercial_rate, months)
        total_interest = (mp * months) - loan_amount
        # 公积金额度覆盖全部贷款额时其实没有商贷部分 —— 别叫「组合贷(公积金X万+商贷0万)」（2026-09-26 点头改）
        if comm <= 0:
            plan_title = f"纯公积金贷 {fmt_wan(gf)} {y}年 等额本息"
            plan_rate = f"公积金{provident_fund_rate}%"
        else:
            plan_title = f"组合贷(公积金{gf/10000:.0f}万+商贷{comm/10000:.0f}万) {y}年 等额本息"
            plan_rate = f"公积金{provident_fund_rate}%+商贷{commercial_rate}%"
        plans.insert(0, {
            "方案": plan_title,
            "贷款额": f"{loan_amount/10000:.2f}万元",
            "年利率": plan_rate,
            "月供": f"{mp:,.2f}元",
            "总利息": f"{total_interest/10000:.2f}万元",
            "适合人群": "有公积金额度，想省利息",
        })

    # 等额本金方案（按最长年限）
    y = max(years)
    months = y * 12
    first_mp = principal_monthly_first(loan_amount, commercial_rate, months)
    total_interest_principal = loan_amount * commercial_rate / 100 * (months + 1) / 2 / 12
    plans.append({
        "方案": f"纯商贷 {y}年 等额本金",
        "贷款额": f"{loan_amount/10000:.2f}万元",
        "年利率": f"{commercial_rate}%",
        "月供": f"首月{first_mp:,.2f}元逐月递减",
        "总利息": f"{total_interest_principal/10000:.2f}万元",
        "适合人群": "前期还款能力强、打算提前还款",
    })

    tips = [
        "提前还款：多数银行放款满1年后才允许，部分有违约金，签字前确认",
        "利率重定价：LPR浮动利率每年1月1日或放款日重定价，降息月供会变",
        "公积金贷款：额度上限、缴存年限要求各城市不同，建议先查当地政策",
    ]

    result = {
        "房价": f"{price_yuan/10000:.2f}万元",
        "首付": f"{ratio*100:g}% = {fmt_wan(down_payment)}",
        "贷款总额": f"{loan_amount/10000:.2f}万元",
        "商贷利率来源": rate_source,
        "方案对比": plans,
        "温馨提示": tips,
    }
    payload = {"success": True, "loan_compare": result}
    if notes:
        payload["note"] = "；".join(notes)
    return json.dumps(payload, ensure_ascii=False)


def tax_breakdown_report(
    price: float,
    area: float,
    hold_years: float = 2,
    is_first_home: bool = True,
    is_only_home: bool = True,
    property_class: str = "ordinary",
    original_price: float = None,
    city: str = None,
    task_id: str = None,
) -> str:
    """
    客户版税费明细单：满二/满五唯一、首套/二套、普宅/非普宅联动判定

    参数:
        price: 成交价（元）
        area: 面积（㎡）
        hold_years: 房产证持有年限（可传小数，如1.5）
        is_first_home: 买方是否首套
        is_only_home: 卖方是否唯一住房（满五唯一免个税的关键）
        property_class: ordinary(普通住宅)/non_ordinary(非普通住宅)
        original_price: 卖方原购入价（元），非普宅/满二差额计税用
        city: 城市（展示政策依据用）
    """
    price_yuan, problem = _norm_money_arg(price, '成交价')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if price_yuan <= 0:
        return json.dumps({"success": False, "error": (
            f"房价要大于 0：收到的是「{price}」")}, ensure_ascii=False)
    area_value, problem = _norm_area_arg(area)
    if problem:
        # 分字段说（原先房价 0 与面积 0 都回「房价和面积需>0」，看不出是哪一个）
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    area = area_value
    hold_years_value, problem = _norm_years_value(hold_years)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    hold_years = 2 if hold_years_value is None else hold_years_value
    first_home, problem = _norm_bool_arg(is_first_home, '买方是否首套',
                                         ('是', '首套', '首套房', '首套住房'),
                                         ('否', '不是', '二套', '二套房'))
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    is_first_home = True if first_home is None else first_home
    only_home, problem = _norm_bool_arg(is_only_home, '卖方是否唯一住房',
                                        ('是', '唯一', '唯一住房'), ('否', '不唯一', '非唯一'))
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    is_only_home = True if only_home is None else only_home
    pclass, problem = _norm_property_class(property_class)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    property_class = 'ordinary' if pclass is None else pclass

    # 卖方原购入价：归一 + 坏值同一句提示（与税calculator 一致；原先 0/负数会被静默按"没给"算，
    # 负数还会把差额算得更大）
    original_value = None
    if original_price is not None and str(original_price).strip() != '':
        original_value, problem = _norm_money_arg(original_price, '卖方原购入价')
        if problem:
            return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
        if original_value <= 0:
            return json.dumps({"success": False, "error": (
                f"卖方原购入价要大于 0：收到的是「{original_price}」"
                f"（不给就按现价估算，清单里会说明）")}, ensure_ascii=False)

    # 口径与 tax_calculator 共用一处（`_tax_assessment`）——两个出口不能再出现两个数
    rules = _tax_assessment(price_yuan, area, hold_years, is_first_home, is_only_home,
                            property_class, original_price=original_value)
    ordinary, full_two, full_five_only = rules["ordinary"], rules["full_two"], rules["full_five_only"]
    deed, vat, personal_tax = rules["deed"], rules["vat"], rules["personal"]

    items = []

    # 1. 契税（买方）
    items.append({
        "税目": "契税", "承担": "买方",
        "金额": f"{deed/10000:.2f}万元 ({rules['deed_rate']*100:.1f}%)",
        "依据": f"{'首套' if is_first_home else '二套'}+{area:g}㎡ → 税率{rules['deed_rate']*100:.1f}%",
    })

    # 2. 增值税（卖方，常转嫁买方）
    items.append({
        "税目": "增值税及附加", "承担": "卖方(常转嫁)",
        "金额": f"{vat/10000:.2f}万元" if vat > 0 else "免征",
        "依据": rules["vat_note"],
    })

    # 3. 个人所得税（卖方）
    items.append({
        "税目": "个人所得税", "承担": "卖方(常转嫁)",
        "金额": f"{personal_tax/10000:.2f}万元" if personal_tax > 0 else "免征",
        "依据": rules["personal_note"],
    })

    total = deed + vat + personal_tax
    lines = ["📋 税费明细单（客户版）", "=" * 32,
             f"成交价: {price_yuan/10000:.2f}万元 | {area:g}㎡ | "
             f"{city or ''}{'（普宅）' if ordinary else '（非普宅）'}",
             f"房本: 满{hold_years:.0f}年{'且唯一' if is_only_home else '非唯一'} | 买方{'首套' if is_first_home else '二套'}",
             "-" * 32]
    for it in items:
        # 承担那一栏原来是「卖方(常转嫁)」—— 嵌在中文括号里读着别扭，渲染时换成中文逗号
        bearer = str(it['承担']).replace('(', '，').replace(')', '')
        lines.append(f"{it['税目']}（{bearer}）: {it['金额']}")
        lines.append(f"  └ {it['依据']}")
    lines.append("-" * 32)
    lines.append(f"税费合计: {total/10000:.2f}万元（实际以税务局核定为准）")
    lines.append("⚠️ 各地政策有差异且随时调整，本清单仅供参考，签约前请以当地最新政策为准。")

    return json.dumps({
        "success": True,
        "tax_breakdown": {
            "city": city, "items": items,
            "total_tax_yuan": round(total, 2),
            "report_text": "\n".join(lines),
        },
    }, ensure_ascii=False)


def roi_calculator(
    price: float,
    monthly_rent: float,
    hold_years: int = 5,
    expected_appreciation: float = 0.05,
    vacancy_months: float = 0,
    property_fee_monthly: float = 0,
    loan_monthly_payment: float = 0,
    task_id: str = None,
) -> str:
    """
    投资回报率计算器

    参数:
        price: 购入价（元，如 400万=4000000；也认「400万」）
        monthly_rent: 月租金（元，如 5000，也认「5000元/月」）
        hold_years: 持有年限（1–40 年）
        expected_appreciation: 预期年增值率（按百分数给：5% 写 5，也可写 0.05 比例）
        vacancy_months: 每年空置月数（0–12），默认 0；给了就算净口径
        property_fee_monthly: 每月物业费（元），默认 0
        loan_monthly_payment: 每月还贷额（元，含本金与利息的现金流），默认 0

    口径：`总收益/总回报率` 只算租金 + 升值（毛）；给了上面三项任一时另出**净口径**字段
    （扣空置、物业费与月供），并在 `note` 里说清。
    """
    price_yuan, problem = _norm_money_arg(price, '购入价')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if price_yuan <= 0:
        return json.dumps({"success": False, "error": (
            f"购入价要大于 0：收到的是「{price}」")}, ensure_ascii=False)
    rent_value, problem = _norm_money_arg(monthly_rent, '月租金', '5000 或「5000元/月」')
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if rent_value < 0:
        return json.dumps({"success": False, "error": (
            f"月租金不能是负数：收到的是「{monthly_rent}」")}, ensure_ascii=False)
    years_value, problem = _norm_years_value(hold_years, '持有年限', low=1, high=40)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if years_value is None:
        years_value = 5
    appr, problem, appr_changed = _norm_growth_rate(expected_appreciation)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    vacancy, problem = _norm_years_value(vacancy_months, '每年空置月数', low=0, high=12)
    if problem:
        return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    fee_value = loan_value = None
    if property_fee_monthly is not None and str(property_fee_monthly).strip() != '':
        fee_value, problem = _norm_money_arg(property_fee_monthly, '每月物业费')
        if problem:
            return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    if loan_monthly_payment is not None and str(loan_monthly_payment).strip() != '':
        loan_value, problem = _norm_money_arg(loan_monthly_payment, '每月还贷额')
        if problem:
            return json.dumps({"success": False, "error": problem}, ensure_ascii=False)
    for _v, _label, _raw in ((fee_value, '每月物业费', property_fee_monthly),
                             (loan_value, '每月还贷额', loan_monthly_payment)):
        if _v is not None and _v < 0:
            return json.dumps({"success": False, "error": (
                f"{_label}不能是负数：收到的是「{_raw}」")}, ensure_ascii=False)
    vacancy = vacancy or 0
    fee_value = fee_value or 0
    loan_value = loan_value or 0

    # 租金回报（毛）
    annual_rent = rent_value * 12
    gross_rental_yield = annual_rent / price_yuan * 100

    # 升值收益
    future_price = price_yuan * (1 + appr) ** years_value
    appreciation_gain = future_price - price_yuan

    # 总收益（毛口径：租金 + 升值）
    total_gain = annual_rent * years_value + appreciation_gain
    total_roi = total_gain / price_yuan * 100
    annual_roi = ((1 + total_roi / 100) ** (1 / years_value) - 1) * 100

    result = {
        "购入价": f"{price_yuan/10000:.2f}万元",
        "月租金": f"{rent_value:.0f}元/月",
        "持有年限": f"{years_value:g}年",
        "预期年增值率": f"{appr*100:g}%",
        "年租金收入": f"{annual_rent:.0f}元",
        "毛租金回报率": f"{gross_rental_yield:.2f}%",
        "预期卖出价": f"{future_price/10000:.2f}万元",
        "升值收益": f"{appreciation_gain/10000:.2f}万元",
        "总收益": f"{total_gain/10000:.2f}万元",
        "总回报率": f"{total_roi:.2f}%",
        "年化回报率": f"{annual_roi:.2f}%",
    }
    notes = []
    if appr_changed:
        notes.append(f"预期年增值率「{expected_appreciation}」我按 {appr*100:g}% 算的")

    # 净口径（只在给了空置/物业费/月供任一项时给，别把毛口径的字段改掉）
    if vacancy or fee_value or loan_value:
        effective_annual_rent = rent_value * (12 - vacancy)
        annual_cost = (fee_value + loan_value) * 12
        net_gain = effective_annual_rent * years_value + appreciation_gain - annual_cost * years_value
        net_total_roi = net_gain / price_yuan * 100
        net_annual_roi = ((1 + net_total_roi / 100) ** (1 / years_value) - 1) * 100
        result["有效年租金"] = f"{effective_annual_rent:.0f}元"
        result["年持有成本"] = f"{annual_cost:.0f}元"
        result["净收益"] = f"{net_gain/10000:.2f}万元"
        result["净租金回报率"] = f"{effective_annual_rent / price_yuan * 100:.2f}%"
        result["净总回报率"] = f"{net_total_roi:.2f}%"
        result["净年化回报率"] = f"{net_annual_roi:.2f}%"
        notes.append(f"净口径：按每年空置 {vacancy:g} 个月、每月物业费 {fmt_budget(fee_value)}、"
                     f"每月还贷 {fmt_budget(loan_value)} 扣算")
    notes.append("投资回报口径：「总收益/总回报率」只算租金收入 + 房价增值，未扣税费与空置；"
                 "「年化回报率」是按总回报率折算的年化近似值（不是 IRR），实际到手以现金为准")
    payload = {"success": True, "calculator": result, "note": "；".join(notes)}
    return json.dumps(payload, ensure_ascii=False)


# 工具注册
TOOLS = [
    {
        "name": "mortgage_calculator",
        "description": (
            "贷款计算器：按房价、首付比例、贷款年限、年利率算月供与总利息，支持等额本息（equal_installment）"
            "与等额本金（equal_principal）两种还款方式。金额按元（400万 记作 4000000，也认「400万」）；"
            "年利率按百分数（4.5% 写 4.5）；首付比例写 0.3（=30%），也可直接写 30。"
            "返回首付金额、贷款金额、月供、总还款额与总利息；写法被换算时会在 note 里说明。"),
        "parameters": {
            "type": "object",
            "properties": {
                "price": {"type": "number", "description": "房价（元）：可写 4000000，也可写「400万」；必须大于 0"},
                "down_payment_ratio": {"type": "number", "description": "首付比例：0.3 表示 30%，也可直接写 30（=30%）；必须大于 0 且小于 1"},
                "loan_years": {"type": "integer", "description": "贷款年限：写 30 或「30年」，1 到 40 年"},
                "interest_rate": {"type": "number", "description": "年利率（百分数）：4.5 表示 4.5%，也可写「4.5%」；必须大于 0"},
                "method": {"type": "string", "enum": ["equal_installment", "equal_principal"], "description": "还款方式：equal_installment(等额本息，每月还款额固定) / equal_principal(等额本金，每月递减)"},
            },
            "required": ["price"],
        },
        "handler": lambda args, **kw: mortgage_calculator(**args),
    },
    {
        "name": "tax_calculator",
        "description": (
            "税费计算器：按房价、面积、买方是否首套、卖方是否唯一、持有年限、普通/非普通住宅，"
            "算契税、增值税与个人所得税，给出各税种金额与税费合计。金额按元（400万 记作 4000000，也认「400万」）；"
            "面积按㎡（写 89 或「89平」）。要出可直接转发客户的税费明细单（带政策依据），用税费明细单。"),
        "parameters": {
            "type": "object",
            "properties": {
                "price": {"type": "number", "description": "房价（元）：可写 4000000，也可写「400万」；必须大于 0"},
                "area": {"type": "number", "description": "面积（㎡）：写 89 或「89平」；必须大于 0"},
                "is_first_home": {"type": "boolean", "description": "买方是否首套（是/否）：首套契税 1%（≤90㎡）或 1.5%（>90㎡，非普宅 3%），二套 3%"},
                "is_only_home": {"type": "boolean", "description": "卖方是否唯一住房（是/否）：满五年且唯一才免个人所得税"},
                "hold_years": {"type": "number", "description": "房产证持有年限（年，可写小数如 1.5）：满 2 年免增值税，满 5 年且唯一免个税"},
                "property_class": {"type": "string", "enum": ["ordinary", "non_ordinary"], "description": "普通住宅(ordinary) / 非普通住宅(non_ordinary)：非普宅且面积>90㎡ 时首套契税按 3%"},
            "original_price": {"type": "number", "description": "卖方原购入价（元）：非普通住宅满 2 年按差额计税要用；不给就按现价估算并在 note 里说明"},
            },
            "required": ["price", "area"],
        },
        "handler": lambda args, **kw: tax_calculator(**args),
    },
    {
        "name": "loan_compare",
        "description": (
            "贷款方案对比器：一次给出纯商贷（各年限等额本息）、组合贷（有公积金额度时）、"
            "纯商贷等额本金的多方案对比（贷款额、年利率、月供、总利息、适合人群），可直接转发客户。"
            "金额按元（400万 记作 4000000，也认「400万」）；年利率按百分数（4.5% 写 4.5）；"
            "首付比例写 0.3（=30%），也可直接写 30；年限列表写「20,30」。写法被换算时会用 note 说明。"),
        "parameters": {
            "type": "object",
            "properties": {
                "price": {"type": "number", "description": "房价（元）：可写 4000000，也可写「400万」；必须大于 0"},
                "down_payment_ratio": {"type": "number", "description": "首付比例：0.3 表示 30%，也可直接写 30（=30%）；必须大于 0 且小于 1"},
                "loan_years_list": {"type": "string", "description": "商贷年限列表：如「20,30」（也认「20年,30年」中文逗号），每个 1–40 年；不传按 20,30"},
                "commercial_rate": {"type": "number", "description": "商贷年利率（百分数）：4.5 表示 4.5%；不传则按城市政策库查、查不到用 3.6%"},
                "provident_fund_rate": {"type": "number", "description": "公积金年利率（百分数，默认 2.85）"},
                "provident_fund_loan_amount": {"type": "number", "description": "公积金贷款额度（元）；传了才出组合贷方案，超过贷款总额时按贷款总额算并说明"},
                "city": {"type": "string", "description": "城市名（不给商贷利率时用来查当地政策）"},
            },
            "required": ["price"],
        },
        "handler": lambda args, **kw: loan_compare(**args),
    },
    {
        "name": "tax_breakdown_report",
        "description": (
            "税费明细单（客户版）：按成交价、面积、买卖双方情况（首套/唯一）、房本年限、普通/非普通住宅，"
            "逐项算出契税、增值税及附加、个人所得税并给出政策依据，生成可直接转发客户的清单；"
            "只要一个总额、不向客户展示明细时用税费计算器。"),
        "parameters": {
            "type": "object",
            "properties": {
                "price": {"type": "number", "description": "成交价（元）"},
                "area": {"type": "number", "description": "面积（㎡）"},
                "hold_years": {"type": "number", "description": "房本持有年限（可传小数如1.5）"},
                "is_first_home": {"type": "boolean", "description": "买方是否首套"},
                "is_only_home": {"type": "boolean", "description": "卖方是否唯一住房"},
                "property_class": {"type": "string", "enum": ["ordinary", "non_ordinary"], "description": "普通/非普通住宅"},
                "original_price": {"type": "number", "description": "卖方原购入价（元），非普宅差额计税用"},
                "city": {"type": "string", "description": "城市"},
            },
            "required": ["price", "area"],
        },
        "handler": lambda args, **kw: tax_breakdown_report(**args),
    },
    {
        "name": "roi_calculator",
        "description": (
            "投资回报率计算器：按购入价、月租金、持有年限、预期年升值率，算毛租金回报率、升值收益、总收益、"
            "总回报率与年化回报率。金额按元（400万 记作 4000000，也认「400万」）；月租金写月租（如 5000，"
            "也认「5000元/月」）；预期年增值率按百分数（5% 写 5，也可写 0.05）。"
            "口径：总收益与总回报率只算租金与升值，未扣税费、物业费、空置与贷款利息；"
            "年化回报率是按总回报率折算的近似值。给了空置月数/物业费/每月还贷任一项时，另给净收益与净回报率。"),
        "parameters": {
            "type": "object",
            "properties": {
                "price": {"type": "number", "description": "购入价（元）：可写 4000000，也可写「400万」；必须大于 0"},
                "monthly_rent": {"type": "number", "description": "月租金（元）：写 5000，也认「5000元/月」；不能是负数"},
                "hold_years": {"type": "number", "description": "持有年限（1 到 40 年，默认 5）"},
                "expected_appreciation": {"type": "number", "description": "预期年增值率（百分数）：5% 写 5，也可写 0.05 比例；范围 −100% 到 100%"},
                "vacancy_months": {"type": "number", "description": "每年空置月数（0–12，默认 0）：给了就算净口径"},
                "property_fee_monthly": {"type": "number", "description": "每月物业费（元，默认 0）：参与净口径"},
                "loan_monthly_payment": {"type": "number", "description": "每月还贷额（元，默认 0，按现金流含本金与利息）：参与净口径"},
            },
            "required": ["price", "monthly_rent"],
        },
        "handler": lambda args, **kw: roi_calculator(**args),
    },
]

for tool in TOOLS:
    registry.register(
        name=tool["name"],
        toolset="real_estate",
        schema={"name": tool["name"], "description": tool["description"], "parameters": tool["parameters"]},
        handler=tool["handler"],
    )
