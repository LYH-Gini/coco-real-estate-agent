# Coco 房产智能体

> Coco 是一个开源房产项目。它不是简单的聊天机器人，而是一套专为房产经纪人打造的"第二大脑"：自动管理客户、智能匹配房源、主动跟进提醒、沉淀经营数据，一行命令即可安装到自己的云服务器。

[![Coco AI](https://img.shields.io/github/v/release/LYH-Gini/coco-real-estate-agent?label=Coco%20AI)](https://github.com/LYH-Gini/coco-real-estate-agent/releases/latest)

> **当前版本：v0.21.5-3** · [Gitee](https://gitee.com/LYH-Gini/coco-real-estate-agent/releases) · [GitHub](https://github.com/LYH-Gini/coco-real-estate-agent/releases/tag/v0.21.5-3)

## 安全与免责声明

**项目定位与软件状态**

1. 本项目的定位是个人学习与技术交流，非商业产品。作者开源本项目仅为分享行业智能体的实现思路与代码，不提供任何形式的担保或技术支持。

2. 本项目未经过第三方安全审计，不应被视为经过安全加固的软件。Coco 会在你指定的服务器上读写数据库、调用工具、收发飞书消息；在用于真实客户与真实业务数据之前，请自行评估风险并完整测试。

3. 本项目仍在持续迭代，版本之间可能出现操作方式、输出文案或数据结构的变化。升级前请先备份数据，并查看对应版本的发布说明。

**使用者的责任**

1. **数据去向**：Coco 会把会话内容（包括你输入的客户信息、房源信息）发送给你自己配置的大模型服务商（如小米 MiMo、DeepSeek、Kimi）用于生成回复，数据是否留存、如何使用取决于该服务商的政策与条款。

2. **生产环境准备**：用于真实客户与业务数据前，请亲自完成全量功能测试（参考仓库 `docs/TESTING_FEISHU_FULL.md`），并确认自动备份任务真实生效。

3. **备份与备份文件保管**：请定期备份数据（项目提供 `backup_db.py`）；完整备份包内含数据库导出、图片归档与 `enc_key.txt`（密钥文件），其敏感程度等同明文客户数据，请与生产库同等保管，不要放在公开位置。因未备份、误删、重装系统、服务器故障、断电等造成的数据丢失，作者和版权持有人不承担责任。

4. **加密密钥保管**：`COCO_ENC_KEY` 是解密客户手机号、微信号的唯一凭证，一旦丢失或泄露将导致客户数据永久无法解密。请独立备份该密钥，并避免将其写入仓库、聊天或公开渠道。因密钥丢失造成的数据不可读，作者和版权持有人不承担责任。

5. **最小权限**：飞书应用只申请运行所需的权限与可用范围；服务器与数据库账号只保留给自己；不要把数据库、后台或调试端口暴露到公网。不需要 Coco 时，及时关闭定时任务与机器人可见范围。

6. **删除类操作的边界**：删除工具提供 `dry_run` 预演，并在存在关联记录时默认拒绝执行；但是否执行由对话内容决定，没有独立于对话的二次确认。执行清理、去重、批量删除前请先备份。

7. **合规使用**：客户与房东的姓名、联系方式、证件信息属于个人信息，其收集、使用与保存期限需符合当地法律法规（如个人信息保护法、房地产中介管理规定等），并取得必要授权。本项目不构成任何投资建议或法律建议。在适用法律允许的最大范围内，对于使用本项目造成的计算机损坏、数据丢失或泄露、文件丢失或其他损害，作者和版权持有人不承担责任。

**不提供保证，不承担责任**

1. 本项目按"现状"（AS-IS）提供，不承诺无缺陷、不保证特定功能完全满足你的需求。

2. AI 模型可能产生错误、幻觉或不准确的信息，使用时请以实际数据为准。贷款政策、税费等外部信息具有时效性，Coco 查询到的政策信息仅供参考，请以当地官方机构的最新公告为准。

3. 使用过程中出现的任何问题或损失，作者和版权持有人不承担由此产生的直接或间接责任；因使用本软件产生的一切后果由使用者自行承担。

## 核心功能

| 模块 | 说明 |
|------|------|
| 客户管理 | S/A/B/C 四级分类，自动计算跟进周期；客户画像管理（预算、户型、区域、装修偏好）；一键添加、查询、更新客户；客户生日管理；**客户建档自动去重**（按手机号/微信识别已存在客户，避免重复建档，可合并更新） |
| 智能匹配 | 多维度加权评分算法——价格 30%、户型 25%、面积 20%、区域 15%、装修 10%，自动推荐最合适房源；预算略差一点的房源也能拿到分数，不会被直接埋没；客户反馈过缺陷（采光差、临街吵等）的房源自动降权并在匹配理由中标注；客户指定区域时，区域相符的房源一律排在前面；新房源入库自动反匹配所有活跃客户 |
| 跟进提醒 | 定时提醒默认关闭，对 Coco 说一句「开启定时任务」即可开启：09:00 上班早报、10:00 与 17:00 逾期提醒、12:30 机会提醒（降价房源捞回 / 新上房源匹配）、20:30 收工小结、周一 08:30 周报；S 级客户 2 天内跟进 |
| 带看管理 | 预约带看、记录带看结果、客户反馈，带看后自动 1 小时回访提醒 |
| 成交管理 | 定金 → 签约 → 贷款 → 过户 → 交房五阶段状态机，自动推进提醒 |
| 数据报告 | 客户统计、房源统计、逾期跟进提醒、经营周报/月报、竞品对比、客户意向度评分 |
| 话术库 | 自定义话术保存复用，覆盖开场、异议处理、逼定、跟进场景 |
| 发布助手 | 一键生成朋友圈、贝壳、安居客、58 同城房源发布文案 |
| 数据加密 | 客户手机号、微信号等敏感字段加密存储，密钥自动生成 |
| 房源去重 | 一键扫描并清理重复房源（按标题+面积+价格），保留最早录入的一条，有关联带看/成交的自动跳过；录入时自动鉴重（按小区名称+房号+面积判重；同一套房可卖可租，出租与出售分开算；重复即拦下、不落库，需确认是否保留） |
| 业主/租客管理 | 录入房源时可一步登记业主联系方式（姓名/电话/微信，电话加密存储，同一业主自动复用不重复）并自动关联房源；此后即可按房源反查业主联系方式（姓名/电话（完整号码）/微信，一次最多 3 套）；出租房源可填「租客要求」（不吸烟/办居住证/学生优先），正式入库并在匹配租客时用于筛选（冲突即排除） |
| 房源详情一次给全 | 问某套房源（给编号或标题都行），一次返回全部字段 + 自动计算的单价（总价÷面积，保留两位小数）+ 业主姓名与联系方式 + 图片、调价记录；标题对不上会列出最接近的候选请你确认，不会拿别的房源顶替 |
| 需求变更追踪 | 客户改预算/换区域自动留痕，预算大幅下调 30% 以上时主动预警"客户可能在看别处的房子" |
| 流失预警 | S 级客户超过 5 天无互动自动降级并提醒，客户快"凉"了 Coco 比你先知道 |
| 营销海报 | 一键生成朋友圈海报图（标题+价格+面积+二维码），文案配图一次搞定 |
| 短视频脚本 | 30 秒卖房口播脚本（抖音/视频号两种风格），钩子+亮点+价格+行动号召直接照读 |
| 渠道分析 | 安居客/贝壳/抖音/转介绍等来源自动统计来客数与成交率，广告费花在哪心里有数 |

## 技术架构

- **框架层**：工具注册、身份注入、系统提示词均按房产场景重写，智能体主动识别自己是"Coco，你的客户和房源管家"
- **存储层**：PostgreSQL 数据库，客户、房源、带看、成交全量业务数据
- **接入层**：飞书，指令交互与消息推送均通过飞书完成；也可扫码接入微信一对一会话
- **模型层**：可接入小米 MiMo、DeepSeek、Kimi 等大模型，安装向导中按需选择
- **部署层**：systemd 服务托管，开机自启，进程崩溃自动拉起

## 安装前准备

从零开始部署 Coco，先准备好服务器与账号，全程约 30 分钟。

### 一、操作系统要求

推荐 Ubuntu 24.04 / 26.04 LTS。

### 二、服务器配置要求

- **CPU**：最低 2 核心，推荐 4 核心及以上
- **内存**：最低 2GB，推荐 4GB 及以上
- **磁盘空间**：至少 10GB 可用空间
- **网络**：需具备公网出站能力（安装依赖、拉取代码、调用模型 API 均需联网）

### 三、前置准备

1. 服务器已开通 root 或 sudo 权限，可正常联网
2. AI 模型 API Key：Coco 支持小米 MiMo、DeepSeek、Kimi 等大模型，请按所选模型提前开通并获取 API Key（如使用 DeepSeek，可在 [DeepSeek 开放平台](https://platform.deepseek.com/usage) 购买），配置向导（coco model）时需要填写
3. 飞书智能体：提前在[飞书开放平台](https://open.feishu.cn/?lang=zh-CN)创建账号。

### 四、服务器购买渠道（可选参考）

Coco 部署在 Linux 服务器上即可，轻量应用服务器或云服务器均可，地域建议选择靠近您的区域：

- **京东云 —— 轻量云主机云主机**：2 核 CPU、2 GB 内存、40 GB 系统盘、3M 峰值带宽，68 元/年起（新用户首单特惠），[购买链接](https://www.jdcloud.com/cn/pages/cloudpromotion)
- **阿里云 —— 轻量应用服务器**：2 核 CPU、2 GB 内存、40 GB 系统盘、最高200Mbps 峰值公网带宽，69 元/年起（新用户首单特惠），[购买链接](https://www.aliyun.com/benefit/scene/swas)
- **腾讯云 —— 轻量应用服务器**：2 核 CPU、2 GB 内存、40 GB 系统盘、3M 峰值带宽，68 元/年起（新用户首单特惠），[购买链接](https://cloud.tencent.com/act/pro/featured-202607?from=11102&page=warmup-202606&s_source=https%3A%2F%2Fcloud.tencent.com%2Fact%2Fpro%2FFeatured#MS)

> 价格为发布时参考价，实际以官网为准。

## 一键安装与配置

### 第一步：一键安装

SSH 重新连接后，按顺序执行：

**前置条件**：只需一台 Linux 服务器（Ubuntu 系统，带 sudo 权限）并能联网，其余依赖由安装脚本自动处理：

- PostgreSQL（业务数据存储）
- Python 3.11 ~ 3.14（脚本用系统 Python 创建虚拟环境；版本不合适时会自动准备兼容版本）
- Node.js（浏览器工具需要，脚本按 26 → 24 → 22 取最新可用版本）
- ripgrep（快速文件搜索）
- ffmpeg（语音消息的音频格式转换）
- 海报字体与渲染器（约 140MB）

> 你无需手动安装 Python、Node.js、ripgrep、ffmpeg 或 PostgreSQL；安装脚本会检测缺失的依赖并自动安装。只需确认服务器有 sudo 权限、并能正常联网。

**执行一键安装**（按服务器所在位置选一条）：

**国内服务器（Gitee 源）：**
```bash
curl -fsSL https://gitee.com/LYH-Gini/coco-real-estate-agent/raw/master/install.sh -o install.sh && bash install.sh
```

**海外服务器（GitHub 源）：**
```bash
curl -fsSL https://raw.githubusercontent.com/LYH-Gini/coco-real-estate-agent/master/install.sh -o install.sh && bash install.sh
```

这个脚本会自动完成所有安装步骤（检测系统环境、安装依赖、克隆代码、创建数据库、注册服务并启动）。

### 第二步：安装后的配置

安装脚本执行成功后，你需要完成以下配置才能使用 Coco。

**1. 刷新环境变量**：

```bash
source ~/.bashrc
```

**2. 验证安装**：

```bash
coco version
```

如果能看到版本号（例如 Coco v0.21.5-3），就说明核心程序安装成功了。

**3. 配置模型**。你需要一个 API Key（DeepSeek API Key 购买：[https://platform.deepseek.com/usage](https://platform.deepseek.com/usage)）：

```bash
coco model
```

**4. 配置飞书**（飞书开放平台：[https://open.feishu.cn/?lang=zh-CN](https://open.feishu.cn/?lang=zh-CN)）：

```bash
coco setup
```

**5. 重启服务**：

```bash
coco restart
```

### 第三步：测试智能体

**1. 打开飞书 App**，搜索你的智能体名称

**2. 发送一条消息**（如"你好"）

**3. 智能体会回复配对码**，在终端执行批准：`coco pairing approve feishu <配对码>`

**4. 批准后再发消息**，智能体应该正常回复

## 常用命令

### 更新

使用单条命令更新至最新版本：

```bash
coco update
```

> 更新会自动备份，客户数据全程保留，不会删除你的加密密钥。

### 查看版本

**查看版本：**
```bash
coco version
```
输出形如：Coco v0.21.5-3。

**查看全部可用命令：**
```bash
coco help
```
列出 version / check / backup / uninstall / help。

注意：想单独查底层框架版本可用 `coco cli --version`。

### 服务管理

> 定时任务如需在重装/重启后自动开启（无需每天手动开），可在服务器的 `.env.db` 里加一行 `COCO_ENABLE_CRON=1` 再重启服务。

**启动服务：**
```bash
coco start
```

**停止服务：**
```bash
coco stop
```

**重启服务：**
```bash
coco restart
```

**查看状态：**
```bash
coco status
```

**查看日志（最近 50 行）：**
```bash
coco logs
```

### 终端输出

**关闭终端输出：**
```bash
coco config set display.platforms.feishu.tool_progress off
```

**开启终端输出：**
```bash
coco config set display.platforms.feishu.tool_progress new
```

### 数据库备份

安装时已自动设置每日凌晨 2 点备份，备份文件在 `~/backups/real_estate/`，保留 30 天。加密密钥同时自动备份到 `~/backups/real_estate/enc_key.txt`。

> 重要：请把 `enc_key.txt` 密钥文件保存到安全的地方（电脑/U盘/网盘）。密钥丢失将导致客户数据永久无法解密。首次使用智能体时 Coco 也会提醒您备份。

**手动备份：**
```bash
coco backup
```

**查看备份列表：**
```bash
coco backups
```

**查看备份文件**（`.dump` 数据库 / `.tar.gz` 图片 / `enc_key.txt` 密钥）：
```bash
ls -la ~/backups/real_estate/
```

**在服务器上打包：**
```bash
( cd ~/backups/real_estate && tar czf ~/coco_backup_$(date +%Y%m%d).tar.gz ./*.dump ./*.tar.gz ./enc_key.txt )
```

**在你自己的电脑上下载：**
```bash
scp <用户名>@<服务器IP>:~/coco_backup_*.tar.gz ~/Desktop/
```

### 服务器迁移

迁移到新服务器时，一条命令完成数据库 + 图片 + 加密密钥恢复：

> **完整操作手册见 [docs/BACKUP_MIGRATION.md](docs/BACKUP_MIGRATION.md)**（含重装系统、换服务器、单独恢复、FAQ 速查卡）

**旧服务器打包（含数据库/图片/加密密钥）：**
```bash
coco backup --migration /root/coco_migration.tar.gz
```

**拷贝到新服务器后一键恢复：**
```bash
coco restore --migration /root/coco_migration.tar.gz
```

**重启服务（发"你好"即完成迁移）：**
```bash
coco restart
```

> 顺序说明：自动恢复数据库 → 图片 → 加密密钥（enc_key.txt 合并进 .env.db），任一步失败即中止并提示。密钥必须先于服务启动恢复，否则旧数据无法解密。

### 恢复

**从某个数据库备份恢复**（备份文件名用上面的 `ls` 查看）：
```bash
coco restore --file real_estate_20260101_020000.dump
```

**从整机迁移包恢复**（数据库 + 图片 + 加密密钥，顺序为数据库 → 图片 → 密钥）：
```bash
coco restore --migration /root/coco_migration.tar.gz
```

**恢复后重启服务：**
```bash
coco restart
```

**体检核对**（数据库 / 密钥 / 备份新鲜度）：
```bash
coco check
```

### 数据清理

**向导式清理已关闭客户 / 已成交房源**（1 预演 / 2 客户 / 3 房源 / 4 两类 / 5 恢复误标状态 / 6 取消）：
```bash
coco data-clean
```

- 先预演再确认：预演只列出会被清理什么，不做任何修改；动手前自动整库备份，备份失败即中止。
- 有关联带看、成交、跟进等记录的默认跳过（保护历史），要连历史一起删需显式加 `--force`。
- 非交互环境用参数：只预演 `coco data-clean --dry-run`；按日期清理 `coco data-clean --kind all --before 2026-01-01 --yes`；恢复误标状态 `coco data-clean --restore --yes`。

### 卸载

**选择卸载程度**（1 保留数据 / 2 卸载并清理状态 / 3 彻底清理），输入 yes 确认：
```bash
coco uninstall
```

- 1、2 档会在动手前自动备份数据库与加密密钥，并打印备份包路径与下载命令；3 档（彻底清理，含数据库）不备份，如需备份请先执行 `coco backup`。

### 重装系统完整恢复流程

**重装会清空服务器所有数据，动手前务必完成前两步。**

**第 1 步：旧服务器备份并打包下载（重装前必做）**

**备份数据库（强制）：**
```bash
coco backup --force
```

**确认备份文件齐全：**
```bash
ls -la ~/backups/real_estate/
```

**打包迁移文件：**
```bash
coco backup --migration /root/coco_migration.tar.gz
```

**下载迁移包到本地电脑（重装后服务器没数据了，务必下载）：**
```bash
scp root@服务器IP:/root/coco_migration.tar.gz ~/Desktop/
```

**第 2 步：重装服务器系统**

在云服务器控制台选择「重装系统」，镜像选 Ubuntu 24.04 / 26.04 LTS，确认执行。这一步会把服务器清空，恢复成一台全新的机器。

推荐 Ubuntu **24.04 / 26.04 LTS**。

**第 3 步：在服务器上安装 Coco**

按文档开头的「一键安装与配置」章节完成安装与配置。

**第 4 步：恢复数据**

**把迁移包传回服务器（本地电脑执行）：**
```bash
scp ~/Desktop/coco_migration.tar.gz root@服务器IP:/root/
```

**执行数据恢复：**
```bash
coco restore --migration /root/coco_migration.tar.gz
```

**重启服务：**
```bash
coco restart
```

**第 5 步：验证**

```bash
coco check
```

预期：数据库/密钥/备份新鲜度全部 PASS；在飞书给 Coco 发"看下房源统计"，数据完整返回（房源/客户/成交都在，品牌名保留）。

## 数据安全

- **字段级加密**：客户手机号、微信号等敏感字段加密存储，数据库泄露也不泄露明文
- **密钥自动生成**：加密密钥随安装自动生成并独立备份，首次使用智能体时 Coco 会主动提醒保存
- **每日自动备份**：每天凌晨 2 点自动备份数据库至 `~/backups/real_estate/`，保留 30 天
- **一键迁移**：迁移新服务器时，一条命令完成数据库、图片、加密密钥的整体打包与恢复

> 重要提示：密钥文件（enc_key.txt）请务必保存到安全的地方（电脑/U 盘/网盘）。密钥丢失将导致客户数据永久无法解密。

## 项目结构

```
coco-real-estate-agent/
├── install.sh                    # 一键安装脚本
├── run_agent.py                  # Agent 核心
├── cli.py                        # 命令行入口
├── agent/
│   ├── real_estate_db.py         # 房产数据库模块（PostgreSQL）
│   ├── real_estate_prompt.py     # Coco 系统提示词
│   ├── prompt_builder.py         # 身份注入（Coco）
│   └── system_prompt.py          # 系统提示词（已注入房产能力）
├── tools/
│   ├── real_estate_customer.py     # 客户管理工具
│   ├── real_estate_property.py     # 房源管理工具
│   ├── real_estate_followup.py     # 跟进管理工具
│   ├── real_estate_analytics.py    # 数据统计工具
│   ├── real_estate_calculator.py   # 计算工具
│   ├── real_estate_communication.py # 沟通工具
│   └── real_estate_policy.py       # 政策工具
├── skills/
│   └── real_estate/SKILL.md     # 房产技能文档
├── scripts/
│   └── backup_db.py             # 数据库备份脚本（pg_dump）
├── toolsets.py                  # 工具集定义（已注册 real_estate）
└── plugins/platforms/feishu/    # 飞书适配器
```

## 使用示例

```
你: 帮我添加客户张三，预算300-500万，想买朝阳区三居室
Coco: 已添加客户张三
     - 手机：138****8001
     - 预算：300-500万
     - 需求：3室，朝阳区
     - 等级：C级（初步接触）

你: 有没有合适的房源推荐给张三？
Coco: 根据张三的需求，为您推荐以下房源：
     1. 望京新城精装三居 - 450万 (匹配度: 85分)

你: 看看今天需要跟进什么
Coco: 每日早报
     - 2位S级客户待跟进
     - 3条今日任务
```

添加客户、推荐房源、跟进提醒，全部在飞书对话里完成。

## 常见问题

### 数据库连接失败

**启动 PostgreSQL 数据库：**
```bash
sudo systemctl start postgresql
```

**查看服务状态：**
```bash
coco status
```

**查看日志（最近 50 行）：**
```bash
coco logs
```

### 飞书消息收不到

**1. 检查 App ID / App Secret 是否正确**（重新配置）：
```bash
coco setup
```

**2. 确认飞书应用已发布**

**3. 确认事件订阅配置正确**

### 智能体不回复

**1. 确认服务在运行：**
```bash
coco status
```

**2. 查看报错：**
```bash
coco logs
```

**3. 确认模型 API Key 有效**（重新配置）：
```bash
coco model
```

## 总结

Coco 把客户管理、房源匹配、跟进提醒、成交管理和经营分析整合进一个对话式助手，目标是让中介把时间花在带看和成交上，而不是翻表格、记提醒、写文案。

- 开源免费，MIT 协议，代码完整可见
- 一行命令安装，半小时内跑起来
- 数据自己掌控，支持加密、备份与迁移

## License

MIT 协议 · Copyright (c) 2026 LYH-Gini · 详见 [LICENSE](LICENSE)

## 致谢

- [Nous Research](https://nousresearch.com)
