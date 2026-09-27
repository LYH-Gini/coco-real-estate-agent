#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Coco 挂钩点自检 —— 同步官方 Hermes 上游代码后运行

用途
    每次把 Coco 的底座同步到官方新版本后，跑这个脚本确认两件事：
      ① Coco 对官方文件的每一处改动都还在（当前清单见 patches/README.md）
      ② Coco 自建的文件（房产模块 / 部署体系 / 文档 / CI）都还在

为什么需要它
    Coco 是「官方代码 + 我们的改动」的形态。同步上游时官方文件会被整批
    替换，我们的改动必须重新应用。漏掉任何一处，症状都很隐蔽：
      · 工具集没注册   → 模型看不到房产工具，Coco 会说"我没有这个能力"
      · 身份文案丢了   → Coco 自我介绍变回官方默认（"我是 Hermes…"）
      · 提示词没注入   → 房产规则失效，模型行为退化成通用助手
      · 启动不建库     → 新装的库没有表（幽灵库事故的同类问题）
      · 配置阈值回退   → 上下文压缩行为回到官方默认（爆上下文风险）
      · 飞书欢迎语丢了 → 经纪人首次对话没有引导，以为机器人是哑的
      · 网关开场白丢了 → 首次对话自我介绍错误
    这个脚本把改动集 + 文件完整性一次扫完，30 秒内给出结论。

用法
    python3 scripts/check_coco_hooks.py                 # 自动定位仓库根
    python3 scripts/check_coco_hooks.py /path/to/repo   # 指定仓库路径

退出码
    0 = 全部通过    1 = 有失败项（列表见输出）
"""

import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- 检查项定义
# 每项：编号 / 名称 / 目标文件 / 必需正则(全部命中才算过) / 失败后果与处理提示
CONTENT_CHECKS = [
    (
        "01",
        "工具集注册",
        "toolsets.py",
        [r'"real_estate"\s*:'],
        "模型看不到房产工具（会自称没有登记房源/客户的能力）。"
        "处理：在 TOOLSETS 里补 real_estate 定义，并把 real_estate 加进 hermes-feishu 的 includes。",
    ),
    (
        "02",
        "Coco 身份文案",
        "agent/prompt_builder.py",
        [r"DEFAULT_AGENT_IDENTITY[\s\S]{0,800}?Coco", r"Coco（可可）"],
        "Coco 自我介绍会变回官方默认（自称 Hermes）。"
        "处理：按 patches/02-prompt-builder-identity.patch 的语义重写身份常量。",
    ),
    (
        "03",
        "房产提示词注入",
        "agent/system_prompt.py",
        [r"real_estate_prompt"],
        "房产业务规则不再注入系统提示词，模型行为退化成通用助手。"
        "处理：恢复对 agent.real_estate_prompt 的导入与拼接。",
    ),
    (
        "04",
        "启动初始化房产库",
        "agent/agent_init.py",
        [r"init_real_estate_db"],
        "CLI 启动路径不建表（agent_init 属 CLI 专用；gateway 有惰性兜底，但别依赖兜底）。"
        "处理：恢复 init_real_estate_db 调用。",
    ),
    (
        "05",
        "上下文压缩阈值",
        "hermes_cli/config_defaults.py",
        [r'"threshold"\s*:\s*0\.8', r'"destructive_slash_confirm"\s*:\s*False'],
        "压缩阈值回到官方默认 0.50（会提前压缩、丢失更多上下文）；"
        "或清空对话类命令又弹回确认框（经纪人不会输入 /always，会卡住开新会话）。"
        "处理：改回 0.8（含 protect_last_n=40）与 destructive_slash_confirm=False。",
    ),
    (
        "06",
        "飞书首次对话功能",
        "plugins/platforms/feishu/adapter.py",
        [r"_maybe_send_coco_welcome|Coco: 首次对话"],
        "经纪人首次对话收不到欢迎语/密钥提醒/定时任务自动注册。"
        "处理：按 patches/06-feishu-adapter-welcome.patch 恢复三处（欢迎语、密钥备份提醒、注册定时任务）。",
    ),
    (
        "07",
        "网关首次对话开场白",
        # 注意：官方 v0.21 把这段逻辑从 gateway/run.py 拆到了 gateway/run_turn.py
        # 的 _hmwa_first_contact_notes()。官方将来再拆分时，按同样方式更新这里的路径
        # （找不到文件即 FAIL，会提醒我们重新定位）。
        "gateway/run_turn.py",
        [r"我是 Coco|你是 Coco|Coco（可可）"],
        "首次对话开场白变回官方文案（并可能带回官方 profile-build 引导）。"
        "处理：按 patches/07-gateway-run-greeting.patch 恢复。",
    ),
    (
        "08",
        "CI 挑标签正则",
        "scripts/sandbox/pick-release-tags.sh",
        # 只在「放宽后」才存在的特征串；被官方版覆盖后会立刻 FAIL
        [r"\(-\[0-9\]\+\)\?"],
        "Coco 用语义化标签（v0.21.3-1），正则应同时认日期式与语义化。\n"
        "处理：放宽 grep 正则为 '^v[0-9]{4}...|^v[0-9]+\\.[0-9]+\\.[0-9]+(-[0-9]+)?$'，\n"
        "      否则继承自官方的 install-e2e 会报 no release tags found。",
    ),
    (
        "09",
        "核心依赖 ddgs",
        "pyproject.toml",
        [r'"ddgs'],
        "ddgs 被官方快照覆盖后，pip install -e . 不会装它 → web_search 注册时的\n"
        "check_fn 检测不到后端、工具被隐藏，Coco 联网查政策会退化成「建议咨询当地」。\n"
        "处理：在 dependencies 里补回 \"ddgs==<版本>\"，并确认 install.sh 里也有它。",
    ),
    (
        "10",
        "E2E 定时触发已移除",
        ".github/workflows/install-e2e.yml",
        # 反向匹配（! 开头）：文件里**不能**出现这个模式
        [r"!^  schedule:"],
        "官方 install-e2e 带每 12 小时 schedule 定时触发，但它测的是 Hermes 本体\n"
        "（uv/Node、上游分发方式）的安装升级，与 Coco 的 install.sh 分发方式不符 →\n"
        "定时跑必失败、每 12 小时制造一次 CI 红灯，并持续消耗 Actions 配额。\n"
        "处理：删掉 on: 下的 schedule 段，保留 workflow_dispatch 与 push tags（参考提交 e1a42a84）。",
    ),
    (
        "11",
        "文档站部署触发已移除",
        ".github/workflows/deploy-site.yml",
        # 反向匹配（! 开头）：这两个触发不能出现
        [r"!^  release:", r"!^  push:"],
        "官方 deploy-site 在 release published / push 上触发，靠 VERCEL_DEPLOY_HOOK 部署文档站；\n"
        "本仓库没有该钩子 → curl -X POST \"\" 报 URL rejected，每次发版必红（与代码无关）。\n"
        "处理：删掉 on: 下的 release / push 段，只留 workflow_dispatch（参考本次提交）。",
    ),
    (
        "13",
        "Coco 运行时配置标准值",
        "scripts/coco_config_align.py",
        [
            r'"agent\.max_turns":\s*500',
            r'"compression\.threshold":\s*0\.8',
            r'"compression\.protect_last_n":\s*40',
            r'"compression\.hygiene_hard_message_limit":\s*5000',
            r'"display\.language":\s*"zh"',
         r'"cron\.catch_up_missed":\s*False',
            r'"display\.language": \("en",\)',
        ],
        "对齐脚本缺失或标准值被改，安装/更新就不会再校正运行时配置，\n"
        "表现为「代码默认值对了但服务器上不生效」或「重装后又回到 150 轮」。\n"
        "处理：恢复 scripts/coco_config_align.py 的 STANDARD 与安装/更新脚本里的调用。",
    ),
    (
        "14",
        "设置向导的默认值（轮次 / 压缩阈值 / 保留条数）",
        "hermes_cli/setup.py",
        [
            r'^\s*config\.setdefault\("agent", \{\}\)\["max_turns"\]\s*=\s*500',
            r'^\s*config\.setdefault\("compression", \{\}\)\["threshold"\]\s*=\s*0\.8',
            r'^\s*config\.setdefault\("compression", \{\}\)\["protect_last_n"\]\s*=\s*40',
            r"Max iterations: 500",
            r"Compression threshold: 0\.8",
            # 官方那句把阈值写回 0.50 的赋值必须不在（行首锚定，避免命中注释里提到的数字）
            r'!^\s*config\["compression"\]\["threshold"\]\s*=\s*0\.50',
        ],
        "官方向导写 max_turns=150、并在后面把压缩阈值写回 0.50（会覆盖我们刚写的 0.8），\n"
        "重跑向导就把 Coco 的设定冲掉，终端提示也会显示错的数字。\n"
        "处理：恢复 COCO-PATCH（向导写 500 / 阈值 0.8 / 保留 40 条，删掉写回 0.50 那句，提示文案同步改对）。",
    ),
    (
        "15",
        "海报不写平台名",
        "tools/real_estate_poster_svg.py",
        # 结构检查：品牌栏必须来自经纪人名片，页脚免责句必须在位
        [r"def _brand\(", r"房源信息以实际看房为准"],
        "海报品牌栏/免责句被改动：品牌应只来自经纪人名片（缺失时不画），页脚固定「房源信息以实际看房为准」。\n"
        "处理：恢复 _brand() 与 _footer() 的默认文案；渲染结果里不得出现平台名。",
    ),
    (
        "16",
        "更新类命令拦截",
        "tools/terminal_tool.py",
        [r"coco_update_block"],
        "Coco 会话里的终端更新命令拦截被移除：经纪人一句“帮我更新”，她就可能自己跑官方 "
        "hermes update（不跑我们的数据库迁移、可能覆盖手改代码）或 update.sh（重启网关连自己一起杀掉）。\n"
        "处理：恢复 COCO-PATCH 调用点（tools/real_estate_update_guard.coco_update_block）。",
    ),
    (
        "12",
        "README 命令不污染终端",
        "README.md",
        [r"!(?m)^\s*cd\s+(/|~)"],
        "对外命令里写裸 `cd <目录> && ...` 会把用户终端切到那个目录，跑完提示符变成\n"
        "`user@host:~/coco$`（用户会以为出问题了）。\n"
        "处理：改自定位写法 —— `coco <命令>`、`git -C <安装目录> ...`，或把 cd 包进括号子 shell `( cd ... && ... )`\n"
        "必须切目录时用括号子 shell `( cd ... && ... )`。",
    ),
    (
        "13",
        "README(中文) 命令不污染终端",
        "README.zh-CN.md",
        [r"!(?m)^\s*cd\s+(/|~)"],
        "同第 12 项：中英两份 README 的命令写法要保持一致，都用自定位写法。",
    ),
    (
        "14",
        "备份迁移手册命令不污染终端",
        "docs/BACKUP_MIGRATION.md",
        [r"!(?m)^\s*cd\s+(/|~)"],
        "同第 12 项：备份/恢复/迁移步骤里的命令都用 venv 绝对路径，不要 cd 到仓库目录。",
    ),
    (
        "16",
        "工具必填参数校验",
        "tools/registry.py",
        [r"COCO-PATCH", r"_missing_required_params"],
        "官方 registry 被上游快照覆盖后，模型漏传必填参数会重新变成 TypeError（模型看到\n"
        "\"Tool execution failed\" 就会自己编答案）。\n"
        "处理：在 dispatch 里补回必填参数校验（标记 COCO-PATCH 2026-09-18）。",
    ),
    (
        "15",
        "飞书实测清单命令不污染终端",
        "docs/TESTING_FEISHU_FULL.md",
        [r"!(?m)^\s*cd\s+(/|~)"],
        "同第 12 项：前置检查与查库模板都用自定位写法（查库用 `export $(grep DATABASE_URL ~/coco/.env.db)` 或一次性取值）。",
    ),
    (
        "B02",
        "coco 转发组",
        "scripts/coco.sh",
        [r"run_hermes", r"gateway\s+\"\$@\"", r"pairing"],
        "coco 缺少安装配置转发组（coco model/setup/gateway/pairing 会失效）。",
    ),
    (
        "B03",
        "默认安装目录",
        "install.sh",
        [r'INSTALL_DIR="\$\{COCO_INSTALL_DIR:-\$HOME/coco\}"', r"COCO_EXPOSE_HERMES"],
        "安装脚本默认安装目录不是 ~/coco，或缺 hermes 暴露开关（命令口径会被破坏）。",
    ),
    (
        "B01",
        "安装默认通道",
        "install.sh",
        [r'COCO_CHANNEL="\$\{COCO_CHANNEL:-master\}"'],
        "install.sh 默认通道不是 master —— 别人一条命令就会装到未验收的测试版。",
    ),
    (
        "B04",
        "Python 版本窗口",
        "pyproject.toml",
        [r'requires-python = ">=3\.11,<3\.15"'],
        "官方快照会把 requires-python 恢复成 <3.14，而 install.sh 与 README 都已按 3.11~3.14 放行\n"
        "→ 出现「脚本放行、pip install -e . 拒绝」的错配（Ubuntu 26.04 自带 3.14 会装不上）。\n"
        "处理：把 requires-python 改回 \">=3.11,<3.15\"，并确认 install.sh 的 _py_ok 判据一致\n"
        "（单测 tests/real_estate/test_install_layout.py::TestPythonVersionWindow 会守这条）。",
    ),
    (
        "B05",
        "对外命令口径（提示词）",
        "agent/real_estate_prompt.py",
        [
            r"【对外命令口径】",
            r"一律给 `coco <子命令>`",
            r"coco model",
            r"coco check",
            r"coco backup",
            r"coco restore --file",
            r"coco pairing approve feishu",
            r"coco config set",
            r"只读诊断类可以你自己跑",
            r"改状态类绝不自己执行",
            r"!hermes (model|setup|backup|restore|pairing|tools|doctor|uninstall)\b",
        ],
        "提示词是模型回答“要敲什么命令”的主依据：必须保留【对外命令口径】段\n"
        "（通用规则 + 命令面 + 常见问法映射），且不得出现裸底座命令\n"
        "（hermes model/setup/backup/restore/pairing/tools/doctor/uninstall）——\n"
        "本机已不暴露 hermes 入口，模型照抄就会给经纪人发敲不到的命令。\n"
        "（允许出现在禁止清单里的：hermes update / hermes gateway restart / hermes --version。）",
    ),
    (
        "B06",
        "对外命令口径（技能文件）",
        "skills/real_estate/SKILL.md",
        [
            r"对外命令口径（2026-09-23 加）",
            r"coco pairing approve feishu",
            r"coco restore --file",
            r"只读诊断类可以 Coco 自己跑",
            r"改状态类绝不自己执行",
            r"!hermes (model|setup|backup|restore|pairing|tools|doctor|uninstall)\b",
        ],
        "技能文件是模型回答“要敲什么命令”的另一处依据：必须保留对外命令口径段，\n"
        "并保持 coco 口径（同 B05）。",
    ),
    (
        "B07",
        "晋升工具的两道守卫",
        "scripts/promote_release.sh",
        [r"备用方案守卫", r"PROMOTE_CONFIRM", r"--only", r"backup-", r"verified/\*"],
        "promote_release.sh 是“开发版→正式版”的唯一入口，2026-09-23 要求两道闸：\n"
        "① 晋升清单里若含 backup-* 标签钉住的提交（禁推正式版的备用方案）→ 直接拒绝，并提示改走 --only；\n"
        "② 推送前打印“将带上的提交清单 + 内容差异”，要求 PROMOTE_CONFIRM=<值> 才继续（防盲推）。\n"
        "这道闸没了 = 有人一条命令就能把滞留内容全推上正式版。",
    ),
    (
        "B08",
        "导入源文件留档步骤",
        "skills/real_estate/real-estate-excel-import/SKILL.md",
        [r"real_estate_docs", r"留档"],
        "经纪人发来的 Excel/合同放在消息缓存目录里，第二天会被系统自动清理（上游行为，我们不改），\n"
        "所以导入前要先复制一份到 $HERMES_HOME/real_estate_docs（备份会把该目录一起打包）。\n"
        "这段步骤没了 = 导入用过的表第二天就找不回来。",
    ),
    (
        "B09",
        "重复房源时的新照片提示",
        "agent/real_estate_prompt.py",
        [r"note_image_archive"],
        "命中重复房源时，新带来的照片会先归档但**不入库** → 必须让 Coco 如实说明，\n"
        "否则经纪人以为照片已经录进去了（要加图他会说“加图”）。",
    ),
    (
        "17",
        "用户可见命令文案（设置向导收尾屏）",
        "hermes_cli/setup_summary.py",
        [r'\("coco setup",', r'\("coco config",', r'\("coco status",', r'!\(\s*"hermes '],
        "设置向导收尾屏会把官方 hermes 命令印给用户（照着敲的是官方命令，甚至可能出现 Coco 禁止的 hermes update）。"
        "处理：把 _EDIT_WIZARD_ROWS / _EDIT_CONFIG_ROWS / _READY_ROWS 与文件里的提示句改回 coco 口径"
        "（该文件属官方层，上游同步会覆盖）。",
    ),
    (
        "18",
        "首次配对提示命令",
        # 官方 v0.21.5 把配对逻辑拆到独立模块（gateway/run_inbound_unauthorized.py），
        # 提示串随之搬家；官方再拆时按同样方式更新路径。
        "gateway/run_inbound_unauthorized.py",
        [r"coco \{profile_arg\}pairing approve ", r"!hermes \{profile_arg\}pairing approve "],
        "新用户首次私聊机器人时，配对提示会让所有者执行官方 hermes 命令（应为 coco pairing approve）。"
        "处理：把提示里的 `hermes ` 改回 `coco `。",
    ),
    (
        "19",
        "更新时的配置迁移提示",
        "hermes_cli/update_cmd_config.py",
        [r"'coco config migrate'", r"!hermes config migrate"],
        "更新过程中的配置迁移提示指回官方 hermes config migrate（用户会照抄）。"
        "处理：把提示里的 hermes config migrate 改回 coco config migrate。",
    ),
    (
        "20",
        "飞书配对页品牌参数",
        "plugins/platforms/feishu/adapter.py",
        [r"from=coco&tp=coco", r'!"from=hermes'],
        "一键配对飞书时，配对链接会带上官方品牌参数（from/tp=hermes），飞书配对页随之显示 Hermes 字样。\n"
        "处理：把 _begin_registration() 里追加的参数改回 from=coco&tp=coco"
        "（参考 patches/09-feishu-registration-brand.patch）。",
    ),
    (
        "21",
        "主页频道提示文案（走语言包）",
        "gateway/run_turn.py",
        [r't\("coco\.home_channel_missing"', r"!Hermes delivers"],
        "官方把「未设主页频道」提示的英文原文写死在这个文件里（含品牌名 Hermes），被飞书翻译后仍带 Hermes。\n"
        "处理：改成 t(\"coco.home_channel_missing\", …)，并跑 scripts/coco_locales_patch.py 补齐 17 个语言包"
        "（参考 patches/10-home-channel-notice-i18n.patch）。",
    ),
    (
        "22",
        "语言包里的 Coco 文案",
        "locales/zh.yaml",
        [r"coco:\s*\n\s*home_channel_missing"],
        "语言包被上游整批替换后，主页频道提示会退化成把键名 coco.home_channel_missing 露给用户。\n"
        "处理：跑 scripts/coco_locales_patch.py 补齐 17 个语言包"
        "（键集一致性由 tests/agent/test_i18n.py 与 tests/real_estate/test_coco_branding.py 守）。",
    ),
    (
        "23",
        "更新与网关重启提示的品牌口径",
        "gateway/run_notifications.py",
        [r"Coco update finished", r'!"Hermes update', r'!"Hermes is back'],
        "更新完成/失败/超时与「网关已上线」这几种提示里写着 Hermes，coco update 之后经纪人会直接看到。\n"
        "处理：把这几处文案换成 Coco，提示里引导的命令名也改成 coco update"
        "（参考 patches/11-user-facing-notice-brand.patch）。",
    ),
    (
        "24",
        "暂停提示的品牌口径",
        "gateway/run_busy.py",
        [r"Coco wasn't paused", r"Coco is already paused"],
        "「暂停 / 恢复」类提示里写着 Hermes（/pause）。\n处理：改成 Coco。",
    ),
    (
        "25",
        "设置向导的平台说明",
        "hermes_cli/setup_platforms.py",
        [r"where Coco delivers"],
        "设置向导打印的「Home Channel」说明里写着 Hermes。\n处理：改成 Coco。",
    ),
    (
        "26",
        "设置向导的 Mattermost 说明",
        # 官方 v0.21.5 把 Mattermost 向导搬到 hermes_cli/gateway_setup_wizard.py。
        "hermes_cli/gateway_setup_wizard.py",
        [r"where Coco delivers"],
        "Mattermost 向导里 HOME_CHANNEL 的帮助文本写着 Hermes。\n处理：改成 Coco。",
    ),
    (
        "27",
        "日报口径与引导菜单话术",
        "agent/real_estate_prompt.py",
        [
            r"【日报口径】",
            r"S / A / B / C 四级都要提",
            r"【引导菜单标准话术】",
            r"我帮你录：客户、房源、跟进、带看结果、成交单",
            r"不能替经纪人带看或成交",
        ],
        "这两段是模型写日报、写引导清单时的口径依据：\n"
        "① 等级分布必须四级全列（漏级会让老板以为那一级没说，真实教训），"
        "「数据要点」只写解读与建议、不重复上面已列的数字；\n"
        "② 引导清单必须用固定话术，且不能说 Coco\"能带看 / 能成交\"（它只能记录与提醒）。\n"
        "处理：恢复 agent/real_estate_prompt.py 里的【日报口径】与【引导菜单标准话术】两段"
        "（单测 tests/real_estate/test_prompt_guards.py 会守）。",
    ),
    (
        "28",
        "定时任务表与同步（2026-09-23 重设计）",
        "agent/coco_cron.py",
        [r"coco_overdue_sentinel", r"coco_opportunity", r"coco_day_end", r"coco_weekly_report",
         r"0 10,17 \* \* \*", r"no_agent", r"_sync_coco_jobs", r"_install_cron_scripts",
         r"_DEPRECATED_JOB_NAMES"],
        "定时任务是 Coco 主动帮经纪人的唯一通道，整张表被换掉就等于把「提醒体系」打回原形。\n"
        "现在的定义（2026-09-23 拍板）：09:00 早报、10:00/17:00 逾期哨兵（纯脚本不烧 token）、"
        "12:30 机会提醒、20:30 收工小结、周一 08:30 周报；午间检查与每 30 分钟检查已取消。\n"
        "处理：恢复这 5 条与三件配套机制——\n"
        "① _install_cron_scripts（cron 脚本只能放 HERMES_HOME/scripts/，这里生成转发入口）；\n"
        "② _sync_coco_jobs（提示词/时间/脚本对齐已注册任务，改代码才真生效）；\n"
        "③ _DEPRECATED_JOB_NAMES（清掉老版本残留的午间/30 分钟任务，避免新旧一起发）。\n"
        "单测：tests/real_estate/test_cron_prompt_sync.py 与 test_cron_scripts.py。",
    ),
    (
        "29",
        "假数据工具已删除（转化漏斗/市场周报）",
        "tools/real_estate_analytics.py",
        [r"!conversion_funnel", r"!weekly_market_report"],
        "这两个工具的输出是假的：conversion_funnel 写的是「假设30%带看/10%意向/3%成交」"
        "（代码注释自认模拟），weekly_market_report 的「本周重点」是写死的三句空话。\n"
        "2026-09-23 决定：没用就删掉，别让经纪人看到编出来的数字。\n"
        "处理：从 tools/real_estate_analytics.py 删掉这两个函数与其注册块，同时清掉"
        "toolsets.py 清单、scripts/smoke_test_real_estate.py 静态清单与用例、"
        "agent/real_estate_prompt.py 的工具清单（否则工具数三处不一致）。\n"
        "周报的真实统计在 scripts/coco_cron_weekly.py（用 db 的真实计数）。",
    ),
    (
        "30",
        "假数据工具不在工具集清单里",
        "toolsets.py",
        [r'!"conversion_funnel"', r'!"weekly_market_report"'],
        "toolsets.py 是「模型能看见哪些工具」的清单：注册表里删了、清单里还留着，"
        "会出现清单数与冒烟静态清单数不一致（tests/real_estate/test_tool_visibility.py 会红）。",
    ),
    (
        "31",
        "网关服务提示里的命令口径（重启/停止超时、会话内改服务）",
        # 官方层文件：上游同步会整批覆盖，故登记为挂钩点。
        "hermes_cli/gateway.py",
        [
            r"Check status: \{sudo\}coco gateway status",
            r"check `coco gateway status` or logs for final shutdown state",
            r"check `coco gateway status` or logs for final state",
            r"Use `coco gateway \{verb\}` from a shell outside the running gateway",
            r"!Check status: \{sudo\}hermes gateway status",
        ],
        "网关重启/停止超时、以及会话内尝试改动服务时，提示里印的是官方 `hermes gateway …`；\n"
        "Coco 实例上没有 hermes 命令入口（install/update 会移除指向本安装目录的软链），\n"
        "用户照着敲会 command not found。\n"
        "处理：把这四处提示改回 `coco gateway …`（该文件属官方层，上游同步会覆盖；\n"
        "单测 tests/hermes_cli/test_gateway_service.py 同步断言 coco 口径）。",
    ),
    (
        "32",
        "官方测试断言：装服务提示命令",
        "tests/hermes_cli/test_ensure_gateway_service.py",
        [
            r'assert "coco gateway" in out',
            r'assert "coco gateway install" in out',
            r'!"hermes gateway',
        ],
        "这条官方测试断言「装后台服务失败时提示用户敲哪条命令」。\n"
        "hermes_cli/gateway.py 已按 Coco 口径打印 coco gateway / coco gateway install，\n"
        "断言没跟着改就会红（功能本身正常）。\n"
        "处理：把断言改回 coco 口径（参考 patches/README.md 第 14 处）。",
    ),
    (
        "33",
        "官方测试断言：命名 profile 提示",
        "tests/hermes_cli/test_gateway_no_new_standalone_profile.py",
        [
            r'assert "coco gateway install" in out and "coco gateway migrate --multiplex" in out',
            r'assert f"coco -p \{profile\} gateway install --force" in out',
            r'!"hermes gateway',
        ],
        "命名 profile 被拒时的三处提示（装在哪 / 怎么合并 / --force 写法）在 hermes_cli/gateway.py 里\n"
        "已是 coco 口径，官方断言写的还是 hermes。\n"
        "处理：把三条断言改回 coco 口径（参考 patches/README.md 第 14 处）。",
    ),
    (
        "34",
        "官方测试断言：配置迁移提示",
        "tests/hermes_cli/test_update_yes_flag.py",
        [
            r'assert "coco config migrate" in out',
            r'!"hermes config migrate"',
        ],
        "更新过程中配置迁移提示跳过时，hermes_cli/update_cmd_config.py 打印的是\n"
        "'coco config migrate'，官方断言写的还是 hermes config migrate。\n"
        "处理：把断言改回 coco 口径（参考 patches/README.md 第 14 处）。",
    ),
    (
        "35",
        "官方测试断言：终端层网关生命周期守卫（两层守卫分组）",
        "tests/hermes_cli/test_gateway_restart_loop.py",
        [
            r'_COCO_GUARDED_CMDS',
            r'_GATEWAY_GUARDED_CMDS',
            r'test_blocks_update_commands_via_coco_guard',
            r'test_coco_guard_ignores_force_flag',
        ],
        "这组用例里，更新类命令（systemctl restart hermes-gateway、hermes gateway restart 等）会被 Coco 的更新守卫先拦下，"
        "返回自有形状（error/status + 中文话术）；官方那组断言要的是 exit_code=1 + 英文 Blocked，混在一起必红"
        "（KeyError: 'exit_code'）。"
        "处理：保持「Coco 守卫组 / 官方网关守卫组」的拆分与各自断言（参考 patches/README.md 第 15 处）。",
    ),
    (
        "36",
        "官方测试断言：CLI 会话不因继承环境变量被拦",
        "tests/hermes_cli/test_gateway_restart_loop.py",
        [
            r'command="pkill -f hermes.*gateway"',
            r'!command="hermes gateway restart"',
        ],
        "这条官方用例验的是「CLI 会话继承了 _HERMES_GATEWAY=1 也不该被官方闸门拦」。更新类命令会被 Coco 更新守卫一律拦下"
        "（不看会话），与本用例无关，所以换用只由官方守卫处理的命令。"
        "处理：保持用 pkill 那条命令（参考 patches/README.md 第 15 处）。",
    ),
    (
        "37",
        "清空对话确认框兜底口径",
        "gateway/run_busy.py",
        [
            r"confirm_required\s*=\s*False",
            r'destructive_slash_confirm"\s*,\s*False',
        ],
        "清空对话类命令（/new、/reset、/undo）的确认框兜底被改回官方口径（缺键或读配置失败都要弹框）。"
        "经纪人不会输入 /always，弹一次就把「开新会话」卡住。"
        "处理：把 _maybe_confirm_destructive_slash 里两处兜底改回 False（参考 patches/README.md 第 16 处）。",
    ),
    (
        "38",
        "飞书工具进展默认关（平台档）",
        "gateway/display_config.py",
        [
            r'"feishu":\s*\{\*\*_TIER_MEDIUM,\s*"tool_progress":\s*"off"\}',
        ],
        "飞书这一档的工具进展默认值被改回官方 new（每调一个工具发一条），经纪人的聊天窗会被这些行刷屏。"
        "处理：改回 {**_TIER_MEDIUM, \"tool_progress\": \"off\"}（参考 patches/README.md 第 18 处）。",
    ),
    (
        "39",
        "飞书工具进展默认关（代码默认值）",
        "hermes_cli/config_defaults.py",
        [
            r'"feishu":\s*\{"tool_progress":\s*"off"\}',
        ],
        "新装默认值里飞书的工具进展又变成显示（官方默认 new）。"
        "处理：改回 \"feishu\": {\"tool_progress\": \"off\"}（参考 patches/README.md 第 18 处）。",
    ),
    (
        "40",
        "飞书工具进展默认关（更新时对齐）",
        "scripts/coco_config_align.py",
        [
            r'"display\.platforms\.feishu\.tool_progress":\s*"off"',
        ],
        "对齐脚本缺了这一项，已装实例跑更新也拉不回「工具进展关」，体检也会一直 WARN。"
        "处理：恢复 STANDARD 里的 display.platforms.feishu.tool_progress = off（参考 patches/README.md 第 18 处）。",
    ),
    (
        "41",
        "官方测试断言：确认框默认值按 Coco 口径",
        "tests/hermes_cli/test_destructive_slash_confirm_gate.py",
        [
            r"def test_default_is_false",
            r'destructive_slash_confirm"\]\s*is False',
        ],
        "官方这两条用例断言「默认要弹确认框」（upstream 默认 True），与 Coco 的口径相反，"
        "改回去就必红。处理：断言保持 False（参考 patches/README.md 第 17 处）。",
    ),
    (
        "42",
        "示例配置的种子值（新装实例第一份 config.yaml）",
        "cli-config.yaml.example",
        [
            r"^\s*threshold:\s*0\.8",
            r"^\s*protect_last_n:\s*40",
            r"^\s*max_turns:\s*500",
            r"!^\s*threshold:\s*0\.50",
            r"!^\s*protect_last_n:\s*20",
        ],
        "install.sh 会把这份示例复制成新实例的 config.yaml：值退回官方（0.50 / 20）时，"
        "新装实例在对齐脚本跑之前就是错的，文档里也自相矛盾。\n"
        "处理：改回 0.8 / 40（max_turns 已是 500，见 patches/README.md 第 19 处）。",
    ),
    (
        "43",
        "压缩兜底值·智能体侧（与出厂默认一致）",
        "agent/agent_init.py",
        [
            r'^\s*threshold = float\(cfg\.get\("threshold", 0\.8\)\)',
            r'^\s*protect_last = int\(cfg\.get\("protect_last_n", 40\)\)',
            # 官方那两处兜底必须不在：配置缺键/读取失败时会退回官方口径，与出厂默认打架
            r'!cfg\.get\("threshold", 0\.50\)',
            r'!cfg\.get\("protect_last_n", 20\)',
        ],
        "配置里没写压缩参数、或配置读取失败时，走的就是这里的兜底值。官方默认 0.50 / 20 与 "
        "hermes_cli/config_defaults.py 的 0.8 / 40 不一致，官方用例 "
        "tests/agent/test_compression_config_defaults.py 会报红（2026-09-27 踩过）。",
    ),
    (
        "44",
        "压缩兜底值·终端/桌面界面侧",
        "tui_gateway/session_compression.py",
        [
            r'\("protect_last_n", 40, 0\)',
            r'!\("protect_last_n", 20, 0\)',
        ],
        "会话里把压缩配置键删掉时，界面侧按这里的兜底值恢复；官方 20 会让保留条数退回 20。",
    ),
    (
        "45",
        "压缩兜底值·上下文切换守卫",
        "hermes_cli/context_switch_guard.py",
        [
            r'getattr\(cc, "protect_last_n", 40\)',
            r'!getattr\(cc, "protect_last_n", 20\)',
        ],
        "压缩器对象上没这个属性时的兜底值；官方 20 与出厂默认 40 不一致。",
    ),
    (
        "46",
        "官方测试断言：更新完成提示品牌",
        "tests/gateway/test_update_command.py",
        [
            r'assert "Coco update finished" in',
            r"!Hermes update finished",
        ],
        "更新完成提示在 gateway/run_notifications.py 里是「✅ Coco update finished.」，官方断言写的还是\n"
        "Hermes update finished（两条）。处理：改回 coco 口径（参考 patches/README.md 第 21 处）。",
    ),
    (
        "47",
        "官方测试断言：重启上线提示品牌（直接断言）",
        "tests/gateway/test_restart_notification.py",
        [
            r'"♻️ Gateway online — Coco is back and ready\."',
            r"!Hermes is back and ready",
        ],
        "网关重启后的上线提示是「♻️ Gateway online — Coco is back and ready.」，官方断言写的是 Hermes。\n"
        "处理：改回 coco 口径（参考 patches/README.md 第 21 处）。",
    ),
    (
        "48",
        "官方测试断言：重启上线提示品牌（重放用例）",
        "tests/gateway/test_restart_notice_replay.py",
        [
            r'ONLINE_NOTICE = "♻️ Gateway online — Coco is back and ready\."',
            r"!Hermes is back and ready",
        ],
        "同一个上线提示常量，官方版本会让这条用例红。处理：改回 coco 口径（参考 patches/README.md 第 21 处）。",
    ),
    (
        "49",
        "官方测试断言：重启上线提示品牌（多 profile 用例）",
        "tests/gateway/test_planned_restart_notice_multiplex.py",
        [
            r'ONLINE_NOTICE = "♻️ Gateway online — Coco is back and ready\."',
            r"!Hermes is back and ready",
        ],
        "同上，多 profile 的重启通知用例。处理：改回 coco 口径（参考 patches/README.md 第 21 处）。",
    ),
    (
        "50",
        "官方测试断言：配对提示里的命令名",
        "tests/gateway/test_unauthorized_sender_notices.py",
        [
            r"`coco -p work pairing approve discord ZZZZ9999`",
            r"!`hermes -p work pairing approve",
        ],
        "陌生人私聊的配对提示由 gateway/run_inbound_unauthorized.py 生成，命令名是 coco，官方断言写的是 hermes。\n"
        "处理：改回 coco 口径（参考 patches/README.md 第 21 处）。",
    ),
    (
        "51",
        "官方测试断言：系统提示词按段落相对顺序校验",
        "tests/agent/test_system_prompt.py",
        [
            r"ordered_markers = \(",
            r"assert positions == sorted\(positions\)",
            r"!assert prompt == expected",
        ],
        "本仓在系统提示词里注入了身份与房产业务段落（第 02/03 处），逐字比对整段提示词必然不符。\n"
        "处理：改回「关键段落相对顺序 + 静态段是前缀」的校验（参考 patches/README.md 第 21 处）。",
    ),
]

# 文件/目录存在性检查：编号 / 名称 / 相对路径 / 类型(file|dir|glob) / 最少数量 / 失败提示
PATH_CHECKS = [
    ("A1", "版本标识", "VERSION", "file", 1, "Coco 版本号丢失（安装/更新终端不再显示版本）"),
    ("A2", "房产数据层", "agent/real_estate_db.py", "file", 1, "房产数据库层丢失"),
    ("A3", "房产提示词", "agent/real_estate_prompt.py", "file", 1, "房产提示词模块丢失"),
    ("A4", "定时任务模块", "agent/coco_cron.py", "file", 1, "定时任务模块丢失（enable_cron 会失效）"),
    ("A5", "房产工具集", "tools/real_estate_*.py", "glob", 20, "房产工具文件缺失（工具数会减少）"),
    ("A6", "房产技能", "skills/real_estate/SKILL.md", "file", 1, "Coco 操作手册（技能）丢失"),
    ("A7", "数据库迁移", "migrations/*.sql", "glob", 1, "迁移目录丢失（表结构升级会失败）"),
    ("A8", "一键安装脚本", "install.sh", "file", 1, "安装脚本丢失（新用户无法安装）"),
    ("A9", "一键更新脚本", "scripts/update.sh", "file", 1, "更新脚本丢失（更新铁律被破坏）"),
    ("A10", "部署体检脚本", "scripts/healthcheck.py", "file", 1, "体检脚本丢失"),
    ("A11", "迁移执行器", "scripts/migrate.py", "file", 1, "迁移执行器丢失"),
    ("A12", "数据库备份脚本", "scripts/backup_db.py", "file", 1, "备份脚本丢失（数据安全网缺失）"),
    ("A13", "工具冒烟脚本", "scripts/smoke_test_real_estate.py", "file", 1, "冒烟脚本丢失（无法做工具层验收）"),
    ("A14", "房产单测", "tests/real_estate/*.py", "glob", 5, "房产单测丢失（回归无保障）"),
    ("A15", "房产 CI 工作流", ".github/workflows/real-estate-tests.yml", "file", 1, "房产测试 CI 丢失"),
    ("A16", "迁移/备份文档", "docs/BACKUP_MIGRATION.md", "file", 1, "备份迁移文档丢失"),
    ("A17", "飞书实测清单", "docs/TESTING_FEISHU_FULL.md", "file", 1, "飞书全量实测清单丢失"),
    ("A18", "官方残留清理脚本", "scripts/prune_official_deleted.sh", "file", 1, "官方已删残留的清理脚本丢失（同步时无法顺带清理）"),
    ("A19", "楼层回填脚本", "scripts/backfill_floor_from_title.py", "file", 1, "历史房源楼层回填脚本丢失（老数据无法批量补楼层）"),
    ("A20", "通道守卫工作流", ".github/workflows/channel-guard.yml", "file", 1, "通道守卫丢失（默认分支被改成测试分支时没人拦，别人会装到测试版）"),
    ("A21", "通道工具", "scripts/coco_channel.sh", "file", 1, "通道工具丢失（稳定版/测试版切换与显示失效）"),
    ("A22", "晋升脚本", "scripts/promote_release.sh", "file", 1, "晋升脚本丢失（测试版无法安全转为正式版）"),
    ("A23", "验收登记脚本", "scripts/mark_verified.sh", "file", 1, "验收登记脚本丢失（无法记录实测评语，晋升闸门形同虚设）"),
    ("A24", "测试号脚本", "scripts/tag_test_version.sh", "file", 1, "测试号脚本丢失（测试版无法编号，容易分不清测的是哪一版）"),
    ("A25", "卸载脚本", "scripts/uninstall.sh", "file", 1, "卸载脚本丢失（正式版实例没有卸载通道，只能重装系统）"),
    ("A26", "PATH 兜底脚本", "scripts/path_guard.sh", "file", 1, "PATH 兜底脚本丢失（coco 入口装到 ~/.local/bin 时用户会敲不到命令）"),
    ("A27", "定时任务脚本", "scripts/coco_cron_*.py", "glob", 5, "定时任务脚本丢失（逾期哨兵/机会提醒/早报/收工小结/周报的数据收集都在这里）"),
    ("A28", "照片归档共用件", "agent/real_estate_media.py", "file", 1, "照片归档共用件丢失（房源照片会留在会被 24 小时清理的缓存目录里，一天后消失）"),
    ("A29", "照片存量修复脚本", "scripts/recover_property_images.py", "file", 1, "照片存量修复脚本丢失（历史房源里已丢的照片没法从备份捞回）"),
    # 2026-09-27 按老板决定删掉的官方目录：登记为「不该存在」，同步若把它们带回来就报警
    ("A30", "已删官方目录·桌面版", "apps", "absent", 0, "apps/ 又被同步带回来了（Coco 不做桌面版，删它是为让用户少下 39MB / 3000+ 文件）"),
    ("A31", "已删官方目录·文档站", "website", "absent", 0, "website/ 又被同步带回来了（官方文档站，装机用不到）"),
    ("A32", "已删官方目录·可选技能", "optional-skills", "absent", 0, "optional-skills/ 又被同步带回来了（官方可选技能，Coco 用不到）"),
    ("A33", "evals 最小必要集（测试收集依赖）", "evals/heartbeat_idle_wire.py", "file", 1,
     "evals 最小必要集缺失 —— tests/gateway 与 tests/agent 里共 10 个用例 import 它，缺了整个区会在收集阶段中断（2026-09-27 踩过）"),
    ("A34", "已删官方目录·前端测试", "tests-js", "absent", 0, "tests-js/ 又被同步带回来了（官方前端测试）"),
    ("A35", "已删官方目录·离线评测（整体）", "evals/acp_empty_session_wire.py", "absent", 0,
     "官方 evals/ 整个被同步带回来了 —— 只需要最小必要集那 13 个文件（见 patches/README.md）"),
    # 2026-09-27 补：测试按「脚本路径」调用（不是 import）的 evals 文件。扫 import 扫不到它们，
    # 而探针还会拉起同目录的兄弟脚本，光按文件名找也找不全。
    ("A36", "evals 运行时脚本·定时任务竞态探针", "evals/cron_timeout_fork_race.py", "file", 1,
     "缺它则 tests/agent/test_deadline_fork_race.py 报 can't open file"),
    ("A37", "evals 运行时脚本·失败写入归属探针", "evals/gateway_failure_ownership/probe.py", "file", 1,
     "缺它则 tests/gateway/test_failure_writer_ownership.py 报 can't open file"),
    ("A38", "evals 运行时脚本·外来写入者（探针的兄弟模块）", "evals/gateway_failure_ownership/foreign_writer.py", "file", 1,
     "缺它则上面的探针能跑起来但观测不达（17/20），测试红得看不出原因（2026-09-27 踩过）"),
]


EVAL_DIRS_MUST_BE_COMPLETE = [
    # 这个目录里的探针脚本会拉起同目录的兄弟脚本（probe.py → foreign_writer.py），
    # 只恢复被测试点名的那一个文件不够 —— 要求整目录与删除前一致。
    "evals/gateway_failure_ownership",
]


def check_content(repo: Path):
    results = []
    for cid, name, rel, patterns, tip in CONTENT_CHECKS:
        target = repo / rel
        if not target.exists():
            results.append((cid, name, False, f"文件不存在：{rel}"))
            continue
        try:
            text = target.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:  # pragma: no cover
            results.append((cid, name, False, f"读取失败：{exc}"))
            continue
        # 模式以 ! 开头 = 反向匹配（不应出现）：用于守「官方版带回来、Coco 必须去掉」的东西
        missing = []
        for _p in patterns:
            if _p.startswith("!"):
                if re.search(_p[1:], text, re.M):
                    missing.append(_p)
            elif not re.search(_p, text, re.M):
                missing.append(_p)
        if missing:
            results.append((cid, name, False, f"未匹配到模式：{missing} —— {tip}"))
        else:
            results.append((cid, name, True, rel))
    return results


def check_paths(repo: Path):
    results = []
    for cid, name, pattern, kind, min_count, tip in PATH_CHECKS:
        if kind == "file":
            ok = (repo / pattern).is_file()
            found = 1 if ok else 0
        elif kind == "dir":
            ok = (repo / pattern).is_dir()
            found = 1 if ok else 0
        elif kind == "absent":
            # 反向检查：这个路径「不该存在」（删过的官方目录被同步带回来时报警）
            ok = not (repo / pattern).exists()
            found = 0 if ok else 1
        else:  # glob
            found = len(list(repo.glob(pattern)))
            ok = found >= min_count
        if kind == "absent":
            detail = f"{pattern}" + ("（已确认不在 ✔）" if ok else "（又出现了）")
        else:
            detail = f"{pattern}" + (f"（找到 {found}，需 ≥{min_count}）" if kind == "glob" else "")
        results.append((cid, name, ok, detail if ok else f"{detail} —— {tip}"))
    return results


def check_eval_references(repo: Path):
    """删目录的验收：测试真正调用到的 evals 文件必须都在。

    两个来源：
      · 代码里写死的 evals 路径（`"evals/x.py"` 或 `"evals" / "x.py"` 拼接写法）；
      · 需要整目录完整的 evals 子目录（探针脚本会拉起同目录的兄弟脚本，光按名字找不到）。
    """
    results = []

    ref_re = re.compile(r"evals/[A-Za-z0-9_./-]+\.py")
    split_re = re.compile(r"[\"']evals[\"']((?:\s*/\s*[\"'][^\"'\n]+[\"'])+)")
    code_hint = re.compile(r"subprocess|sys\.executable|Path|str\(|parents\[")
    refs = set()
    for base in ("tests", "scripts"):
        for src in (repo / base).rglob("*.py"):
            try:
                text = src.read_text(encoding="utf-8", errors="ignore")
            except Exception:  # pragma: no cover
                continue
            for line in text.splitlines():
                if "#" in line:
                    line = line.split("#", 1)[0]
                if "evals" not in line or not code_hint.search(line):
                    continue
                refs.update(ref_re.findall(line))
                for tail in split_re.findall(line):
                    parts = re.findall(r"[\"']([^\"'\n]+)[\"']", tail)
                    if parts:
                        refs.add("evals/" + "/".join(parts))
    missing = sorted(r for r in refs if not (repo / r).exists())
    results.append((
        "A39", "评测引用完整（测试调用到的 evals 文件都在）", not missing,
        f"扫到 {len(refs)} 处引用，全部在位" if not missing
        else f"缺 {missing} —— 删目录时必须按「测试实际调用到的文件」逐个恢复，光扫 import 必然漏",
    ))

    # 这些目录要求「与删除前一致」：里面的脚本会互相拉起（probe → foreign_writer）
    baseline = "fa1f5d7^"  # 删官方目录那次提交的上一个提交
    for sub in EVAL_DIRS_MUST_BE_COMPLETE:
        try:
            out = subprocess.run(
                ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", baseline, "--", sub],
                capture_output=True, text=True, timeout=30,
            )
        except Exception:  # pragma: no cover
            out = None
        if out is None or out.returncode != 0:
            results.append(("A40", f"评测目录完整·{sub}", True, f"跳过（取不到基线 {baseline}，可能不是完整克隆）"))
            continue
        want = [ln for ln in out.stdout.splitlines() if ln.strip()]
        gone = [p for p in want if not (repo / p).exists()]
        results.append((
            "A40", f"评测目录完整·{sub}", not gone,
            f"{len(want)} 个文件都在" if not gone
            else f"缺 {gone} —— 这个目录里的脚本会互相拉起，必须整目录恢复（见 patches/README.md 删目录一节）",
        ))
    return results


def main() -> int:
    repo = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    print("=" * 74)
    print(" Coco 挂钩点自检")
    print(f" 仓库：{repo}")
    print("=" * 74)

    all_results = check_content(repo) + check_paths(repo) + check_eval_references(repo)

    width = 34
    print(f"\n{'编号':<5}{'检查项':<{width}}{'结果':<6}说明")
    print("-" * 74)
    failures = []
    for cid, name, ok, detail in all_results:
        pad = width - sum(2 if ord(ch) > 127 else 1 for ch in name)
        status = "PASS" if ok else "FAIL"
        print(f"{cid:<5}{name}{' ' * max(pad, 1)}{status:<6}{detail}")
        if not ok:
            failures.append((cid, name, detail))

    print("-" * 74)
    total = len(all_results)
    print(f"合计 {total} 项：通过 {total - len(failures)}，失败 {len(failures)}")

    if failures:
        print("\n【需要处理】")
        for cid, name, detail in failures:
            print(f"  [{cid}] {name}\n       {detail}")
        print("\n提示：Coco 的改动语义说明见 patches/README.md；")
        print("      同步上游的操作流程见 docs/UPSTREAM_SYNC.md。")
        return 1

    print("\n全部通过 —— 挂钩点与自建文件都在位。")
    print("下一步：跑 scripts/smoke_test_real_estate.py（工具层冒烟）与单测。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
