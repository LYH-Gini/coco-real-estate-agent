"""历史房源楼层回填脚本的回归测试（2026-09-21 加）。

要求：历史房源要按同一套房号规则回填楼层。
脚本默认只演练（--dry-run），确认清单后再 --apply；**只填空缺、绝不覆盖已有楼层**。

真实踩过的坑（本文件就是为它写的）：最初用 `iter_available_properties` 取房源，
那是匹配专用的精简投影（不含 floor 字段）→ 所有房源都被判成“缺楼层”，
--apply 会把已经录好的楼层覆盖掉。现在改用 search_properties（走 to_dict，含 floor）。
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "backfill_floor_from_title.py"


def _seed(tmp_path):
    db = tmp_path / "re.db"
    url = f"sqlite:///{db}"
    os.environ["DATABASE_URL"] = url
    from agent.real_estate_db import RealEstateDB, init_real_estate_db

    init_real_estate_db(url)
    inst = RealEstateDB(url)
    a = inst.add_property(title="263栋1006", community="金盘", price=1_500_000, area=76.0)
    inst.update_property(a["id"], floor="10层")          # 已有楼层：不许被覆盖
    inst.add_property(title="263栋1008", community="金盘", price=1_600_000, area=78.0)
    inst.add_property(title="雅居乐金沙湾 3室2厅", community="雅居乐金沙湾", price=2_600_000, area=115.0)
    return url


def _run(url, *args):
    env = {**os.environ, "DATABASE_URL": url, "COCO_ENV_FILE": "/nonexistent"}
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=REPO_ROOT, env=env)


def _floors(url):
    con = sqlite3.connect(url.replace("sqlite:///", ""))
    return dict(con.execute("select title, floor from re_properties"))


def test_dry_run_lists_proposals_without_writing(tmp_path, monkeypatch):
    url = _seed(tmp_path)
    before = _floors(url)
    out = _run(url, "--dry-run").stdout
    assert "缺楼层 2 套" in out, out          # 只有 263栋1008 与雅居乐缺（263栋1006 已有）
    assert "可推断 1 套" in out, out          # 雅居乐无房号 → 推不出
    assert "263栋1008" in out and "房号 1008" in out
    assert "演练模式" in out
    assert _floors(url) == before, "演练模式不该改动数据"


def test_apply_fills_only_missing_and_keeps_existing(tmp_path, monkeypatch):
    url = _seed(tmp_path)
    out = _run(url, "--apply").stdout
    assert "已回填 1 套" in out, out
    floors = _floors(url)
    assert floors["263栋1008"] == "10层"
    assert floors["263栋1006"] == "10层"      # 原有的值（不许被覆盖成别的）
    assert floors["雅居乐金沙湾 3室2厅"] is None  # 无依据 → 不臆造


def test_apply_is_idempotent(tmp_path, monkeypatch):
    url = _seed(tmp_path)
    _run(url, "--apply")
    out = _run(url, "--apply").stdout
    assert "已回填 0 套" in out, out
