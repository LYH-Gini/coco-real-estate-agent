# Coco 房产助理 - 备份/迁移/恢复 完整操作手册

> 适用场景：日常数据备份、服务器重装系统、迁移到新服务器、误删数据恢复。
> 操作对象：房源数据、客户数据、跟进/带看/成交记录、房源图片、加密密钥。

---

## 一、先理解：数据安全三件套

Coco 的数据由三部分组成，**缺一不可**：

| 组成 | 内容 | 丢了会怎样 |
|------|------|-----------|
| ① 数据库 | 房源、客户、跟进、带看、成交、话术（PostgreSQL） | 所有业务数据没了 |
| ② 加密密钥 | COCO_ENC_KEY（Fernet 密钥，自动生成） | 客户手机号/微信号**永久无法解密** |
| ③ 房源图片 | 房源图片文件 | 图片丢失 |

> ⚠️ 第②条最关键：密钥在 `~/coco/.env.db` 和 `~/backups/real_estate/enc_key.txt` 各有一份。**密钥一旦丢失，就算数据库还在，客户的手机号微信号也永远解不开。** 请务必把 `enc_key.txt` 备份到电脑/网盘/U盘。

---

## 二、日常备份（系统自动做，也可手动）

### 自动备份

安装时已设置定时任务：**每天凌晨 2:00 自动备份**，保留 30 天，位置 `~/backups/real_estate/`。

备份内容包括：数据库 dump + 房源图片压缩包 + 加密密钥。

### 手动备份（随时执行）

```bash
coco backup
```

### 查看备份列表

```bash
coco backups
```

---

## 三、重装系统 / 换新服务器（完整流程）

> 一句话流程：**旧服务器打包 → 下载到电脑 → 新服务器一键安装 → 上传迁移包 → 一条命令恢复**

### 第 1 步：在旧服务器上打包（重装/迁移前）

**先手动备份一次，确保数据最新：**
```bash
coco backup
```

**打包**（数据库备份 + 图片备份 + 加密密钥）：
```bash
coco backup --migration /root/coco_migration.tar.gz
ls -lh /root/coco_migration.tar.gz
```

### 第 2 步：把迁移包下载到本地电脑

**在您自己的电脑上执行**（Windows 用 CMD/PowerShell，把 IP 换成服务器公网 IP）：
```bash
scp root@服务器IP:/root/coco_migration.tar.gz ~/Desktop/
```

> 迁移包很小（几 MB），也可以从服务器下载到手机再上传，怎么方便怎么来。

### 第 3 步：在新服务器上一键安装

按服务器所在地区选一条：

**国内服务器（Gitee 源）：**
```bash
curl -fsSL https://gitee.com/LYH-Gini/coco-real-estate-agent/raw/master/install.sh -o install.sh && bash install.sh
```

**港澳台地区及海外服务器（GitHub 源）：**
```bash
curl -fsSL https://raw.githubusercontent.com/LYH-Gini/coco-real-estate-agent/master/install.sh -o install.sh && bash install.sh
```

安装完成后按提示做两件配置（安装脚本会打印说明）：

**配置模型**（小米 MiMo，填 API Key）：
```bash
coco model
```

**配置飞书**（填 App ID 和 App Secret）：
```bash
coco setup
```

> 飞书开放平台那边：如果服务器 IP 变了，记得更新事件订阅 URL。

### 第 4 步：上传迁移包并恢复

**在您自己电脑上执行：把迁移包传到新服务器**：
```bash
scp ~/Desktop/coco_migration.tar.gz root@新服务器IP:/root/
```

**SSH 登录新服务器后执行：**
```bash
coco restore --migration /root/coco_migration.tar.gz
```

恢复顺序（脚本自动执行，任一步失败即中止并提示）：
1. 恢复数据库（房源/客户/跟进/成交）
2. 恢复房源图片
3. 合并加密密钥到 `.env.db`（**必须最先恢复好，否则旧数据无法解密**）

### 第 5 步：重启并验证

**重启服务并查看状态**（`coco status` 应显示运行中）：
```bash
coco restart
sleep 10
coco status
```

然后在飞书里给 Coco 发一条消息（如"查一下房源统计"），确认数据回来了。

---

## 四、单独恢复场景

### 只恢复数据库（比如误删数据）

**先看有哪些备份：**
```bash
coco backups
```

**恢复指定的数据库备份并重启服务：**
```bash
coco restore --file real_estate_20260101_020000.dump
coco restart
```

### 只恢复房源图片

```bash
coco restore --migration /root/coco_migration.tar.gz --images-file real_estate_images_20260101_020000.tar.gz
```

### 密钥丢了怎么办

**这是最危险的情况。** 处理办法：
1. 找 `~/backups/real_estate/enc_key.txt` 或 `~/coco/.env.db` 里的 `COCO_ENC_KEY=` 行（如果服务器还没重装）
2. 找到后立即复制保存到安全的地方
3. 如果两个地方都没有了：数据库里客户手机号/微信号将无法解密（其他字段如姓名、房源数据不受影响）。**没有补救办法，只能吃一堑长一智。**

---

## 五、常见问题（FAQ）

**Q1：恢复时报 "认证失败 / password authentication failed"？**
A：备份脚本从 `.env.db` 读取数据库密码。如果手动改过密码或 `.env.db` 被删，用 `--db-url` 指定：
```bash
coco restore --file xxx.dump --db-url "postgresql://hermes:密码@localhost:5432/hermes_agent"
```

**Q2：恢复后客户手机号显示乱码/解不开？**
A：加密密钥不匹配。确认恢复前 `.env.db` 里的 `COCO_ENC_KEY` 和备份时的密钥一致（restore_migration 会自动处理，手动 restore 时要自己核对）。

**Q3：迁移包里的图片文件缺失？**
A：打包时确认 `ls ~/backups/real_estate/` 里有 `real_estate_images_*.tar.gz`。没有的话数据库和密钥也能恢复，图片单独再备份。

**Q4：重装后忘了配置飞书/模型？**
A：服务能启动但机器人不回复。执行 `coco model` 和 `coco setup` 重新配置，然后 `coco restart`。

**Q5：备份文件多久清理？**
A：数据库备份自动保留 30 天，更早的自动删除；图片包只保留最近 2 份（每份都是全量，装的是全部房源照片）。重要节点（如迁移前）建议手动把备份文件下载到本地留档。

**Q6：房源照片在服务器上找不到了（出海报提示缺照片）？**

A：照片可能已被消息缓存的 24 小时清理规则删掉，可从备份图片包里捞回（默认只演练，看清结果再加 `--apply`）：
```bash
coco images-recover
coco images-recover --apply
```

---

## 六、速查卡（复制到手机备忘录）

**旧服务器打包：**
```bash
coco backup --migration /root/coco_migration.tar.gz
```

**电脑下载：**
```bash
scp root@旧IP:/root/coco_migration.tar.gz ~/Desktop/
```

**新服务器一键安装**（按服务器所在地区选一条）：

**国内服务器（Gitee 源）：**
```bash
curl -fsSL https://gitee.com/LYH-Gini/coco-real-estate-agent/raw/master/install.sh -o install.sh && bash install.sh
```

**港澳台地区及海外服务器（GitHub 源）：**
```bash
curl -fsSL https://raw.githubusercontent.com/LYH-Gini/coco-real-estate-agent/master/install.sh -o install.sh && bash install.sh
```

**新服务器配置（装完提示时做）：**
```bash
coco model && coco setup
```

**电脑上传迁移包：**
```bash
scp ~/Desktop/coco_migration.tar.gz root@新IP:/root/
```

**新服务器恢复：**
```bash
coco restore --migration /root/coco_migration.tar.gz
coco restart
```
