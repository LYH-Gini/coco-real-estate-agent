"""安装脚本的「加密密钥永不变」回归（2026-09-28）

规则：**密钥就是第一次安装生成的那把，永不更换**（除泄漏后明确要求更换）。
换密钥的后果是灾难级 —— 库里客户联系方式是旧密钥加密的，换了就永久解不开；
旧版 install.sh 每次运行都生成新密钥，还会把密钥备份 `enc_key.txt` 覆盖成新的。

本文件不复述实现、也不读源码做字符串断言：它把 install.sh 里
`# ===== COCO-SECRETS-BEGIN/END =====` 之间的那段**真代码**抽出来，
在沙箱目录里真跑，逐场景断言行为（沿用 / 生成 / 留旧备份 / 空值不算 / 只留最近 3 份）。
"""
import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL = REPO_ROOT / "install.sh"
BEGIN = "# ===== COCO-SECRETS-BEGIN ====="
END = "# ===== COCO-SECRETS-END ====="

KEY_OLD = "OLDLYk3-0hIp0rH8KkN2O6AqQ4bLZ0QzW2m8Xk1VpQ0="
KEY_NEW = "NEWLYk3-0hIp0rH8KkN2O6AqQ4bLZ0QzW2m8Xk1VpQ0="


def _secrets_block() -> str:
    text = INSTALL.read_text(encoding="utf-8")
    assert BEGIN in text and END in text, "install.sh 里的密钥逻辑标记块不见了（测试按它抽取代码）"
    return text.split(BEGIN, 1)[1].split(END, 1)[0]


def _run(tmp_path: Path, env_extra: dict):
    """把标记块抽出来真跑一遍，返回 (stdout, stderr, returncode)"""
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "set -euo pipefail\n"
        'ok()   { echo "[OK] $*"; }\n'
        'warn() { echo "[WARN] $*"; }\n'
        + _secrets_block()
        + textwrap.dedent(
            """
            collect_previous_secrets
            echo "PREV_ENC_KEY=$PREV_ENC_KEY"
            echo "PREV_DB_PASSWORD=$PREV_DB_PASSWORD"
            echo "PREV_SECRETS_FROM=$PREV_SECRETS_FROM"
            keep_old_enc_key_backup
            """
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "TMPDIR_C": str(tmp_path)}
    env.update(env_extra)
    r = subprocess.run(["bash", str(probe)], env=env, capture_output=True, text=True)
    return r


def _fields(out: str) -> dict:
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line and line.count("=") >= 1
                and not line.startswith("["))


def _sandbox(tmp_path: Path, install_dir: Path | None = None):
    home = tmp_path / "home"
    (home / "backups" / "real_estate").mkdir(parents=True)
    install = install_dir or (home / "coco")
    install.mkdir(parents=True, exist_ok=True)
    return home, install


def _env(home: Path, install: Path, extra=None):
    e = {"HOME": str(home), "INSTALL_DIR": str(install)}
    e.update(extra or {})
    return e


class TestReuseExistingKey:
    def test_fresh_machine_collects_nothing(self, tmp_path):
        """全新机器：两处都没有 → 收集为空（由 install.sh 生成新密钥）"""
        home, install = _sandbox(tmp_path)
        r = _run(tmp_path, _env(home, install))
        assert r.returncode == 0, r.stderr
        f = _fields(r.stdout)
        assert f["PREV_ENC_KEY"] == "", r.stdout
        assert f["PREV_DB_PASSWORD"] == "", r.stdout
        assert f["PREV_SECRETS_FROM"] == "", r.stdout

    def test_reuses_key_and_password_from_existing_env_db(self, tmp_path):
        """已经装过（有 .env.db）：密钥与数据库口令都沿用"""
        home, install = _sandbox(tmp_path)
        (install / ".env.db").write_text(
            f"DB_HOST=localhost\nDB_USER=hermes\nDB_PASSWORD=pwd-old\nCOCO_ENC_KEY={KEY_OLD}\n",
            encoding="utf-8")
        r = _run(tmp_path, _env(home, install))
        assert r.returncode == 0, r.stderr
        f = _fields(r.stdout)
        assert f["PREV_ENC_KEY"] == KEY_OLD, r.stdout
        assert f["PREV_DB_PASSWORD"] == "pwd-old", r.stdout
        assert f["PREV_SECRETS_FROM"].endswith(".env.db"), r.stdout

    def test_falls_back_to_enc_key_backup(self, tmp_path):
        """安装目录没了（重装系统/删目录）但密钥备份还在 → 用备份里的密钥"""
        home, install = _sandbox(tmp_path)
        (home / "backups" / "real_estate" / "enc_key.txt").write_text(
            f"COCO_ENC_KEY={KEY_OLD}\n", encoding="utf-8")
        r = _run(tmp_path, _env(home, install))
        assert r.returncode == 0, r.stderr
        f = _fields(r.stdout)
        assert f["PREV_ENC_KEY"] == KEY_OLD, r.stdout
        assert f["PREV_SECRETS_FROM"].endswith("enc_key.txt"), r.stdout
        assert f["PREV_DB_PASSWORD"] == "", "备份里只有密钥，没有口令"

    def test_empty_value_is_not_treated_as_a_key(self, tmp_path):
        """`.env.db` 里 `COCO_ENC_KEY=`（空值）不算有密钥 → 继续往下找备份，找不到就为空"""
        home, install = _sandbox(tmp_path)
        (install / ".env.db").write_text("DB_PASSWORD=pwd-old\nCOCO_ENC_KEY=\n", encoding="utf-8")
        (home / "backups" / "real_estate" / "enc_key.txt").write_text(
            f"COCO_ENC_KEY={KEY_OLD}\n", encoding="utf-8")
        r = _run(tmp_path, _env(home, install))
        assert r.returncode == 0, r.stderr
        f = _fields(r.stdout)
        assert f["PREV_ENC_KEY"] == KEY_OLD, r.stdout
        assert f["PREV_DB_PASSWORD"] == "pwd-old", r.stdout


class TestKeepOldKeyBackup:
    def test_old_key_backup_is_kept_when_key_changes(self, tmp_path):
        """真要换密钥（泄漏场景）时，旧的密钥备份必须先留一份带时间戳的"""
        home, install = _sandbox(tmp_path)
        bdir = home / "backups" / "real_estate"
        (bdir / "enc_key.txt").write_text(f"COCO_ENC_KEY={KEY_OLD}\n", encoding="utf-8")
        r = _run(tmp_path, _env(home, install, {"COCO_ENC_KEY": KEY_NEW}))
        assert r.returncode == 0, r.stderr
        baks = sorted(bdir.glob("enc_key.txt.bak-*"))
        assert len(baks) == 1, r.stdout
        assert KEY_OLD in baks[0].read_text(encoding="utf-8")
        assert KEY_OLD in (bdir / "enc_key.txt").read_text(encoding="utf-8"), "原来那份不该被动"

    def test_no_backup_when_key_is_unchanged(self, tmp_path):
        """沿用同一个密钥（正常重装）→ 不留重复备份"""
        home, install = _sandbox(tmp_path)
        bdir = home / "backups" / "real_estate"
        (bdir / "enc_key.txt").write_text(f"COCO_ENC_KEY={KEY_OLD}\n", encoding="utf-8")
        r = _run(tmp_path, _env(home, install, {"COCO_ENC_KEY": KEY_OLD}))
        assert r.returncode == 0, r.stderr
        assert list(bdir.glob("enc_key.txt.bak-*")) == [], r.stdout

    def test_only_keeps_the_three_newest_backups(self, tmp_path):
        """备份只留最近 3 份，别把备份目录堆满"""
        home, install = _sandbox(tmp_path)
        bdir = home / "backups" / "real_estate"
        (bdir / "enc_key.txt").write_text(f"COCO_ENC_KEY={KEY_OLD}\n", encoding="utf-8")
        for i in range(1, 4):
            (bdir / f"enc_key.txt.bak-2026010100000{i}").write_text(
                f"COCO_ENC_KEY=old{i}\n", encoding="utf-8")
        r = _run(tmp_path, _env(home, install, {"COCO_ENC_KEY": KEY_NEW}))
        assert r.returncode == 0, r.stderr
        assert len(list(bdir.glob("enc_key.txt.bak-*"))) == 3, r.stdout


class TestInstallScriptWiring:
    """真的接线：这些函数必须在 install.sh 里被调用，且顺序对（删目录之前收集）"""

    def test_collect_runs_before_clone(self):
        text = INSTALL.read_text(encoding="utf-8")
        call = text.index("collect_previous_secrets      #")
        clone = text.index("    clone_project\n")
        assert call < clone, "必须在 clone_project（它会 rm -rf 安装目录）之前收集旧密钥"

    def test_setup_database_reuses_when_available(self):
        text = INSTALL.read_text(encoding="utf-8")
        db = text.split("setup_database()", 1)[1]
        assert 'if [[ -n "${PREV_ENC_KEY:-}" ]]' in db, "setup_database 没走沿用分支"
        assert 'if [[ -n "${PREV_DB_PASSWORD:-}" ]]' in db, "数据库口令也要沿用"
        assert "ALTER USER" in db, "角色已存在时要补 ALTER，否则重装后连不上原库"

    def test_enc_key_backup_keeps_old_copy_first(self):
        text = INSTALL.read_text(encoding="utf-8")
        assert text.index("keep_old_enc_key_backup       #") < text.index(
            'grep "^COCO_ENC_KEY=" "$INSTALL_DIR/.env.db" > ~/backups/real_estate/enc_key.txt')
