"""提示词护栏的回归测试（防被删/被改回去）。

2026-09-21 真实教训：Coco 说「update_property 工具不支持楼层字段」，而系统早已支持——
它沿用了几轮前的旧结论、没重新调用工具，导致经纪人被误导。为此加了一条通用护栏
「禁止臆断工具能力」。本文件钉住它，以及同批加的楼层/朝向字段规则仍在位。
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT = (REPO_ROOT / "agent" / "real_estate_prompt.py").read_text(encoding="utf-8")
MANUAL = (REPO_ROOT / "skills" / "real_estate" / "SKILL.md").read_text(encoding="utf-8")


class TestNoCapabilityGuessingGuard:
    def test_prompt_has_the_guard(self):
        assert "禁止臆断工具能力" in PROMPT
        # 核心要求：先真的调用工具；只有明确报错才允许说“不支持”
        assert "先调用对应工具用真实参数试一次" in PROMPT
        assert "未知参数" in PROMPT
        # 旧结论不算证据
        assert "不算证据" in PROMPT

    def test_manual_has_the_guard(self):
        assert "禁止臆断工具能力" in MANUAL

    def test_floor_orientation_rules_still_present(self):
        """同批加的楼层/朝向规则不能被顺手删掉"""
        assert "楼层/朝向等字段要单独传参" in PROMPT
        assert "楼层与朝向等字段必须单独传参" in MANUAL


from agent.coco_cron import _AVAILABLE_JOBS  # noqa: E402

DAILY_PROMPT = next(item[3] for item in _AVAILABLE_JOBS if item[2] == "coco_daily_report")


class TestReportWordingGuards:
    """日报口径（2026-09-23 要求）：等级四级全列、数据要点只留解读

    真实教训：早报写"暂无 S 级 / A 级高意向客户"，把 B 级漏了；根源是早报的
    定时任务提示词只点了 S/A，且"数据要点"重复了上面已经列过的数字。
    """

    def test_prompt_requires_all_four_tiers(self):
        assert "【日报口径】" in PROMPT
        assert "S / A / B / C 四级都要提" in PROMPT
        assert "不要重复上面已经单列过的数字" in PROMPT

    def test_manual_has_report_rule(self):
        assert "日报口径（2026-09-23 加）" in MANUAL
        assert "S/A/B/C 四级都要提" in MANUAL

    def test_daily_cron_prompt_contract(self):
        """盯实际注册的早报提示词（2026-09-23 重设计：数据由脚本给，模型只成文）

        早报不再自己调工具、不再做数据汇总播报；板块缺了就照实写"无"、不许编造。
        等级四级全列的规矩仍在【日报口径】里，管的是"要报数字"的场景（见上个用例）。
        """
        assert "Script Output" in DAILY_PROMPT
        assert "老板早，今天的情况：" in DAILY_PROMPT
        assert "生日板块只列数据里给出的客户" in DAILY_PROMPT
        assert "不许添加" in DAILY_PROMPT
        assert "S/A级客户状态" not in DAILY_PROMPT, "旧口径（只点 S/A）是漏 B 级的根源"


class TestCronTableGuards:
    """定时任务表（2026-09-23 重设计）：5 条、时间、生日硬规则、只提醒不执行"""

    def test_prompt_lists_the_five_jobs(self):
        for token in ("09:00 上班早报", "10:00、17:00 逾期哨兵", "12:30 机会提醒",
                      "20:30 收工小结", "周一 08:30 周报"):
            assert token in PROMPT, token
        assert "午间" not in PROMPT or "取消" in PROMPT

    def test_prompt_says_reminder_only(self):
        assert "只是**提醒**" in PROMPT
        assert "不替经纪人跟进" in PROMPT

    def test_birthday_only_when_recorded(self):
        assert "已录入" in PROMPT
        assert "不许按年龄、星座、购房时间推测" in PROMPT

    def test_manual_matches_the_table(self):
        assert "定时任务（2026-09-23 重设计）" in MANUAL
        assert "10:00 与 17:00 逾期哨兵" in MANUAL


class TestGuidanceMenuGuards:
    """引导菜单话术（2026-09-23 选定版本 2）：只列 Coco 真能做的事"""

    def test_prompt_has_fixed_menu(self):
        assert "【引导菜单标准话术】" in PROMPT
        assert "我帮你录：客户、房源、跟进、带看结果、成交单" in PROMPT
        assert "我帮你配：客户 ↔ 房源匹配" in PROMPT
        assert "我帮你推：成交节点提醒（定金→签约→贷款→过户→交房）" in PROMPT
        assert "我帮你出：日报 / 周报 / 业绩看板" in PROMPT

    def test_prompt_forbids_claiming_viewing_and_deal(self):
        assert "不能替经纪人带看或成交" in PROMPT

    def test_prompt_covers_greeting_scene(self):
        """确认：这段清单是 Coco 打招呼时说的（不是早报带的）"""
        assert "打招呼（开场自我介绍）时" in PROMPT
        assert "开场带这段清单是可以的" in PROMPT

    def test_manual_has_menu_script(self):
        assert "引导菜单话术（2026-09-23 加）" in MANUAL
        assert "我帮你**录**（客户/房源/跟进/带看结果/成交单）" in MANUAL
