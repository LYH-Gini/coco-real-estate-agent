"""update_birthday 回归（2026-09-27，第十二组第 4 项，F408–F412）

背景（实测证据：`/root/coco-tool-audit/results/raw/t87.log`、`results/update_birthday.json`）：
① **传布尔直接崩**：`birthday=true` → `AttributeError: 'bool' object has no attribute 'strip'`，
   模型收到 `[TOOL_ERROR] Tool execution failed…`；
② **"只记得月日"被拒**：`05-06` 报"生日格式错误"，但库里与提醒侧都支持 MM-DD
   （`db.get_birthday_customers` 注释明写"经纪人只记得月日，两种都要认"）；
③ **写法不归一**：`1988-5-6` 被收下但原样存库、回执也原样念；`1988/05/06`、`1990年5月6日` 一律拒绝；
④ **未来日期照收**：`2099-01-01` 成功入库；
⑤ **已关闭客户能改生日、却不会进提醒**（提醒只含在跟客户），回执照旧说"已设置"。

口径（2026-09-27 定）：写法归一（`-` / `/` / 中文年月日 / 不补零 / 只给月日 / 8 位数字）→
统一存 `YYYY-MM-DD` 或 `MM-DD`；不合理日期（未来、2 月 30 日、非法月份）拦下给中文提示；
回执说清"归一了什么""提醒会不会到"；认不出的写法给可写写法清单。
"""
import json
from datetime import datetime

import pytest


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_birthday as m
    monkeypatch.setattr(m, "_get_db", lambda: db)
    return m


def _new_customer(db, name="生日改写-客户", phone="13700009001", status="active"):
    info = db.add_customer(name=name, phone=phone, tier="B")
    if status != "active":
        db.update_customer(info["id"], status=status)
    return info["id"]


def _stored(db, cid):
    rows = db.get_customer(cid)
    return (rows or {}).get("birthday")


def _set(m, cid, birthday):
    return json.loads(m.update_birthday(customer_id=cid, birthday=birthday))


# ── ① 非文本参数不再崩 ────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [True, False])
def test_bool_birthday_is_rejected_in_chinese(wired, db, bad):
    cid = _new_customer(db, phone="13700009002")
    out = _set(wired, cid, bad)
    text = json.dumps(out, ensure_ascii=False)
    assert out["success"] is False, text
    assert "生日" in text and "可以这样写" in text, f"要给中文写法清单，实际 {text[:160]}"
    assert "TOOL_ERROR" not in text and "Traceback" not in text
    assert _stored(db, cid) is None, "拦下的输入不许落库"


def test_unknown_phrase_gives_writable_forms(wired, db):
    cid = _new_customer(db, phone="13700009003")
    out = _set(wired, cid, "下个月六号")
    text = json.dumps(out, ensure_ascii=False)
    assert out["success"] is False and "下个月六号" in text
    assert "1988-05-06" in text and "05-06" in text, "要给出可写的写法"
    assert _stored(db, cid) is None


# ── ② 只给月日要收下（与库/提醒侧口径一致）────────────────────────────
def test_month_day_only_is_accepted(wired, db):
    cid = _new_customer(db, phone="13700009004")
    out = _set(wired, cid, "05-06")
    assert out["success"] is True, json.dumps(out, ensure_ascii=False)[:200]
    assert _stored(db, cid) == "05-06", _stored(db, cid)
    assert "只填了月日" in (out.get("message") or ""), out.get("message")


# ── ③ 写法归一 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expect", [
    ("1988/05/06", "1988-05-06"),
    ("1988-5-6", "1988-05-06"),
    ("1990年5月6日", "1990-05-06"),
    ("19880506", "1988-05-06"),
    (" 1988 - 05 - 06 ", "1988-05-06"),
])
def test_birthday_forms_are_normalised(wired, db, raw, expect):
    cid = _new_customer(db, phone="13700009005")
    out = _set(wired, cid, raw)
    assert out["success"] is True, f'{raw} 应被认出来：{json.dumps(out, ensure_ascii=False)[:200]}'
    assert _stored(db, cid) == expect, _stored(db, cid)


def test_normalised_value_is_stated_back(wired, db):
    cid = _new_customer(db, phone="13700009006")
    out = _set(wired, cid, "1988/05/06")
    msg = out.get("message") or ""
    assert "1988/05/06" in msg and "1988-05-06" in msg, f"要说清归一成了什么：{msg!r}"


# ── ④ 不合理日期要拦 ──────────────────────────────────────────────────
def test_future_birthday_is_rejected(wired, db):
    cid = _new_customer(db, phone="13700009007")
    out = _set(wired, cid, "2099-01-01")
    text = json.dumps(out, ensure_ascii=False)
    assert out["success"] is False and "未来" in text, text[:200]
    assert _stored(db, cid) is None


@pytest.mark.parametrize("bad", ["2026-02-30", "1988-13-01", "1988-00-10", "1988-05-32"])
def test_impossible_dates_are_rejected(wired, db, bad):
    cid = _new_customer(db, phone="13700009008")
    out = _set(wired, cid, bad)
    assert out["success"] is False, f'{bad} 不该入库：{json.dumps(out, ensure_ascii=False)[:160]}'
    assert _stored(db, cid) is None
    # 2 月 30 日要给"这一天不存在"的说法
    if bad == "2026-02-30":
        assert "不存在" in (out.get("error") or ""), out.get("error")


# ── ⑤ 已关闭客户：能改，但要说明提醒不会到 ─────────────────────────────
def test_closed_customer_warns_about_reminder(wired, db):
    cid = _new_customer(db, phone="13700009009", status="closed")
    out = _set(wired, cid, "1988-05-06")
    assert out["success"] is True
    msg = out.get("message") or ""
    assert "已关闭" in msg and "不会包含" in msg, f"要如实说清提醒不会到：{msg!r}"


def test_active_customer_no_closed_warning(wired, db):
    cid = _new_customer(db, phone="13700009010")
    out = _set(wired, cid, "1988-05-06")
    assert "已关闭" not in (out.get("message") or "")


# ── ⑥ 反向读回：与生日提醒两端口径一致 ────────────────────────────────
def test_written_birthday_is_readable_by_check(wired, db):
    cid = _new_customer(db, phone="13700009011")
    today = datetime.now().strftime("%m-%d")
    _set(wired, cid, today)
    out = json.loads(wired.birthday_check())
    names = [c.get("name") for c in (out.get("today_birthdays") or [])]
    assert "生日改写-客户" in names, names
    assert _stored(db, cid) == today, "只给月日的写法要原样存 MM-DD（提醒侧按 MM-DD 认）"


def test_bad_forms_leave_existing_value_untouched(wired, db):
    cid = _new_customer(db, phone="13700009012")
    _set(wired, cid, "1988-05-06")
    _set(wired, cid, "2099-01-01")          # 未来 → 拒
    _set(wired, cid, "下个月六号")            # 认不出 → 拒
    assert _stored(db, cid) == "1988-05-06", "被拒的输入不许把原值改坏"
