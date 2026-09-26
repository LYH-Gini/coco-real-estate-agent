"""房源照片归档：照片进库前搬进不会被清理的目录（2026-09-26）

背景（实测查清）：网关保活线程**每小时**把 `$HERMES_HOME/cache/images`（老实例 `$HERMES_HOME/image_cache`）
里最后修改时间超过 24 小时的文件删掉 —— 而经纪人发来的房源照片就存在那里、`re_properties.images`
存的就是那些路径 ⇒ 照片上传一天后从磁盘消失，库里留下打不开的路径（海报 B 款会反过来找经纪人要照片）。

本文件钉四件事：
1. 归档行为（复制、幂等、链接/缺文件原样保留、同名不同图不互相覆盖）；
2. `add_property` / `add_property_images` 存进库的是**归档后**的路径；
3. 备份把归档目录 / 缓存目录 / **当天**海报都打进去（老实现只认第一个缓存目录、且完全不认归档目录）；
4. 恢复按包内前缀各归各位；图片包按份数滚动保留；备份端与工具层的归档目录口径一致。
"""
import json
import os
import sys
import tarfile
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT  # noqa: F401 —— conftest 已把仓库根塞进 sys.path

from agent.real_estate_media import archive_images, images_archive_dir, is_archived


# ---------- 归档行为 ----------
@pytest.fixture
def archive(tmp_path, monkeypatch):
    """归档目录指向临时目录；返回 (归档目录, 造源文件的函数)"""
    target = tmp_path / "real_estate_images"
    monkeypatch.setenv("COCO_IMAGES_DIR", str(target))

    def _make_cache_photo(name: str, data: bytes = b"photo-bytes"):
        src_dir = tmp_path / "cache" / "images"
        src_dir.mkdir(parents=True, exist_ok=True)
        path = src_dir / name
        path.write_bytes(data)
        return path

    return target, _make_cache_photo


def test_local_photo_is_copied_into_archive(archive):
    target, make = archive
    src = make("img_abc123.jpg")
    got, detail = archive_images(str(src))

    archived = Path(got)
    assert archived.parent == target
    assert archived.exists() and archived.read_bytes() == b"photo-bytes"
    assert archived.name.startswith("img_abc123_") and archived.suffix == ".jpg"
    assert detail["archived"] == 1
    assert src.exists(), "原缓存文件要留着（消息里引用的还是它，24 小时后由网关自己回收）"


def test_same_photo_twice_does_not_duplicate(archive):
    target, make = archive
    src = make("img_abc123.jpg")
    first, _ = archive_images(str(src))
    second, detail = archive_images(str(src))

    assert first == second
    assert detail["archived"] == 0 and detail["already"] == 1
    assert len(list(target.iterdir())) == 1


def test_already_archived_path_is_untouched(archive):
    _target, make = archive
    src = make("img_abc123.jpg")
    archived, _ = archive_images(str(src))

    again, detail = archive_images(archived)
    assert again == archived
    assert detail["archived"] == 0


def test_url_and_missing_path_keep_original_value(archive):
    target, _make = archive
    url = "https://example.com/a.jpg"
    missing = "/tmp/does-not-exist-coco.jpg"
    got, detail = archive_images(f"{url},{missing}")

    assert got == f"{url},{missing}"
    assert detail["missing"] == [missing]
    assert not any(target.iterdir()), "链接和找不到的文件都不该在归档目录里造东西"


def test_same_name_different_content_does_not_overwrite(archive):
    target, make = archive
    first, _ = archive_images(str(make("img_same.jpg", b"first")))
    second, _ = archive_images(str(make("img_same.jpg", b"second")))

    assert first != second
    assert {Path(first).read_bytes(), Path(second).read_bytes()} == {b"first", b"second"}
    assert len(list(target.iterdir())) == 2


def test_empty_value_stays_empty(archive):
    assert archive_images(None) == (None, {"archived": 0, "already": 0, "missing": [], "failed": []})
    assert archive_images("  ")[0] is None


def test_archive_dir_reads_env_at_call_time(tmp_path, monkeypatch):
    """目录要现算（读环境变量），不能缓存在 import 期 —— 否则多 profile / 改配置都不生效"""
    monkeypatch.setenv("COCO_IMAGES_DIR", str(tmp_path / "one"))
    assert images_archive_dir() == tmp_path / "one"
    monkeypatch.setenv("COCO_IMAGES_DIR", str(tmp_path / "two"))
    assert images_archive_dir() == tmp_path / "two"
    assert is_archived(str(tmp_path / "two" / "img_x.jpg"))


# ---------- 导入源文件留档（Excel / 合同这类） ----------
def test_document_is_copied_into_docs_archive(tmp_path, monkeypatch):
    from agent.real_estate_media import archive_document, documents_archive_dir

    monkeypatch.setenv("COCO_DOCS_DIR", str(tmp_path / "docs"))
    src = tmp_path / "房源表.xlsx"
    src.write_bytes(b"xlsx-bytes")

    stored, status = archive_document(str(src))

    assert status == "copied"
    assert Path(stored).parent == documents_archive_dir() == tmp_path / "docs"
    assert Path(stored).read_bytes() == b"xlsx-bytes"
    assert Path(stored).name.endswith("_房源表.xlsx"), Path(stored).name
    assert src.exists(), "原文件不动"


def test_document_archive_is_idempotent_and_missing_is_reported(tmp_path, monkeypatch):
    from agent.real_estate_media import archive_document, documents_archive_dir

    monkeypatch.setenv("COCO_DOCS_DIR", str(tmp_path / "docs"))
    src = tmp_path / "合同.pdf"
    src.write_bytes(b"pdf")

    first, _ = archive_document(str(src))
    second, status = archive_document(str(src))
    assert first == second and status == "already"
    assert len(list(documents_archive_dir().iterdir())) == 1

    gone, missing_status = archive_document(str(tmp_path / "没有这个文件.xlsx"))
    assert missing_status == "missing" and gone.endswith("没有这个文件.xlsx")


# ---------- 工具层：入库的是归档后的路径 ----------
def _registry(tmp_path, monkeypatch, archive_dir):
    url = f"sqlite:///{tmp_path}/image_archive.db"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("COCO_IMAGES_DIR", str(archive_dir))
    from agent.real_estate_db import init_real_estate_db

    init_real_estate_db(url)
    import model_tools  # noqa: F401 —— 触发工具发现与注册

    from tools.registry import registry

    return registry


def _call(registry, name, args):
    return json.loads(registry.get_entry(name).handler(args, session_id="agent:main:feishu:dm:oc_x"))


def test_add_property_stores_archived_path(tmp_path, monkeypatch):
    archive_dir = tmp_path / "real_estate_images"
    registry = _registry(tmp_path, monkeypatch, archive_dir)
    photo = tmp_path / "cache" / "images" / "img_777aaa.jpg"
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(b"front-photo")

    out = _call(registry, "add_property", {
        "title": "归档测试 1号楼2单元101", "price": 1_000_000, "area": 88.0,
        "image_paths": str(photo),
    })

    assert out["success"] is True
    stored = out["property"]["images"]
    assert stored.startswith(str(archive_dir)), f"库里应存归档路径，实际 {stored}"
    assert Path(stored).exists()
    assert "image_archive" not in out, "归档成功时不该给告警字段"


def test_add_property_images_archives_and_keeps_missing_warning(tmp_path, monkeypatch):
    archive_dir = tmp_path / "real_estate_images"
    registry = _registry(tmp_path, monkeypatch, archive_dir)
    created = _call(registry, "add_property", {"title": "归档测试 2号楼101", "price": 900_000, "area": 70.0})
    pid = created["property"]["id"]
    photo = tmp_path / "cache" / "images" / "img_888bbb.jpg"
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(b"living-room")

    out = _call(registry, "add_property_images", {"property_id": pid, "images": str(photo)})
    assert out["added_count"] == 1
    stored = out["property"]["images"]
    assert stored.startswith(str(archive_dir)) and Path(stored).exists()

    missing = _call(registry, "add_property_images",
                    {"property_id": pid, "images": "/tmp/not-here-coco.jpg"})
    warnings = " ".join(missing.get("warnings") or [])
    assert "没找到" in warnings, "找不到的本地文件仍要如实提示（口径不变）"


# ---------- 备份 / 恢复 ----------
def _backup_module():
    scripts = Path(REPO_ROOT) / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import backup_db

    return backup_db


def _fake_home(tmp_path, monkeypatch):
    """把 HOME 与 HERMES_HOME 都指到临时目录，备份/工具两侧解析到同一个地方"""
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(home / ".hermes"))
    monkeypatch.delenv("COCO_IMAGES_DIR", raising=False)
    return home / ".hermes"


@pytest.fixture
def backup_env(tmp_path, monkeypatch):
    hermes = _fake_home(tmp_path, monkeypatch)
    backup_db = _backup_module()
    mgr = backup_db.DatabaseBackup(database_url="postgresql://u:p@localhost/db",
                                   backup_dir=str(tmp_path / "bk"))
    return mgr, hermes, backup_db


def _write(path: Path, data: bytes = b"x", age_days: float = 0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if age_days:
        ts = time.time() - age_days * 86400
        os.utime(path, (ts, ts))
    return path


def test_backup_packs_archive_cache_and_todays_posters(backup_env):
    mgr, hermes, _ = backup_env
    _write(hermes / "real_estate_images" / "img_archived_11111111.jpg")
    _write(hermes / "real_estate_docs" / "20260926_abcdef12_房源表.xlsx")
    _write(hermes / "image_cache" / "img_legacy.jpg")
    _write(hermes / "cache" / "images" / "img_new_layout.jpg")
    _write(hermes / "posters" / "poster_1_A_today.png")
    _write(hermes / "posters" / "poster_1_A_yesterday.png", age_days=1)

    name = mgr._backup_images("20260926_000000")
    with tarfile.open(Path(mgr.backup_dir) / name) as tar:
        names = set(tar.getnames())

    assert "real_estate_images/img_archived_11111111.jpg" in names
    assert "real_estate_docs/20260926_abcdef12_房源表.xlsx" in names, "导入用源文件也要进包（留档才有意义）"
    assert "images/img_legacy.jpg" in names, "老缓存目录要进包"
    assert "images/img_new_layout.jpg" in names, "新缓存目录也要进包（老实现只认第一个目录）"
    assert "posters/poster_1_A_today.png" in names
    assert "posters/poster_1_A_yesterday.png" not in names, "海报只备份当天那份"


def test_backup_archive_dir_matches_tool_layer(backup_env):
    """备份端与工具层的归档目录必须同口径（只允许一处实现，不许漂移）"""
    mgr, _hermes, _ = backup_env
    assert mgr._archive_dir() == images_archive_dir()


def test_restore_routes_members_back_to_their_dirs(backup_env):
    mgr, hermes, _ = backup_env
    src_tar = Path(mgr.backup_dir) / "real_estate_images_20260926_010101.tar.gz"
    src_tar.parent.mkdir(parents=True, exist_ok=True)
    staged = [("real_estate_images/img_archived_22222222.jpg", b"archived"),
              ("real_estate_docs/20260926_abcdef12_房源表.xlsx", b"doc"),
              ("images/img_legacy.jpg", b"legacy"),
              ("posters/poster_1_A_x.png", b"poster")]
    with tarfile.open(src_tar, "w:gz") as tar:
        for arcname, data in staged:
            blob = src_tar.parent / Path(arcname).name
            blob.write_bytes(data)
            tar.add(blob, arcname=arcname)
            blob.unlink()

    assert mgr.restore_images(src_tar.name) is True
    assert (hermes / "real_estate_images" / "img_archived_22222222.jpg").read_bytes() == b"archived"
    assert (hermes / "real_estate_docs" / "20260926_abcdef12_房源表.xlsx").read_bytes() == b"doc"
    assert (hermes / "posters" / "poster_1_A_x.png").read_bytes() == b"poster"
    cache_hit = any((hermes / d / "img_legacy.jpg").exists()
                    for d in ("image_cache", "cache/images"))
    assert cache_hit, "老包里的 images/ 按老规矩解到缓存目录"


def test_old_backup_without_prefixes_still_restores(backup_env):
    """老备份包只有 `images/xxx`（没有前缀区分）也要能恢复 —— 向后兼容"""
    mgr, hermes, _ = backup_env
    old_tar = Path(mgr.backup_dir) / "real_estate_images_20260101_000000.tar.gz"
    old_tar.parent.mkdir(parents=True, exist_ok=True)
    blob = old_tar.parent / "img_old.jpg"
    blob.write_bytes(b"old")
    with tarfile.open(old_tar, "w:gz") as tar:
        tar.add(blob, arcname="images/img_old.jpg")
    blob.unlink()

    assert mgr.restore_images(old_tar.name) is True
    assert any((hermes / d / "img_old.jpg").exists() for d in ("image_cache", "cache/images"))


def test_image_tars_keep_only_the_newest(backup_env):
    """图片包装的是全部照片归档，按份数滚动保留（否则「照片总量 × 天数」线性堆磁盘）"""
    mgr, _hermes, _ = backup_env
    assert mgr.keep_image_tars == 2
    for stamp in ("20260920_000000", "20260921_000000", "20260922_000000", "20260923_000000"):
        (Path(mgr.backup_dir) / f"real_estate_images_{stamp}.tar.gz").write_bytes(b"x")

    mgr._cleanup_old_image_tars()
    left = sorted(p.name for p in Path(mgr.backup_dir).glob("real_estate_images_*.tar.gz"))
    assert left == ["real_estate_images_20260922_000000.tar.gz",
                    "real_estate_images_20260923_000000.tar.gz"]


def test_image_tar_rotation_never_touches_dumps(backup_env):
    mgr, _hermes, _ = backup_env
    dump = Path(mgr.backup_dir) / "real_estate_20260901_020000.dump"
    dump.parent.mkdir(parents=True, exist_ok=True)
    dump.write_bytes(b"dump")
    mgr._cleanup_old_image_tars()

    assert dump.exists(), "图片包轮转不许动数据库备份（数据库备份按天留 30 天）"


def test_zero_keeps_all_image_tars(tmp_path, monkeypatch):
    _fake_home(tmp_path, monkeypatch)
    backup_db = _backup_module()
    mgr = backup_db.DatabaseBackup(database_url="postgresql://u:p@localhost/db",
                                   backup_dir=str(tmp_path / "bk"), keep_image_tars=0)
    for stamp in ("20260920_000000", "20260921_000000"):
        (Path(mgr.backup_dir) / f"real_estate_images_{stamp}.tar.gz").write_bytes(b"x")
    mgr._cleanup_old_image_tars()

    assert len(list(Path(mgr.backup_dir).glob("real_estate_images_*.tar.gz"))) == 2
