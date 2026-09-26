"""房源图片查询（list_property_images）回归：查得到、说得清（2026-09-26 第 68 项，本组最后一个工具）

本文件钉五件事：
① 有图 → `images` + `count` + 一句 `message`（说清共几张、怎么发）；
② **没有图片时如实说明**（原先只回空数组没说一句，或干脆说"房源不存在或不在售"）；
③ **已售/已租的房源也能查图**（2026-09-26 拍板放开），只给 `warnings` 说明状态；
④ 不存在 → 「没有编号 N 的房源…」（不再与"不在售"混一句）；编号形态给中文提示；
⑤ 与 `add_property_images` 写进去的逐项一致（写读同源）。
"""
import json
import os

import pytest
from conftest import make_property  # noqa: F401

from tools import real_estate_images as m_images

PHOTO_DIR = "/tmp/coco_img_probe"


@pytest.fixture(autouse=True)
def photo_files():
    os.makedirs(PHOTO_DIR, exist_ok=True)
    for name in ("a.jpg", "b.jpg", "c.jpg", "d.jpg"):
        open(os.path.join(PHOTO_DIR, name), "wb").write(b"x")
    return PHOTO_DIR


@pytest.fixture
def wired(db, monkeypatch):
    monkeypatch.setattr(m_images, "_get_db", lambda: db)
    return db


def _list(pid):
    return json.loads(m_images.list_property_images(property_id=pid))


def _add(pid, images):
    return json.loads(m_images.add_property_images(property_id=pid, images=images))


def _img(name):
    return os.path.join(PHOTO_DIR, name)


def _archived(name):
    """入库存的是**归档后**的路径：照片进库前会被复制进归档目录（网关缓存目录 24 小时后会自动清理，
    见 agent/real_estate_media.py）—— 按原文件主体名找回回归档副本，断言口径仍是「读写同源的这张照片」"""
    from agent.real_estate_media import images_archive_dir

    hits = sorted(images_archive_dir().glob(f"{os.path.splitext(name)[0]}_*"))
    assert hits, f"归档目录里没有 {name} 对应的照片：{images_archive_dir()}"
    return str(hits[0])


def _mk(db, **kw):
    data = dict(title="图片小区 1号楼101", community="图片小区", district="朝阳区",
                price=1_000_000, area=80.0, status="available", property_type="second_hand")
    data.update(kw)
    return make_property(db, **data)["id"]


# ---------- ① 有图 ----------
def test_lists_images_with_count_and_message(wired):
    pid = _mk(wired)
    _add(pid, f"{_img('a.jpg')},{_img('b.jpg')}")
    out = _list(pid)
    assert out["success"] is True, out
    assert out["images"] == [_archived("a.jpg"), _archived("b.jpg")], out
    assert out["count"] == 2, out
    assert "有 2 张图片" in out["message"], out
    assert "MEDIA" in out["message"], out


def test_many_images_message_is_truncated_but_list_is_full(wired):
    pid = _mk(wired)
    _add(pid, ",".join(_img(f"{i}.jpg") for i in range(5)) if False else
         ",".join(f"/tmp/pic_{i}.jpg" for i in range(5)))
    out = _list(pid)
    assert out["count"] == 5 and len(out["images"]) == 5, out
    assert "…" in out["message"], out          # 文案里只列前三张，其余用省略号
    assert out["message"].count("/tmp/pic_") <= 3, out["message"]


# ---------- ② 没有图片 ----------
def test_no_images_is_stated_honestly(wired):
    pid = _mk(wired, images=None)
    out = _list(pid)
    assert out["success"] is True, out
    assert out["images"] == [] and out["count"] == 0, out
    assert "还没有图片" in out["message"], out


def test_empty_string_images_column_is_treated_as_none(wired):
    """images 列是空串（历史数据）→ 也按"没有图片"说，不是报错"""
    pid = _mk(wired)
    with wired.engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text("update re_properties set images='' where id=:i"), {"i": pid})
    out = _list(pid)
    assert out["success"] is True and out["count"] == 0, out
    assert "还没有图片" in out["message"], out


# ---------- ③ 已售/已租也能查 ----------
@pytest.mark.parametrize("status,label", [("sold", "已售"), ("rented", "已租")])
def test_sold_or_rented_property_images_are_readable(wired, status, label):
    pid = _mk(wired, status=status)
    _add(pid, _img("a.jpg"))
    out = _list(pid)
    assert out["success"] is True and out["count"] == 1, out
    warn = " ".join(out.get("warnings") or [])
    assert label in warn and "照常给你" in warn, out


# ---------- ④ 不存在 / 编号形态 ----------
def test_missing_property_says_so(wired):
    out = _list(999999)
    assert out["success"] is False and "没有编号 999999 的房源" in out["error"], out
    assert "不在售" not in out["error"], out


@pytest.mark.parametrize("bad", ["abc", True, []])
def test_bad_id_gives_chinese_hint(wired, bad):
    out = json.loads(m_images.list_property_images(property_id=bad))
    assert out.get("success") is not True and out.get("error"), out
    assert "图片" in out["error"] or "编号" in out["error"], out


# ---------- ⑤ 写读同源 ----------
def test_write_then_read_roundtrip(wired):
    pid = _mk(wired)
    _add(pid, [_img("a.jpg"), _img("b.jpg")])
    _add(pid, _img("c.jpg"))
    out = _list(pid)
    assert out["images"] == [_archived("a.jpg"), _archived("b.jpg"), _archived("c.jpg")], out
    detail = wired.get_property(pid)
    assert detail["images"] == ",".join(out["images"]), (detail["images"], out["images"])


def test_description_and_params_are_documented():
    from tools.registry import registry

    entry = registry.get_entry("list_property_images")
    desc = entry.schema["description"]
    assert "已售" in desc and "没有图片" in desc, desc
    assert len(desc) > 40, desc
    props = entry.schema["parameters"]["properties"]
    assert len(props["property_id"]["description"]) > 8, props
