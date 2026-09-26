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

## 改动清单（共 13 处官方文件 + 2 个自有文档）

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
- **改什么**：把默认身份常量等内容替换为 Coco 版本（"你是 Coco（可可），经纪人的
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
