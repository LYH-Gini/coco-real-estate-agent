"""工具注册契约：所有房产工具都必须能在网关的真实调用方式下工作。

网关调用 handler 时会注入 session_id / task_id 等运行时参数（registry.dispatch → entry.handler(args, **kwargs)）。
handler 若写成 `lambda args, **kw: fn(**kw)`，这些参数会被解包进函数 → TypeError: unexpected keyword argument，
表现为"工具调用失败"而模型只能自己兜底编答案。2026-08-12（get_agent_brand）与 2026-09-18（get_property_form）
各踩过一次，本测试把它们一次性钉死。
"""
import json
import os

INJECTED = {"session_id": "agent:main:feishu:dm:oc_x", "task_id": "t1", "chat_id": "oc_x"}


def _load_all_tools(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/contract.db"
    monkeypatch.setenv("DATABASE_URL", url)
    from agent.real_estate_db import init_real_estate_db
    init_real_estate_db(url)
    import model_tools  # noqa: F401 —— 触发工具发现与注册
    from tools.registry import registry
    return registry


def test_no_handler_rejects_injected_kwargs(tmp_path, monkeypatch):
    """逐个调用 real_estate 全部工具（注入框架参数），不得出现 unexpected keyword argument"""
    registry = _load_all_tools(tmp_path, monkeypatch)
    names = sorted(registry.get_tool_names_for_toolset("real_estate"))
    assert len(names) >= 60, f"工具数异常：{len(names)}"

    offenders = []
    for name in names:
        entry = registry.get_entry(name)
        if entry is None or entry.handler is None:
            continue
        try:
            entry.handler({}, **INJECTED)
        except TypeError as exc:
            if "unexpected keyword argument" in str(exc):
                offenders.append(f"{name}: {exc}")
        except Exception:
            # 其他异常（缺参数/缺库/缺依赖）不影响本契约，只看注册写法
            pass
    assert not offenders, "以下工具的 handler 注册写法错误（应为 lambda args, **kw: fn(**args)）：\n" + "\n".join(offenders)


def test_form_tools_return_usable_payload_with_injected_kwargs(tmp_path, monkeypatch):
    """表单类工具在注入参数下要真能返回模板（这两个工具是经纪人最常用的入口）"""
    registry = _load_all_tools(tmp_path, monkeypatch)
    for tool, key in (("get_property_form", "form"), ("get_customer_form", "form")):
        entry = registry.get_entry(tool)
        data = json.loads(entry.handler({}, **INJECTED))
        assert data["success"] is True, f"{tool} 调用失败：{data}"
        assert data[key].strip(), f"{tool} 返回空模板"
