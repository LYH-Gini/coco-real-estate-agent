"""「一串多个值」的参数：数组与逗号串都要认（2026-09-26）

起因：模型把这类参数**当数组传是常态**（`image_paths=["/tmp/a.jpg"]`、`tags=["近地铁","学区房"]`），
而实现按逗号串写（`.split(',')`）或原样塞进 `Text` 列 → 实测崩：

| 调用 | 崩法 |
|---|---|
| `add_property(image_paths=[…])` | `AttributeError: 'list' object has no attribute 'split'` |
| `add_property(images=[…])` | 同上 |
| `add_property(tags=[…])` | 数据库层 `type 'list' is not supported`（离线库也一样） |
| `update_property(tags=[…])` | 数据库层同上 |

契约（与 F102/F117 同族）：**数组元素逐个过同一套清洗**（转文本、去空白、丢空项），
"没给"与"给了空"归到同一档（不往库里写空串）；字符串照旧原样走各自的归一化。
"""
import json

import pytest
from conftest import make_property  # noqa: F401

from agent.real_estate_input import as_comma_text


# ---------- 共用件 ----------
@pytest.mark.parametrize("value,expected", [
    (None, None),
    ("", None),
    ("   ", None),
    ([], None),
    (["", "   "], None),
    ("/tmp/a.jpg", "/tmp/a.jpg"),                    # 字符串原样（不重排、不拆分）
    ("a.jpg, b.jpg", "a.jpg, b.jpg"),
    (["/tmp/a.jpg", "/tmp/b.jpg"], "/tmp/a.jpg,/tmp/b.jpg"),
    ([" /tmp/a.jpg ", "", "/tmp/b.jpg"], "/tmp/a.jpg,/tmp/b.jpg"),   # 去空白 + 丢空项
    (("/tmp/a.jpg", "/tmp/b.jpg"), "/tmp/a.jpg,/tmp/b.jpg"),         # 元组也认
    (["近地铁", "学区房"], "近地铁,学区房"),
    ([1, 2], "1,2"),                                  # 数字元素转文本（别让模型挨骂）
])
def test_as_comma_text(value, expected):
    assert as_comma_text(value) == expected


def test_as_comma_text_does_not_reorder_or_dedupe():
    """数组按原顺序拼（去重/归一留给各字段自己的口径，别在这里偷偷改语义）"""
    assert as_comma_text(["b", "a", "b"]) == "b,a,b"


# ---------- add_property：图片路径 / 标签 ----------
def _registry(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/comma_args.db"
    monkeypatch.setenv("DATABASE_URL", url)
    from agent.real_estate_db import init_real_estate_db

    init_real_estate_db(url)
    import model_tools  # noqa: F401 —— 触发工具发现与注册

    from tools.registry import registry

    return registry


def _add(registry, **kw):
    args = {"title": "逗号串参数 1号楼101", "price": 1_000_000, "area": 80.0}
    args.update(kw)
    out = registry.get_entry("add_property").handler(args, session_id="agent:main:feishu:dm:oc_x")
    return json.loads(out)


class TestUpdatePropertyAcceptsArrays:
    """update_property 的 tags 传数组原先崩在数据库层（type 'list' is not supported）"""

    def _update(self, registry, pid, **kw):
        args = {"property_id": pid}
        args.update(kw)
        out = registry.get_entry("update_property").handler(args, session_id="agent:main:feishu:dm:oc_x")
        return json.loads(out)

    def test_tags_as_array(self, tmp_path, monkeypatch):
        registry = _registry(tmp_path, monkeypatch)
        pid = _add(registry, title="改标签 2号楼201")["property"]["id"]
        data = self._update(registry, pid, tags=["电梯房", "南北通透"])
        assert data["success"] is True, data
        assert data["property"]["tags"] == "电梯房,南北通透", data["property"].get("tags")

    def test_tags_comma_string_unchanged(self, tmp_path, monkeypatch):
        registry = _registry(tmp_path, monkeypatch)
        pid = _add(registry, title="改标签 2号楼202")["property"]["id"]
        data = self._update(registry, pid, tags="电梯房,南北通透")
        assert data["property"]["tags"] == "电梯房,南北通透", data["property"].get("tags")

    def test_empty_tags_array_keeps_missing(self, tmp_path, monkeypatch):
        """空数组＝没给（按"未填"处理，不写空串）"""
        registry = _registry(tmp_path, monkeypatch)
        pid = _add(registry, title="改标签 2号楼203")["property"]["id"]
        data = self._update(registry, pid, tags=[])
        assert data["success"] is True, data
        assert data["property"]["tags"] is None, data["property"].get("tags")


class TestAddPropertyAcceptsArrays:
    """数组写法不许崩（原先 image_paths/images 抛 AttributeError、tags 崩在数据库层）"""

    def test_image_paths_as_array(self, tmp_path, monkeypatch):
        registry = _registry(tmp_path, monkeypatch)
        data = _add(registry, title="数组图片 1号楼101",
                    image_paths=["/tmp/a.jpg", "/tmp/b.jpg"])
        assert data["success"] is True, data
        assert data["property"]["images"] == "/tmp/a.jpg,/tmp/b.jpg", data["property"].get("images")

    def test_images_as_array(self, tmp_path, monkeypatch):
        registry = _registry(tmp_path, monkeypatch)
        data = _add(registry, title="数组图片 1号楼102", images=["/tmp/c.jpg", "/tmp/d.jpg"])
        assert data["success"] is True, data
        assert data["property"]["images"] == "/tmp/c.jpg,/tmp/d.jpg", data["property"].get("images")

    def test_images_and_image_paths_merge(self, tmp_path, monkeypatch):
        registry = _registry(tmp_path, monkeypatch)
        data = _add(registry, title="数组图片 1号楼103", images=["/tmp/e.jpg"],
                    image_paths="/tmp/f.jpg")
        assert data["property"]["images"] == "/tmp/e.jpg,/tmp/f.jpg", data["property"].get("images")

    def test_tags_as_array_stored_as_comma_text(self, tmp_path, monkeypatch):
        """原先 tags=[…] 直接塞进 Text 列 → 数据库层报错（type 'list' is not supported）"""
        registry = _registry(tmp_path, monkeypatch)
        data = _add(registry, title="数组标签 1号楼104", tags=["近地铁", "学区房"])
        assert data["success"] is True, data
        assert data["property"]["tags"] == "近地铁,学区房", data["property"].get("tags")

    def test_empty_array_is_treated_as_missing(self, tmp_path, monkeypatch):
        """给了空数组 = 没给（不往库里写空串）"""
        registry = _registry(tmp_path, monkeypatch)
        data = _add(registry, title="空数组 1号楼105", images=[], image_paths=["", "  "],
                    tags=[])
        assert data["success"] is True, data
        assert data["property"]["images"] is None, data["property"].get("images")
        assert data["property"]["tags"] is None, data["property"].get("tags")

    def test_comma_string_still_works(self, tmp_path, monkeypatch):
        """逗号串写法行为不变（别把能用的写法改坏）"""
        registry = _registry(tmp_path, monkeypatch)
        data = _add(registry, title="逗号串 1号楼106", image_paths="/tmp/g.jpg,/tmp/h.jpg",
                    tags="近地铁,学区房")
        assert data["property"]["images"] == "/tmp/g.jpg,/tmp/h.jpg", data["property"].get("images")
        assert data["property"]["tags"] == "近地铁,学区房", data["property"].get("tags")
