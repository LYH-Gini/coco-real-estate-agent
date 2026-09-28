"""
Coco 房产工具 - 政策查询（城市房贷政策）

口径（2026-09-28 定，别再改回来）：
    政策数字**不预存** —— 政策时效性强（限购 / 首付 / 利率随时调整），存下来就有给过期数字的风险。
    所以 `get_loan_policy` / `list_policy_cities` 一律返回**一句可以直接回复经纪人的话**
    （「我当场联网查最新的，连来源和日期一起发」），并把"模型该怎么做"放进 `note_for_model`
    （联网检索、标明来源与检索日期、禁止凭记忆报数字）。

    为什么不再说「本地政策库已停用」（真实教训，`/root/coco-tool-audit/FINDINGS.md` F440/F441）：
    经纪人问「你收录了哪些城市的政策？」，模型手上只有工具的说明书与报错，只能把它们转述出去 ——
    回了一段「结论：本地政策库已停用…当前做法…」的系统说明书，读起来像提示词。
    对外文案必须是产品语言、且不解释系统怎么收录；给模型的话放 `note_for_model`（见【对外说话规则】）。

    历史：早先的实现会读 `skills/real_estate/references/policies.json` 并返回里面的固定政策。
    实测（`/root/coco-tool-audit/results/raw/t84.log`）只要往那个文件里塞数据，工具就会把它当政策返回；
    该分支已删除，文件保留为空（`{}`）仅作占位。要恢复本地政策库，从 git 历史取回实现即可。
"""
import json

from tools.registry import registry

# 给模型看的说明（`note_for_model`，不要念给经纪人）—— 两个政策入口共用
NOTE_FOR_MODEL = (
    "政策不预存：先用联网检索查该城市的最新政策，结论里标明来源与检索日期；"
    "查不到就如实说查不到，并建议咨询当地房管局/公积金中心/贷款银行，禁止凭记忆报旧政策数字。"
    "回复经纪人时用工具给的那句话就行，不要把工具名、参数名，或「本地库/知识库/收录」"
    "这类内部说法讲给他（见【对外说话规则】）。")


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
    """获取城市贷款政策（不预存数字，给一句可直接回复经纪人的话）"""
    return json.dumps({
        "success": False,
        "error": (f"{_city_text(city)}房贷政策我当场联网查最新的给你"
                  "（首付比例／贷款利率／限购／公积金都行），连来源和日期一起发；"
                  "查不到可靠的，我直说查不到，并建议你以当地房管局、公积金中心或贷款银行的答复为准。"),
        "note_for_model": NOTE_FOR_MODEL,
    }, ensure_ascii=False)


def list_policy_cities(task_id: str = None) -> str:
    """政策咨询引导（不预存政策数字，给一句可直接回复经纪人的话）"""
    return json.dumps({
        "success": False,
        "error": ("各地政策数字我没有预先存下来（首付、利率、限购随时会变，存着容易给你过期的）。"
                  "你说要查哪个城市，我当场联网查最新的，连来源和日期一起发你。"),
        "note_for_model": NOTE_FOR_MODEL,
    }, ensure_ascii=False)


registry.register(
    name="get_loan_policy",
    toolset="real_estate",
    schema={"name": "get_loan_policy", "description": (
        "城市房贷政策查询（限购、首付比例、贷款利率、公积金、商贷年限）。"
        "本工具不预存政策数字，只返回一句可直接回复经纪人的说明；要给经纪人具体政策，"
        "请联网检索并在结论里标明来源与更新日期，禁止凭记忆报数字。"), "parameters": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名称，如 北京、上海（用于回显问的是哪个城市）"},
            "policy_type": {"type": "string", "enum": ["限购政策", "首付比例", "贷款利率", "公积金贷款", "商贷年限"],
                            "description": "政策类型（可选；联网检索时用它限定查哪一项）"},
        },
    }},
    handler=lambda args, **kw: get_loan_policy(**args),
)

registry.register(
    name="list_policy_cities",
    toolset="real_estate",
    schema={"name": "list_policy_cities", "description": (
        "经纪人问「你收录了哪些城市的政策／能不能查政策」时调用。"
        "本工具不预存政策数字，返回一句可直接回复经纪人的说明；"
        "照那句话回复即可，不要解释系统机制、不要列举工具或参数。"), "parameters": {
        "type": "object",
        "properties": {},
    }},
    handler=lambda args, **kw: list_policy_cities(),
)
