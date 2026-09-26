"""birthday_check 回归（2026-09-27，第十二组第 3 项，F404–F407）

背景（实测证据：`/root/coco-tool-audit/results/raw/t86.log`、`results/birthday_check.json`）：
① **整行客户资料倒给模型**：返回里是 21 个字段的整行私料（phone/wechat/notes/预算/来源…），
   生日提醒只需要"谁、什么等级、今天还是明天"；字段还全是英文键；
② **密文外露**：把某客户的 phone 改成密文串（模拟密钥不一致的库），工具直接把
   `gAAAAA…` 当电话交给模型 —— 契约 8 要求读路径给可读提示 + `warning_key_mismatch`；
③ **空态不分说法**：空库 与「有客户但今明没人过生日」返回一模一样（两个空列表、零说明）——
   契约 32 要求分开说；
④ **没有一句给经纪人看的中文结论**；代码里还留着一行死代码（`tomorrow = now.replace(day=now.day+1)`）。

口径（2026-09-27 老板批准）：与定时任务侧 `scripts/coco_cron_daily.collect_birthdays`
（`{"when": "今天/明天", "name", "tier"}`，中文、精简）对齐 —— 工具也返回精简行，
老键名保留、内容精简，并给一句中文 `message`。
"""
import json
from datetime import datetime, timedelta

import pytest

YEAR = datetime.now().year - 30
TODAY = datetime.now().strftime("%m-%d")
TOMORROW = (datetime.now() + timedelta(days=1)).strftime("%m-%d")
DAY_AFTER = (datetime.now() + timedelta(days=2)).strftime("%m-%d")
FIELDS_ALLOWED = {"id", "name", "tier", "birthday"}


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_birthday as m
    monkeypatch.setattr(m, "_get_db", lambda: db)
    return m


def _call(m):
    return json.loads(m.birthday_check())


def _add(db, name, phone, birthday=None):
    info = db.add_customer(name=name, phone=phone, tier="A")
    if birthday:
        db.update_customer(info["id"], birthday=birthday)
    return info["id"]


# ── ① 精简行：只给提醒需要的字段 ──────────────────────────────────────
def test_rows_are_brief_and_have_no_contacts(wired, db):
    _add(db, "生日精简-今天", "13700007001", f"{YEAR}-{TODAY}")
    out = _call(wired)
    row = (out["today_birthdays"] or [{}])[0]
    assert set(row) == FIELDS_ALLOWED, f"行里只该有提醒需要的字段，实际 {sorted(row)}"
    for leaked in ("phone", "wechat", "notes", "budget_min", "budget_max", "feishu_id"):
        assert leaked not in json.dumps(out, ensure_ascii=False), f"不该把 {leaked} 交给模型"


def test_row_carries_readable_bits(wired, db):
    cid = _add(db, "生日精简-今天", "13700007002", f"{YEAR}-{TODAY}")
    row = (_call(wired)["today_birthdays"] or [{}])[0]
    assert row["id"] == cid, "保留客户编号，便于后续写跟进/拟祝福"
    assert row["name"] == "生日精简-今天"
    assert row["tier"] == "A"
    assert row["birthday"] == TODAY, "生日给「月-日」可读形式（库里可能是 YYYY-MM-DD）"


# ── ② 密文防御 ────────────────────────────────────────────────────────
def test_ciphertext_never_leaks_and_warns(wired, db):
    cid = _add(db, "生日密文-今天", "13700007003", f"{YEAR}-{TODAY}")
    # 用**另一把密钥**加密 → 这才是真实的"密钥不一致"场景（应用侧解不开，密文原样落库）
    from cryptography.fernet import Fernet
    from agent.real_estate_db import Customer
    cipher = Fernet(Fernet.generate_key()).encrypt(b"13700007003").decode()
    with db.get_session() as s:
        s.query(Customer).filter(Customer.id == cid).update({Customer.phone: cipher})
        s.commit()
    out = _call(wired)
    text = json.dumps(out, ensure_ascii=False)
    assert "gAAAAA" not in text, "库里是密文时不许把它当电话交给模型"
    assert out.get("warning_key_mismatch"), "命中密文要给可读提示（别静默）"


# ── ③ 空态分说法 ──────────────────────────────────────────────────────
def test_empty_library_says_register_first(wired, db):
    out = _call(wired)
    assert out["success"] is True
    msg = out.get("message", "")
    assert "还没有客户" in msg, f"空库要说「先登记客户」，实际 {msg!r}"


def test_nobody_has_birthday_today_is_its_own_wording(wired, db):
    _add(db, "生日空态-没录生日", "13700007004", None)
    msg = _call(wired).get("message", "")
    assert "都没有客户过生日" in msg, f"有客户但今明没人过生日，要与空库分开说，实际 {msg!r}"
    assert "1 位客户录了生日" not in msg or "0 位" in msg or "没有" in msg


def test_only_tomorrow_has_birthday(wired, db):
    _add(db, "生日空态-明天", "13700007005", f"{YEAR}-{TOMORROW}")
    out = _call(wired)
    assert out["count_today"] == 0 and out["count_tomorrow"] == 1
    msg = out.get("message", "")
    assert "今天没有客户过生日" in msg and "明天 1 位" in msg, msg


def test_both_days_have_birthday_message(wired, db):
    _add(db, "生日空态-今天", "13700007006", f"{YEAR}-{TODAY}")
    _add(db, "生日空态-明天", "13700007007", f"{YEAR}-{TOMORROW}")
    out = _call(wired)
    msg = out.get("message", "")
    assert "今天 1 位客户过生日" in msg and "明天 1 位" in msg, msg
    assert "（A 级）" in msg, "要带等级，便于判断轻重"
    assert out["count_today"] == 1 and out["count_tomorrow"] == 1


# ── ④ 名单口径 ────────────────────────────────────────────────────────
def test_only_active_customers_with_birthday(wired, db):
    _add(db, "生日名单-今天", "13700007008", f"{YEAR}-{TODAY}")
    closed = _add(db, "生日名单-已关闭", "13700007009", f"{YEAR}-{TODAY}")
    db.update_customer(closed, status="closed")
    _add(db, "生日名单-后天", "13700007010", f"{YEAR}-{DAY_AFTER}")
    names = [r["name"] for r in _call(wired)["today_birthdays"]]
    assert names == ["生日名单-今天"], names


def test_legacy_keys_still_present(wired, db):
    _add(db, "生日键名-今天", "13700007011", f"{YEAR}-{TODAY}")
    out = _call(wired)
    assert "today_birthdays" in out and "tomorrow_birthdays" in out, "老键名要保留（有消费方）"


def test_mm_dd_birthday_is_recognised(wired, db):
    """只记了月日的生日（MM-DD）同样要认出来，否则提醒会静默漏人。"""
    _add(db, "生日写法-MMDD", "13700007012", TODAY)
    names = [r["name"] for r in _call(wired)["today_birthdays"]]
    assert "生日写法-MMDD" in names, names
