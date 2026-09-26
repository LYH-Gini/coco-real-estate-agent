"""update_owner 回归（2026-09-25 新增缺口工具 —— 拍板「92 个清单内测完 + 补 1 个」）

背景：`re_owners` 有姓名/手机/微信/脱敏身份证/信任度备注/备注六个字段，而**整个工具集里没有任何
"改房东"的入口** —— 录错只能删了重建（会把名下房源的关联断掉）。本工具按客户侧 `update_customer`
的既定口径实现：改联系方式先查重（**排除自己**）、每次改动写留痕（**加密字段只留掩码**）、
身份证只存脱敏、房源关联不受影响。
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_owner as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


def _call(**kwargs):
    import tools.real_estate_owner as m

    return json.loads(m.update_owner(**kwargs))


def _owner(db, name="房东甲", phone="13800001111", wechat="wx_a", **extra):
    return db.add_owner(name=name, phone=phone, wechat=wechat, **extra)


def _row(db, oid):
    with db.get_session() as s:
        return s.execute(text("SELECT name, phone, wechat, id_masked, trust_note, notes "
                              "FROM re_owners WHERE id = :i"), {"i": oid}).fetchone()


def _changes(db, oid=None):
    sql = "SELECT field, old_value, new_value FROM re_owner_changes"
    params = {}
    if oid is not None:
        sql += " WHERE owner_id = :i"
        params["i"] = oid
    with db.get_session() as s:
        return s.execute(text(sql), params).fetchall()


# ==================== ① 逐字段能改、留痕正确 ====================

class TestFields:
    def test_update_each_field(self, wired):
        oid = _owner(wired)["id"]
        out = _call(owner_id=oid, name="房东乙", phone="13900002222", wechat="wx_b",
                    id_number="460005199001011234", trust_note="价格坚挺", notes="两套房")
        assert out["success"] is True, out
        row = _row(wired, oid)
        assert row[0] == "房东乙" and row[1] == "13900002222" and row[2] == "wx_b"
        assert row[3] == "4600**********1234"        # 身份证只存脱敏
        assert row[4] == "价格坚挺" and row[5] == "两套房"

    def test_message_lists_changed_fields(self, wired):
        oid = _owner(wired)["id"]
        out = _call(owner_id=oid, phone="13900002222", notes="两套房")
        assert out["message"] == "房东 房东甲 的资料已更新（手机号、备注）", out["message"]

    def test_returns_owner_with_full_contact_when_key_ok(self, wired):
        """正常密钥下给完整号（经纪人本人是唯一接收方；脱敏只用于留痕与读不出来时）"""
        oid = _owner(wired)["id"]
        out = _call(owner_id=oid, notes="备注")
        assert out["owner"]["phone"] == "13800001111", out["owner"]
        assert "warning_key_mismatch" not in out

    def test_ciphertext_owner_is_defended_in_return(self, wired):
        """库里是读不出来的密文（密钥不一致的真实形态）→ 不许把密文当号码回带"""
        oid = _owner(wired)["id"]
        with wired.get_session() as s:
            s.execute(text("UPDATE re_owners SET phone = :p WHERE id = :i"),
                      {"p": "gAAAAA" + "x" * 40, "i": oid})
            s.commit()
        out = _call(owner_id=oid, notes="备注")
        assert "gAAAA" not in json.dumps(out, ensure_ascii=False), out
        assert out.get("warning_key_mismatch"), sorted(out.keys())

    def test_trace_masks_encrypted_fields(self, wired):
        oid = _owner(wired)["id"]
        _call(owner_id=oid, phone="13900002222", notes="两套房")
        changes = dict((r[0], (r[1], r[2])) for r in _changes(wired, oid))
        assert changes["phone"] == ("138****1111", "139****2222"), changes
        assert changes["notes"] == (None, "两套房"), changes

    def test_unchanged_value_writes_no_trace(self, wired):
        oid = _owner(wired)["id"]
        _call(owner_id=oid, phone="13800001111")      # 同一个号（写法一致）
        assert _changes(wired, oid) == []

    def test_phone_written_inscription_is_normalized(self, wired):
        oid = _owner(wired)["id"]
        _call(owner_id=oid, phone="139-0000-2222")
        assert _row(wired, oid)[1] == "13900002222"

    def test_blank_name_is_ignored_not_written(self, wired):
        oid = _owner(wired)["id"]
        out = _call(owner_id=oid, name="   ", notes="只改备注")
        assert out["success"] is True
        assert _row(wired, oid)[0] == "房东甲"

    def test_empty_call_is_rejected(self, wired):
        oid = _owner(wired)["id"]
        out = _call(owner_id=oid)
        assert out["success"] is False
        assert out["error"] == ("没看到要改的内容 —— 说一下改哪一项？"
                                "（姓名/手机号/微信号/身份证/信任度备注/备注）")


# ==================== ② 改联系方式先查重（排除自己）====================

class TestDedupe:
    def test_conflict_with_another_owner_is_blocked(self, wired):
        a = _owner(wired, name="房东甲", phone="13800001111")
        b = _owner(wired, name="房东乙", phone="13800002222")
        out = _call(owner_id=b["id"], phone="138 0000 1111")      # 写成甲号（带空格写法）
        assert out["success"] is False and out["duplicate"] is True, out
        assert "已经是另一位房东" in out["error"] and "房东甲" in out["error"], out["error"]
        assert "说一声我把他并过来" in out["error"]
        assert _row(wired, b["id"])[1] == "13800002222", "拦下后不许落库"
        assert _changes(wired, b["id"]) == []

    def test_same_owner_own_number_is_allowed(self, wired):
        """排除自己：把写法换一下重新填自己的号，不该被当成撞号"""
        a = _owner(wired, name="房东甲", phone="13800001111")
        out = _call(owner_id=a["id"], phone="+86 138-0000-1111")
        assert out["success"] is True, out
        assert _row(wired, a["id"])[1] == "13800001111"

    def test_wechat_conflict_blocked(self, wired):
        _owner(wired, name="房东甲", phone=None, wechat="wx_shared")
        b = _owner(wired, name="房东乙", phone=None, wechat="wx_b")
        out = _call(owner_id=b["id"], wechat="wx_shared")
        assert out["success"] is False and out["duplicate"] is True, out
        assert _row(wired, b["id"])[2] == "wx_b"

    def test_ciphertext_blocks_dedupe_with_hint(self, wired):
        """库里躺着读不出来的密文（密钥不一致的真实形态）→ 不强行查重，给中文提示"""
        cid = _owner(wired, name="登录的房东", phone="13800001111")["id"]
        with wired.get_session() as s:
            s.execute(text("UPDATE re_owners SET phone = :p WHERE id = :i"),
                      {"p": "gAAAAA" + "x" * 40, "i": cid})
            s.commit()
        other = _owner(wired, name="另一位房东", phone="13800002222")["id"]
        out = _call(owner_id=other, phone="13900003333")
        assert out["success"] is False and "密钥" in out["error"], out
        assert _row(wired, other)[1] == "13800002222"

    def test_name_only_change_skips_dedupe(self, wired):
        _owner(wired, name="房东甲", phone="13800001111")
        b = _owner(wired, name="房东乙", phone="13800002222")
        assert _call(owner_id=b["id"], name="房东丙")["success"] is True


# ==================== ③ 目标与编号 ====================

class TestTarget:
    def test_missing_owner(self, wired):
        out = _call(owner_id=999999, notes="x")
        assert out["success"] is False
        assert out["error"] == "房东不存在，请先在房东列表里核对编号"

    def test_bad_id_form(self, wired):
        out = _call(owner_id="abc", notes="x")
        assert out["success"] is False and "没能识别" in out["error"], out

    def test_bool_id_rejected_by_framework(self, wired):
        import tools.real_estate_owner as m
        from tools.registry import registry

        raw = registry.dispatch("update_owner", {"owner_id": True, "notes": "x"})
        out = json.loads(raw)
        assert out.get("success") is not True and "要是数字" in (out.get("error") or ""), out


# ==================== ④ 列宽截断（PostgreSQL 上 varchar 超长会整单失败）====================

class TestColumnLimits:
    def test_trust_note_clipped_with_warning(self, wired):
        oid = _owner(wired)["id"]
        out = _call(owner_id=oid, trust_note="价" * 250)
        assert out["success"] is True
        assert len(_row(wired, oid)[4]) == 200
        assert out["warnings"] == ["信任度备注超过 200 字，只保留了前 200 字（其余内容可放进备注里）。"]

    def test_name_clipped_with_warning(self, wired):
        oid = _owner(wired)["id"]
        out = _call(owner_id=oid, name="名" * 150)
        assert len(_row(wired, oid)[0]) == 100
        assert out["warnings"] == ["房东姓名超过 100 字，只保留了前 100 字。"]


# ==================== ⑤ 房源关联不受影响 ====================

class TestRelationsPreserved:
    def test_properties_stay_linked_after_update(self, wired):
        oid = _owner(wired, name="房东甲", phone="13800001111")["id"]
        p = wired.add_property(title="名下房源", price=1_500_000, area=80.0,
                              property_type="second_hand", status="available")
        wired.update_property(p["id"], owner_id=oid)
        _call(owner_id=oid, name="房东甲改名", phone="13900002222")
        with wired.get_session() as s:
            linked = s.execute(text("SELECT owner_id FROM re_properties WHERE id = :i"),
                               {"i": p["id"]}).fetchone()[0]
        assert linked == oid


# ==================== ⑥ 描述与 schema ====================

class TestSchema:
    def test_description_states_behavior(self):
        from tools.registry import registry

        desc = registry.get_entry("update_owner").schema["description"]
        for word in ("修改房东", "身份证（只存脱敏）", "先查重", "不会悄悄改", "字段清单"):
            assert word in desc, (word, desc)

    def test_required_is_only_owner_id(self):
        from tools.registry import registry

        schema = registry.get_entry("update_owner").schema
        assert schema["parameters"]["required"] == ["owner_id"]
        assert set(schema["parameters"]["properties"]) == {
            "owner_id", "name", "phone", "wechat", "id_number", "trust_note", "notes"}

    def test_visible_in_real_estate_toolset(self):
        """新工具必须同时进可见清单（注册了但不在清单里 = 模型看不到）"""
        import toolsets

        toolset = toolsets.TOOLSETS["real_estate"]
        names = set()
        for key in ("core_tools", "tools", "extra_tools", "resolved_tools", "add_tools"):
            value = toolset.get(key)
            if isinstance(value, (list, tuple, set)):
                names |= set(value)
        if not names:      # 结构变了就直接看整个 dict 的字符串形式
            names = set(json.dumps(toolset, ensure_ascii=False).replace('"', ' ').split())
        assert "update_owner" in names
