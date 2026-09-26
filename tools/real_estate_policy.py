"""
Coco 房产工具 - 政策查询（城市房贷政策）

口径（2026-09-27 定，别再改回来）：
    本地政策库**已停用** —— 政策时效性强（限购 / 首付 / 利率随时调整），收录固定数据会误导经纪人。
    所以 `get_loan_policy` / `list_policy_cities` 一律如实回「本地库已停用」，并引导经纪人说一声、
    由 Coco 联网查（结论里标明来源与日期），**绝不返回固定的政策数字**。

    历史：早先的实现会读 `skills/real_estate/references/policies.json` 并返回里面的固定政策。
    实测（`/root/coco-tool-audit/results/raw/t84.log`）只要往那个文件里塞数据，工具就会把它当政策返回；
    该分支已删除，文件保留为空（`{}`）仅作占位。要恢复本地政策库，从 git 历史取回实现即可。
"""
import json

from tools.registry import registry

STOPPED_HEAD = "本地政策库已停用（政策时效性强，为避免误导不再收录固定数据）。"
ASK_ONLINE = "跟我说一声，我联网查给你，并在结论里标明来源和日期。"


def _city_text(city) -> str:
    """城市名只有是正常短文本时才回显：数字 / 布尔 / 空串 / 超长 / 结构体一律不回显。"""
    if isinstance(city, str):
        text = city.strip()
        if text and len(text) <= 20:
            return f"「{text}」的"
    return ""


def get_loan_policy(
    city: str = None,
    policy_type: str = None,
    task_id: str = None,
) -> str:
    """获取城市贷款政策（本地库已停用，只给联网查询的引导）"""
    return json.dumps({
        "success": False,
        "error": f"{STOPPED_HEAD}要查{_city_text(city)}最新房贷政策，{ASK_ONLINE}",
    }, ensure_ascii=False)


def list_policy_cities(task_id: str = None) -> str:
    """列出已收录政策的城市（本地库已停用，恒为空）"""
    return json.dumps({
        "success": False,
        "error": f"{STOPPED_HEAD}要查某地的最新房贷政策，{ASK_ONLINE}",
    }, ensure_ascii=False)


registry.register(
    name="get_loan_policy",
    toolset="real_estate",
    schema={"name": "get_loan_policy", "description": (
        "城市房贷政策查询（限购、首付比例、贷款利率、公积金、商贷年限）。"
        "本地政策库已停用，本工具只会返回「请联网查询」的提示；要给经纪人具体政策，"
        "请走联网检索并标明来源与更新日期，禁止凭记忆报数字。"), "parameters": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名称，如 北京、上海（用于回显问的是哪个城市）"},
            "policy_type": {"type": "string", "enum": ["限购政策", "首付比例", "贷款利率", "公积金贷款", "商贷年限"],
                            "description": "政策类型（本工具不使用；联网检索时可用它限定范围）"},
        },
    }},
    handler=lambda args, **kw: get_loan_policy(**args),
)

registry.register(
    name="list_policy_cities",
    toolset="real_estate",
    schema={"name": "list_policy_cities", "description": (
        "列出已收录政策的城市。本地政策库已停用（不收录固定数据），"
        "本工具只会返回「已停用」的说明，给不出城市清单。"), "parameters": {
        "type": "object",
        "properties": {},
    }},
    handler=lambda args, **kw: list_policy_cities(),
)
