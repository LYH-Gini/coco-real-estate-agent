"""exclusive_expiring 独家委托到期：写入入口、展示口径、days/limit 边界、空态（2026-09-25）

覆盖 F109–F113：
① F109 `exclusive_until`（独家委托到期日）**业务工具层没有任何写入入口** → 工具在生产上永远为空；
   给 add_property/update_property 补参数（日期归一 + 中文提示）+ 模板栏位；
② F110 展示口径自己拼：出租租金显示成 0 万、已过期那行写成「已过期到期」、标题带 📌 表情、
   时机提示每套重复一遍 → 复用房源侧 `_fmt_price`、措辞改成「已过期」/「还有 N 天到期」、去表情、提示合并到末尾；
③ F111 命中无上限、无 count/total/truncated（301 套 → 221KB）→ 加 limit + 三件齐 + 截断说明；
④ F112 `days` 传非数字/None 崩 → 非数字/None 按默认 30、0 保留原义、负数按 0；
⑤ F113 空态分不清「没登记」与「没登记但不在窗口」→ 空列表时按两种情况分别说明。
"""
import json
from datetime import datetime, timedelta

import pytest

from tools.real_estate_owner import exclusive_expiring
from tools.real_estate_property import add_property, update_property

MONTH_LATER = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_owner._get_db", lambda: db)
    monkeypatch.setattr("tools.real_estate_property._get_db", lambda: db)
    return db


def _call(**kwargs):
    return json.loads(exclusive_expiring(**kwargs))


def _add(db, title, days_off, *, ptype="second_hand", price=2_000_000, status="available",
         via_tool=False):
    if via_tool:
        date = (datetime.now() + timedelta(days=days_off)).strftime("%Y-%m-%d")
        r = json.loads(add_property(title=title, price=price, area=88.0, property_type=ptype,
                                    exclusive_until=date, force=True))
        pid = r["property"]["id"]
    else:
        r = json.loads(add_property(title=title, price=price, area=88.0, property_type=ptype,
                                    force=True))
        pid = r["property"]["id"]
        db.update_property(pid, exclusive_until=datetime.now() + timedelta(days=days_off))
    if status != "available":
        db.update_property(pid, status=status)
    return pid


# ---------- ① 写入入口 ----------

def test_add_property_can_record_exclusive_until(tool_db):
    r = json.loads(add_property(title="独家录入房", price=1_700_000, area=80.0,
                                exclusive_until="2026-12-31", force=True))
    assert r["success"] is True, r
    assert tool_db.get_property(r["property"]["id"])["exclusive_until"] is not None, r


@pytest.mark.parametrize("written", ["2026/11/15", "2026.11.15", "2026年11月15日"])
def test_date_forms_accepted(tool_db, written):
    r = json.loads(add_property(title="日期写法房", price=1_700_000, area=80.0,
                                exclusive_until=written, force=True))
    assert r["success"] is True and r["property"]["exclusive_until"], (written, r)


def test_bad_date_gets_chinese_hint(tool_db):
    r = json.loads(add_property(title="坏日期房", price=1_700_000, area=80.0,
                                exclusive_until="年底", force=True))
    assert r["success"] is False and "独家委托到期日没能识别" in r["error"], r


def test_update_property_can_change_exclusive_until(tool_db):
    pid = _add(tool_db, "待改独家房", 15)
    r = json.loads(update_property(property_id=pid, exclusive_until=MONTH_LATER))
    assert r["success"] is True, r
    assert tool_db.get_property(pid)["exclusive_until"] is not None


def test_schema_and_form_declare_exclusive_until(tool_db):
    from tools.registry import registry
    from tools.real_estate_property import get_property_form
    for tool in ("add_property", "update_property"):
        props = registry.get_entry(tool).schema["parameters"]["properties"]
        assert "exclusive_until" in props and "独家委托到期日" in props["exclusive_until"]["description"], tool
    assert "独家委托到期日" in json.loads(get_property_form())["form"]


# ---------- ② 展示口径 ----------

def test_rental_price_shows_monthly_rent(tool_db):
    _add(tool_db, "出租独家房", 20, ptype="rental", price=2500)
    msg = _call(days=40)["message"]
    line = next(ln for ln in msg.splitlines() if "出租独家房" in ln)
    assert "2500元/月" in line and "0万" not in line, line


def test_expired_wording_reads_naturally(tool_db):
    _add(tool_db, "已过期独家房", -5)
    msg = _call(days=30)["message"]
    line = next(ln for ln in msg.splitlines() if "已过期独家房" in ln)
    assert "已过期" in line and "已过期到期" not in line, line


def test_no_emoji_and_hint_once(tool_db):
    for i in range(3):
        _add(tool_db, f"提示房{i}", i + 1)
    msg = _call(days=30)["message"]
    assert "📌" not in msg and "!" not in msg, msg
    assert msg.count("到期前是重新谈委托条件或建议调价的窗口") == 1, msg


def test_sorted_by_urgency(tool_db):
    _add(tool_db, "十五天独家房", 15)
    _add(tool_db, "三天独家房", 3)
    days = [i["days_remaining"] for i in _call(days=30)["items"]]
    assert days == sorted(days), days


def test_only_available_and_within_window(tool_db):
    inside = _add(tool_db, "窗口内独家房", 10)
    _add(tool_db, "窗口外独家房", 60)
    sold = _add(tool_db, "已售独家房", 10, status="sold")
    ids = [i["id"] for i in _call(days=30)["items"]]
    assert inside in ids and sold not in ids, ids
    assert len(ids) == 1, ids


# ---------- ③ limit 与形状 ----------

@pytest.mark.parametrize("bad", [0, -1, -100, "abc", None, ""])
def test_bad_limit_falls_back_to_default(tool_db, bad):
    for i in range(25):
        _add(tool_db, f"条数房{i}", 1)
    r = _call(days=30, limit=bad)
    assert len(r["items"]) == 20, (bad, len(r["items"]))
    assert r["total"] == 25 and r["truncated"] is True, r


def test_huge_limit_capped_and_total_full(tool_db):
    for i in range(260):
        _add(tool_db, f"大量独家房{i}", 1)
    r = _call(days=30, limit=100000)
    assert len(r["items"]) == 200 and r["total"] == 260 and r["truncated"] is True, r
    assert "共 260 套独家委托" in r["message"] and "要我多列就说一声" in r["message"], r["message"].splitlines()[:2]


def test_shape_three_pieces(tool_db):
    _add(tool_db, "形状房", 5)
    r = _call(days=30)
    assert {"count", "total", "truncated"} <= set(r), sorted(r)


# ---------- ④ days 边界 ----------

@pytest.mark.parametrize("bad", ["abc", None])
def test_bad_days_falls_back_to_default(tool_db, bad):
    _add(tool_db, "二十天独家房", 20)
    r = _call(days=bad)
    assert r["success"] is True and len(r["items"]) == 1, (bad, r)


def test_days_zero_means_expired_and_today(tool_db):
    _add(tool_db, "已过期房", -3)
    _add(tool_db, "三十天后房", 30)
    ids = [i["id"] for i in _call(days=0)["items"]]
    assert len(ids) == 1, ids


def test_negative_days_behaves_like_zero(tool_db):
    _add(tool_db, "负数窗口房", -3)
    assert len(_call(days=-5)["items"]) == len(_call(days=0)["items"]), "负数应按 0 处理"


# ---------- ⑤ 空态两种 ----------

def test_empty_when_nothing_registered_points_to_the_write_path(tool_db):
    msg = _call(days=30)["message"]
    assert "无独家委托到期" in msg and "还没有登记过独家委托到期日" in msg, msg


def test_empty_when_registered_but_out_of_window_reports_count(tool_db):
    _add(tool_db, "很远才到期房", 90)
    r = _call(days=30)
    assert r["items"] == [] and "库里共登记 1 套独家委托" in r["message"], r
    assert "天到期" in r["message"] or "已过期" in r["message"], r


def test_empty_shape_is_stable(tool_db):
    r = _call(days=30)
    assert r["success"] is True and r["count"] == 0 and r["total"] == 0 and r["truncated"] is False, r
