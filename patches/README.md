# Coco 改动集（patches）

本目录记录 **Coco 相对官方 Hermes 源码的全部改动**，用途有两个：

1. **看得清**：同步官方新版本时，知道自己改了官方哪些地方、每处是干什么的；
2. **不遗漏**：同步后配合 `scripts/check_coco_hooks.py` 逐项自检，防止改动丢失。

## 这些补丁怎么来的

以官方 **2026-08-09 的主线快照**（commit `3bd844edf1777a680115f88a68474b4fb434092f`）为基准，
与 Coco 当前代码逐文件对比生成。

> ⚠️ **重要用法说明**：这些 `.patch` 文件是**改动记录**，不是拿来机械 `git apply` 的。
> 官方一旦改动了同一个文件（几乎必然），补丁就会 apply 失败。
> **正确的做法**：读本文件下面每一处的「改什么 / 为什么 / 上游变了怎么办」，
> 在新底座上重新实现，再用自检脚本验证结果。

## 改动清单（共 22 处官方文件 + 2 个自有文档）

| 编号 | 官方文件 | 改动内容 |
|---|---|---|
| 01 | `toolsets.py` | 注册 `real_estate` 工具集，并把 `real_estate` 挂进 `hermes-feishu` 工具集的 `includes` |
| 02 | `agent/prompt_builder.py` | 把身份文案常量（`DEFAULT_AGENT_IDENTITY`、能力说明等）内容换成 Coco |
| 03 | `agent/system_prompt.py` | 把房产提示词 `get_real_estate_prompt()` 拼进稳定提示词片段 |
| 04 | `agent/agent_init.py` | 启动时调用 `init_real_estate_db()` 初始化房产库 |
| 05 | `hermes_cli/config_defaults.py` | 压缩阈值 `threshold` 0.50→**0.8**、`protect_last_n` 20→**40** |
| 06 | `plugins/platforms/feishu/adapter.py` | 首次对话三件事：发欢迎语、发加密密钥备份提醒、自动注册定时任务 |
| 07 | `gateway/run_turn.py`（官方 v0.21 起从 `gateway/run.py` 拆到这里） | 首次对话开场白换成 Coco 自我介绍；关闭官方 profile-build 引导 |
| 08 | `scripts/sandbox/pick-release-tags.sh` | 标签过滤正则放宽：同时认「日期式 `vYYYY.M.D`」和「语义化 `vX.Y.Z`（含 `-N` 后缀）」 |
| 09 | `plugins/platforms/feishu/adapter.py` | 一键配对的链接参数改成 `from=coco&tp=coco`（官方是 `from=hermes&tp=hermes`，飞书配对页会显示 Hermes 字样） |
| 10 | `gateway/run_turn.py` + `locales/*.yaml`（17 个） | 「未设主页频道」提示改走 i18n 键 `coco.home_channel_missing`，文案换成 Coco 品牌；17 个语言包由 `scripts/coco_locales_patch.py` 追加（官方有键集一致性测试，必须全加） |
| 11 | `gateway/run_notifications.py`、`gateway/run_busy.py`、`hermes_cli/setup_platforms.py`、`hermes_cli/gateway.py` | 面向用户的提示去掉 Hermes：更新完成/失败/超时、网关已上线、暂停/恢复、设置向导的 Home Channel 说明；提示里引导的命令名 `hermes update` → `coco update` |
| 12 | `gateway/run_inbound_unauthorized.py`（官方 v0.21.5 起从 `gateway/run_inbound.py` 拆出） | 首次私聊的配对提示里命令名 `hermes {profile_arg}pairing approve` → `coco …`；房主侧提示里的 `hermes pairing approve` → `coco …` |
| 13 | `hermes_cli/gateway_setup_wizard.py`（官方 v0.21.5 起从 `hermes_cli/gateway.py` 拆出） | Mattermost 向导的 Home Channel 帮助文本 `where Hermes delivers` → `where Coco delivers` |
| 14 | `tests/hermes_cli/test_ensure_gateway_service.py`、`tests/hermes_cli/test_gateway_no_new_standalone_profile.py`、`tests/hermes_cli/test_update_yes_flag.py` | 官方测试里断言「提示用户敲哪条命令」的字符串改成 coco 口径（`coco gateway` / `coco gateway install` / `coco config migrate` …） |
| 15 | `tests/hermes_cli/test_gateway_restart_loop.py` | 终端层网关生命周期守卫那组：官方一条参数化用例按「谁拦的」拆成两组（更新类命令由 Coco 更新守卫先拦、按自有形状断言；其余由官方网关守卫拦）；「CLI 会话不该被拦」那条改用只由官方守卫处理的命令 |
| 16 | `gateway/run_busy.py` | 清空对话类命令（/new、/reset、/undo）的确认框兜底：官方缺键即弹框，Coco 缺键即直接执行 |
| 17 | `tests/hermes_cli/test_destructive_slash_confirm_gate.py` | 官方两条断言「默认要弹确认框」的用例改按 Coco 口径（默认不弹），与第 05 处的默认值改动配套 |
| 18 | `hermes_cli/config_defaults.py`、`gateway/display_config.py`、`scripts/coco_config_align.py` | 飞书默认不显示工具进展行：代码默认值、平台档默认值、更新时对齐三处都写成 `off`（官方飞书档默认 `new`，每调一个工具发一条） |
| 19 | `hermes_cli/setup.py`、`cli-config.yaml.example` | 向导默认值补齐：删掉官方那句把压缩阈值写回 `0.50` 的、提示文案改成实写的 500 / 0.8；示例配置（新装实例的种子）阈值 `0.50`→**0.8**、`protect_last_n` `20`→**40** |
| 20 | `agent/agent_init.py`、`tui_gateway/session_compression.py`、`hermes_cli/context_switch_guard.py` | 压缩兜底值补齐：官方 `0.50` / `20` → **0.8 / 40**，与第 05 处的出厂默认值全口径一致（配置缺键或读取失败时走的就是这些兜底） |
| 21 | `tests/gateway/test_update_command.py`、`test_restart_notification.py`、`test_restart_notice_replay.py`、`test_planned_restart_notice_multiplex.py`、`test_unauthorized_sender_notices.py`、`tests/agent/test_system_prompt.py` | 官方测试断言改按 Coco 口径：更新完成/重启上线/配对提示里的 Hermes 字样改 Coco（第 11 处的配套）；系统提示词那条从「逐字比对」改成「关键段落相对顺序 + 静态段是前缀」 |
| 22 | `gateway/run_busy.py`、`gateway/run_inbound.py`、`gateway/slash_commands.py`、`agent/onboarding.py` | 经纪人连发消息时看到的英文提示全部改中文：连发状态行（含 `2 min elapsed, running: terminal` 这类英文细节）、一次性提示、`/busy` 命令回复、更新/重启期间的排队提示 |

另有 2 个**自有文档**（不属于官方代码，同步时直接保留即可）：
`README.md`、`README.zh-CN.md`。

## 逐处说明

### 01 toolsets.py —— 工具集注册（最关键，漏了模型就"没能力"）
- **改什么**：在 `TOOLSETS` 字典里新增 `real_estate` 工具集定义；并在 `hermes-feishu`
  工具集的 `includes` 列表中加入 `"real_estate"`。
- **为什么**：Hermes 靠工具集清单决定"哪些工具发给模型"。不注册，模型完全看不到
  房产工具，会回答"我没有这个能力"。
- **上游变了怎么办**：官方若重构工具集结构，按新结构重新挂载；关键是
  **两个条件都要满足**（定义 + 被 hermes-feishu 引用）。

### 02 agent/prompt_builder.py —— 身份文案
- **改什么**：把默认身份常量等内容替换为 Coco 版本（"你是 Coco，经纪人的
  客户和房源管家…"），以及"你能做什么"的回答指引。
- **为什么**：这是 Coco 自我介绍的第一来源。注意 Coco 的身份文案在**共 7 处**都有
  副本（adapter 欢迎语、gateway 开场白、prompt_builder、房产提示词、SOUL.md、
  技能文件等），改身份必须全部同步，否则会出现"改了这里那里还是旧文案"。
- **上游变了怎么办**：定位新版的对应常量名（可能改名），重新替换内容。

### 03 agent/system_prompt.py —— 注入房产提示词
- **改什么**：导入 `get_real_estate_prompt()` 并把返回值拼进稳定提示词片段。
- **为什么**：房产规则（登记规范、匹配原则、统计口径等）靠它注入。
- **上游变了怎么办**：找到新版系统提示词的拼装位置（函数名/变量名可能变），
  在同一位置恢复注入。**必须保持"稳定片段"语义**（不要拼进每轮变化的部分，
  否则会破坏 prompt 缓存、增加成本）。

### 04 agent/agent_init.py —— 启动初始化数据库
- **改什么**：导入并调用 `init_real_estate_db(db_url)`。
- **为什么**：CLI 启动路径需要建表。（注：gateway 启动路径不经过 agent_init，
  靠 `get_real_estate_db()` 的惰性初始化兜底；两条路都得在。）
- **上游变了怎么办**：找到新版 CLI 初始化入口，恢复调用。

### 05 hermes_cli/config_defaults.py —— 压缩阈值
- **改什么**：`threshold` 0.50 → **0.8**；`protect_last_n` 20 → **40**。
- **为什么**：2026-08-29 定的统一配置（与生产 Coco 保持一致），
  压得太早会丢上下文。
- **上游变了怎么办**：新版若换了配置键名或结构，按新结构设置相同语义的值。

### 06 plugins/platforms/feishu/adapter.py —— 首次对话三件事
- **改什么**：
  1. `_maybe_send_coco_welcome`：首次对话发送固定欢迎语（marker 文件控制只发一次）；
  2. 首次对话把加密密钥（`~/backups/real_estate/enc_key.txt`）内容直接发给经纪人
     —— 密钥丢失=客户手机号永久不可解密，所以要在聊天记录里留一份；
  3. 首次对话自动注册定时任务（早报/午间/逾期，走 `register_coco_cron_jobs`）。
- **为什么**：这三件事决定了经纪人第一次接触 Coco 的体验与数据安全兜底。
- **上游变了怎么办**：官方对飞书适配器改动频繁（同期约 25 个提交）。找到新版
  "接收消息 → 处理"的入口，把这三件事挂回去；marker 文件路径保持
  `~/.hermes/.coco_welcome_sent`。

### 07 gateway/run.py —— 首次对话开场白
- **改什么**：网关首次对话的开场白换成 Coco 自我介绍；跳过官方的
  profile-build 引导流程。
- **为什么**：不经 adapter 的路径（模型自己输出自我介绍时）也需要正确文案，
  否则问"你是谁"会得到官方默认回答。
- **上游变了怎么办**：gateway/run.py 是官方改动最频繁的文件之一（同期 2,000+
  提交）。定位新版"首次对话/开场白"的逻辑点重新挂钩，不要试图保留旧代码块。

### 08 scripts/sandbox/pick-release-tags.sh —— CI 挑标签的正则
- **改什么**：把只认日期式 `vYYYY.M.D` 的 grep 正则，放宽为同时认语义化版本
  （`^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9]+)?$`）。
- **为什么**：Coco 的发布标签是 `v0.21.3-1` 这种语义化格式，不是官方的日期式。
  不改这条，继承自官方的 install-e2e 工作流会报 `no release tags found`。
- **上游变了怎么办**：只要 Coco 还用语义化标签，这个放宽就必须保留；
  官方若改了该脚本的挑标签方式，按新方式重新放宽。

### 09 plugins/platforms/feishu/adapter.py —— 配对链接的品牌参数
- **改什么**：`_begin_registration()` 里给飞书返回的配对链接追加的参数，
  由 `from=hermes&tp=hermes` 改成 `from=coco&tp=coco`。
- **为什么**：这两个参数是我们自己加的（飞书不返回它们），但飞书配对页会据此
  带出官方品牌文案（页面上出现「Hermes Agent 正在配置中…」）。
- **上游变了怎么办**：参数追加写在 `_begin_registration()` 的 `qr_url +=` 一行，
  官方若改了配对流程（例如换成别的注册接口），在新流程里同样只追加 coco 品牌参数。

### 10 gateway/run_turn.py + locales/*.yaml —— 「未设主页频道」提示的品牌与语言
- **改什么**：官方把这条提示的英文原文写死在 `gateway/run_turn.py`（`📬 No home channel is set for … A home channel is where Hermes delivers …`）。
  改成走 i18n：`t("coco.home_channel_missing", platform=…, sethome_cmd=…)`；17 个语言包各追加一个 `coco.home_channel_missing` 键
  （中文/繁体写中文，其它语言先用英文），由 `scripts/coco_locales_patch.py` 追加。
- **为什么**：原文里的品牌名是 Hermes，经纪人侧会看到（飞书客户端把英文提示自动翻译成中文时照搬了这个词）。
  顺带把「系统/频道类提示一律称 Coco」写进 `agent/real_estate_prompt.py` 的【品牌口径】，防止模型转述时又抄出 Hermes。
- **上游变了怎么办**：官方若把这条提示也 i18n 了（换成它自己的键），按官方新键重挂；
  只要它是硬编码英文，就用 `scripts/coco_locales_patch.py` + 这一行改写维持 Coco 口径。
  **注意**：`tests/agent/test_i18n.py` 强制「非英文语言包的键集必须与 en.yaml 完全一致」，
  所以 17 个语言包缺一个都会测试失败 —— 同步后务必跑一次该脚本。

### 11 用户可见提示的品牌口径（更新 / 重启 / 暂停 / 向导）
- **改什么**：把这几处官方字符串里的品牌名 Hermes 换成 Coco ——
  `gateway/run_notifications.py`（更新完成/失败/超时、「♻️ 网关已上线」）、
  `gateway/run_busy.py`（暂停/恢复）、`hermes_cli/setup_platforms.py` 与 `hermes_cli/gateway.py`（Home Channel 说明）；
  同时把提示里引导的命令名 `hermes update` 改成 `coco update`（对外只暴露 coco 命令）。
- **为什么**：这些提示会直接发到经纪人的飞书会话里（尤其 `coco update` 跑完那三条），
  出现别的产品名会让人以为装错了东西。
- **没动的同类文本**：`plugins/platforms/{mattermost,slack,matrix,discord}/adapter.py` 里同款
  「📬 Home Channel: where Hermes delivers …」—— 这些平台 Coco 不用、向导也不显示，暂不改；
  要改的话照本条的写法，在 `scripts/check_coco_hooks.py` 里加同款自检条目。
- **上游变了怎么办**：这些是硬编码字符串（不在 `locales/*.yaml` 里），官方改文案后要按新文案重挂，
  自检 23–26 负责把它们报出来。

### 14 tests/ 下三处官方测试断言 —— 用户可见命令的品牌口径
- **改什么**（只改断言里那条命令串，用例名/前置条件/断言结构不动）：
  - `tests/hermes_cli/test_ensure_gateway_service.py`：`assert "hermes gateway" in out` → `assert "coco gateway" in out`；
    两处 `assert "hermes gateway install" in out` → `assert "coco gateway install" in out`。
  - `tests/hermes_cli/test_gateway_no_new_standalone_profile.py`：`hermes gateway install` / `hermes gateway migrate --multiplex` /
    `hermes -p {profile} gateway install --force` → 对应的 `coco …` 口径。
  - `tests/hermes_cli/test_update_yes_flag.py`：`assert "hermes config migrate" in out` → `assert "coco config migrate" in out`。
- **为什么**：这几条断言盯的是「装服务失败 / 命名 profile 被拒 / 更新时配置迁移跳过」时提示用户敲哪条命令。
  与第 11 处的改动配套（`hermes_cli/gateway.py` 打印 `coco gateway` / `coco gateway install`、
  `hermes_cli/update_cmd_config.py` 打印 `coco config migrate`），官方文案改了口径、官方断言没跟着改 → 测试红而功能正常。
- **不动的**：`import hermes_cli.gateway` 这类模块名、`tmp_path / "hermes"` 这类目录名、以及 docstring 里对官方命令的引用
  （它们不是品牌断言，改了反而会误导）。
- **上游变了怎么办**：同步上游会把 `tests/` 覆盖回官方断言。跑 `python3 scripts/check_coco_hooks.py`
  （第 32/33/34 项，负向匹配守着 `assert "hermes gateway…` 不许回来）能立刻发现，按本条把断言改回 coco 口径即可。

### 15 tests/hermes_cli/test_gateway_restart_loop.py —— 两层守卫的返回形状不同
- **背景**：终端工具里有两层守卫，先 Coco 的更新守卫（`tools/real_estate_update_guard.py`，自有功能），再官方的网关生命周期守卫。
- **改什么**（只动这一组用例，生产代码不改）：
  - 官方那条参数化用例拆成两组列表：`_COCO_GUARDED_CMDS`（`systemctl restart hermes-gateway`、
    `systemctl --user restart hermes-gateway`、`systemctl stop hermes-gateway.service`、
    `hermes gateway restart`、`hermes gateway uninstall`）与 `_GATEWAY_GUARDED_CMDS`（`launchctl …`、`pkill -f hermes.*gateway`）。
  - 新增 `test_blocks_update_commands_via_coco_guard`：断言更新类命令被拦且话术是我们那句（含「服务器」「coco update」）。
  - 原 `test_force_true_cannot_bypass_block` 改名为 `test_coco_guard_ignores_force_flag`（Coco 守卫不看 force，强制也拦）。
  - `test_cli_agent_session_not_blocked_by_inherited_env` 的命令换成 `pkill -f hermes.*gateway`，保留它原本要验的「环境变量误触发」。
- **为什么**：Coco 的更新守卫**不看会话、不看 force**，一律拦下更新类命令，返回自有形状
  `{"error": <中文话术>, "status": …}`；而官方断言要的是 `exit_code=1` + 英文 `Blocked`。两者形状不同，
  混在一条参数化用例里必然假红（`KeyError: 'exit_code'`）。
- **上游变了怎么办**：同步会把该测试文件覆盖回官方版本。跑 `python3 scripts/check_coco_hooks.py`
  （第 35/36 项守着两组拆分与那条 `pkill` 命令）能立刻发现，按本条重新拆分即可。

### 16 gateway/run_busy.py —— 清空对话类命令的确认框兜底
- **背景**：网关判定「要不要先弹确认框」读的是 `approvals.destructive_slash_confirm`，
  官方的兜底是 `True`——**配置里没这个键、或者读配置报错时，也会弹框**。
- **改什么**（只改 `_maybe_confirm_destructive_slash` 里的两处兜底）：
  - `confirm_required` 初值 `True` → `False`；
  - `approvals.get("destructive_slash_confirm", True)` → `False`。
  - 新语义：**缺键、读配置失败都按「不弹框」处理**；只有把该键显式写成 `true` 时才弹。
- **为什么**：经纪人不会输入 `/always`，官方兜底一旦生效就会把「开新会话」卡住。
  配套另有两处（不属于本项）：`hermes_cli/config_defaults.py` 的新装默认值（自检第 05 项守着）、
  `scripts/coco_config_align.py` 的更新时对齐（会把被显式写成 `true` 的拉回 `false`）。
  本次之前的问题正是「代码默认值与更新时对齐都是关的，但网关这一层兜底仍是官方的开」——
  配置里没有该键的实例仍会被弹框拦住。
- **上游变了怎么办**：同步会把该文件覆盖回官方口径。跑 `python3 scripts/check_coco_hooks.py`
  （第 37 项守着这两处兜底）能立刻发现，按本条改回 `False` 即可。

### 17 tests/hermes_cli/test_destructive_slash_confirm_gate.py —— 官方断言随默认值口径
- **背景**：官方这两条用例断言 `DEFAULT_CONFIG` 里的确认框默认值是 `True`（新装要弹框）。
- **改什么**：`test_default_is_true` 改名 `test_default_is_false` 并断言 `False`；
  `test_existing_user_config_without_key_gets_default` 的期望值同样改成 `False`
  （它验的是「用户配置缺键时由 `DEFAULT_CONFIG` 补齐」，语义不变，只是补的值按 Coco 口径）。
- **为什么**：第 05 处把默认值改成 `False` 之后，这两条从 2026-09-21 起一直是红的。
  它们断言的正是那个被我们改掉的默认值，属于「官方断言随口径调整」的老做法（同第 14 处）。
- **上游变了怎么办**：同步会覆盖回官方版本。跑 `python3 scripts/check_coco_hooks.py`
  （第 41 项守着这两条断言）能立刻发现，按本条改回 Coco 口径即可。

### 18 飞书默认不显示工具进展行（三处）
- **改什么**：
  - `hermes_cli/config_defaults.py` 的 `display.platforms` 增加 `"feishu": {"tool_progress": "off"}`；
  - `gateway/display_config.py` 的 `_PLATFORM_DEFAULTS["feishu"]` 改成
    `{**_TIER_MEDIUM, "tool_progress": "off"}`（**不要直接改 `_TIER_MEDIUM`**，
    它被 mattermost / matrix / buzz / whatsapp 共用）；
  - `scripts/coco_config_align.py` 的 `STANDARD` 加 `display.platforms.feishu.tool_progress = "off"`、
    `OFFICIAL_DEFAULTS` 加 `("new",)`（官方飞书档默认）、`LABELS` 加人话名，
    这样已装实例跑更新时会被对齐回关。
- **为什么**：飞书是经纪人/客户侧的收件箱，官方档默认 `new`（每调一个工具发一条进展），
  实际就是刷屏。关掉只影响「工具进展行」，不影响忙碌提示、中间状态话术与最终回复
  （那些是 `busy_ack_detail` / `interim_assistant_messages` 等另外的键）。
- **要开回来的实例**：`coco config set display.platforms.feishu.tool_progress new`（或 `all`），
  显式值优先于这三处默认。
- **上游变了怎么办**：跑 `python3 scripts/check_coco_hooks.py`（第 38/39/40 项分别守这三处）。

### 19 设置向导的默认值与示例配置种子值

- **改什么**：
  - `hermes_cli/setup.py` 的 `_apply_default_agent_settings()`：**删掉官方那句
    `config["compression"]["threshold"] = 0.50`** —— 它紧跟在我们的 `= 0.8` 后面，
    会把刚写好的值覆盖回 `0.5`；终端提示 `_info("  Max iterations: 150", … "Compression
    threshold: 0.50")` 与实际写入值不符，改成 `500` / `0.8`。
  - `cli-config.yaml.example`：`threshold` `0.50` → `0.8`、`protect_last_n` `20` → `40`
    （`max_turns` 已是 `500`）。
- **为什么**：这个函数被 `hermes setup` 与快速向导两条路径调用（`setup.py` 第 624 行、
  `setup_quick.py` 第 114 行），谁重跑一次向导，实例的压缩阈值就回到 0.5（压缩更早触发、
  每次多烧 token），终端还会告诉用户「150 轮 / 阈值 0.50」。
  示例配置是 `install.sh` 复制给新实例的第一份 `config.yaml`：种子值退回官方时，
  新装实例在对齐脚本跑之前就是错的。
- **不动的**：`compression.hygiene_hard_message_limit` 不写死在向导里 —— 官方默认就是
  `5000`，运行时自然生效；`coco_config_align.py` 的 `STANDARD` 已显式对齐它。
- **上游变了怎么办**：跑 `python3 scripts/check_coco_hooks.py`（**第 14 项**守向导、
  **第 42 项**守示例配置，其中写回 `0.50` 的那行是反向检查：它一回来就报 FAIL）。
  同步清单里这两个文件都登记在 `scripts/sync_upstream.sh` 的 `HOOK_FILES`。

### 20 压缩兜底值与出厂默认对齐（三处）

- **改什么**：把「配置里读不到这个键时的兜底值」从官方口径改成 Coco 口径：
  - `agent/agent_init.py::_compression_threshold()`：`cfg.get("threshold", 0.50)` → `0.8`
  - `agent/agent_init.py::_parse_compression_config()`：`cfg.get("protect_last_n", 20)` → `40`
  - `tui_gateway/session_compression.py::_COMPRESSION_INT_KEYS`：`("protect_last_n", 20, 0)` → `(…, 40, 0)`
  - `hermes_cli/context_switch_guard.py`：`getattr(cc, "protect_last_n", 20)` → `…, 40)`
- **为什么**：第 05 处把出厂默认值改成了 0.8 / 40，但这几处兜底没跟上。它们生效的场景是
  「实例的 config.yaml 里没写这个键」或「配置读取失败」——这时压缩阈值会悄悄退回 0.50、
  保留条数退回 20，比 Coco 标准更早压缩、聊天上下文被更早收走。官方用例
  `tests/agent/test_compression_config_defaults.py` 断言「兜底值必须等于出厂默认值」，因此在
  我们没改齐之前它一直是红的（2026-09-27 扩面扫描才暴露）。
- **上游变了怎么办**：官方只要还是 0.50 / 20 这两个兜底，同步后就要按上面的位置重改（三处文件的
  函数/常量名基本稳定，改的是数字）。改完 `scripts/check_coco_hooks.py` 的 **43 / 44 / 45** 三项会
  自动守住（正向守我们的值、反向守官方值不在）；回归用例
  `tests/real_estate/test_compression_defaults_aligned.py` 会断言「兜底 == 出厂默认」。
- **注意**：`agent/agent_init.py` 同时还有第 04 处的改动（启动建房产表），同步时两处一起重打。

### 21 官方测试断言按 Coco 口径（品牌文案 + 系统提示词）

- **背景**：这一批和第 14 处同类 —— 功能是对的、提示词也是 Coco 口径，红的是官方断言里写死的
  Hermes 字样；第 21 处的系统提示词那条则是「本仓比官方多注入了段落」导致的逐字比对必然不符。
  （2026-09-27 扩面跑 `tests/gateway` + `tests/agent` 时一次性暴露 10 条。）
- **改什么**（只改期望值，用例名、前置条件、断言结构都不动）：
  - `tests/gateway/test_update_command.py`：两处 `assert "Hermes update finished" in …` → `Coco update finished`。
  - `tests/gateway/test_restart_notification.py`：两处上线提示 `…Hermes is back and ready.` → `…Coco is back and ready.`。
  - `tests/gateway/test_restart_notice_replay.py`、`test_planned_restart_notice_multiplex.py`：
    模块常量 `ONLINE_NOTICE` 同上改 Coco。
  - `tests/gateway/test_unauthorized_sender_notices.py`：`` `hermes -p work pairing approve …` `` → `` `coco -p work …` ``。
  - `tests/agent/test_system_prompt.py::test_coding_prompt_orders_shared_context_before_workspace`：
    把 `expected = "\n\n".join((…))` + `assert prompt == expected` 换成 `ordered_markers` 元组 +
    逐段 `prompt.index()` 的相对顺序断言，并把静态段断言改为 `prompt.startswith(_cached_system_prompt_static)`
    （静态段是整段提示词的前缀，缓存不变这条不变量仍然被守住）。
- **为什么不能靠「接受常红」**：这批噪声会淹掉真问题 —— 本轮的「删 evals 漏了两个脚本」「压缩兜底值
  没改齐」就是这么被发现的，44 条红里当时只有 9 条是这两类真问题。
- **上游变了怎么办**：同步上游会把 `tests/` 覆盖回官方断言。跑 `python3 scripts/check_coco_hooks.py`
  的第 **46–51** 项（正向守 Coco 口径、反向守 Hermes 字样不许回来）能立刻发现，按本条改回来即可。
- **配套的测试夹具口径**：`tests/agent/test_413_compression.py` 与 `test_in_place_preflight_rewind.py`
  的预期按官方出厂压缩口径（0.50 / 20）书写，本轮给它们加了 `autouse` 夹具显式钉住官方值
  （`monkeypatch.setitem(DEFAULT_CONFIG["compression"], …)`），这样它们测的是预压缩/原地回卷行为本身，
  不会再被第 05 处的出厂默认值连带变红。

## evals/ 最小必要集（保留 16 个文件，不是官方补丁）

- 「用户装机用不到」而删掉的官方目录里，`evals/` 有个例外：`tests/gateway`（3 个）与 `tests/agent`（1 个）等
  共 **10 个测试模块 `from evals... import ...`**，整个删掉会让这两个区在**收集阶段就中断**
  （7278 + 7938 条一条都跑不了；2026-09-27 扩面扫描时踩到）。
- 因此 `evals/` 只保留最小必要集 **16 个文件（约 141KB）**：`heartbeat_idle_wire.py`、
  `providers/reasoning_shapes.py`、`compaction/{fixtures,jev_arm}.py`、`completion_backlog_probe.py`、
  `mcp_device_flow.py`、`codebase_navigability/__init__.py`、`api_delegation_http_probe.py`、
  `postmortem/forensics/{common,logcalls}.py`，以及 `evals/`、`evals/postmortem/`、
  `evals/postmortem/forensics/` 三个空的 `__init__.py`（以上 13 个是 `import` 依赖）；
  再加 3 个**被测试按脚本路径调用**的文件：`cron_timeout_fork_race.py`、
  `gateway_failure_ownership/probe.py`、`gateway_failure_ownership/foreign_writer.py`。
- **删目录的验收教训（2026-09-27 一次扫描、一次补漏都是这么踩的）**：恢复时不能只扫 `import`。
  ① 测试还会用 `Path(...) / "evals" / "x.py"` 这种**拼接写法**把脚本当子进程跑起来（`can't open file`）；
  ② 那个脚本自己还会拉起**同目录的兄弟模块**（`probe.py` → `foreign_writer.py`），这时连报错都指不出来
  （表现为探针跑起来了但观测不达 17/20）。可靠顺序是：按引用恢复文件 → **真跑一遍受影响的测试文件**
  → 靠自检 **A36–A40** 守住（三个显式文件 + 引用扫描 + 「`evals/gateway_failure_ownership` 与删除前一致」）。
- 同步时 `evals` 仍留在 `COCO_DONT_SYNC` 里（官方那 215 个文件不再带回来）：自检 **A33** 守最小集在位、
  **A35** 守「官方 evals/ 整体没被带回来」。

### 22 连发消息等提示的中文口径（经纪人可见）
- **改什么**：把这四类官方英文提示换成短中文 ——
  `gateway/run_busy.py`（连发消息状态行 6 种 + 排队降级尾巴 + 状态细节 + 更新/重启期间提示）、
  `gateway/run_inbound.py`（优先路径的同类更新/重启提示 2 处）、
  `gateway/slash_commands.py`（`/busy` 的名字与回复，另附 `_BUSY_MODE_BEHAVIOR_ZH` 中文口径表）、
  `agent/onboarding.py`（网关版一次性提示 4 条 + 工具耗时提示 1 条）。
- **为什么**：经纪人在 Coco 干活时连发消息，会收到 `↪ Redirected current run. I'll adjust using your correction.`
  这类英文；状态细节还带 `2 min elapsed, running: terminal, iteration 3/500`（英文工具名）。
  Coco 的界面语言虽然是中文（`display.language: zh`，见 10），但这些串是**写死在代码里的**，
  不走语言包，所以显示语言调不掉它们。经纪人看不懂英文，也就不知道"我刚发的消息到底被怎么处理了"。
- **怎么改的**：直接写中文短句（不新建语言包键）—— 这样与 `display.language` 解耦，
  某台实例语言被写回英文时也不会退回去。细节只保留「已跑 N 分钟 · 第 N 步」，不再打印内部英文工具名；
  `/busy` 的模式集合仍以官方 `_BUSY_MODE_BEHAVIOR` 为准，只替换展示文案。
- **连带改了 4 个官方单测的断言文本**（原文断言英文串）：`tests/gateway/test_busy_session_ack.py`、
  `test_subagent_protection.py`、`test_busy_command.py`、`test_multiplex_busy_input_mode.py`。
- **上游变了怎么办**：官方若把这些提示也 i18n 了（换成语言包键），按官方新键重挂并把中文写进语言包；
  只要它还是硬编码，就按 `patches/22-busy-notice-cn.patch` 的语义在新版里重新替换。

### 23 经纪人可见的英文提示改中文（选项提示行 / 更新确认 / 中继"其他"行）

- **改什么**：三处硬编码英文 → 中文：
  - `gateway/platforms/base.py` 的澄清选项提示行：`Reply with the number, the option text, or your own answer.`
    → `回数字、选项原文，或直接说你的答案。`（多选版一并改）；
  - `gateway/run_notifications.py` 的更新确认提示：`☤ **Update needs your input:** … Reply \`/approve\` (yes) or …`
    → `☤ **更新需要你确认：** … 回 \`/approve\` 表示同意、\`/deny\` 表示不同意，也可以直接说你的答案。`；
  - `gateway/relay/adapter.py` 的"其他"选项行：`✏️ Other (type your answer)` → `✏️ 其他（我来补充）`。
  - **同族（已单独处理）**：`tools/clarify_tool.py` 不再给选项自动标英文 "(Recommended)"，标签也改中文
    （见自检 57；那条是"别替经纪人做选择"的产品决定，不只是语言问题）。
- **为什么**：这些串**写死在代码里、不走语言包**，所以 `display.language: zh` 调不掉它们；
  实测经纪人问政策时，选项列表最后一句就是英文，他当场问"这行字怎么还是英文"。
- **上游变了怎么办**：同步会把这三处覆盖回英文。跑 `python3 scripts/check_coco_hooks.py`
  （**58 / 59 / 60** 三项正向守中文、反向守英文不许回来）会立刻 FAIL；按本条改回中文即可。
  **更省事**：这些中文已登记在自动重打表里，同步后直接 `python3 scripts/coco_cn_strings.py --apply`
  即可自动改回（锚点失效会报 `ANCHOR`，那时才需要人工按本条重做并更新表）。

### 25 `coco doctor` 输出中文化 + 两个假问题的修正

- **改了什么**：体检横幅（`🩺 Coco 部署体检`）、各章节标题（安全公告 / Python 环境 / 依赖包 /
  目录结构 / 可用工具 / 技能源 / 记忆存储 …）、汇总行（`发现 N 个需要处理的问题：`、
  `全部检查通过！🎉`、`已自动修好 N 个问题。`、`提示：能自动修的问题，跑「coco doctor --fix」会帮你修。`）、
  以及提示里引导的命令名（`hermes setup/update/tools/config path/doctor --fix` → `coco …`）。
- **顺带修掉两个假问题**：① 体检总报"缺 `~/.local/bin/hermes` 软链"—— 我们按设计移除了 hermes 命令，
  照官方查 hermes 会**每次体检都报一个永远修不好的问题**，`--fix` 还会把 hermes 命令装回来；
  现在 Coco 安装只查 `coco` 链接。② 汇总里的"跑 `hermes setup` 配密钥"改成 `coco setup`。
- **逐项明细行（同批续做）**：`doctor_platform.py` / `doctor_tools.py` 里体检输出的每一行
  （Python 环境 / 依赖包 / 目录结构 / 外部工具 / 库日志模式 / 命令入口 / 工具可用性等）也改成了中文，
  含 `(system dependency not met)`→`（系统依赖没装齐）`、`(optional, not installed)`→`（可选，未安装）`、
  `Could not warm npx cache …`、`Lightpanda selected but binary not found` 等。
- **上游变了怎么办**：同步会覆盖回英文，跑 `python3 scripts/coco_cn_strings.py --apply` 自动改回；
  锚点失效会报 `ANCHOR`（需人工按本条重做并更新表）。同批被改动文案带出的官方用例断言更新在
  `tests/hermes_cli/test_doctor*.py`（断言本来就是英文原文，一并进表）。
- **配置检查 / 目录与库检查两块（同批续做）**：`doctor_config.py`（配置文件、过时配置项、
  托管范围、xAI 下线、插件导入路径）、`doctor_state.py`（目录结构、SOUL.md、记忆文件、state.db 统计
  与 WAL 检查、技能源、记忆服务、配置档），以及 `doctor.py` 的登录状态行、`auth_codex.py` /
  `auth_xai.py` 的凭据提示、`doctor_connectivity.py` / `doctor_live.py` 的「未配置」。
  至此 `coco doctor` 输出除包名/工具名（Python、OpenAI SDK、git、工具 id 等，翻了反而看不懂）外全中文。
- **还没翻的**：macOS / Windows / Termux 专属分支的个别细节（Linux 上不出现）。

### 24 `coco` 命令文案里的"推荐/不建议"改中文

- **改什么**（只改文案，不动逻辑）：8 个官方文件里的英文"推荐/不建议"字样 → 中文：
  - `hermes_cli/uninstall.py`：卸载菜单 `(Recommended - you can reinstall later with your settings intact)`
    → `(推荐：只删程序，配置和会话都留着，之后重装还在)`（同屏的 `(Warning: …)` 一并中文化）；
  - `hermes_cli/gateway.py` 三处：裸机 `not recommended`、`Switch to a per-user service (recommended for personal use)`、
    `--force … (not recommended`；
  - `hermes_cli/subcommands/gateway.py` 两处 help：`(recommended for WSL, Docker, Termux)`、
    `(not recommended: two pollers on one bot token, port conflicts)`；
  - `hermes_cli/main_platform_setup.py`：`1. Separate bot number (recommended)`；
  - `hermes_cli/cli_commands_mixin.py`：`Restart recommended for gateway/dashboard processes …`；
  - `hermes_cli/model_setup_flows_azure.py`：`Recommended by Microsoft. …`；
  - `hermes_cli/secrets_cli.py` / `hermes_cli/onepassword_secrets_cli.py`：`(not recommended)`。
- **为什么**：老板要求"涉及推荐的地方都用中文"（起因是飞书里那句英文 "(Recommended)"）。
  这些是 `coco` 命令输出/help 里的英文，用户敲命令时看得见。
- **没动的**：同文件里其它英文句子（如 `Run 'coco update' to install.`）不属于"推荐"字样，
  属更大的话题「`coco` 命令文案整体中文化」——本轮只处理"推荐/不建议"，其余待老板单独拍板。
- **上游变了怎么办**：同步会把这几处覆盖回英文。跑 `python3 scripts/check_coco_hooks.py`
  （**61–68** 八项守着）会立刻 FAIL；按本条改回中文即可。

## 使用方法（同步时）

```bash
# 1. 交换血前先看这份清单，确认要重新应用哪几处
cat patches/README.md

# 2. 同步（脚本会替换官方层文件、保留 Coco 自有文件，并列出需要人工处理的点）
bash scripts/sync_upstream.sh <官方版本tag>

# 3. 逐处重新应用上面的改动
#    （按语义在新版代码里实现，不要机械 apply patch）

# 4. 自检：确认挂钩点与自建文件都在（清单以脚本内 CONTENT_CHECKS/PATH_CHECKS 为准）
python3 scripts/check_coco_hooks.py

# 5. 三层验收
python3 scripts/healthcheck.py
python3 scripts/smoke_test_real_estate.py
# 单测 + 飞书实测见 docs/UPSTREAM_SYNC.md
```

## 已知局限

- 补丁基于 2026-08-09 基准生成；我们的代码导入点与任何单一官方快照都不完全重合，
  因此 patch 中可能夹带少量"官方版本漂移"的内容。**以本文件的语义说明为准。**
- `07-gateway-run-greeting.patch` 体积较大（官方该文件改动频繁），仅作参考。
  实际同步时按语义在新版里重新挂钩。

### 26 `coco setup` 向导文案中文化（骨架 + 收尾屏）

- **改了什么**：`hermes_cli/setup.py`（向导横幅 / 章节名 / 各节提问与提示 / 取消与返回提示 / 输入校验 /
  非交互提示）与 `hermes_cli/setup_summary.py`（"配置完成"那屏：工具可用情况、文件位置、命令清单、结束框），
  共 93 处；命令一律 `coco`（`coco setup|model|config edit|config set`），产品名一律 `Coco`。
- **框线对齐**：中文是双宽字符，`_print_banner` 的框改由 `_disp_width()` / `_boxed()` 按显示列宽补空格
  —— 中英混排不做这一步会把框撑歪。
- **收尾屏的"说明"列**：由各行自带（环境变量名带「缺 」、操作提示写「跑「coco …」」），模板只加括号，
  否则会出现「缺 跑「coco setup」配置」这种病句。
- **连带改动**：官方用例断言（`tests/hermes_cli/test_setup_agent_settings.py`、`test_setup_noninteractive.py`、
  `test_setup_reset_backup.py`、`test_setup_summary_provider_warning.py`）与自有用例
  `tests/real_estate/test_setup_wizard_defaults.py`，以及自检第 **14** 项守向导默认值那两条提示正则。
- **上游变了怎么办**：同步会覆盖回英文，跑 `python3 scripts/coco_cn_strings.py --apply` 自动改回；
  报 `ANCHOR`（官方改写过这段）才需人工按本条重做并更新表。

### 27 网关配置向导文案中文化（骨架 + Telegram / BlueBubbles / Webhooks）

- **改了什么**：`hermes_cli/gateway_setup_wizard.py`（向导横幅、平台选择菜单、未授权用户四选项、
  allowlist 与主页频道询问、服务安装/启动/重启询问与失败提示、WSL/Termux/不支持平台的兜底说明）
  与 `hermes_cli/setup_platforms.py`（平台清单与收尾、Telegram / BlueBubbles / Webhooks 三条流程、
  Home Channel 缺失提醒、重启失败提示），共 120 处；命令一律 `coco`、产品名一律 `Coco`。
- **同处①的两个手法**：横幅复用 `setup.py` 的 `_boxed()` 按显示列宽补空格（中文双宽）；
  未授权处理的四个选项、去掉了句尾的内部配置键（`unauthorized_dm_behavior: decline`）。
- **连带改动**：官方用例断言（`tests/hermes_cli/test_setup_irc.py` 两处：收尾文案 + 平台选择提问的
  查找条件）与自有用例/自检（`tests/real_estate/test_coco_branding.py` 与自检第 **25** 项的品牌针）。
- **还没做（下一处）**：平台注册表里的说明与 help（Mattermost 等）、Weixin / QQ Bot / Signal 三条流程，
  以及平台清单每行括号里的状态词（`configured` / `not configured` / `partially configured`）——
  后者是**语义值**（三处判断逻辑在比它），建议在**显示层**翻译，不要动比较逻辑。
- **上游变了怎么办**：同步会覆盖回英文，跑 `python3 scripts/coco_cn_strings.py --apply` 自动改回。

### 28 网关向导的平台专属文案中文化（微信 / QQ / Signal + 平台注册表说明）

- **改了什么**：`hermes_cli/gateway_setup_wizard.py` 共 123 处 —— 微信流程（扫码登录说明、依赖缺失、
  私聊/群聊授权、白名单与主页频道）、QQ 机器人流程（扫码/手动、App ID 与 Secret、白名单、主页频道）、
  Signal 流程（signal-cli 安装与连通性、账号号码、白名单、群聊），以及 `_PLATFORMS` 注册表里
  **Mattermost / BlueBubbles / QQ Bot / Yuanbao** 四个平台的步骤说明、提问与帮助文本。
- **两处口径决定**：① Signal 的链接设备名 `signal-cli link -n "HermesAgent"` → `-n "Coco"`；
  ② 微信/QQ 的授权选项在更早一轮已是中文（`用私聊配对审批（推荐）` 等），**保持原样不再改**，
  只补了同组里漏掉的一句英文（`Disable direct messages`）。
- **品牌针同步**：Mattermost 的 help 由英文变成中文后，自检第 **26** 项与 `tests/real_estate/test_coco_branding.py`
  的针一并改成 `Coco 送定时任务结果与通知的频道 ID`（第 25 项同理）。
- **一个易踩的坑**：**别从工具输出里照抄带电话号码的示例串** —— 输出会把号段打码（`+15551234567`
  显示成 `+155****4567`），照抄必然配不上；改这类文案时从文件里取原文（`src.index('  Example: ')`）。
- **上游变了怎么办**：同步会覆盖回英文，跑 `python3 scripts/coco_cn_strings.py --apply` 自动改回。

### 29 `coco model` 文案中文化（模型主流程 + 切换结果 + 服务商配置）

- **改了什么**：5 个官方文件共 133 处 —— `model_setup_flows.py`（各服务商的模型选择、接口地址、
  免费额度与登录失败提示）、`model_setup_flows_common.py`（登录流程与取消）、`auth_model_picker.py`
  （模型选择菜单的「自定义模型名」「跳过（保持当前）」）、`cli_model_switch_mixin.py`（切换结果块
  与 `/model` 帮助）、`main_provider_setup.py`（服务商/自定义服务商/推理强度与 Claude Code 凭据）。
- **两处口径**：MoA 与 auth 两条命令改用 `coco cli moa configure` / `coco cli auth upgrade`
  （`coco` 无对应子命令，走逃生口）；服务商与模型名、环境变量名、`/model` 的参数、`tokens` 一律保留。
- **还没做（③b）**：冷门服务商流程（`model_setup_flows_custom.py` / `_azure.py` / `_bedrock.py`）
  与**密钥录入页的说明与提问**（`config_defaults.py` 的 `OPTIONAL_ENV_VARS`，`model`/`setup`/`tools` 三处共用）。
- **一个不属于本批的红灯**：`tests/hermes_cli/test_model_catalog.py::TestDefaultModelFromCache::…`
  读 `website/static/api/model-catalog.json`，而 `website/` 已在 `fa1f5d79`（删除装机用不到的官方目录）整体删除
  —— 属既有红灯，与文案无关，别再当新问题查。
- **上游变了怎么办**：同步会覆盖回英文，跑 `python3 scripts/coco_cn_strings.py --apply` 自动改回。
