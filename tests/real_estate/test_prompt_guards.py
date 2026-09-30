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


class TestOutwardWordingGuard:
    """对外说法护栏（2026-09-28 加）

    真实教训：经纪人问「你收录了哪些城市的政策？」，Coco 把工具的说明书与报错原文
    （「本地政策库已停用…本工具只会返回…」）改写了一遍发给他，读起来像提示词。
    根因是当时**没有任何一条规则**管"怎么对经纪人说话"。本文件钉住这条规则不被删。
    """

    def test_prompt_has_the_rule(self):
        assert "【对外说话规则】" in PROMPT
        # 不出现内部构造
        assert "工具名、参数名、字段英文键与英文枚举值" in PROMPT
        # 不解释系统怎么运作
        assert "不向经纪人解释系统怎么运作" in PROMPT
        # note_for_model / ask 是给模型看的
        assert "note_for_model" in PROMPT
        # 有"照那句话回复"的兜底
        assert "就照那句说" in PROMPT

    def test_manual_has_the_rule(self):
        assert "对外说话规则（2026-09-28 加）" in MANUAL
        assert "不向他解释系统怎么运作" in MANUAL
        assert "note_for_model" in MANUAL


class TestConciseReportingGuards:
    """先给结论、别一次倒一屏；匹配分数只说一种说法（2026-09-29）"""

    def test_matching_report_is_narrowed(self):
        assert "只报两件事：匹配到几位" in PROMPT
        assert "还有 N 位沾边" in PROMPT
        assert "不要一次把每位客户的预算/区域/户型都列出来" in PROMPT

    def test_score_wording_is_fixed(self):
        assert "匹配度 X 分" in PROMPT
        assert "系统评分" in PROMPT and "自造词" in PROMPT

    def test_price_change_reports_only_itself(self):
        assert "【改价只报改价】" in PROMPT
        assert "不要顺手去查降价捞回名单" in PROMPT

    def test_customer_edit_reports_only_itself(self):
        """改客户资料只报改资料（2026-09-29 实测：只改预算，却回了一屏匹配结果与建议）"""
        assert "【改客户资料只报改资料】" in PROMPT
        assert "不要顺手跑匹配" in PROMPT

    def test_manual_has_the_customer_edit_rule(self):
        assert "改客户资料只报改资料" in MANUAL
        assert "顺手跑匹配" in MANUAL

    def test_manual_matches(self):
        assert "只报\"匹配到几位 ＋ 最匹配的 1~2 位（名字 + 一句理由）\"" in MANUAL
        assert "改价只报改价" in MANUAL


class TestNoAbsolutePromiseGuard:
    """不做绝对承诺（2026-09-28 加）

    真实教训：Coco 说过「更新过程是安全的」「保证后续匹配不报错」「图片永远不会丢」——
    替系统打包票，出事就是产品背锅。规则里要求改说"查到的事实 + 他自己能做的动作"。
    """

    def test_prompt_has_the_rule(self):
        assert "【不做绝对承诺】" in PROMPT
        assert "保证、一定、绝对、永远、100%" in PROMPT
        assert "查到的事实" in PROMPT

    def test_security_answer_has_no_absolute_claim(self):
        """加密口径里不许再出现"不会泄露 / 不外传"这类承诺（来自真实回复里的原话）

        判据看整个「数据安全应答」段；开头的问法示例用的是「会被泄露吗」，不会误伤。
        """
        section = PROMPT.split("# 数据安全应答")[1].split("# 强制规则")[0]
        # 这两句同属承诺（服务器账号、密钥文件、备份包、模型服务商都能碰到数据）。
        for bad in ("不会泄露", "不外传", "绝不会", "保证",
                    "只有这套系统读得到", "不对公网开放"):
            assert bad not in section, f"数据安全应答里还有绝对承诺「{bad}」"

    def test_security_answer_is_not_recited(self):
        """这一段是给模型的说明：必须写明"不要把这一整段原样念给他"、不要列"项目/说明"表

        真实教训（2026-09-28 22:39 实测）：Coco 把提示词整段念了出去 —— 「对客户（对外口径）」
        的小标题、"客户可以放心的话术"、密钥、备份全都倒出来，还带"具体保护措施/项目/说明"表格。
        """
        section = PROMPT.split("# 数据安全应答")[1].split("# 强制规则")[0]
        assert "不要把这一整段原样念给他" in section
        assert "不要另起" in section and "表格" in section
        for meta in ("对外口径", "对内口径", "话术"):
            assert f"（{meta}）" not in section, f"段里还留着会被照念的元标签「{meta}」"

    def test_manual_has_the_rule(self):
        assert "不做绝对承诺" in MANUAL
        assert "绝不会泄露" in MANUAL


class TestIdNumberStorageWording:
    """身份证号是加密存全号的（2026-09-29 实测）

    真实回复：经纪人问「还有哪些信息没填」，Coco 自己补了一句「身份证号系统只存脱敏版
    （前 4 + 后 4），原号不落库，全号需你自己存档」—— 与实现不符（加密存全号、查档给完整号码），
    会让经纪人以为这个号白记了。
    """

    def test_prompt_states_the_storage_rule(self):
        section = PROMPT.split("# 数据安全应答")[1].split("# 强制规则")[0]
        assert "身份证号（客户与房东）都是加密存全号的" in section
        # 规则要把错说法点名禁掉，否则模型还是会照旧印象说
        for bad in ("只存脱敏版", "原号不落库"):
            assert bad in section, f"提示词没点名禁止「{bad}」这个说法"

    def test_manual_states_the_same(self):
        assert "身份证号（客户与房东）是加密存全号的" in MANUAL
        for bad in ("只存脱敏版", "原号不落库"):
            assert bad in MANUAL, f"操作手册没点名禁止「{bad}」这个说法"


class TestCustomerIntakeDoesNotAddOnItsOwn:
    """建档就只建档（2026-09-29 实测）

    真实回复：经纪人只发了一段自由文本，Coco 自己建了一条「2026-10-01 10:00 细聊购房需求」
    的跟进提醒（库里 followup 可查到），还往备注里写了一句自己的归纳 —— 两项他都没说过。
    """

    def test_prompt_forbids_self_service(self):
        assert "建档就只建档" in PROMPT
        assert "自己顺手建跟进提醒" in PROMPT
        assert "替他写备注" in PROMPT
        assert "要不要我给这位客户设个回访提醒" in PROMPT

    def test_manual_has_the_rule(self):
        assert "建档就只建档" in MANUAL
        assert "自己顺手建跟进提醒" in MANUAL
        assert "替他写备注" in MANUAL


class TestOutwardWordingNamedExamples:
    """对外措辞：点名禁掉工具名 / 内部键 / 英文枚举（2026-09-29 实测）

    真实回复：「我用 update_customer 补进他的档案」「客户 ID 37」「阶段 lead、状态 active」——
    规则里原本只有"不出现工具名/英文键/英文枚举"的说法，没点具体形态，模型照旧。
    """

    def test_prompt_names_the_three_bad_shapes(self):
        assert "点名禁掉这三种写法" in PROMPT
        assert "update_customer 补进他的档案" in PROMPT
        assert "客户 ID 37" in PROMPT
        assert "阶段 lead、状态 active" in PROMPT

    def test_prompt_lists_cn_stage_and_status(self):
        assert "【客户阶段与状态的中文说法】" in PROMPT
        assert "潜在 / 意向 / 强意向 / 已看房 / 谈判 / 成交中 / 售后维护 / 流失" in PROMPT
        assert "在跟 / 暂缓 / 已关闭" in PROMPT

    def test_manual_matches(self):
        assert "点名禁掉这三种写法" in MANUAL
        assert "客户阶段与状态一律说中文" in MANUAL

    def test_prompt_forbids_system_as_subject(self):
        """不把"系统"当主语（2026-09-29 实测：「系统提示客户很可能在别处看到了更便宜的房子」）"""
        assert "不把\"系统\"当主语" in PROMPT
        assert "系统提示 / 系统判断 / 系统里显示" in PROMPT

    def test_manual_forbids_system_as_subject(self):
        assert "不把\"系统\"当主语" in MANUAL
        assert "系统提示 / 系统判断" in MANUAL


class TestNoInstanceSpecificExamples:
    """产品文案中立：示例里不出现真实城市/片区/小区名（2026-09-30）

    起因：经纪人实测时 Coco 凭空说了"某某地暂无可匹配的存量"，而那个地方整个对话里
    从没出现过 —— 手册「使用示例」当时用的正是某座城市的真实片区与小区名，
    模型就把示例里的城市当成了"库里的现实"。给每个经纪人用的同一份文案，
    示例必须是占位符（XX市 / XX区 / 某小区）。
    """

    # 曾经出现在示例里的城市/片区/小区名（别再写回去）
    PLACE_WORDS = ("北京", "海口", "朝阳", "望京", "通州", "恒大", "美丽沙", "海阔天", "美兰")

    def test_prompt_has_no_real_place_names(self):
        for w in self.PLACE_WORDS:
            assert w not in PROMPT, f"提示词里出现了具体地名「{w}」"

    def test_manual_has_no_real_place_names(self):
        for w in self.PLACE_WORDS:
            assert w not in MANUAL, f"操作手册里出现了具体地名「{w}」"

    def test_prompt_forbids_unverified_stock_claims(self):
        assert "【不许给没查过的库存结论】" in PROMPT
        assert "不许提资料里没出现过的城市" in PROMPT
        assert "示例里的城市" in PROMPT

    def test_manual_has_the_same_rule(self):
        assert "不许给没查过的库存结论" in MANUAL
        assert "示例里的城市/区域不是库里的数据" in MANUAL

