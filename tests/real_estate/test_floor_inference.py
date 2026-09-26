"""楼层推断（L2 依据：房号）与「自动推断 + 回显依据」的回归测试（2026-09-21 加）。

背景：经纪人问「这套房的楼层、朝向是？」——Coco 本来应该自己从房号推断（房号 = 楼层+户号），
但系统没有这条规则，只能问人。现在补上：录入/更新时若没给楼层，就按标题/地址里的房号推断，
并在工具返回里给出依据，让 Coco 转述给经纪人核对（错了纠正一句即可）。

本文件钉住：推断规则（含真实样本）、不误判（年份/面积/价格）、不回推朝向、明说值优先。
"""
import json

REAL_CASES = {
    # 真实数据里的房号写法
    "海口美兰区桂林洋海阔天空, 7号楼2单元301": ("3层", "房号 301"),
    "15号楼1单元1703": ("17层", "房号 1703"),
    "263栋1006": ("10层", "房号 1006"),
    "262栋1009": ("10层", "房号 1009"),
    "1号楼1单元101": ("1层", "房号 101"),
    "8号楼2单元302": ("3层", "房号 302"),
    "7号楼2单元1602": ("16层", "房号 1602"),
    "115号楼115单元101": ("1层", "房号 101"),
    "某小区 10号楼2单元2501": ("25层", "房号 2501"),
}

NO_INFERENCE_CASES = [
    "雅居乐金沙湾",                 # 无房号
    "海口某小区 2015年建的 110平",     # 年份/面积，不是房号
    "某某花园 总价260万 110㎡",       # 价格/面积
    "3号楼 共18层",                 # 只写了总层数
]


class TestInferFloor:
    def test_real_room_numbers(self):
        from tools.real_estate_property import infer_floor

        for title, (want, why) in REAL_CASES.items():
            got, got_why = infer_floor(title)
            assert got == want, f"{title} → {got!r}，期望 {want!r}"
            assert got_why == why, f"{title} 的依据应为 {why!r}，实际 {got_why!r}"

    def test_no_inference_without_evidence(self):
        """没有房号依据时必须返回 None —— 绝不臆造"""
        from tools.real_estate_property import infer_floor

        for title in NO_INFERENCE_CASES:
            got, why = infer_floor(title)
            assert got is None and why is None, f"{title} 不该推出楼层，实际 {got!r}/{why!r}"

    def test_written_floor_descriptor_wins(self):
        """标题里明写「顶楼/高楼层」时优先采用（比房号推断更可靠）"""
        from tools.real_estate_property import infer_floor

        assert infer_floor("3号楼 共18层 顶楼")[0] == "顶层"
        assert infer_floor("某小区中楼层 2号楼2单元501")[0] == "中楼层"

    def test_address_is_used_when_title_has_nothing(self):
        from tools.real_estate_property import infer_floor

        got, why = infer_floor("雅居乐金沙湾", "7号楼2单元1602")
        assert got == "16层" and why == "房号 1602"


class TestToolWiring:
    def _registry(self, tmp_path, monkeypatch):
        url = f"sqlite:///{tmp_path}/floor.db"
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("COCO_ENC_KEY", "")
        from agent.real_estate_db import init_real_estate_db

        init_real_estate_db(url)
        import model_tools  # noqa: F401

        from tools.registry import registry

        return registry

    def test_add_without_floor_infers_and_echoes_evidence(self, tmp_path, monkeypatch):
        registry = self._registry(tmp_path, monkeypatch)
        out = json.loads(registry.get_entry("add_property").handler(
            {"title": "7号楼2单元1602", "community": "海阔天空", "price": 2_000_000, "area": 110.0},
            session_id="agent:main:feishu:dm:oc_x",
        ))
        assert out["property"]["floor"] == "16层"
        assert "房号 1602" in out["inferred"]["floor"], out.get("inferred")
        assert "转述" in out["note_inferred"]

    def test_explicit_floor_wins_over_inference(self, tmp_path, monkeypatch):
        registry = self._registry(tmp_path, monkeypatch)
        out = json.loads(registry.get_entry("add_property").handler(
            {"title": "7号楼2单元1602", "community": "海阔天空", "price": 2_000_000, "area": 110.0,
             "floor": "11楼"},
            session_id="agent:main:feishu:dm:oc_x",
        ))
        assert out["property"]["floor"] == "11层"
        assert "inferred" not in out, "经纪人明说了楼层就不该再推断覆盖"

    def test_update_infers_from_stored_title(self, tmp_path, monkeypatch):
        registry = self._registry(tmp_path, monkeypatch)
        added = json.loads(registry.get_entry("add_property").handler(
            {"title": "263栋1006", "community": "金盘", "price": 1_500_000, "area": 76.0},
            session_id="agent:main:feishu:dm:oc_x",
        ))
        pid = added["property"]["id"]
        # 先把楼层清掉，模拟历史数据，再用 update 触发推断
        from tools.real_estate_property import _get_db

        _get_db().update_property(pid, floor=None)
        out = json.loads(registry.get_entry("update_property").handler(
            {"property_id": pid}, session_id="agent:main:feishu:dm:oc_x",
        ))
        assert out["property"]["floor"] == "10层"
        assert "房号 1006" in out["inferred"]["floor"]

    def test_orientation_is_never_inferred(self, tmp_path, monkeypatch):
        """朝向推不出来 —— 必须由经纪人告知"""
        registry = self._registry(tmp_path, monkeypatch)
        out = json.loads(registry.get_entry("add_property").handler(
            {"title": "7号楼2单元1602", "community": "海阔天空", "price": 2_000_000, "area": 110.0},
            session_id="agent:main:feishu:dm:oc_x",
        ))
        assert out["property"].get("orientation") in (None, "")
