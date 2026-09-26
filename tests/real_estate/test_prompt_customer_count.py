"""系统提示词的客户数口径必须指向 total（2026-09-24）

背景：`list_customers` 的 `count` 现在是"本次返回条数"（上限 200），**真实总数在 `total`**。
提示词原先三处教 Coco"先调 `list_customers(limit=10000)` 拿到真实 count 再汇报"——
客户超过 200 位时它会报错数（大库上把 1.2 万位说成 200 位），而且 `limit=10000` 这种
"一次拉全量"的写法本身就是撑爆上下文的隐患。

本文件钉住两条契约：提示词里不得再出现全量拉取写法；客户数口径要点到 total 字段。
"""
from agent.real_estate_prompt import get_real_estate_prompt


def test_prompt_never_asks_for_a_full_dump():
    prompt = get_real_estate_prompt()
    assert "limit=10000" not in prompt, "提示词仍在要求一次性拉全量客户（应改为 list_customers() + total）"


def test_customer_count_sections_point_to_total_field():
    prompt = get_real_estate_prompt()
    assert prompt.count("total") >= 4, f"客户数口径应多处点到 total，实际 {prompt.count('total')} 处"


def test_no_section_calls_count_the_customer_total():
    # count 可以出现（它确实是返回字段），但不能被描述成"客户总数 / 真实数量"
    prompt = get_real_estate_prompt()
    for bad in ("拿到真实 count", "返回的真实 count"):
        assert bad not in prompt, f"提示词里仍有会被误读的写法：{bad}"
