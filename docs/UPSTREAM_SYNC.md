# Coco 同步官方上游（UPSTREAM SYNC）作业手册

> 适用场景：官方 Hermes 发布新版本，需要把 Coco 的底座同步过去。
> 这是一份**照着走**的清单。每一步都有对应的检查项，因为它对应着过去真实踩过的坑。

---

## 零、动手前的三个前提

- [ ] **仓库历史保持线性**（经纪人的更新命令用的是 `git pull --ff-only`）。
      任何 rebase / 强推都会让所有老实例更新失败。**这是铁律。**
- [ ] **Gitee 仓库未超配额**：免费版单仓库上限 **500MB**，超了平台会**锁死推拉**。
      同步前先记录当前体积（见第五步），超过 350MB 就要开始准备预案。
- [ ] **不在生产实例上直接试**。先在本地仓库和测试实例上走完流程，再让经纪人更新。

---

## 一、同步流程（七步）

### 第 1 步｜记录基线
```bash
cd ~/coco   # 或本地分析仓库
git log --oneline -1                 # 当前提交
cat VERSION                          # Coco 版本
du -sh .git                          # 仓库体积（记下来，用于对比）
python3 scripts/check_coco_hooks.py  # 同步前的挂钩点状态（应全 PASS）
```

### 第 2 步｜拉取官方新版本快照并替换官方层
```bash
bash scripts/sync_upstream.sh v2026.9.14   # 换成实际的官方版本 tag
```
脚本会：拉官方快照 → 替换官方层文件 → **保留 Coco 自有文件** → 打印
「需要人工处理的改动点」。

官方在两次同步之间删掉/改名的旧文件会一直留在本地（快照式替换只增不减），脚本每次
会把它们算出来并列出数量。要顺带清理，执行时加 `--prune-official-deleted`：

```bash
bash scripts/sync_upstream.sh v2026.9.14 --dry-run --prune-official-deleted   # 先演练看清单
bash scripts/sync_upstream.sh v2026.9.14 --prune-official-deleted             # 真实执行并清理
```

清理走 `scripts/prune_official_deleted.sh`，保护规则是「**只删登记在官方基线里的文件**」——
Coco 自有文件、挂钩点文件、我们的业务目录（`agent/real_estate_*`、`tools/real_estate_*`、
`migrations/`、`patches/`、`docs/`、`tests/real_estate/`）永不在候选里；本地有未提交改动的
文件也会跳过。删掉的清单存档在 `.sync-backup/prune-*/`，回滚用 `git checkout HEAD -- <路径>`。
规则本身可自检：`bash scripts/prune_official_deleted.sh --selftest`。

⚠️ 覆盖范围：这个开关清理的是「**当前底座之后**官方删掉/改名的文件」（即基线里登记过、
新快照里没有的）。比基线更老的年份残留（官方在我们同步到 0.21.3 之前就删掉的老文件）
不在它的候选里，需要一次性人工清理 —— 2026-09-21 已清过一批 36 个（方法见 SKILL.md
「官方已删文件残留」一节：拿官方 0.20 初始快照与当前底座做集合差 + 按完整路径核引用）。

### 第 3 步｜逐处重新应用 Coco 的改动
按 `patches/README.md` 的语义说明，在新版代码里重新实现我们改过的每一处（清单与处数见该文件，别照旧数字）：

| 编号 | 文件 | 优先确认 |
|---|---|---|
| 01 | `toolsets.py` | 定义 + 被 hermes-feishu 引用，两个条件都要 |
| 02 | `agent/prompt_builder.py` | 身份常量名是否变 |
| 03 | `agent/system_prompt.py` | 是否仍拼在"稳定片段"（会破坏 prompt 缓存的话要调） |
| 04 | `agent/agent_init.py` | CLI 初始化入口位置 |
| 05 | `hermes_cli/config_defaults.py` | 压缩阈值 0.8 / protect_last_n 40 |
| 06 | `plugins/platforms/feishu/adapter.py` | 欢迎语 + 密钥提醒 + 注册定时任务，三件都要 |
| 07 | `gateway/run_turn.py` | 开场白 + 关闭官方 profile-build 引导 |
| 08 | `scripts/sandbox/pick-release-tags.sh` | 挑标签正则要同时认日期式与语义化标签（`v0.21.3-1`） |
| 09 | `pyproject.toml` | 依赖里必须保留 `ddgs`（web_search 后端） |
| 10 | `.github/workflows/install-e2e.yml` | **不能带 `schedule:` 定时触发**——该 E2E 测的是 Hermes 本体的安装升级，与 Coco 的 install.sh 分发方式不符，定时跑必失败并持续吃 Actions 配额 |
| 11 | `.github/workflows/deploy-site.yml` | **不能带 `release:` / `push:` 触发**——官方靠 `VERCEL_DEPLOY_HOOK` 部署文档站，本仓库没有该钩子，每次发版必红；只留 `workflow_dispatch` |
| 17 | `hermes_cli/setup_summary.py` | 收尾屏三张表 + 提示句必须是 `coco` 命令（官方版是 `hermes`） |
| 18 | `gateway/run_inbound.py` | 配对码提示必须是 `coco pairing approve`（官方版是 `hermes pairing approve`） |
| 19 | `hermes_cli/update_cmd_config.py` | 配置迁移提示必须是 `coco config migrate` |
| 12 | `pyproject.toml` | `requires-python` 必须是 `>=3.11,<3.15`（Coco 已放行 3.14：依赖全有 cp314 轮子、单测全绿）；官方版是 `<3.14`，被同步冲回就会出现「install.sh 放行、pip 拒绝」的错配 |

### 第 4 步｜自检 + 处理部署体系
```bash
python3 scripts/check_coco_hooks.py    # 应全部 PASS
```
再检查官方是否改动了这些**我们自建**的东西所依赖的机制：
- [ ] `install.sh` 的网关环境补丁（`setup_gateway_env_patch`）：该补丁给
      `hermes gateway install` 生成的用户服务补 EnvironmentFile；新装实例跑的是系统服务
      （自带 EnvironmentFile），补丁仅作老实例兼容。官方若改了 gateway install 机制，
      补丁可能失效 → **幽灵库事故会重现**
      （症状：机器人回复"登记成功"，库里却没有数据）
- [ ] `scripts/update.sh` 的 7 步流程是否仍然适用
- [ ] `scripts/migrate.py` 的迁移执行器与官方数据库结构是否冲突

### 第 5 步｜三层验收（缺一不可）

| 关卡 | 命令 | 通过标准 | 谁跑 |
|---|---|---|---|
| ① 部署体检 | `python3 scripts/healthcheck.py` | 各项无 FAIL | 我 |
| ② 工具冒烟 | `python3 scripts/smoke_test_real_estate.py` | 工具逐个真实调用，无 EXC | 我 |
| ③ 单元测试 | `.venv-dev/bin/python -m pytest tests/real_estate/ -q` | 全绿 | 我 |
| ④ **飞书全量实测** | 按 `docs/TESTING_FEISHU_FULL.md` 逐条发指令 | 逐条对照，关键链路查库确认 | **负责人** |

> ⚠️ **冒烟 ≠ 没问题**：冒烟只证明"工具本身能跑"，证明不了"模型会在该用的时候调用它"。
> 第 ④ 关必须真人在飞书里走一遍，且**每测一项都查数据库确认落库**，不能只看回复文本。

### 第 6 步｜灰度发布
1. 先在你自己的服务器（测试实例）跑完整更新链路：
   `git -C ~/coco pull && bash ~/coco/scripts/update.sh`
2. 更新后确认：
   - [ ] 飞书里 Coco 自我介绍正确（不是官方默认文案）
   - [ ] 房产工具可用（例如"看下房源统计"）
   - [ ] **网关进程环境里有 DATABASE_URL**（防幽灵库）：
     `systemctl show -p MainPID --value hermes-agent | xargs -I{} sudo cat /proc/{}/environ | tr '\0' '\n' | grep DATABASE_URL`
3. 确认无误后，再通知其他经纪人更新。

### 版本号规则（派生版本号）
Coco 版本号格式：`<官方底座版本>-<Coco 第几次发行>`，例如 **`0.21.3-1`**。

- 同步到官方 0.22.0 后 → `0.22.0-1`；同一底座下再发一版 → `0.21.3-2`
- **不出现裸的 `0.21.3`**（避免与官方原版混淆）
- 改版本号只需改仓库根 `VERSION` 一个文件（install.sh / update.sh 自动读取并显示底座）
- `UPSTREAM_VERSION` 文件仍保留官方 tag（`v2026.9.14`），供 CI 比对，格式不要改
- 发版时记得同步：README 中英文徽章 + 博客 id=70 + GitHub Release

### 第 7 步｜发版与记账
```bash
# 更新 VERSION、打标签、推送双仓库
bash scripts/push_all.sh
```
每次同步都记一笔（写在提交信息或本文件末尾的记录区）：
本次同步到哪个官方版本 · 改了哪些文件 · 体积涨到多少 · 遇到什么问题 · 怎么解决的。

---

## 二、每次必查清单（对应过去的真实事故）

**第一件事：全量扫一遍「被静默覆盖」的内容**（2026-09-15 靠这招抓出 SOUL.md 与 pyproject 的 ddgs 依赖被冲）：

```bash
# 同步前含中文的文件 vs 现在含中文的文件，差集 ≈ 被官方版替换掉的我们的内容
git grep -l -P '[\x{4e00}-\x{9fff}]' <同步前的 tag> | sed 's/^[^:]*://' | sort > /tmp/old_zh.txt
git grep -l -P '[\x{4e00}-\x{9fff}]' HEAD | sed 's/^[^:]*://' | sort > /tmp/new_zh.txt
comm -23 /tmp/old_zh.txt /tmp/new_zh.txt    # 逐个确认：是「我们被覆盖」还是「官方自身演进」
```

判断口径：官方自身演进常见于 i18n、钉钉/企微/编辑器类适配器；**与我们相关的（身份文案、依赖、README、脚本注释）中文消失就是被覆盖**。

**第二件事：逐项核对依赖**——官方同步会整体替换 `pyproject.toml`，我们加的依赖会被冲掉：

```bash
grep -c '"ddgs' pyproject.toml    # 应为 1；为 0 说明被冲（ddgs 没了 web_search 会失效）
```


- [ ] **配置版本号**：官方 `_config_version` 变了没？（对照
      `hermes_cli/config_defaults.py` 的 `"_config_version"` 值）
      变了就确认 `scripts/update.sh` 里有跑 `hermes config migrate`。
      （已知：我们 33 → 官方 0.21.3 是 44）
- [ ] **Python 版本要求**：官方 `pyproject.toml` 的 `requires-python` 变了没？
      （已知 0.21.3 仍是 `>=3.11,<3.14`，与我们一致）
- [ ] **新增依赖**：官方新增的依赖要确保 `pip install -e .` 能装上（走 pyproject）
- [ ] **飞书适配器改动**：官方对 adapter 的改动是否影响我们的挂钩点（第 06 项）
- [ ] **网关环境补丁**：`setup_gateway_env_patch` 是否仍生效（防幽灵库）
- [ ] **身份文案一致性**：改身份时，7 处副本都要同步（adapter 欢迎语、gateway
      开场白、prompt_builder、房产提示词、SOUL.md、技能文件、README）
- [ ] **仓库体积**：记录数值；> 350MB 时启动体积预案
- [ ] **标签格式**：Coco 用语义化 `vX.Y.Z`，官方用日期式 `vYYYY.M.D`；
      继承来的脚本若报"找不到标签"，先看格式匹配（历史坑）

---

## 三、体积预案（Gitee 500MB 天花板）

实测：一次跨版本同步让仓库 **+80MB**（首次引入大批新文件）；同一批文件再同步只 +20MB。
现状：约 70MB。推算：约 1.5-2 年用满 500MB（官方约 6 周一个大版本）。

按顺序触发：
1. **> 350MB**：记录并观察；跑 `git gc --aggressive` 看看能回收多少
2. **> 400MB**：评估切 GitHub 为主源（容量 10GB，但国内访问慢）或升 Gitee 配额
3. **接近 500MB**：一次性重写历史瘦身 —— ⚠️ 会破坏老实例的快进更新，
   **必须提前一个版本铺垫 + 公告经纪人执行一次性重置命令**，属最后手段

---

## 四、回滚手段

| 情况 | 怎么回滚 |
|---|---|
| 代码有问题 | 更新前打的 git tag / 上一个提交：`git reset --hard <tag>` 后推送 |
| 数据有问题 | `scripts/backup_db.py` 在更新前自动备份，按 `docs/BACKUP_MIGRATION.md` 恢复 |
| 仓库不可用 | Gitee / GitHub 双源，一个挂了切另一个（install.sh 会自动探测） |

---

## 五、同步记录

> 每次同步后在此追加一行，便于追踪"我们跟到哪个版本了"。

| 日期 | Coco 版本 | 同步到官方版本 | 体积 | 备注 |
|---|---|---|---|---|
| 2026-09-15 | v2.0.0 | 官方 v0.21.3（`v2026.9.14`） | 70MB → 约150MB | 首次同步（0.20.0→0.21.3） |
| 2026-09-27 | v0.21.5-1 | 官方 v0.21.5（`v2026.9.24`） | 267MB → 320MB | 0.21.3→0.21.5（官方两版合计 8128 个文件变更）；官方把配对提示拆到 `gateway/run_inbound_unauthorized.py`、Mattermost 向导搬到 `hermes_cli/gateway_setup_wizard.py`，挂钩点 18/26 随之改路径；`_config_version` 44 → 46（老实例更新时由 `scripts/migrate_config.py` 自动迁移） |

---

## 附：为什么不能直接 `git merge upstream`

Coco 仓库是"复制官方文件后重新初始化 git"的形态：一次性导入了 8,598 个文件，
官方几万个提交的历史被压平成一个提交。**两个仓库没有共同祖先**，
`git pull upstream` 会直接报 unrelated histories；强行加
`--allow-unrelated-histories` 等于把 8,600 个文件全当冲突处理，比重做一遍还慢。

所以采用**快照式同步**（本手册的流程）：把官方新版文件整体替换进来 + 重新应用
我们对官方文件的每一处改动。已实测：这种方式下，老实例的 `git pull --ff-only` 能正常快进，
文件替换正确，Coco 自有文件与未跟踪的运行时文件都不会丢。
