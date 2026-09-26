"""房源"不能用"时的中文说明（2026-09-26 抽出共用件）

由来：同一句判断原先散在文案、海报两处（都是「房源不存在或不在售」），经纪人看不出这套其实在库、
只是已售/已租。文案那轮先修，现在抽成一处实现，`generate_listing_copy` 与 `generate_property_poster`
（以及后面的九宫格/口播稿）共用，避免第三次复制。

本文件只钉共用件本身；两个工具侧的接入在各自文件里另有用例
（`test_listing_copy_guards.py` / `test_poster_guards.py`）。
"""
import pytest

from tools.real_estate_property import unavailable_property_note


def test_missing_property_says_the_id():
    text, label = unavailable_property_note(999999, None)
    assert "没有编号 999999 的房源" in text, text
    assert "不在售" not in text, text          # 不许再把它跟"已售"混成一句
    assert label is None


@pytest.mark.parametrize("status,phrase,action,label", [
    ("sold", "已经售出", "发在售文案", "已售"),
    ("rented", "已经出租", "发在租文案", "已租"),
])
def test_default_action_follows_status(status, phrase, action, label):
    text, got_label = unavailable_property_note(5, {"title": "某小区 1号楼101", "status": status})
    assert phrase in text and action in text, text
    assert "某小区 1号楼101" in text, text        # 要点到是哪一套
    assert got_label == label, got_label


def test_action_can_be_overridden_for_other_tools():
    """海报/九宫格/口播稿传自己的动作词，措辞同一套"""
    text, _ = unavailable_property_note(5, {"title": "某小区 1号楼101", "status": "sold"},
                                        action="出海报")
    assert "已经售出，不能出海报" in text, text


def test_unknown_status_is_reported_as_is():
    """库里状态是别的值时如实说状态，不硬套"已售" """
    text, label = unavailable_property_note(5, {"title": "某小区 1号楼101", "status": "weird"},
                                            action="发在售文案")
    assert "现在状态是weird，不能发在售文案" in text, text
    assert label == "weird", label


def test_title_absent_still_readable():
    text, _ = unavailable_property_note(7, {"status": "sold"})
    assert text.startswith("编号 7 的房源") and "（）" not in text, text
