"""get_viewing 回归（2026-09-25 第五组「带看」第 45 项，F195）

背景（实测，见 /root/coco-tool-audit/results/raw/t47.before.log）：
① 带看状态与客户意向直出内部值（`status="done"`、`result="interested"`），经纪人看到英文枚举
   —— 而「记跟进」早就给了 `type_label` 中文，同族口径不一致；
② 带看时间直出 ISO（`2026-09-28T14:30:00`）—— Coco 会连中间那个 `T` 一起念出来；
③ 客户或房源被外部删掉时详情里是 `null`（生产删除路径会连带清带看，这里只核防御表现）；
④ 描述只有 6 个字（「查看带看详情」），没说能给什么。

本文件钉住修好之后的行为（原值一律保留，只补给人看的字段）。
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text


@pytest.fixture
def wired(db, monkeypatch):
    import tools.real_estate_viewing as m

    monkeypatch.setattr(m, "_get_db", lambda: db)
    return db


@pytest.fixture
def cid(wired):
    return wired.add_customer(name="详情客户", phone="13800001111", tier="A",
                              customer_type="buy_second_hand")["id"]


@pytest.fixture
def pid(wired):
    return wired.add_property(title="详情房源 1号楼101", price=1500000, area=80,
                              property_type="second_hand", status="available")["id"]


def _call(**kwargs):
    import tools.real_estate_viewing as m

    return json.loads(m.get_viewing(**kwargs))


def _schedule(cid, pid, days=3, when=None):
    import tools.real_estate_viewing as m

    when = when or (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d 14:30")
    out = json.loads(m.schedule_viewing(customer_id=cid, property_id=pid, viewing_time=when))
    assert out["success"] is True, out
    return out["viewing"]["id"]


def _record(**kwargs):
    import tools.real_estate_viewing as m

    return json.loads(m.record_viewing(**kwargs))


# ==================== ① 状态与意向给中文 ====================
class TestLabels:
    def test_status_and_result_labels(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status="已完成", result="感兴趣")
        v = _call(viewing_id=vid)["viewing"]
        assert v["status"] == "done" and v["status_label"] == "已完成", v
        assert v["result"] == "interested" and v["result_label"] == "感兴趣", v

    @pytest.mark.parametrize("status,label", [("scheduled", "待带看"), ("done", "已完成"),
                                              ("cancelled", "已取消")])
    def test_each_status_has_label(self, wired, cid, pid, status, label):
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status=status)
        assert _call(viewing_id=vid)["viewing"]["status_label"] == label

    @pytest.mark.parametrize("result,label", [("interested", "感兴趣"),
                                              ("not_interested", "不感兴趣"),
                                              ("pending", "再考虑")])
    def test_each_result_has_label(self, wired, cid, pid, result, label):
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, result=result)
        assert _call(viewing_id=vid)["viewing"]["result_label"] == label

    def test_no_result_no_label(self, wired, cid, pid):
        """还没记意向就不臆造一个中文说法"""
        vid = _schedule(cid, pid)
        v = _call(viewing_id=vid)["viewing"]
        assert v["result"] is None and not v["result_label"], v

    def test_unknown_status_falls_back_to_raw(self, wired, cid, pid):
        """库里存了认不出的值（外部改库）→ label 回落原值，不硬编成中文"""
        vid = _schedule(cid, pid)
        wired.update_viewing(vid, status="weird", result="odd")
        v = _call(viewing_id=vid)["viewing"]
        assert v["status_label"] == "weird" and v["result_label"] == "odd", v

    def test_raw_values_kept(self, wired, cid, pid):
        """原值一律保留（机器读），只补给人看的字段"""
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status="已完成", result="感兴趣", feedback="户型还行")
        v = _call(viewing_id=vid)["viewing"]
        for key in ("id", "customer_id", "property_id", "status", "result", "feedback",
                    "viewing_time", "created_at"):
            assert key in v, (key, v)


# ==================== ② 时间给可读形态 ====================
class TestTimeLabel:
    def test_time_label_is_readable(self, wired, cid, pid):
        when = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d 14:30")
        vid = _schedule(cid, pid, when=when)
        v = _call(viewing_id=vid)["viewing"]
        assert v["viewing_time_label"] == when, v
        assert v["viewing_time"].startswith(when.replace(" ", "T")), v      # 原值照旧

    def test_time_label_single_digit_hour(self, wired, cid, pid):
        when = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d 09:05")
        vid = _schedule(cid, pid, when=when)
        assert _call(viewing_id=vid)["viewing"]["viewing_time_label"] == when


# ==================== ③ 孤儿客户/房源给可读标注 ====================
class TestOrphanLabels:
    def test_deleted_customer_is_labelled(self, wired, cid, pid):
        """外部改库把客户删了（生产删除路径会连带清带看，这里只核防御）"""
        vid = _schedule(cid, pid)
        with wired.get_session() as s:
            s.execute(text("DELETE FROM re_customers WHERE id = :i"), {"i": cid})
            s.commit()
        v = _call(viewing_id=vid)["viewing"]
        assert v["customer_name"] == f"已删除客户（id={cid}）", v

    def test_deleted_property_is_labelled(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        with wired.get_session() as s:
            s.execute(text("DELETE FROM re_properties WHERE id = :i"), {"i": pid})
            s.commit()
        v = _call(viewing_id=vid)["viewing"]
        assert v["property_title"] == f"已删除房源（id={pid}）", v

    def test_normal_names_untouched(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        v = _call(viewing_id=vid)["viewing"]
        assert v["customer_name"] == "详情客户" and v["property_title"] == "详情房源 1号楼101", v


# ==================== ④ 编号形态与不存在 ====================
class TestIdForms:
    @pytest.mark.parametrize("bad", ["abc", True])
    def test_bad_id_forms(self, wired, bad):
        out = _call(viewing_id=bad)
        assert out["success"] is not True, out
        assert "带看编号" in out["error"] and "数字" in out["error"], out["error"]

    def test_numeric_string_still_works(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        out = _call(viewing_id=str(vid))
        assert out["success"] is True and out["viewing"]["id"] == vid, out

    def test_unknown_viewing_hint(self, wired):
        out = _call(viewing_id=999999)
        assert out["success"] is not True, out
        assert "带看记录不存在" in out["error"] and "带看记录列表" in out["error"], out["error"]


# ==================== ⑤ 反向读回 ====================
class TestReadBack:
    def test_matches_schedule(self, wired, cid, pid):
        when = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d 09:15")
        vid = _schedule(cid, pid, when=when)
        v = _call(viewing_id=vid)["viewing"]
        assert v["id"] == vid and v["customer_id"] == cid and v["property_id"] == pid, v
        assert v["status"] == "scheduled" and v["viewing_time_label"] == when, v

    def test_matches_record(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status="已完成", result="不感兴趣", feedback="临街吵")
        v = _call(viewing_id=vid)["viewing"]
        assert (v["status"], v["status_label"]) == ("done", "已完成"), v
        assert (v["result"], v["result_label"]) == ("not_interested", "不感兴趣"), v
        assert v["feedback"] == "临街吵", v


# ==================== ⑦ 回访提醒可见（有据可查） ====================
class TestReminderVisible:
    def test_done_viewing_shows_reminder(self, wired, cid, pid):
        """带看完成后自动安排了回访提醒 → 详情里给提醒本身与一句可读说明"""
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status="已完成", result="感兴趣")
        v = _call(viewing_id=vid)["viewing"]
        assert v.get("reminder"), v
        label = v.get("reminder_label") or ""
        assert label.startswith("已安排回访提醒（") and label.endswith("）"), label
        assert "None" not in label, label
        assert len(v["reminder"]["next_date"]) >= 16, v["reminder"]

    def test_reminder_time_matches_row(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status="已完成")
        v = _call(viewing_id=vid)["viewing"]
        when = v["reminder"]["next_date"].replace(" ", "T")[:16]
        assert v["reminder_label"] == f"已安排回访提醒（{when.replace('T', ' ')}）", v["reminder_label"]

    def test_no_reminder_for_scheduled(self, wired, cid, pid):
        """还没完成的带看不该说有回访提醒"""
        vid = _schedule(cid, pid)
        v = _call(viewing_id=vid)["viewing"]
        assert "reminder" not in v and "reminder_label" not in v, v

    def test_no_reminder_after_cancel(self, wired, cid, pid):
        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status="已取消")
        v = _call(viewing_id=vid)["viewing"]
        assert "reminder" not in v and "reminder_label" not in v, v

    def test_reminder_is_the_same_row_as_followups(self, wired, cid, pid):
        """详情里的提醒 == 客户跟进历史里那条提醒（同一行，不是另编一句）"""
        import tools.real_estate_followup as f

        vid = _schedule(cid, pid)
        _record(viewing_id=vid, status="已完成")
        v = _call(viewing_id=vid)["viewing"]
        with wired.get_session() as s:
            row = s.execute(text("SELECT id, type, next_date FROM re_followups"
                                 " WHERE source_viewing_id = :v AND type = 'reminder'"),
                            {"v": vid}).fetchone()
        assert row and v["reminder"]["id"] == row[0], (row, v["reminder"])


# ==================== ⑥ 描述给模型的能力说明 ====================
class TestSchema:
    def test_description_states_ability(self):
        from tools.registry import registry

        schema = registry.get_entry("get_viewing").schema
        desc = schema["description"]
        assert len(desc) > 30, desc
        for word in ("带看时间", "客户意向", "带看编号"):
            assert word in desc, (word, desc)
        assert "viewing_id" in schema["parameters"]["properties"], schema["parameters"]
