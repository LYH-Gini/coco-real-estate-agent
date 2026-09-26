"""房源字段进不了库 / 进不了海报的回归测试（2026-09-21 修）

背景（真实事故）：经纪人报「Coco 不识别楼层，模板 B 的楼层/朝向两栏显示 —」。
查出来的根因不是数据层，而是**工具 schema 没声明这两个参数**——模型看不到 → 永远不传 →
库里永远是 NULL → 海报只能显示 —。同类缺口一次查出 16 处（update_property 缺小区/区域/装修，
search_property 缺类型，update_customer 缺微信/生日，list_customers 缺客户类型…）。

本文件钉四件事：
1. 不变量：**所有房产工具的业务参数都必须在 schema 里声明**（否则模型看不到 = 功能不存在）；
2. 楼层/朝向的归一化（16楼→16层、朝北→北）；
3. 网关真实调用方式下，update_property / add_property 能把楼层朝向写进库；
4. 模板 B 的海报 SVG 会读出这两栏（缺失时是 —）。
"""
import inspect
import pkgutil

import pytest

INJECTED = {"task_id", "agent_id", "session_id", "chat_id", "channel_id", "conversation_id"}


def _registry(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/fields.db"
    monkeypatch.setenv("DATABASE_URL", url)
    from agent.real_estate_db import init_real_estate_db

    init_real_estate_db(url)
    import model_tools  # noqa: F401 —— 触发工具发现与注册

    from tools.registry import registry

    return registry


def _tool_functions():
    """收集 tools/real_estate_*.py 里所有同名工具函数（工具名 → 函数对象）"""
    import importlib

    import tools as tools_pkg

    found = {}
    for mod in pkgutil.iter_modules(tools_pkg.__path__):
        if not mod.name.startswith("real_estate"):
            continue
        m = importlib.import_module(f"tools.{mod.name}")
        for attr in dir(m):
            fn = getattr(m, attr)
            if inspect.isfunction(fn) and fn.__module__ == m.__name__:
                found.setdefault(attr, fn)
    return found


class TestSchemaDeclaresBusinessParams:
    """模型能看到才算功能存在：业务参数缺 schema 声明 = 这个字段永远录不进去"""

    def test_no_business_param_missing_from_schema(self, tmp_path, monkeypatch):
        registry = _registry(tmp_path, monkeypatch)
        funcs = _tool_functions()
        names = sorted(registry.get_tool_names_for_toolset("real_estate"))

        offenders = []
        for name in names:
            fn = funcs.get(name)
            if fn is None:
                continue
            schema = (registry.get_entry(name).schema or {}).get("parameters") or {}
            props = set((schema.get("properties") or {}).keys())
            sig = inspect.signature(fn)
            params = [p for p, v in sig.parameters.items()
                      if p not in INJECTED and v.kind in (v.POSITIONAL_OR_KEYWORD, v.KEYWORD_ONLY)]
            missing = [p for p in params if p not in props]
            if missing:
                offenders.append(f"{name}: 缺 {', '.join(missing)}")
        assert not offenders, (
            "以下工具的业务参数没有写进 schema（模型看不到，字段永远录不进去）：\n  "
            + "\n  ".join(offenders)
        )

    def test_floor_and_orientation_are_declared(self, tmp_path, monkeypatch):
        """本事故的核心两个字段，单独再钉一次（宁可重复也要防回归）"""
        registry = _registry(tmp_path, monkeypatch)
        for tool in ("add_property", "update_property"):
            props = ((registry.get_entry(tool).schema or {}).get("parameters") or {}).get("properties") or {}
            assert "floor" in props, f"{tool} 的 schema 没有 floor"
            assert "orientation" in props, f"{tool} 的 schema 没有 orientation"


class TestFloorOrientationNormalization:
    def test_floor(self):
        from tools.real_estate_property import _norm_floor

        cases = {
            "16楼": "16层",
            "十六楼": "16层",
            "16F": "16层",
            "16层": "16层",
            " 16 楼 ": "16层",
            "5/18层": "5层（共18层）",
            "中楼层": "中楼层",
            "高楼层": "高楼层",
            "低楼层": "低楼层",
            "顶层": "顶层",
            "负一层": "负一层",
            "301": "301",       # 认不出的原样保留，绝不臆造
            None: None,
            "": None,
        }
        for raw, want in cases.items():
            assert _norm_floor(raw) == want, f"{raw!r} → {_norm_floor(raw)!r}，期望 {want!r}"

    def test_orientation(self):
        from tools.real_estate_property import _norm_orientation

        cases = {
            "北": "北", "朝北": "北", "北向": "北", "北面": "北",
            "南": "南", "朝南": "南", "正南": "南",
            "南北通": "南北通透", "南北": "南北通透", "南北通透": "南北通透",
            "东南向": "东南", "东西通透": "东西通透",
            "一线看海": "一线看海",   # 描述性文字不能被剥成空串
            None: None, "": None,
        }
        for raw, want in cases.items():
            assert _norm_orientation(raw) == want, f"{raw!r} → {_norm_orientation(raw)!r}，期望 {want!r}"


class TestFieldsReachDatabase:
    """按网关真实调用方式（handler + 注入运行时参数）走一遍，确认真的写库"""

    def test_update_property_writes_floor_and_orientation(self, tmp_path, monkeypatch):
        registry = _registry(tmp_path, monkeypatch)
        monkeypatch.setenv("COCO_ENC_KEY", "")
        added = registry.get_entry("add_property").handler(
            {"title": "海阔天空 7号楼2单元1602", "price": 2_000_000, "area": 110.0,
             "community": "海阔天空", "district": "美兰-桂林洋", "rooms": 3, "halls": 2},
            session_id="agent:main:feishu:dm:oc_x", task_id="t1",
        )
        import json

        pid = json.loads(added)["property"]["id"]

        # 经纪人说的写法（16楼 / 朝北）→ 工具应归一后写库
        out = registry.get_entry("update_property").handler(
            {"property_id": pid, "floor": "16楼", "orientation": "朝北"},
            session_id="agent:main:feishu:dm:oc_x", task_id="t1",
        )
        data = json.loads(out)
        assert data["success"] is True
        assert data["property"]["floor"] == "16层", data["property"].get("floor")
        assert data["property"]["orientation"] == "北", data["property"].get("orientation")

        # 再查一次库（不是看返回值）

        detail = json.loads(registry.get_entry("get_property_detail").handler({"property_id": pid}))
        assert detail["property"]["floor"] == "16层"
        assert detail["property"]["orientation"] == "北"

    def test_add_property_stores_floor_and_orientation(self, tmp_path, monkeypatch):
        registry = _registry(tmp_path, monkeypatch)
        import json

        out = registry.get_entry("add_property").handler(
            {"title": "测试楼盘 1号楼801", "price": 1_800_000, "area": 88.0,
             "floor": "8楼", "orientation": "南北通", "has_elevator": 1, "parking": 0},
            session_id="agent:main:feishu:dm:oc_x",
        )
        p = json.loads(out)["property"]
        assert p["floor"] == "8层"
        assert p["orientation"] == "南北通透"


class TestTemplateBShowsThem:
    """模板 B（极简高级）会读这两栏；缺失时是 —（实际看到的现象）"""

    BASE = {
        "title": "今日主推",
        "properties": [{
            "title": "海阔天空 1602", "community": "海阔天空", "district": "美兰-桂林洋",
            "area": 110.0, "rooms": 3, "halls": 2, "price": 2_000_000,
            "property_type": "second_hand", "floor": "16层", "orientation": "北",
        }],
        "agent": {"company": "某某房产", "name": "张三", "phone": "13800000000"},
    }

    def test_svg_contains_floor_and_orientation(self):
        from tools.real_estate_poster_svg import template_b

        svg = template_b(dict(self.BASE, properties=[dict(self.BASE["properties"][0])]))
        assert "楼层" in svg and "朝向" in svg
        assert "16层" in svg and ">北<" in svg, "模板 B 没有把楼层/朝向画进 SVG"
        assert "—" not in svg.split("楼层")[1][:400], "楼层/朝向仍显示空占位"

    def test_missing_fields_fall_back_to_placeholder(self):
        """没录入时显示 —（现状说明，不是 bug；录进去就会显示真值）"""
        from tools.real_estate_poster_svg import template_b

        prop = dict(self.BASE["properties"][0])
        prop["floor"] = None
        prop["orientation"] = None
        svg = template_b(dict(self.BASE, properties=[prop]))
        assert "—" in svg


class TestElevatorNotAssumed:
    """电梯没提到就不许按「有」记（2026-09-26）

    默认值 1 会把"经纪人从没说过"变成文案/口播稿里的卖点（"有电梯"）。改成留空后：
    库里存 NULL、详情显示「未录入」，显式 1/0 照旧。
    """

    def _add(self, registry, title, **kw):
        import json

        out = registry.get_entry("add_property").handler(
            {"title": title, "price": 1_000_000, "area": 80.0, **kw},
            session_id="agent:main:feishu:dm:oc_x",
        )
        return json.loads(out)["property"]

    def test_unspecified_stays_empty(self, tmp_path, monkeypatch):
        import json

        registry = _registry(tmp_path, monkeypatch)
        p = self._add(registry, "电梯未提到 1号楼101")
        assert p["has_elevator"] is None, p.get("has_elevator")
        detail = json.loads(
            registry.get_entry("get_property_detail").handler({"property_id": p["id"]}))
        assert "电梯 未录入" in detail["message"], detail["message"]

    def test_explicit_yes_and_no_kept(self, tmp_path, monkeypatch):
        registry = _registry(tmp_path, monkeypatch)
        assert self._add(registry, "电梯有 2号楼102", has_elevator=1)["has_elevator"] == 1
        assert self._add(registry, "电梯无 3号楼103", has_elevator=0)["has_elevator"] == 0
