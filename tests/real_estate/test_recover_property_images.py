"""房源照片存量修复脚本的回归测试（2026-09-26 加）。

背景：网关每小时清理「最后修改时间超过 24 小时」的图片缓存文件，历史房源的照片路径正指着那个目录。
脚本负责：① 把还活着的缓存照片补进归档目录并改库路径；② 从备份图片包按文件名捞回已丢的照片。
口径（定）：**默认只演练，演练必须没有副作用**；不删任何东西，找不回来的保留原路径并列出。

真实踩过的坑：第一版演练里也真的把文件复制进归档目录（archive_one 的副作用），于是紧随其后的
--apply 会把这些照片当成「已在归档目录」，**数据库路径永远得不到更新** —— 演练和执行的结论不一致。
现在 archive_one(copy=False) 只算目标文件名，不落盘。
"""
import os
import re
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "recover_property_images.py"


def _count(out: str, label: str):
    m = re.search(re.escape(label) + r"\s*: (\d+)", out)
    return int(m.group(1)) if m else None


def _seed(tmp_path):
    """造三套：① 缓存里还活着的照片 ② 文件已丢但备份包里有 ③ 彻底找不回来"""
    db_path = tmp_path / "re.db"
    url = f"sqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    from agent.real_estate_db import RealEstateDB, init_real_estate_db

    cache = tmp_path / "image_cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "img_alive.jpg").write_bytes(b"alive-photo")

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "img_lost.jpg").write_bytes(b"recovered-photo")
    with tarfile.open(backup_dir / "real_estate_images_20260920_000000.tar.gz", "w:gz") as tar:
        tar.add(stage / "img_lost.jpg", arcname="images/img_lost.jpg")

    init_real_estate_db(url)
    inst = RealEstateDB(url)
    inst.add_property(title="修复测试 1号楼101", price=1_000_000, area=80.0,
                      images=str(cache / "img_alive.jpg"))
    inst.add_property(title="修复测试 2号楼202", price=1_200_000, area=90.0,
                      images=str(cache / "img_lost.jpg"))
    inst.add_property(title="修复测试 3号楼303", price=1_300_000, area=95.0,
                      images=str(cache / "img_nowhere.jpg"))
    inst.add_property(title="修复测试 4号楼404", price=1_400_000, area=99.0,
                      images="https://example.com/remote.jpg")
    return url, cache, backup_dir, tmp_path / "archive"


def _run(url, archive_dir, backup_dir, *args):
    env = {**os.environ, "DATABASE_URL": url, "COCO_ENV_FILE": "/nonexistent",
           "COCO_IMAGES_DIR": str(archive_dir)}
    return subprocess.run([sys.executable, str(SCRIPT), "--backup-dir", str(backup_dir), *args],
                          capture_output=True, text=True, cwd=REPO_ROOT, env=env)


def _images(url):
    con = sqlite3.connect(url.replace("sqlite:///", ""))
    return dict(con.execute("select title, images from re_properties"))


def test_dry_run_changes_nothing(tmp_path):
    """演练必须没有副作用：不落盘、不写库（第一版就是在这里翻的车）"""
    url, cache, backup_dir, archive = _seed(tmp_path)
    before = _images(url)

    out = _run(url, archive, backup_dir).stdout

    assert "演练（未落盘、未写库）" in out, out
    assert _count(out, "补进归档（从缓存搬）") == 1, out
    assert _count(out, "从备份包捞回") == 1, out
    assert _count(out, "仍找不回来（保留原路径）") == 1, out
    assert _images(url) == before, "演练不该改库"
    assert not list(archive.glob("*")) if archive.exists() else True, "演练不该往归档目录写文件"


def test_apply_archives_and_recovers(tmp_path):
    url, cache, backup_dir, archive = _seed(tmp_path)

    out = _run(url, archive, backup_dir, "--apply").stdout

    assert _count(out, "补进归档（从缓存搬）") == 1, out
    assert _count(out, "从备份包捞回") == 1, out
    images = _images(url)
    alive = Path(images["修复测试 1号楼101"])
    recovered = Path(images["修复测试 2号楼202"])
    assert alive.parent == archive and alive.read_bytes() == b"alive-photo"
    assert recovered.parent == archive and recovered.read_bytes() == b"recovered-photo"
    assert images["修复测试 3号楼303"].endswith("img_nowhere.jpg"), "找不回来的保留原路径（不删、不臆造）"
    assert images["修复测试 4号楼404"] == "https://example.com/remote.jpg", "网络链接不动"
    assert (cache / "img_alive.jpg").exists(), "源文件不删（缓存目录由网关自己回收）"


def test_apply_is_idempotent(tmp_path):
    url, _cache, backup_dir, archive = _seed(tmp_path)
    _run(url, archive, backup_dir, "--apply")

    out = _run(url, archive, backup_dir, "--apply").stdout

    assert _count(out, "补进归档（从缓存搬）") == 0, out
    assert _count(out, "从备份包捞回") == 0, out
    assert _count(out, "需要改库的房源") == 0, out


def test_db_path_pointing_at_archive_gets_updated(tmp_path):
    """文件已经在归档目录、库里还指着老缓存路径 → 也要改成归档路径（否则 24 小时后照样失效）"""
    url, cache, backup_dir, archive = _seed(tmp_path)
    archive.mkdir(parents=True, exist_ok=True)
    # 手工造出「已归档但库里没更新」的状态
    import subprocess as sp
    sp.run([sys.executable, "-c", (
        "import os,sys;"
        f"sys.path.insert(0, {str(REPO_ROOT)!r});"
        f"os.environ['COCO_IMAGES_DIR']={str(archive)!r};"
        "from agent.real_estate_media import archive_one;"
        f"print(archive_one({str(cache / 'img_alive.jpg')!r})[0])"
    )], check=True, capture_output=True, text=True, cwd=REPO_ROOT)

    out = _run(url, archive, backup_dir, "--apply").stdout

    assert "改库路径（文件已在归档目录）" in out, out
    assert str(Path(_images(url)["修复测试 1号楼101"]).parent) == str(archive)
