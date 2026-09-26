"""get_script_by_name 按名字取话术：名称归一、重名说清、内置话术分工（2026-09-26，第十一组第 76 项）

F383–F389 的回归（改前：`t76a` 该行 5 格 ⚠️、`t80` 35 例 **19 红**、`t80b` 空库 4 例 2 红）：
① F383 名字**前后空白不认**（库里叫「议价话术」，问「  议价话术  」就说"话术不存在"）；
② F384 空串/纯空白/换行 → `话术不存在:    `，分不清"你没说名字"还是"库里没有"；
③ F385 名字传列表或字典 → 崩，**还把完整 SELECT 语句与表名列名回给模型**；
④ F386 **内置话术名全查不到**：`greeting`/`开场白`/`首次联系`/`逼定成交` 一律"话术不存在"，
   而 `get_script` 明明给得出来 —— 两个数据源的分工在读取侧完全没说清；
⑤ F387 **空库**与"没有这条"用同一句；
⑥ F388 存量同名多条取哪条、有几份都不说；
⑦ F389 返回体没有 `message`、没有场景中文名；描述 7 字、参数说明 4 字。

**2026-09-26 拍板**：存量重名**给编号最小那条**（与 `save_script` 的 `force=true` 覆盖目标
指向同一行才自洽），但**必须说清有几份**、并给一句"要合成一份就说覆盖"。
"""
import json

import pytest

from tools.real_estate_scripts import get_script_by_name, save_script


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_scripts._get_db", lambda: db)
    return db


def call(name):
    return json.loads(get_script_by_name(name=name))


def err_of(name):
    r = call(name)
    assert r.get("success") is not True, r
    return r["error"]


def seed(db, name, content, scenario="custom"):
    row = db.add_script(name=name, content=content, scenario=scenario)
    return row["id"]


# ---------- ① 正常路径 ----------

def test_gets_the_script_with_chinese_scenario_and_readable_message(tool_db):
    sid = seed(tool_db, "议价话术", "这套价格我再帮您跟业主争取一下。", "closing")
    r = call("议价话术")
    assert r["success"] is True, r
    assert r["script"]["content"] == "这套价格我再帮您跟业主争取一下。", r
    assert r["script"]["scenario_label"] == "逼定成交", r
    assert set(r["script"]) >= {"id", "name", "scenario", "content", "created_at"}, sorted(r["script"])
    assert r["message"] == f"「议价话术」（编号 {sid}，场景：逼定成交）：这套价格我再帮您跟业主争取一下。", r["message"]


def test_scenario_label_falls_back_for_legacy_raw_values(tool_db):
    """改前存进去的场景是中文原样（如「逼定」），读的时候不许念英文键、也不许空白"""
    seed(tool_db, "老话术", "内容", "逼定")
    assert call("老话术")["script"]["scenario_label"] == "逼定"


# ---------- ② 名称归一 ----------

@pytest.mark.parametrize("raw", ["  议价话术  ", "议价话术  ", "  议价话术"])
def test_surrounding_spaces_are_ignored(tool_db, raw):
    seed(tool_db, "议价话术", "内容")
    r = call(raw)
    assert r["success"] is True and r["script"]["name"] == "议价话术", (raw, r)


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_blank_name_says_the_name_is_missing(tool_db, blank):
    seed(tool_db, "议价话术", "内容")
    err = err_of(blank)
    assert "话术名称不能为空" in err and "不存在" not in err, err


def test_names_stay_exact_match(tool_db):
    """名字是经纪人自己起的，不做大小写折叠（'ABC话术' 与 'abc话术' 是两条）"""
    seed(tool_db, "ABC话术", "内容")
    assert call("ABC话术")["success"] is True
    assert "没有叫" in err_of("abc话术")


# ---------- ③ 名字类型错：不崩、不泄内部 ----------

@pytest.mark.parametrize("bad", [["议价话术"], {"a": 1}])
def test_non_text_name_gives_chinese_hint_without_leaking_sql(tool_db, bad):
    r = call(bad)
    assert r.get("success") is not True, r
    assert "话术名称要给一个名字（如「议价话术」）" in r["error"] and "没法用" in r["error"], r
    blob = json.dumps(r, ensure_ascii=False)
    for leak in ("ProgrammingError", "SELECT", "re_scripts", "sqlite", "parameters"):
        assert leak not in blob, blob


# ---------- ④ 空态：空库 / 没这条 / 内置话术名字 ----------

def test_empty_library_says_nothing_saved_yet(tool_db):
    err = err_of("议价话术")
    assert err == "话术库里还一条话术都没存过。先存一条（跟我说「存一条话术」），再按名字取。", err


def test_missing_name_points_at_the_library(tool_db):
    seed(tool_db, "议价话术", "内容")
    err = err_of("不存在的话术")
    assert err == "话术库里没有叫「不存在的话术」的。要看库里都有什么，跟我说「列一下话术库」。", err


def test_builtin_scenario_names_explain_the_two_libraries(tool_db):
    """get_script 能取到的名字，在这里必须说清"它是内置话术"，不能都说"不存在"（跨工具对账）"""
    from tools.real_estate_communication import SCRIPTS, SCENARIO_ORDER

    seed(tool_db, "议价话术", "内容")           # 让库非空，避免走到"空库"那一句
    for key in SCENARIO_ORDER:
        err = err_of(key)
        assert "不在话术库里" in err and "内置话术的场景名" in err, (key, err)
    for sc, subs in SCRIPTS.items():
        for sub in subs:
            err = err_of(sub)
            assert "不在话术库里" in err and "内置话术" in err, (sc, sub, err)


def test_builtin_child_scenario_names_the_parent_scenario(tool_db):
    seed(tool_db, "议价话术", "内容")
    err = err_of("首次联系")
    assert err == "「首次联系」不在话术库里 —— 它是内置话术「开场白」里的子场景。要看那条话术，跟我说「开场白的首次联系」。", err


# ---------- ⑤ 存量重名：给最早那条 + 说清份数 ----------

def test_legacy_duplicates_report_which_one_and_how_many(tool_db):
    first = seed(tool_db, "重名话术", "第一版")
    second = seed(tool_db, "重名话术", "第二版")
    r = call("重名话术")
    assert r["script"]["id"] == first, r            # 拍板：给编号最小那条
    assert r["duplicate_count"] == 2, r
    assert f"（编号 {first}、{second}）" in r["message"], r["message"]
    assert f"这里给你编号 {first} 那份" in r["message"], r["message"]
    assert "要合成一份就说「用这版覆盖重名话术」" in r["message"], r["message"]
    assert r["message"].startswith(f"「重名话术」（编号 {first}，场景："), r["message"]


def test_single_row_has_no_duplicate_fields(tool_db):
    seed(tool_db, "议价话术", "内容")
    r = call("议价话术")
    assert "duplicate_count" not in r and "份" not in r["message"], r


# ---------- ⑥ 与 save_script 对账：存了就能取、覆盖后取到新版 ----------

def test_round_trip_with_save_script(tool_db):
    saved = json.loads(save_script(name="三方一致话术", content="三方一致内容", scenario="follow_up"))
    got = call("三方一致话术")
    assert got["script"]["id"] == saved["script"]["id"], (saved, got)
    assert got["script"]["content"] == "三方一致内容" and got["script"]["scenario"] == "follow_up", got
    listed = [s for s in tool_db.list_scripts(limit=100) if s["id"] == saved["script"]["id"]]
    assert listed and listed[0]["content"] == "三方一致内容", listed


def test_after_force_overwrite_the_same_row_is_returned(tool_db):
    json.loads(save_script(name="议价话术", content="第一版"))
    json.loads(save_script(name="议价话术", content="第二版", force=True))
    assert call("议价话术")["script"]["content"] == "第二版"


# ---------- ⑦ 只读 + 描述 ----------

def test_reads_only(tool_db):
    seed(tool_db, "议价话术", "内容")
    before = [(s["id"], s["name"], s["content"]) for s in tool_db.list_scripts(limit=100)]
    call("议价话术")
    call("  议价话术  ")
    call("不存在的话术")
    call("greeting")
    assert [(s["id"], s["name"], s["content"]) for s in tool_db.list_scripts(limit=100)] == before


def test_schema_description_and_param_docs_are_substantial():
    from tools.registry import registry

    schema = registry._tools["get_script_by_name"].schema
    desc = schema["description"]
    assert "同名" in desc and "get_script" in desc and "空白" in desc, desc
    prop = schema["parameters"]["properties"]["name"]
    assert len(prop["description"]) >= 12, prop
