"""
Coco 房产工具 - 政策查询
城市贷款政策查询
"""
import json
from pathlib import Path
from tools.registry import registry


# 加载政策知识库
def _load_policies():
    """加载城市政策知识库"""
    policy_file = Path(__file__).parent.parent / "skills" / "real_estate" / "references" / "policies.json"
    if policy_file.exists():
        with open(policy_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def get_loan_policy(
    city: str = None,
    policy_type: str = None,
    task_id: str = None,
) -> str:
    """
    获取城市贷款政策
    
    参数:
        city: 城市名称（如 北京、上海）
        policy_type: 政策类型（限购政策/首付比例/贷款利率/公积金贷款/商贷年限）
    """
    policies = _load_policies()
    
    if not policies:
        return json.dumps({"success": False, "error": "政策知识库已停用（政策信息时效性强，为避免误导未收录固定数据），请用 web_search 查询该城市最新政策"}, ensure_ascii=False)
    
    # 如果没有指定城市，返回所有城市列表
    if not city:
        cities = list(policies.keys())
        return json.dumps({
            "success": True, 
            "message": "请指定城市，已收录的城市有：",
            "cities": cities
        }, ensure_ascii=False)
    
    # 查找城市政策
    if city not in policies:
        # 尝试模糊匹配
        matched_city = None
        for c in policies.keys():
            if city in c or c in city:
                matched_city = c
                break
        
        if matched_city:
            city = matched_city
        else:
            available = list(policies.keys())
            return json.dumps({
                "success": False, 
                "error": f"未找到 {city} 的政策信息",
                "available_cities": available
            }, ensure_ascii=False)
    
    city_policy = policies[city]
    
    # 如果指定了政策类型，返回特定政策
    if policy_type:
        if policy_type in city_policy:
            return json.dumps({
                "success": True,
                "city": city,
                "policy_type": policy_type,
                "policy": city_policy[policy_type],
                "update_date": city_policy.get("更新日期", "未知")
            }, ensure_ascii=False)
        else:
            available_types = [k for k in city_policy.keys() if k != "更新日期"]
            return json.dumps({
                "success": False,
                "error": f"未找到 {city} 的 {policy_type}",
                "available_types": available_types
            }, ensure_ascii=False)
    
    # 返回该城市的全部政策
    return json.dumps({
        "success": True,
        "city": city,
        "policies": city_policy,
        "update_date": city_policy.get("更新日期", "未知"),
        "note": "政策信息仅供参考，具体以当地房管局最新公告为准"
    }, ensure_ascii=False)


def list_policy_cities(task_id: str = None) -> str:
    """列出所有已收录政策的城市"""
    policies = _load_policies()
    cities = list(policies.keys())
    return json.dumps({
        "success": True,
        "cities": cities,
        "count": len(cities)
    }, ensure_ascii=False)


registry.register(
    name="get_loan_policy",
    toolset="real_estate",
    schema={"name": "get_loan_policy", "description": "获取城市贷款政策（限购、首付、利率、公积金等）。注意：本地政策知识库已停用（政策时效性强），查询城市贷款政策请优先使用 web_search，本工具通常返回空库提示", "parameters": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名称，如 北京、上海"},
            "policy_type": {"type": "string", "enum": ["限购政策", "首付比例", "贷款利率", "公积金贷款", "商贷年限"], "description": "政策类型"},
        },
    }},
    handler=lambda args, **kw: get_loan_policy(**args),
)

registry.register(
    name="list_policy_cities",
    toolset="real_estate",
    schema={"name": "list_policy_cities", "description": "列出所有已收录政策的城市", "parameters": {
        "type": "object",
        "properties": {},
    }},
    handler=lambda args, **kw: list_policy_cities(),
)
