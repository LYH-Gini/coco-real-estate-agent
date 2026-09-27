#!/usr/bin/env python3
"""
Coco 房产智能体 - 数据库备份脚本（PostgreSQL 版）
支持定时自动备份、数据库备份保留30天、图片包保留最近2份、数据变化检查、备份日志
使用 pg_dump 导出，恢复用 pg_restore

`backup --migration <文件>` 会把**最新的**数据库备份 + **最新的**图片包 + 加密密钥打成一个迁移包
（换机器 / 重装系统用）；图片包每份都是全量，所以只装最新那份。
"""
import os
import subprocess
import sys
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

# 安装目录按本文件位置推导（2026-09-21 自定位，改目录名不用改代码）
_REPO_ROOT = Path(__file__).resolve().parent.parent

# 控制台输出抗编码：Windows 上 stdout 默认跟随控制台代码页（cp1252/GBK），脚本里的中文
# print 会抛 UnicodeEncodeError，把备份/恢复整个带崩（2026-09-20 真机实测：计划任务注册
# 成功、一跑备份就崩在 _log 的 print 上）。这里一次性把两个流改成 UTF-8 + 替换不可编码字符，
# 覆盖脚本内所有 print；日志文件本来就是显式 UTF-8 打开，不受影响。
for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _load_db_config():
    """
    从 .env.db 或环境变量读取数据库配置
    优先级: 环境变量 DATABASE_URL > .env.db 文件
    """
    database_url = os.getenv("DATABASE_URL", "")
    if database_url:
        return database_url

    # 尝试从 .env.db 读取
    candidates = [
        _REPO_ROOT / ".env.db",
        Path.cwd() / ".env.db",
    ]
    for env_file in candidates:
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip()
    return ""


def _is_today(path: Path) -> bool:
    """文件的最后修改时间是不是「今天」（按本机时区；海报只备份当天那份）"""
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return False
    now = datetime.now()
    return (modified.year, modified.month, modified.day) == (now.year, now.month, now.day)


class DatabaseBackup:
    """PostgreSQL 数据库备份管理器"""

    def __init__(self, database_url: str = None, backup_dir: str = None,
                 keep_image_tars: int = 2):
        """
        初始化备份管理器

        Args:
            database_url: PostgreSQL 连接串（默认从 .env.db 读取）
            backup_dir: 备份目录（默认 ~/backups/real_estate/）
            keep_image_tars: 图片包保留份数（默认 2；<=0 表示不自动清理）
        """
        self.database_url = database_url or _load_db_config()
        if not self.database_url:
            raise RuntimeError("无法获取 DATABASE_URL，请检查 .env.db 或环境变量")
        self.keep_image_tars = keep_image_tars

        if backup_dir:
            self.backup_dir = Path(backup_dir)
        else:
            self.backup_dir = Path.home() / "backups" / "real_estate"

        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.backup_dir / "backup.log"
        self.hash_file = self.backup_dir / ".last_hash"

    def _log(self, message: str):
        """写入日志"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] {message}"
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(log_line + "\n")
        print(log_line)

    def _run_pg_dump(self, out_path: Path) -> bool:
        """执行 pg_dump 导出到文件"""
        env = os.environ.copy()
        env["PGPASSWORD"] = self._extract_password()
        cmd = [
            "pg_dump",
            "-Fc",  # 自定义格式，便于 pg_restore
            "--no-owner",
            "--no-privileges",
            "-f", str(out_path),
            self._extract_conn(),
        ]
        try:
            result = subprocess.run(cmd, env=env, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=300)
            if result.returncode != 0:
                self._log(f"pg_dump 失败: {result.stderr.strip()[:500]}")
                return False
            return True
        except FileNotFoundError:
            self._log("错误: 未找到 pg_dump，请确认 postgresql-client 已安装")
            return False
        except subprocess.TimeoutExpired:
            self._log("错误: pg_dump 超时")
            return False

    def _extract_conn(self) -> str:
        """从 URL 提取连接串（去掉密码、保留用户名，pg_dump 用 PGPASSWORD 传密码）"""
        url = self.database_url
        if "@" in url:
            prefix, rest = url.split("@", 1)
            # prefix 形如 postgresql://user:password 或 postgresql://user
            if "://" in prefix:
                scheme, cred = prefix.split("://", 1)
                if ":" in cred:
                    user = cred.split(":", 1)[0]
                    return f"{scheme}://{user}@{rest}"
                return f"{scheme}://{cred}@{rest}"
            if ":" in prefix:
                scheme = prefix.split(":", 1)[0]
                return f"{scheme}://{rest}"
        return url

    def _extract_password(self) -> str:
        """从 URL 提取密码"""
        url = self.database_url
        if "@" in url:
            prefix = url.split("@", 1)[0]
            if ":" in prefix:
                parts = prefix.split(":", 2)
                if len(parts) == 3:
                    return parts[2]
        return ""

    def _get_db_snapshot(self) -> str:
        """计算数据库变化指纹（表行数 + 最大更新时间）"""
        env = os.environ.copy()
        env["PGPASSWORD"] = self._extract_password()
        sql = (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' ORDER BY table_name"
        )
        try:
            result = subprocess.run(
                ["psql", self._extract_conn(), "-t", "-A", "-c", sql],
                env=env, capture_output=True, text=True, timeout=60,
            )
            tables = [t.strip() for t in result.stdout.splitlines() if t.strip()]
            if not tables:
                return "empty"
            # 每张表行数
            parts = []
            for t in tables:
                r = subprocess.run(
                    ["psql", self._extract_conn(), "-t", "-A", "-c",
                     f'SELECT count(*) FROM "{t}"'],
                    env=env, capture_output=True, text=True, timeout=60,
                )
                parts.append(f"{t}={r.stdout.strip()}")
            return "|".join(parts)
        except Exception as e:
            return f"error:{e}"

    def _has_changed(self) -> bool:
        """检查数据是否变化"""
        current = self._get_db_snapshot()
        if not self.hash_file.exists():
            return True
        last = self.hash_file.read_text(encoding="utf-8").strip()
        return current != last

    def _save_hash(self):
        """保存当前数据指纹"""
        self.hash_file.write_text(self._get_db_snapshot(), encoding="utf-8")

    def pack_migration(self, out_path: str) -> bool:
        """把最新的数据库备份 + 最新的图片包 + 加密密钥打成一个迁移包（换机器 / 重装系统用）

        图片包每份都是**全量**，所以只装最新那份 —— 装两份等于把同一份照片打两遍，白白翻倍体积，
        而恢复端本来也只用最新那份。
        """
        import tarfile

        dumps = sorted(self.backup_dir.glob("real_estate_*.dump"))
        if not dumps:
            self._log("迁移包没打成: 备份目录里没有数据库备份（先执行 coco backup）")
            return False
        newest_dump = dumps[-1]
        image_tars = sorted(self.backup_dir.glob("real_estate_images_*.tar.gz"))
        newest_images = image_tars[-1] if image_tars else None
        key_file = self.backup_dir / "enc_key.txt"

        items = [newest_dump]
        if newest_images:
            items.append(newest_images)
        if key_file.exists():
            items.append(key_file)

        out = Path(out_path).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(out, "w:gz") as tar:
                for item in items:
                    tar.add(item, arcname=item.name)
            os.chmod(out, 0o600)
        except Exception as e:
            self._log(f"迁移包没打成: {e}")
            return False

        self._log(f"迁移包已生成: {out}（{out.stat().st_size / 1024 / 1024:.1f} MB）")
        for item in items:
            self._log(f"  已装入: {item.name}")
        missing = [name for name, present in (("图片包", newest_images), ("enc_key.txt", key_file.exists()))
                   if not present]
        if missing:
            self._log(f"  注意: 缺 {'、'.join(missing)}（数据库能恢复，缺的这几样要单独备份）")
        return True

    def _cleanup_old_backups(self, keep_days: int = 30):
        """清理过期的数据库备份（按天保留，默认 30 天）"""
        cutoff_date = datetime.now() - timedelta(days=keep_days)
        for backup_file in self.backup_dir.glob("real_estate_*.dump"):
            try:
                date_str = backup_file.stem.replace("real_estate_", "")
                file_date = datetime.strptime(date_str, "%Y%m%d_%H%M%S")
                if file_date < cutoff_date:
                    backup_file.unlink()
                    self._log(f"删除旧备份: {backup_file.name}")
            except ValueError:
                continue

    def _cleanup_old_image_tars(self, keep: int = None):
        """图片包按**份数**滚动保留（默认 2 份）

        为什么不是按天：图片包装的是**全部**房源照片归档，每天一份会按「照片总量 × 天数」线性堆磁盘
        （一份 2GB 的归档，留 30 天就是 60GB）。归档目录本身在磁盘上，图片包是第二份（迁移/换机器用），
        留最近两份足够覆盖「今天的备份坏了还能用昨天的」。
        """
        keep = self.keep_image_tars if keep is None else keep
        if keep is None or keep <= 0:
            return
        tars = sorted(self.backup_dir.glob("real_estate_images_*.tar.gz"))
        for old_tar in tars[:-keep]:
            try:
                old_tar.unlink()
                self._log(f"删除旧备份: {old_tar.name}")
            except OSError:
                continue

    def backup(self, force: bool = False) -> bool:
        """执行备份"""
        self._log("=" * 50)
        self._log("开始数据库备份")

        # 检查数据变化
        if not force and not self._has_changed():
            self._log("数据无变化，跳过备份")
            return True

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"real_estate_{timestamp}.dump"
        backup_path = self.backup_dir / backup_filename

        try:
            if not self._run_pg_dump(backup_path):
                return False

            os.chmod(backup_path, 0o600)
            self._save_hash()
            self._cleanup_old_backups()

            # 备份房源图片目录（tar.gz，与数据库备份同名）
            image_tar = self._backup_images(timestamp)
            self._cleanup_old_image_tars()
            if image_tar:
                file_size = backup_path.stat().st_size
                self._log(f"备份成功: {backup_filename} ({file_size} bytes) + 图片 {image_tar}")
            else:
                file_size = backup_path.stat().st_size
                self._log(f"备份成功: {backup_filename} ({file_size} bytes)")
            return True

        except Exception as e:
            self._log(f"备份失败: {str(e)}")
            return False

    def _image_dir_specs(self):
        """要进包的 (tar 内前缀, 目录) —— 备份与恢复共用这一份，避免两边口径漂移。

        - `real_estate_images/`：房源照片归档目录，数据库 `re_properties.images` 存的就是它的路径；
          网关缓存目录里的照片 24 小时后会被自动清理，所以这一份是照片的真正归处，**必须进包**。
        - `images/`：网关媒体缓存（老 `image_cache` 与新 `cache/images` 都认；两个都在就都打包，
          不再像以前那样只认第一个）。
        - `posters/`：海报成品，只打包**当天**的（海报是一次性交付物，隔天的天天进包只会堆垃圾）。
        - `real_estate_docs/`：导入/留档用的源文件（Excel、合同这类）。
        """
        return [
            ("real_estate_images", self._archive_dir()),
            ("real_estate_docs", self._docs_dir()),
            ("images", Path.home() / ".hermes" / "image_cache"),
            ("images", Path.home() / ".hermes" / "cache" / "images"),
            ("images", _REPO_ROOT / ".hermes" / "cache" / "images"),
            ("posters", Path.home() / ".hermes" / "posters"),
        ]

    def _archive_dir(self) -> Path:
        """照片归档目录：口径与工具层同一处（agent/real_estate_media.images_archive_dir）"""
        return self._media_dir("images_archive_dir", Path.home() / ".hermes" / "real_estate_images")

    def _docs_dir(self) -> Path:
        """导入源文件留档目录：同上（agent/real_estate_media.documents_archive_dir）"""
        return self._media_dir("documents_archive_dir", Path.home() / ".hermes" / "real_estate_docs")

    def _media_dir(self, fn_name: str, fallback: Path) -> Path:
        try:
            import sys

            if str(_REPO_ROOT) not in sys.path:
                sys.path.insert(0, str(_REPO_ROOT))
            import agent.real_estate_media as media

            return Path(getattr(media, fn_name)())
        except Exception:  # noqa: BLE001 —— 备份不能因为导不到工具而失败，退回默认位置
            return fallback

    def _image_file_specs(self):
        """本次要进包的 (arcname, 源文件) 列表；没有可打包的文件时返回空列表"""
        specs, seen = [], set()
        for prefix, src in self._image_dir_specs():
            if not src.is_dir():
                continue
            for f in sorted(src.iterdir()):
                if not f.is_file() or f.name.startswith("."):
                    continue
                if prefix == "posters" and not _is_today(f):
                    continue
                arcname = f"{prefix}/{f.name}"
                if arcname in seen:          # 两个缓存目录同名时按先出现的算，不重复入包
                    continue
                seen.add(arcname)
                specs.append((arcname, f))
        return specs

    def _backup_images(self, timestamp: str) -> str:
        """打包照片归档 / 图片缓存 / 当天海报到备份目录，返回 tar 文件名（无文件可打包返回空字符串）"""
        import tarfile
        specs = self._image_file_specs()
        if not specs:
            return ""
        tar_name = f"real_estate_images_{timestamp}.tar.gz"
        tar_path = self.backup_dir / tar_name
        try:
            with tarfile.open(tar_path, "w:gz") as tar:
                for arcname, f in specs:
                    tar.add(f, arcname=arcname)
            os.chmod(tar_path, 0o600)
            return tar_name
        except Exception as e:
            self._log(f"图片备份失败: {e}")
            return ""

    def _restore_target_for(self, prefix: str):
        """tar 内的前缀 → 解包目标目录（与备份端同一份目录清单）"""
        if prefix != "images":
            for known, src in self._image_dir_specs():
                if known == prefix:
                    try:
                        src.mkdir(parents=True, exist_ok=True)
                    except Exception:
                        return None
                    return src if os.access(src, os.W_OK) else None
            return None
        # 其余（含老备份包里的纯 `images/xxx`）继续按老规矩解到第一个可写的缓存目录
        for cand in [Path.home() / ".hermes" / "image_cache",
                     Path.home() / ".hermes" / "cache" / "images",
                     _REPO_ROOT / ".hermes" / "cache" / "images"]:
            try:
                cand.mkdir(parents=True, exist_ok=True)
                if os.access(cand, os.W_OK):
                    return cand
            except Exception:
                continue
        return None

    def restore_images(self, image_tar_filename: str) -> bool:
        """恢复房源图片备份（按 tar 内的前缀解到对应目录：归档/缓存/海报各归各位）"""
        import tarfile
        tar_path = self.backup_dir / image_tar_filename
        if not tar_path.exists():
            self._log(f"错误: 图片备份不存在: {image_tar_filename}")
            return False
        try:
            counts = {}
            with tarfile.open(tar_path, "r:gz") as tar:
                for member in tar.getmembers():
                    if not member.isfile():
                        continue
                    parts = member.name.split("/")
                    target_dir = self._restore_target_for(parts[0] if len(parts) > 1 else "images")
                    if target_dir is None:
                        continue
                    f = tar.extractfile(member)
                    if not f:
                        continue
                    (target_dir / parts[-1]).write_bytes(f.read())
                    counts[target_dir] = counts.get(target_dir, 0) + 1
            if not counts:
                self._log(f"图片恢复失败: {image_tar_filename} 里没有可识别的图片文件")
                return False
            for target_dir, count in counts.items():
                self._log(f"图片恢复成功: {image_tar_filename} -> {target_dir} ({count} 张图片)")
            return True
        except Exception as e:
            self._log(f"图片恢复失败: {e}")
            return False

    def restore(self, backup_filename: str) -> bool:
        """恢复备份"""
        backup_path = self.backup_dir / backup_filename
        if not backup_path.exists():
            self._log(f"错误: 备份文件不存在: {backup_filename}")
            return False

        env = os.environ.copy()
        env["PGPASSWORD"] = self._extract_password()

        # 先删除并重建数据库对象（用 --clean）
        cmd = [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "-d", self._extract_conn(),
            str(backup_path),
        ]
        try:
            result = subprocess.run(cmd, env=env, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=600)
            if result.returncode != 0:
                self._log(f"恢复失败: {result.stderr.strip()[:500]}")
                return False
            self._save_hash()
            self._log(f"恢复成功: {backup_filename}")
            return True
        except FileNotFoundError:
            self._log("错误: 未找到 pg_restore，请确认 postgresql-client 已安装")
            return False
        except subprocess.TimeoutExpired:
            self._log("错误: pg_restore 超时")
            return False

    def _restore_enc_key(self) -> bool:
        """从备份目录的 enc_key.txt 恢复加密密钥到 .env.db（已有则替换，无则追加）"""
        enc_key_file = self.backup_dir / "enc_key.txt"
        if not enc_key_file.exists():
            self._log("错误: 加密密钥备份不存在: enc_key.txt")
            return False

        new_key_line = None
        for line in enc_key_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("COCO_ENC_KEY=") and len(line.split("=", 1)[1].strip()) > 0:
                new_key_line = line
                break
        if not new_key_line:
            self._log("错误: enc_key.txt 中未找到有效的 COCO_ENC_KEY")
            return False

        env_db_candidates = [
            _REPO_ROOT / ".env.db",
            Path.cwd() / ".env.db",
        ]
        env_db = None
        for cand in env_db_candidates:
            if cand.exists():
                env_db = cand
                break
        if env_db is None:
            self._log("错误: 未找到 .env.db，无法合并加密密钥")
            return False

        lines = env_db.read_text(encoding="utf-8").splitlines()
        replaced = False
        for i, line in enumerate(lines):
            if line.strip().startswith("COCO_ENC_KEY="):
                lines[i] = new_key_line
                replaced = True
                break
        if not replaced:
            lines.append(new_key_line)
        env_db.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            os.chmod(env_db, 0o600)
        except OSError:
            pass
        self._log(f"加密密钥已恢复并写入 {env_db}（{'替换' if replaced else '追加'}）")
        return True

    def restore_migration(self, backup_filename: str = None, images_filename: str = None,
                          migration_tar: str = None) -> bool:
        """迁移恢复：一条命令完成 数据库 + 图片 + 加密密钥 恢复

        Args:
            backup_filename: 数据库备份文件名（默认取备份目录最新 dump）
            images_filename: 图片备份文件名（默认取备份目录最新 images tar.gz）
            migration_tar: 迁移打包文件（coco_migration.tar.gz），若指定先解包到备份目录
        """
        if migration_tar:
            import tarfile
            tar_path = Path(migration_tar)
            if not tar_path.exists():
                self._log(f"错误: 迁移打包文件不存在: {migration_tar}")
                return False
            try:
                with tarfile.open(tar_path, "r:gz") as tar:
                    try:
                        tar.extractall(self.backup_dir, filter="data")
                    except TypeError:
                        # Python < 3.12 无 filter 参数，回退（迁移包为自产文件，风险可控）
                        tar.extractall(self.backup_dir)
                self._log(f"迁移包已解包到 {self.backup_dir}: {migration_tar}")
            except Exception as e:
                self._log(f"迁移包解包失败: {e}")
                return False

        if backup_filename is None:
            dumps = sorted(self.backup_dir.glob("real_estate_*.dump"))
            if not dumps:
                self._log("错误: 备份目录没有数据库备份文件")
                return False
            backup_filename = dumps[-1].name
        if images_filename is None:
            images = sorted(self.backup_dir.glob("real_estate_images_*.tar.gz"))
            if images:
                images_filename = images[-1].name

        self._log(f"迁移恢复开始: 数据库={backup_filename} 图片={images_filename or '无'}")

        # 顺序: 先恢复数据库, 再恢复图片, 最后恢复密钥
        if not self.restore(backup_filename):
            self._log("迁移恢复中止: 数据库恢复失败")
            return False

        if images_filename:
            if not self.restore_images(images_filename):
                self._log("迁移恢复中止: 图片恢复失败")
                return False
        else:
            self._log("提示: 未找到图片备份，跳过图片恢复")

        if not self._restore_enc_key():
            self._log("迁移恢复中止: 加密密钥恢复失败")
            return False

        self._log("迁移恢复完成: 数据库 + 图片 + 加密密钥 全部成功")
        print("============================================")
        print("迁移恢复完成！请执行以下步骤：")
        print("  1. sudo systemctl restart hermes-agent")
        print("  2. 给智能体发送\"你好\"，定时任务会自动注册")
        print("============================================")
        return True

    def list_backups(self) -> list:
        """列出所有备份"""
        backups = []
        for backup_file in sorted(self.backup_dir.glob("real_estate_*.dump"), reverse=True):
            stat = backup_file.stat()
            backups.append({
                "filename": backup_file.name,
                "size": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        return backups

    def get_status(self) -> dict:
        """获取备份状态"""
        backups = self.list_backups()
        return {
            "database_url": self._extract_conn(),
            "backup_dir": str(self.backup_dir),
            "total_backups": len(backups),
            "latest_backup": backups[0] if backups else None,
            "log_file": str(self.log_file),
        }


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="Coco 房产智能体 - PostgreSQL 数据库备份工具")
    parser.add_argument("action", choices=["backup", "restore", "restore_migration", "list", "status"], help="操作类型")
    parser.add_argument("--db-url", default=None, help="PostgreSQL 连接串（默认读 .env.db）")
    parser.add_argument("--backup-dir", default=None, help="备份目录")
    parser.add_argument("--force", action="store_true", help="强制备份（忽略数据变化）")
    parser.add_argument("--keep-image-tars", type=int, default=2,
                        help="图片包保留份数（默认 2；0 = 不自动清理）")
    parser.add_argument("--migration", default=None, metavar="输出文件",
                        help="打成迁移包（最新的数据库备份 + 最新的图片包 + 密钥）到指定文件，换机器/重装用")
    parser.add_argument("--restore-file", help="恢复指定备份文件")
    parser.add_argument("--images-file", help="迁移恢复时指定图片备份文件")
    parser.add_argument("--migration-tar", help="迁移打包文件路径（coco_migration.tar.gz），restore_migration 先解包再恢复")

    args = parser.parse_args()

    backup_mgr = DatabaseBackup(args.db_url, args.backup_dir, keep_image_tars=args.keep_image_tars)

    if args.action == "backup":
        if args.migration:
            # 一条命令搞定：先按常规备一份（数据没变化会自动跳过）。这一步失败**不拦着**打迁移包 ——
            # 这时候人往往正急着把数据搬走，用现有最新那份也比空手强，但要在日志里如实说清。
            if not backup_mgr.backup(force=args.force):
                backup_mgr._log("本次备份没成功（原因见上），迁移包用现有最新那份继续打")
            exit(0 if backup_mgr.pack_migration(args.migration) else 1)
        exit(0 if backup_mgr.backup(force=args.force) else 1)
    elif args.action == "restore":
        if not args.restore_file:
            print("错误: 请指定 --restore-file 参数")
            exit(1)
        exit(0 if backup_mgr.restore(args.restore_file) else 1)
    elif args.action == "restore_migration":
        exit(0 if backup_mgr.restore_migration(
            backup_filename=args.restore_file,
            images_filename=args.images_file,
            migration_tar=args.migration_tar,
        ) else 1)
    elif args.action == "list":
        backups = backup_mgr.list_backups()
        if not backups:
            print("暂无备份")
        else:
            print(f"共 {len(backups)} 个备份：")
            for b in backups:
                print(f"  {b['filename']} ({b['size']} bytes) - {b['created']}")
    elif args.action == "status":
        status = backup_mgr.get_status()
        print("备份状态：")
        for k, v in status.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
