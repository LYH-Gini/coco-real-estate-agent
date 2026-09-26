"""金额/单位展示（元 ↔ 万）的唯一实现（2026-09-25 收口）

此前 8 个文件里散着 49 处 `除以 10000` / 拼"万"，光 `_fmt_price` 就有 4 份副本
（房源/文案/海报/短视频），精度与空值文案各不相同 —— 改一处漏三处，实测踩过两次
（出租显示 `0万`、状态直出英文）。这里收成一份，调用方按需要传精度与空值文案。

刻意**不**收进来的（各自语境不同，别硬套）：
- `real_estate_calculator.py`：贷款额/税费/投资回报用的是"万元"表格口径，同一函数里
  精度本就分档（.0f/.1f/.2f），与"房源价格怎么说"不是一回事；
- `poster_svg._unit_price_text`：海报大字用"万/㎡"（≥1万时），与列表里的"元/㎡"刻意不同；
- `customer._display_change_value`：变更留痕要精确到 0.01 万并去掉尾零（`.2f` 去零），
  与"说给经纪人听"的口径不同。
"""
def fmt_wan(amount, digits=2, near_int=False):
    """元 → "万"口径文本。

    整数万省略小数（4000000 → `400万`）；非整万保留 digits 位（1850000 → `185.00万`）。
    near_int=True 用于海报大字标题：与整数万相差 <0.05 万时直接取整（29.96万 → `30万`），
    免得大字里出现"28.96万"这种零头。
    """
    amount = float(amount)
    wan = amount / 10000
    if near_int and abs(wan - round(wan)) < 0.05:
        return f"{round(wan):.0f}万"
    if wan == int(wan):
        return f"{wan:.0f}万"
    return f"{wan:.{digits}f}万"


def fmt_price(prop, digits=2, empty="未录入", near_int=False):
    """房源价格展示（系统存元）：出租 → `2500元/月`、出售 → `150万` / `28.37万`。

    prop 是房源 dict（至少含 price / property_type）。空值文案与精度由调用方指定：
    房源详情/组合用默认（未录入、两位小数），文案/海报/短视频用（价格待定、一位小数）。
    **0 与负数也按"未填"处理**（价格列 NOT NULL，没填价在库里就是 0）—— 不能说成"0万"。
    """
    price = prop.get("price")
    if price in (None, ""):
        return empty
    try:
        price = float(price)
    except (TypeError, ValueError):
        return empty
    if price <= 0:
        # 价格列是 NOT NULL，"没填价"在库里的实际形态是 0 —— 说成"0万"比不说更糟
        # （经纪人会当成"这套不要钱"，客户也会当真）
        return empty
    if prop.get("property_type") == "rental":
        return f"{price:.0f}元/月"
    return fmt_wan(price, digits, near_int)


def fmt_budget(value):
    """预算展示（系统存元）→ `300万-500万` 里的单值形态：≥1 万按万说、低于 1 万按元说。

    低于 1 万按元说：客户的租房预算常见 5000 元，说成"0.5万"（或早期 bug 的"0万"）反而难读。
    """
    if value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    if amount < 10000:
        return f"{amount:.0f}元"
    wan = amount / 10000
    return f"{wan:.0f}万" if wan == int(wan) else f"{wan:.1f}万"


def fmt_delta(amount, digits=1):
    """差额展示（带正负号）：涨价/降价各说一头 → `+15.0万` / `-15.0万`"""
    return f"{float(amount) / 10000:+.{digits}f}万"


def fmt_unit_price(value, digits=2):
    """单价展示（元/㎡）：统一两位小数（20000.0 → `20000.00`）"""
    return f"{float(value):.{digits}f}"
