#!/usr/bin/env python3
"""房源照片存量修复：把还活着的缓存照片补进归档，再从备份图片包捞回已经丢掉的（默认只演练）。

背景（2026-09-26 查清）：网关每小时清理一次「最后修改时间超过 24 小时」的图片缓存文件，而历史
房源的照片路径正指着那个目录 → 老记录里的照片可能已经不在磁盘上。新录入的照片从此会先归档
（见 agent/real_estate_media.py），本脚本负责把**存量**补齐：

① 补齐归档：库里指着缓存目录、文件还在的照片 → 复制进归档目录并改库路径（从此免疫清理）；
② 从备份捞回：文件已经不在本机、但备份图片包（`~/backups/real_estate/real_estate_images_*.tar.gz`）
   里有同名文件的 → 解出来放进归档目录并改库路径。

**不删任何东西**：既不在本机、备份里也找不到的，保留原路径并单独列出来，由人决定怎么处理。

用法（在已装实例的服务器上跑）：
    python3 scripts/recover_property_images.py                 # 默认演练：只打清单
    python3 scripts/recover_property_images.py --limit 20      # 只看前 20 条
    python3 scripts/recover_property_images.py --apply         # 真正落盘 + 写库
    --backup-dir /path/to/backups --archive-dir /path/to/archive   # 覆盖默认位置
"""
from __future__ import annotations

import argparse
import os
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from backfill_floor_from_title import _load_env_db  # noqa: E402 —— 同一套 .env.db 读取口径


def _tar_index(backup_dir: Path):
    """备份图片包索引：{文件名: (包路径, 包内成员名)}，新包优先（后写入的覆盖先写入的）"""
    index = {}
    for tar_path in sorted(backup_dir.glob("real_estate_images_*.tar.gz")):
        try:
            with tarfile.open(tar_path) as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        index.setdefault(Path(member.name).name, (tar_path, member.name))
        except (tarfile.TarError, OSError) as exc:
            print(f"  ⚠️ 备份包读不了，跳过：{tar_path.name}（{exc}）")
    return index


def _extract_to_archive(tar_path: Path, member_name: str, archive_dir: Path, *, copy: bool = True):
    """把备份包里的成员解到归档目录（按内容指纹命名，与录入时的命名一致）；copy=False 只算目标名"""
    import hashlib

    with tarfile.open(tar_path) as tar:
        handle = tar.extractfile(member_name)
        if handle is None:
            return None
        data = handle.read()
    digest = hashlib.sha1(data).hexdigest()[:8]
    stem = Path(member_name).stem
    ext = Path(member_name).suffix.lower() or ".jpg"
    target = archive_dir / f"{stem}_{digest}{ext}"
    if copy and not target.exists():
        target.write_bytes(data)
    return target


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="房源照片存量修复（默认只演练）")
    ap.add_argument("--apply", action="store_true", help="真正落盘 + 写库（默认只演练）")
    ap.add_argument("--dry-run", action="store_true", help="显式演练（默认行为）")
    ap.add_argument("--limit", type=int, default=0, help="只看前 N 条照片路径（便于抽样）")
    ap.add_argument("--backup-dir", default=None, help="备份目录（默认 ~/backups/real_estate）")
    ap.add_argument("--archive-dir", default=None, help="归档目录（默认取系统口径）")
    args = ap.parse_args(argv)

    _load_env_db()
    if not os.environ.get("DATABASE_URL"):
        print("  提示：没有 DATABASE_URL（可在安装目录放 .env.db，或用 COCO_ENV_FILE 指向它）")
        return 2

    from agent.real_estate_db import get_real_estate_db
    from agent.real_estate_media import archive_one, images_archive_dir

    archive_dir = Path(args.archive_dir) if args.archive_dir else images_archive_dir()
    archive_dir.mkdir(parents=True, exist_ok=True)
    backup_dir = Path(args.backup_dir) if args.backup_dir else Path.home() / "backups" / "real_estate"

    db = get_real_estate_db()
    # status="all"：已售/已租的房源照片同样要修（资料补录也在用）
    props = db.search_properties(status="all", limit=200000)

    scanned = repaired_archive = repaired_backup = already = unresolved = 0
    planned, missing_items, updated_ids = [], [], []

    print(f"  归档目录：{archive_dir}")
    print(f"  备份目录：{backup_dir}（{len(list(backup_dir.glob('real_estate_images_*.tar.gz')))} 个图片包）")
    print(f"  房源 {len(props)} 套，开始核对照片……")

    tar_index = None
    for prop in props:
        paths = [x.strip() for x in (prop.get("images") or "").split(",") if x.strip()]
        if not paths:
            continue
        new_paths = []
        for path in paths:
            if args.limit and scanned >= args.limit:
                new_paths.append(path)
                continue
            scanned += 1
            # 演练时不落盘（copy=False）—— 演练必须没有副作用，否则第二次执行会误以为「已经归档过了」
            stored, status = archive_one(path, copy=args.apply)
            if status in ("copied", "to-copy", "already", "url"):
                new_paths.append(stored)
                if status in ("copied", "to-copy"):
                    repaired_archive += 1
                    planned.append(("归档", prop.get("id"), path, stored))
                elif status == "already" and stored != path:
                    planned.append(("改库路径（文件已在归档目录）", prop.get("id"), path, stored))
                else:
                    already += 1
                continue
            # 本机没有：去备份图片包里找同名文件
            if tar_index is None:
                tar_index = _tar_index(backup_dir)
            hit = tar_index.get(Path(path).name)
            if hit:
                tar_path, member_name = hit
                target = _extract_to_archive(tar_path, member_name, archive_dir, copy=args.apply)
                if target:
                    repaired_backup += 1
                    planned.append(("捞回", prop.get("id"), path, str(target)))
                    new_paths.append(str(target))
                    continue
            unresolved += 1
            missing_items.append((prop.get("id"), path))
            new_paths.append(path)
        changed = any(new != old for new, old in zip(new_paths, paths))
        if changed:
            updated_ids.append(prop.get("id"))
            if args.apply:
                db.update_property(prop.get("id"), images=",".join(new_paths))

    mode = "执行" if args.apply else "演练（未落盘、未写库）"
    print()
    print(f"  [{mode}] 核对照片 {scanned} 张：")
    print(f"    已在归档目录/链接（不用动）: {already}")
    print(f"    补进归档（从缓存搬）      : {repaired_archive}")
    print(f"    从备份包捞回              : {repaired_backup}")
    print(f"    仍找不回来（保留原路径）  : {unresolved}")
    print(f"    需要改库的房源            : {len(updated_ids)} 套")
    if planned:
        print("    前几条处理明细：")
        for action, pid, old, new in planned[:10]:
            print(f"      [{action}] 房源 {pid}：{old} → {new}")
    if missing_items:
        print("    找不回来的照片（不会自动删除，需要人工决定）：")
        for pid, path in missing_items[:10]:
            print(f"      房源 {pid}：{path}")
        if len(missing_items) > 10:
            print(f"      …… 另有 {len(missing_items) - 10} 条")
    if not args.apply and (repaired_archive or repaired_backup):
        print()
        print("  这是演练。确认无误后加 --apply 真正执行（会复制照片并更新数据库里的图片路径）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
