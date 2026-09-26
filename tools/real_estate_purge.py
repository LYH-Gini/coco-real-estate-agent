"""
Coco 房产工具 - 数据清理（彻底删除 / 批量清理）

经纪人要"删除数据"时，此前系统只能改状态（已售/已租/已关闭），记录连同电话、价格、
业主信息一直留在库里。这里提供真正的删除入口，保护口径与去重一致：
有关联带看/成交/跟进/需求变更/转介绍记录的，默认不删（宁可留着不误删），
必须经纪人明确要求"连历史一起删"才连带删除。
"""
import json

from agent.real_estate_input import norm_id
from tools.registry import registry


def _get_db():
    from agent.real_estate_db import get_real_estate_db
    return get_real_estate_db()


def _dump(result) -> str:
    return json.dumps(result, ensure_ascii=False)


def delete_property(property_id: int = None, title: str = None, force: bool = False,
                    dry_run: bool = False, task_id: str = None) -> str:
    """彻底删除一套房源（从库里移除，不是改成已售/已租）

    property_id 或 title 二选一；有关联带看/成交/跟进的默认拒删并说明原因，
    force=True 表示经纪人已明确要求"连历史一起删"；dry_run=True 只报告不动手。
    """
    if property_id is not None:
        property_id, problem = norm_id(property_id, '房源编号')
        if problem:
            return _dump({"success": False, "error": problem})
    if property_id is None and not title:
        return _dump({"success": False, "error": "请提供房源编号或房源标题"})
    return _dump(_get_db().delete_property(property_id=property_id, title=title,
                                           force=force, dry_run=dry_run))


def delete_customer(customer_id: int = None, name: str = None, phone: str = None,
                    force: bool = False, dry_run: bool = False, task_id: str = None) -> str:
    """彻底删除一位客户（从库里移除，不是把状态改成已关闭）

    customer_id，或 name（同名时再带 phone 区分）；有关联跟进/带看/成交/需求变更/
    转介绍记录的默认拒删并说明原因，force=True 才连带删除；dry_run=True 只报告不动手。
    """
    if customer_id is not None:
        customer_id, problem = norm_id(customer_id, '客户编号', '，可在客户列表里查')
        if problem:
            return _dump({"success": False, "error": problem})
    if customer_id is None and not name:
        return _dump({"success": False, "error": "请提供客户编号或客户姓名"})
    return _dump(_get_db().delete_customer(customer_id=customer_id, name=name, phone=phone,
                                           force=force, dry_run=dry_run))


def purge_data(kind: str = 'all', statuses: list = None, before: str = None,
               mode: str = 'delete', force: bool = False, dry_run: bool = True,
               task_id: str = None) -> str:
    """批量清理：按状态（已售/已租、已关闭）与录入时间批量删除或标记

    kind: property / customer / all；before: 只清理 YYYY-MM-DD 之前录入的；
    mode='delete' 彻底删除（默认），mode='archive' 只把状态标成已成交/已关闭；
    dry_run 默认 True（只列清单不动手），经纪人确认后才用 dry_run=False。
    """
    if kind not in ('property', 'customer', 'all'):
        return _dump({"success": False, "error": "kind 只能是 property / customer / all"})
    return _dump(_get_db().purge_data(kind=kind, statuses=statuses, before=before,
                                      mode=mode, force=force, dry_run=dry_run))


registry.register(
    name="delete_property",
    toolset="real_estate",
    schema={"name": "delete_property", "description": (
        "彻底删除一套房源（从数据库里移除，连同它的调价记录）；不是把状态改成已售/已租——"
        "只想让它不再出现在在售/匹配里请改用 update_property(status=...)。"
        "有关联带看/成交/跟进记录的默认拒删并说明原因，经纪人明确要求\"连历史一起删\"时才传 force=true。"
        "dry_run=true 只报告将删什么、不动手。"), "parameters": {
        "type": "object",
        "properties": {
            "property_id": {"type": "integer", "description": "房源编号（与 title 二选一）"},
            "title": {"type": "string", "description": "房源标题（按标题定位，同名会要求报编号）"},
            "force": {"type": "boolean", "description": "连关联的带看/成交/跟进一起删（默认 false）"},
            "dry_run": {"type": "boolean", "description": "只报告不动手（默认 false）"},
        },
    }},
    handler=lambda args, **kw: delete_property(**args),
)

registry.register(
    name="delete_customer",
    toolset="real_estate",
    schema={"name": "delete_customer", "description": (
        "彻底删除一位客户（从数据库里移除，连同跟进/带看/成交/需求变更/转介绍记录）；"
        "不是把状态改成已关闭——只想不再跟进请改用 update_customer(status=\"closed\")。"
        "有历史记录时默认拒删并说明原因，经纪人明确要求\"连历史一起删\"时才传 force=true。"
        "dry_run=true 只报告将删什么、不动手。"), "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "integer", "description": "客户编号（与 name 二选一）"},
            "name": {"type": "string", "description": "客户姓名（同名时请同时传 phone 区分）"},
            "phone": {"type": "string", "description": "手机号（用于区分同名客户）"},
            "force": {"type": "boolean", "description": "连关联的跟进/带看/成交一起删（默认 false）"},
            "dry_run": {"type": "boolean", "description": "只报告不动手（默认 false）"},
        },
    }},
    handler=lambda args, **kw: delete_customer(**args),
)

registry.register(
    name="purge_data",
    toolset="real_estate",
    schema={"name": "purge_data", "description": (
        "批量清理数据：按状态清理已售/已租房源、已关闭客户（可加 before 只清某个日期之前录入的）。"
        "mode='delete' 彻底删除（默认），mode='archive' 只把状态标成已成交/已关闭。"
        "dry_run 默认 true → 先列出将删/将跳过什么及原因，经纪人确认后再用 dry_run=false 执行。"
        "有关联历史的记录默认跳过，force=true 才连历史一起删。"), "parameters": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["property", "customer", "all"],
                     "description": "清理对象：房源 / 客户 / 两者（默认 all）"},
            "statuses": {"type": "array", "items": {"type": "string"},
                         "description": "要清理的状态，默认房源=sold,rented、客户=closed"},
            "before": {"type": "string", "description": "只清理该日期（YYYY-MM-DD）之前录入的"},
            "mode": {"type": "string", "enum": ["delete", "archive"],
                     "description": "delete 彻底删除（默认）/ archive 只改状态"},
            "force": {"type": "boolean", "description": "连关联历史一起删（默认 false）"},
            "dry_run": {"type": "boolean", "description": "只列清单不动手（默认 true）"},
        },
    }},
    handler=lambda args, **kw: purge_data(**args),
)
