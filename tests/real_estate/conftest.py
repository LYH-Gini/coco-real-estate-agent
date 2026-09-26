"""Coco 房产模块测试 - 公共夹具

隔离原则：
- 全部使用 sqlite 临时文件库，绝不连接生产 PostgreSQL
- 加密测试使用测试专用 Fernet 密钥，非生产密钥
- 每个测试独立建库，用完即删
"""
import os
import sys
import tempfile
import pytest
from pathlib import Path

# 保证能 import 仓库根目录的 agent/ 包
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.real_estate_db import RealEstateDB  # noqa: E402

# 测试专用加密密钥（Fernet 格式，base64 的 32 字节）。与生产密钥无关。
TEST_ENC_KEY = "SGNfcHJvZHVjdGlvbl90ZXN0X2tleV9mb3JfY29jb18zMmJ5dGVzXzA="  # noqa: F841 保留备用


def _valid_fernet_key() -> str:
    """生成一个确定性的合法 Fernet 密钥（urlsafe base64 of 32 bytes）。"""
    import base64
    raw = b"coco-test-key-32-bytes-exactly!!"  # 恰好 32 字节
    assert len(raw) == 32, len(raw)
    return base64.urlsafe_b64encode(raw).decode()


@pytest.fixture
def db(tmp_path, monkeypatch):
    """无加密模式的临时库（每个测试独立）"""
    db_path = tmp_path / "re_test.db"
    instance = RealEstateDB(f"sqlite:///{db_path}")
    yield instance
    # sqlite 文件随 tmp_path 由 pytest 自动清理


@pytest.fixture
def enc_db(tmp_path, monkeypatch):
    """加密模式的临时库：COCO_ENC_KEY 指向测试密钥"""
    monkeypatch.setenv("COCO_ENC_KEY", _valid_fernet_key())
    db_path = tmp_path / "re_test_enc.db"
    instance = RealEstateDB(f"sqlite:///{db_path}")
    yield instance
    monkeypatch.delenv("COCO_ENC_KEY")


def make_customer(db, **overrides):
    """造一个标准买房客户，可按需覆盖字段"""
    data = dict(
        name="测试客户",
        tier="A",
        budget_min=3_000_000,
        budget_max=5_000_000,
        area_pref="90-120",
        layout_pref="3室2厅",
        location="美兰区",
        customer_type="buy_second_hand",
        status="active",
    )
    data.update(overrides)
    return db.add_customer(**data)


def make_property(db, **overrides):
    """造一套标准二手房，可按需覆盖字段"""
    data = dict(
        title="测试房源",
        community="海甸岛某小区",
        district="美兰-海甸岛",
        price=4_000_000,
        area=100.0,
        rooms=3,
        halls=2,
        renovation="精装",
        property_type="second_hand",
        status="available",
    )
    data.update(overrides)
    return db.add_property(**data)


# 2026-08-28: paths 过滤器验证注释——tests/real_estate/** 变更应触发房产 CI
