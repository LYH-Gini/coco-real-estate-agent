"""没提到的字段不许填成值（2026-09-29）

实测：经纪人只发了一行房源文字（里面没有"电梯"两个字），档案里却记成"有电梯"、回执也照实回了
"电梯：有"；车位则一直默认成"无"（详情写"车位：无"，和"未录入"混成一种说法）。

本文件钉住四件事：
1. add_property 只在原话里提到电梯/车位时才采纳这两个参数，其余留空＝还没确认；
2. 车位不再默认 0（未确认就是空，详情显示"未录入"）；
3. 返回里给 `unconfirmed` 清单，回执能一句话带过；
4. update_property（补录是明确指令）不受此限。
"""
import json

from agent.real_estate_db import init_real_estate_db


def _registry(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/unconfirmed.db"
    monkeypatch.setenv("DATABASE_URL", url)
    init_real_estate_db(url)
    import model_tools  # noqa: F401 —— 触发工具发现与注册

    from tools.registry import registry

    return registry


def _add(registry, **payload):
    payload.setdefault("title", "海阔天空 7号楼2单元1602")
    payload.setdefault("price", 1850000)
    payload.setdefault("area", 128.0)
    out = registry.get_entry("add_property").handler(payload, session_id="agent:main:feishu:dm:oc_x")
    return json.loads(out)


class TestGuessedValuesAreNotStored:
    def test_dropped_when_text_says_nothing(self, tmp_path, monkeypatch):
        data = _add(_registry(tmp_path, monkeypatch), has_elevator=1, parking=1)
        assert data["success"] is True
        assert data["property"]["has_elevator"] is None, data["property"]["has_elevator"]
        assert data["property"]["parking"] is None, data["property"]["parking"]
        assert "电梯" in data["ignored_guess"]
        assert "车位" in data["ignored_guess"]

    def test_kept_when_text_mentions_them(self, tmp_path, monkeypatch):
        data = _add(_registry(tmp_path, monkeypatch),
                    tags="电梯房,带车位", has_elevator=1, parking=1)
        assert data["property"]["has_elevator"] == 1
        assert data["property"]["parking"] == 1
        assert "ignored_guess" not in data

    def test_note_explains_why_blank(self, tmp_path, monkeypatch):
        data = _add(_registry(tmp_path, monkeypatch), has_elevator=1)
        assert "电梯" in data["note_ignored_guess"]

    def test_zero_is_a_real_answer_not_blank(self, tmp_path, monkeypatch):
        """明确说"没电梯/没车位"要能存下去，不能被当成"没提到"丢掉"""
        data = _add(_registry(tmp_path, monkeypatch),
                    tags="楼梯房,无车位", has_elevator=0, parking=0)
        assert data["property"]["has_elevator"] == 0
        assert data["property"]["parking"] == 0


class TestUnconfirmedReport:
    def test_blank_fields_are_listed(self, tmp_path, monkeypatch):
        data = _add(_registry(tmp_path, monkeypatch))
        assert "电梯" in data["unconfirmed"]
        assert "车位" in data["unconfirmed"]
        assert "卫数" in data["unconfirmed"]

    def test_confirmed_fields_are_not_listed(self, tmp_path, monkeypatch):
        data = _add(_registry(tmp_path, monkeypatch),
                    tags="电梯房,带车位", has_elevator=1, parking=0, bathrooms=1)
        assert "电梯" not in data["unconfirmed"]
        assert "车位" not in data["unconfirmed"]
        assert "卫数" not in data["unconfirmed"]


class TestDetailAndUpdate:
    def test_detail_prints_not_entered(self, tmp_path, monkeypatch):
        registry = _registry(tmp_path, monkeypatch)
        pid = _add(registry, has_elevator=1)["property"]["id"]
        detail = json.loads(registry.get_entry("get_property_detail").handler(
            {"property_id": pid}, session_id="agent:main:feishu:dm:oc_x"))
        assert "电梯 未录入" in detail["message"]
        assert "车位 未录入" in detail["message"]

    def test_update_property_can_fill_them(self, tmp_path, monkeypatch):
        registry = _registry(tmp_path, monkeypatch)
        pid = _add(registry)["property"]["id"]
        updated = json.loads(registry.get_entry("update_property").handler(
            {"property_id": pid, "has_elevator": 1, "parking": 0},
            session_id="agent:main:feishu:dm:oc_x"))
        assert updated["property"]["has_elevator"] == 1
        assert updated["property"]["parking"] == 0
