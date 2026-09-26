"""delete_script 删话术：回执带编号名字、删不存在的分情况说（2026-09-26，第十一组第 78 项）

F397–F400 的回归（改前：`t76a` 该行 1 格 ⚠️、`t82` 33 例 **14 红**）：
① F397 回执只有三个字「话术已删除」—— **不带编号、不带名字**（同名两条时删完没法核对删的是哪条）；
② F398 删不存在的编号只说「话术不存在」（不带编号、不给下一步）；
③ F399 `script_id=0` / `-1` 也说「话术不存在」（编号本身不对，该说"要是正整数"）；
④ F400 描述 4 字、参数说明 4 字，**没写清这是彻底删除**（删了就取不回来）。

**2026-09-26 拍板**：回执里明说「删了就取不回来了」（硬删，没有回收站），
`deleted` 结构化里带上正文，便于"要恢复就照着重存"。
"""
import json

import pytest

from tools.real_estate_scripts import delete_script, get_script_by_name, list_scripts, save_script


@pytest.fixture
def tool_db(db, monkeypatch):
    monkeypatch.setattr("tools.real_estate_scripts._get_db", lambda: db)
    return db


def call(sid):
    return json.loads(delete_script(script_id=sid))


def seed(db, name, scenario="custom", content="内容"):
    return db.add_script(name=name, content=content, scenario=scenario)["id"]


# ---------- ① 回执能核对删的是哪条 ----------

def test_receipt_carries_id_name_and_scenario(tool_db):
    sid = seed(tool_db, "跟进话术", "follow_up", "跟进内容")
    r = call(sid)
    assert r["success"] is True, r
    assert r["message"] == f"已删除话术「跟进话术」（编号 {sid}，场景：跟进维护）。删了就取不回来了。", r["message"]
    assert r["deleted"] == {"id": sid, "name": "跟进话术", "scenario": "follow_up",
                            "scenario_label": "跟进维护", "content": "跟进内容",
                            "created_at": None} or r["deleted"]["content"] == "跟进内容", r["deleted"]


def test_deleted_payload_keeps_the_content_for_restoring(tool_db):
    sid = seed(tool_db, "议价话术", "closing", "这套价格我再帮您争取一下。")
    r = call(sid)
    assert r["deleted"]["content"] == "这套价格我再帮您争取一下。", r["deleted"]
    assert r["deleted"]["scenario_label"] == "逼定成交", r["deleted"]


def test_legacy_chinese_scenario_value_is_read_back_as_is(tool_db):
    sid = seed(tool_db, "老库存话术", "逼定")
    assert call(sid)["deleted"]["scenario_label"] == "逼定"


# ---------- ② 真删 + 不牵连同名 ----------

def test_row_is_really_gone(tool_db):
    sid = seed(tool_db, "议价话术")
    call(sid)
    assert tool_db.get_script(sid) is None
    assert [s for s in tool_db.list_scripts(limit=100) if s["id"] == sid] == []


def test_deleting_one_of_two_same_named_scripts_leaves_the_other(tool_db):
    first = seed(tool_db, "重名话术", content="第一版")
    second = seed(tool_db, "重名话术", content="第二版")
    call(first)
    rest = tool_db.list_scripts(limit=100)
    assert [s["id"] for s in rest] == [second], rest
    assert json.loads(get_script_by_name(name="重名话术"))["script"]["content"] == "第二版"


def test_deleted_script_is_gone_from_both_read_paths(tool_db):
    sid = seed(tool_db, "待删话术", "greeting")
    call(sid)
    assert json.loads(get_script_by_name(name="待删话术"))["success"] is not True
    assert [s for s in json.loads(list_scripts())["scripts"] if s["id"] == sid] == []


# ---------- ③ 删不存在的 / 空库 / 编号不对 ----------

def test_missing_id_says_the_number_and_the_next_step(tool_db):
    seed(tool_db, "议价话术")
    r = call(99999)
    assert r.get("success") is not True, r
    assert r["error"] == "话术库里没有编号 99999 的话术。要看库里都有什么，跟我说「列一下话术库」。", r["error"]


def test_repeat_delete_says_the_number_is_gone(tool_db):
    """再删同一条（库里还有别的话术）要说"没有编号 X 的话术"；若删的是最后一条则走空库那句"""
    sid = seed(tool_db, "议价话术")
    seed(tool_db, "留着的话术")
    call(sid)
    r = call(sid)
    assert r.get("success") is not True and f"没有编号 {sid} 的话术" in r["error"], r


def test_repeat_delete_of_the_last_script_falls_back_to_empty_library(tool_db):
    sid = seed(tool_db, "only")
    call(sid)
    r = call(sid)
    assert r.get("success") is not True and "没有可删的" in r["error"], r


def test_empty_library_says_nothing_to_delete(tool_db):
    """空库这句要同时适用于"从没存过"与"刚把最后一条删掉"（不能说是"还没存过"）"""
    r = call(1)
    assert r.get("success") is not True, r
    assert r["error"] == "话术库里现在一条话术都没有，没有可删的。要存话术跟我说一声。", r["error"]


@pytest.mark.parametrize("bad", [0, -1, -100])
def test_non_positive_id_asks_for_a_positive_number(tool_db, bad):
    seed(tool_db, "议价话术")
    r = call(bad)
    assert r.get("success") is not True, r
    assert r["error"] == f"话术编号要是正整数（如 12），收到的是「{bad}」。可在话术列表里查。", r["error"]


@pytest.mark.parametrize("bad", ["abc", ["1"], 1.5, True])
def test_bad_shaped_id_gives_form_hint_and_deletes_nothing(tool_db, bad):
    sid = seed(tool_db, "议价话术")
    r = call(bad)
    assert r.get("success") is not True, r
    assert tool_db.get_script(sid) is not None, "坏形态的编号不该删掉任何东西"
    blob = json.dumps(r, ensure_ascii=False)
    assert "ProgrammingError" not in blob and "DELETE" not in blob, blob


def test_id_as_numeric_string_is_accepted(tool_db):
    sid = seed(tool_db, "议价话术")
    assert call(str(sid))["success"] is True


# ---------- ④ 描述与参数 ----------

def test_schema_says_it_is_a_permanent_delete():
    from tools.registry import registry

    schema = registry._tools["delete_script"].schema
    desc = schema["description"]
    assert "取不回来" in desc and "12 条" in desc, desc
    prop = schema["parameters"]["properties"]["script_id"]
    assert len(prop["description"]) >= 12 and "正整数" in prop["description"], prop
