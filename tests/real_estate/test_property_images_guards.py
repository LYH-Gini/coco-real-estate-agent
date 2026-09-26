"""房源图片（add_property_images）回归：加图要如实报数、已售也能加（2026-09-26 第 67 项）

本文件钉六件事：
① 回执说清**新增 N 张／M 张已存在**（原先按输入条数报，重复加同一张会说"已添加 2 张"而库里一张没变）；
② 没给图（空串/空数组/只有逗号）→ 中文提示，不当成"成功了但加了 0 张"；
③ `images` **数组写法也认**（原先 `AttributeError: 'list' object has no attribute 'split'`）；
④ **已售/已租房源也能加图**（资料补录），只给 `warnings` 说明状态；不存在则分开说；
⑤ 本地路径在本机找不到 → 一句 `warnings`（不拦）；图片链接不误报；
⑥ 只动目标房源的 images 列，别的行一个字段不改。
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
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        open(os.path.join(PHOTO_DIR, name), "wb").write(b"x")
    return PHOTO_DIR


@pytest.fixture
def wired(db, monkeypatch):
    monkeypatch.setattr(m_images, "_get_db", lambda: db)
    return db


def _add(**kw):
    return json.loads(m_images.add_property_images(**kw))


def _img(name):
    return os.path.join(PHOTO_DIR, name)


def _archived(name):
    """入库的是**归档后**的路径：照片进库前会被复制进归档目录（网关缓存目录 24 小时后会自动清理，见
    agent/real_estate_media.py）—— 这里按原文件主体名找回归档副本，断言口径仍是「存的是这张照片」"""
    from agent.real_estate_media import images_archive_dir

    hits = sorted(images_archive_dir().glob(f"{os.path.splitext(name)[0]}_*"))
    assert hits, f"归档目录里没有 {name} 对应的照片：{images_archive_dir()}"
    return str(hits[0])


def _mk(db, **kw):
    data = dict(title="图片小区 1号楼101", community="图片小区", district="朝阳区",
                price=1_000_000, area=80.0, status="available", property_type="second_hand")
    data.update(kw)
    return make_property(db, **data)["id"]


# ---------- ① 如实报数 ----------
def test_new_images_counted_honestly(wired):
    pid = _mk(wired)
    out = _add(property_id=pid, images=f"{_img('a.jpg')},{_img('b.jpg')}")
    assert out["success"] is True, out
    assert out["added_count"] == 2 and out["skipped_count"] == 0 and out["count"] == 2, out
    assert "已添加 2 张" in out["message"], out


def test_duplicate_images_report_skipped(wired):
    """重复加同一张：不许说"已添加 2 张"（库里没变）"""
    pid = _mk(wired)
    _add(property_id=pid, images=_img("a.jpg"))
    out = _add(property_id=pid, images=f"{_img('a.jpg')},{_img('a.jpg')}")
    assert out["success"] is True, out
    assert out["added_count"] == 0 and out["skipped_count"] == 2, out
    assert "已添加" not in out["message"], out
    assert "没有重复添加" in out["message"] and out["count"] == 1, out


def test_partial_duplicate_reports_both_numbers(wired):
    pid = _mk(wired)
    _add(property_id=pid, images=_img("a.jpg"))
    out = _add(property_id=pid, images=f"{_img('a.jpg')},{_img('b.jpg')}")
    assert out["added_count"] == 1 and out["skipped_count"] == 1, out
    assert "已添加 1 张" in out["message"] and "1 张已在库里" in out["message"], out


# ---------- ② 空输入 ----------
@pytest.mark.parametrize("value", ["", "   ", [], ",", " , "])
def test_empty_images_gives_hint_not_fake_success(wired, value):
    pid = _mk(wired)
    out = _add(property_id=pid, images=value)
    assert out["success"] is False and "没说要加哪些图" in out["error"], out


# ---------- ③ 数组写法 ----------
def test_images_accepts_array(wired):
    pid = _mk(wired)
    out = _add(property_id=pid, images=[_img("a.jpg"), _img("b.jpg")])
    assert out["success"] is True and out["added_count"] == 2, out
    assert out["property"]["images"] == f"{_archived('a.jpg')},{_archived('b.jpg')}", out["property"]["images"]


def test_images_array_elements_trimmed_and_empties_dropped(wired):
    pid = _mk(wired)
    out = _add(property_id=pid, images=[f" {_img('a.jpg')} ", "", "   "])
    assert out["added_count"] == 1, out
    assert out["property"]["images"] == _archived("a.jpg"), out["property"]["images"]


# ---------- ④ 已售/已租也能加 ----------
@pytest.mark.parametrize("status,label", [("sold", "已售"), ("rented", "已租")])
def test_unavailable_property_can_still_take_images(wired, status, label):
    pid = _mk(wired, status=status)
    out = _add(property_id=pid, images=_img("a.jpg"))
    assert out["success"] is True, out
    assert out["added_count"] == 1, out
    warn = " ".join(out.get("warnings") or [])
    assert label in warn and "照样记上了" in warn, out


def test_missing_property_is_reported_clearly(wired):
    out = _add(property_id=999999, images=_img("a.jpg"))
    assert out["success"] is False and "没有编号 999999 的房源" in out["error"], out
    assert "不在售" not in out["error"], out


# ---------- ⑤ 本地文件找不到 ----------
def test_missing_local_file_warns_but_does_not_block(wired):
    pid = _mk(wired)
    out = _add(property_id=pid, images="/tmp/definitely-not-here-9f3a.jpg")
    assert out["success"] is True, out
    warn = " ".join(out.get("warnings") or [])
    assert "没找到" in warn, out


def test_remote_url_does_not_warn(wired):
    pid = _mk(wired)
    out = _add(property_id=pid, images="https://example.com/house.jpg")
    assert out["success"] is True and not out.get("warnings"), out


# ---------- ⑥ 只动自己的那一列 ----------
def test_only_target_row_changes(wired):
    pid = _mk(wired, title="目标房源 1号楼101")
    other = _mk(wired, title="别的房源 2号楼202")
    before = wired.get_property(other)["images"]
    _add(property_id=pid, images=_img("a.jpg"))
    after = wired.get_property(other)["images"]
    assert before == after, (before, after)
    assert wired.get_property(pid)["images"] == _archived("a.jpg")


def test_description_and_params_are_documented():
    from tools.registry import registry

    entry = registry.get_entry("add_property_images")
    desc = entry.schema["description"]
    assert "已售" in desc and "数组" in desc and "重复" in desc, desc
    props = entry.schema["parameters"]["properties"]
    assert "逗号" in props["images"]["description"], props
    assert len(props["property_id"]["description"]) > 8, props
