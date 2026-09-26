"""迁移包打包（`coco backup --migration <文件>`）的回归测试。

背景：图片包改成按份数滚动后，每份都是**全量**。文档里原来那条手写 tar 用的是通配符
`real_estate_images_*.tar.gz`，会把两份全量副本都装进迁移包（白白翻倍体积），而恢复端本来只用
最新那份。现在收口成一条命令：最新的数据库备份 + 最新的图片包 + 密钥。
"""
import os
import sys
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import backup_db  # noqa: E402


def _fake_backup_dir(tmp_path, *, dumps=2, images=2, key=True):
    bk = tmp_path / "backups"
    bk.mkdir(parents=True, exist_ok=True)
    stamps = ("20260925_020000", "20260926_020000")
    for stamp in (stamps[-dumps:] if dumps else ()):
        (bk / f"real_estate_{stamp}.dump").write_bytes(b"dump-" + stamp.encode())
    for stamp in (stamps[-images:] if images else ()):
        (bk / f"real_estate_images_{stamp}.tar.gz").write_bytes(b"images-" + stamp.encode())
    if key:
        (bk / "enc_key.txt").write_text("COCO_ENC_KEY=xxx\n", encoding="utf-8")
    return bk


def _mgr(tmp_path, backup_dir):
    return backup_db.DatabaseBackup(database_url="postgresql://u:p@localhost/db",
                                    backup_dir=str(backup_dir))


def test_migration_pack_takes_newest_of_each(tmp_path):
    bk = _fake_backup_dir(tmp_path)
    out = tmp_path / "coco_migration.tar.gz"

    assert _mgr(tmp_path, bk).pack_migration(str(out)) is True

    with tarfile.open(out) as tar:
        names = sorted(tar.getnames())
    assert names == ["enc_key.txt", "real_estate_20260926_020000.dump",
                     "real_estate_images_20260926_020000.tar.gz"], names
    assert oct(out.stat().st_mode)[-3:] == "600", "迁移包含密钥，权限要收紧"


def test_migration_pack_without_dump_fails_loudly(tmp_path):
    bk = _fake_backup_dir(tmp_path, dumps=0)
    out = tmp_path / "coco_migration.tar.gz"

    assert _mgr(tmp_path, bk).pack_migration(str(out)) is False
    assert not out.exists()


def test_migration_pack_reports_missing_pieces(tmp_path, capsys):
    """没有图片包/密钥时照样能打包，但必须说清缺了什么（不能静默少装）"""
    bk = _fake_backup_dir(tmp_path, images=0, key=False)
    out = tmp_path / "coco_migration.tar.gz"

    assert _mgr(tmp_path, bk).pack_migration(str(out)) is True

    with tarfile.open(out) as tar:
        assert tar.getnames() == ["real_estate_20260926_020000.dump"]
    log = (bk / "backup.log").read_text(encoding="utf-8")
    assert "缺 图片包、enc_key.txt" in log, log


def test_cli_backup_migration_flag_writes_bundle(tmp_path, monkeypatch):
    """`backup --migration <文件>`：参数被接住，并且先走了一次常规备份（这里桩掉，不连真库）"""
    bk = _fake_backup_dir(tmp_path)
    called = {}
    monkeypatch.setattr(backup_db.DatabaseBackup, "backup",
                        lambda self, force=False: called.setdefault("backup", True) or True)
    out = tmp_path / "cli_migration.tar.gz"

    with pytest.raises(SystemExit) as exc:
        monkeypatch.setattr(sys, "argv", ["backup_db.py", "backup",
                                          "--db-url", "postgresql://u:p@localhost/db",
                                          "--backup-dir", str(bk), "--migration", str(out)])
        backup_db.main()

    assert (exc.value.code or 0) == 0, exc.value.code
    assert called.get("backup") is True, "打迁移包之前要先常规备份一次"
    with tarfile.open(out) as tar:
        names = tar.getnames()
    assert "real_estate_20260926_020000.dump" in names
    assert "real_estate_images_20260926_020000.tar.gz" in names
    assert os.path.exists(out)
