"""list_scripts 话术库列表：三件齐、场景认中文、历史值归同一档（2026-09-26，第十一组第 77 项）

F390–F396 的回归（改前：`t76a` 该行 4 格 ⚠️、`t81` 43 例 **28 红**）：
① F390 只有 `count` 没有 `total`/`truncated` —— 125 条的库里只回 100 条、一句不说，第 101 条起静默消失；
② F391 **没有 `limit` 参数**（想看多几条都没法说）；
③ F392 场景筛选**只认英文精确值**：`逼定成交`/`开场白`/`Greeting` 全筛不到（回 0 条、不报错），
   而改前原样存进库的中文值（「逼定」）反而筛得到 —— 两个方向都不对；
④ F393 场景乱值**静默回空列表**（与"这个场景下确实没有"分不清）；
⑤ F394 场景传列表 → 崩，**还把 SELECT 语句、表名列名、LIMIT/OFFSET 参数回给模型**；
⑥ F395 空库回 `{"success": true, "scripts": [], "count": 0}`（一句人话都没有）；每项也没有场景中文名；
⑦ F396 描述 13 字、`scenario` 连参数说明都没有。

**2026-09-26 拍板**：默认列 50 条、上限 200（与房东列表同一套契约）。
**契约 33 一并落实**：按档位筛时要把**历史写法**归到同一档（改前存的中文原样值、大小写变体、
「自定义」档的空串与 NULL 都要筛得到）。
"""
import json

import pytest

from tools.real_estate_scripts import list_scripts, save_script


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_scripts._get_db", lambda: db)
    return db


def call(**kwargs):
    return json.loads(list_scripts(**kwargs))


def names(r):
    return [s["name"] for s in r["scripts"]]


def seed(db, name, scenario="custom", content="内容"):
    return db.add_script(name=name, content=content, scenario=scenario)["id"]


# ---------- ① 三件齐与对账 ----------

def test_returns_count_total_truncated(tool_db):
    for i in range(3):
        seed(tool_db, f"话术{i}")
    r = call()
    assert r["success"] is True, r
    assert r["count"] == len(r["scripts"]) == 3, r
    assert r["total"] == 3 and r["truncated"] is False, r


def test_truncation_is_reported_not_silent(tool_db):
    for i in range(60):
        seed(tool_db, f"话术{i:02d}")
    r = call()
    assert r["count"] == 50, r          # 默认 50
    assert r["total"] == 60 and r["truncated"] is True, r
    assert r["message"] == "话术库共 60 条，这里列最近 50 条（最新登记优先）。要我多列就说一声。", r["message"]


def test_latest_first(tool_db):
    seed(tool_db, "最早")
    seed(tool_db, "中间")
    seed(tool_db, "最新")
    assert names(call()) == ["最新", "中间", "最早"]


def test_every_item_carries_chinese_scenario_name(tool_db):
    seed(tool_db, "议价话术", "closing")
    seed(tool_db, "老库存话术", "逼定")          # 改前原样存的中文值
    by_name = {s["name"]: s["scenario_label"] for s in call()["scripts"]}
    assert by_name == {"议价话术": "逼定成交", "老库存话术": "逼定"}, by_name
    assert set(call()["scripts"][0]) >= {"id", "name", "scenario", "content", "created_at", "scenario_label"}


# ---------- ② limit ----------

@pytest.mark.parametrize("bad,expected", [(0, 3), (-1, 3), ("abc", 3), (None, 3)])
def test_limit_falls_back_to_default(tool_db, bad, expected):
    for i in range(3):
        seed(tool_db, f"话术{i}")
    kw = {} if bad is None else {"limit": bad}
    r = call(**kw)
    assert r["count"] == expected, (bad, r)


def test_limit_is_capped_at_200(tool_db):
    for i in range(3):
        seed(tool_db, f"话术{i}")
    r = call(limit=99999)
    assert r["count"] == 3 and r["total"] == 3, r


def test_limit_can_be_lowered(tool_db):
    for i in range(4):
        seed(tool_db, f"话术{i}")
    r = call(limit=2)
    assert r["count"] == 2 and r["total"] == 4 and r["truncated"] is True, r


# ---------- ③ 场景筛选：认中文、认别名、历史值归同一档 ----------

@pytest.mark.parametrize("raw,expected", [
    ("closing", ["议价话术"]), ("逼定成交", ["议价话术"]), ("逼定", ["议价话术"]), ("促单", ["议价话术"]),
    ("greeting", ["开场话术"]), ("开场白", ["开场话术"]), ("Greeting", ["开场话术"]),
    ("跟进", ["跟进话术"]), ("异议处理", []),
])
def test_scenario_filter_accepts_chinese_and_english(tool_db, raw, expected):
    seed(tool_db, "议价话术", "closing")
    seed(tool_db, "开场话术", "greeting")
    seed(tool_db, "跟进话术", "follow_up")
    assert names(call(scenario=raw)) == expected, raw


def test_filter_groups_legacy_spellings_into_one_bucket(tool_db):
    """契约 33：改前原样存进库的中文值，按档位筛时要一起筛出来"""
    seed(tool_db, "新值话术", "closing")
    seed(tool_db, "老值话术", "逼定")
    assert sorted(names(call(scenario="逼定成交"))) == ["新值话术", "老值话术"]
    assert call(scenario="逼定成交")["total"] == 2


def test_custom_bucket_includes_legacy_empty_and_null(tool_db):
    seed(tool_db, "正常自定义", "custom")
    seed(tool_db, "空串档", "")
    seed(tool_db, "空档", None)
    got = set(names(call(scenario="自定义")))
    assert got == {"正常自定义", "空串档", "空档"}, got
    assert call(scenario="自定义")["total"] == 3


def test_unknown_scenario_is_refused_with_the_chinese_list(tool_db):
    seed(tool_db, "议价话术", "closing")
    r = call(scenario="乱写的场景")
    assert r.get("success") is not True, r
    assert "未知场景「乱写的场景」。可用场景：①开场白（greeting）" in r["error"], r["error"]
    assert "⑤自定义（custom）" in r["error"], r["error"]


@pytest.mark.parametrize("bad", [["closing"], {"a": 1}])
def test_non_text_scenario_gives_hint_without_leaking_sql(tool_db, bad):
    r = call(scenario=bad)
    assert r.get("success") is not True, r
    assert "话术场景要给一个说法（如「开场白」）" in r["error"] and "没法用" in r["error"], r
    blob = json.dumps(r, ensure_ascii=False)
    for leak in ("ProgrammingError", "SELECT", "re_scripts", "LIMIT", "OFFSET"):
        assert leak not in blob, blob


def test_blank_scenario_lists_everything(tool_db):
    seed(tool_db, "议价话术", "closing")
    assert call(scenario="")["count"] == 1
    assert call(scenario="   ")["count"] == 1
    assert call(scenario=None)["count"] == 1


# ---------- ④ 空态两种说法 ----------

def test_empty_library_message(tool_db):
    assert call()["message"] == "话术库里还一条话术都没存过。跟我说「存一条话术」，把要记的话发我。", call()["message"]


def test_scenario_without_scripts_says_how_many_in_total(tool_db):
    seed(tool_db, "议价话术", "closing")
    seed(tool_db, "跟进话术", "follow_up")
    r = call(scenario="异议处理")
    assert r["count"] == 0 and r["total"] == 0, r
    assert r["message"] == "「异议处理」下还没有话术（话术库共 2 条）。", r["message"]


# ---------- ⑤ 回执 ----------

def test_message_lists_names_and_ids(tool_db):
    seed(tool_db, "议价话术", "closing")
    seed(tool_db, "跟进话术", "follow_up")
    r = call(scenario="逼定成交")
    sid = r["scripts"][0]["id"]
    assert r["message"] == f"「逼定成交」下共 1 条（最新的在前）：①「议价话术」（编号 {sid}）。要说哪条的原文，跟我说名字。", r["message"]


def test_message_caps_the_name_list_at_five(tool_db):
    for i in range(8):
        seed(tool_db, f"话术{i}")
    r = call()
    assert "这里列最新的 5 条" in r["message"] and "要全列就说一声" in r["message"], r["message"]
    assert r["message"].count("「话术") == 5, r["message"]


def test_empty_library_message_is_not_the_same_as_scenario_empty(tool_db):
    assert "都没存过" in call()["message"]
    seed(tool_db, "议价话术", "closing")
    assert "都没有话术" not in call()["message"]


# ---------- ⑥ 与 save_script 往返 / 只读 ----------

def test_saved_script_shows_up_in_the_list(tool_db):
    saved = json.loads(save_script(name="三方一致话术", content="三方一致内容", scenario="follow_up"))
    row = [s for s in call()["scripts"] if s["id"] == saved["script"]["id"]]
    assert row and row[0]["content"] == "三方一致内容", row
    assert row[0]["scenario_label"] == "跟进维护", row


def test_reads_only(tool_db):
    seed(tool_db, "议价话术", "closing")
    before = [(s["id"], s["name"], s["scenario"]) for s in tool_db.list_scripts(limit=100)]
    call()
    call(scenario="开场白")
    call(scenario="乱写的场景")
    assert [(s["id"], s["name"], s["scenario"]) for s in tool_db.list_scripts(limit=100)] == before


# ---------- ⑦ 描述与参数 ----------

def test_schema_description_and_params():
    from tools.registry import registry

    schema = registry._tools["list_scripts"].schema
    desc = schema["description"]
    assert "最新的在前" in desc and "get_script" in desc and "被截断" in desc, desc
    props = schema["parameters"]["properties"]
    for key in ("scenario", "limit"):
        assert len(props[key]["description"]) >= 12, (key, props[key])
    assert props["limit"]["type"] == "integer"
