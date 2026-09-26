# Coco 全量飞书实测清单（2026-08-12 确立）

大版本修改/更新后必须跑完这套实测，才能对外宣称"可用"。**铁律：飞书每测一项后，立刻查 PostgreSQL 确认落库，不轻信 Coco 回复**（2026-08-12 幽灵库事故教训：Coco 曾回复"添加成功 #23/23套"但 PostgreSQL 实际 0 条，数据写进了 ~/.hermes/real_estate.db sqlite 幽灵库）。

## 前置检查（服务器）

```bash
coco update   # 更新到最新版并重启服务
git -C ~/coco log --oneline -1                                # 确认版本
coco check  # 预期 FAIL 0
~/coco/venv/bin/python ~/coco/scripts/smoke_test_real_estate.py  # 预期零崩溃
```

## 飞书实测清单（工具逐个走一遍，分 5 批）

每批发完，把 Coco 回复与数据库比对。涉及在售房源的营销测试用**在售**房源（如滨海华庭），不用已售出的。

### 第一批：计算器 + 客户管理（9 条）
| 指令 | 预期工具 | 验证点 |
|---|---|---|
| 帮我算一下，贷款 200 万，30 年，年利率 3.9%，每月月供多少 | mortgage_calculator | 月供 9433.36 元 |
| 帮我算一下二手房过户税费，总价 200 万，满五唯一 | tax_calculator | 契税 3 万+评估 1 万+中介 2 万=6.01 万 |
| 帮我算一下投资回报率，总价 150 万，月租金 5000 元 | roi_calculator | 毛回报 4% |
| 把[客户]的预算改成 X-Y 万，来源改成[渠道] | update_customer | 预算/来源都变 + 变更留痕（source 参数 2026-08-12 补上） |
| 查看[客户]的客户详情 | get_customer | 完整 |
| 列出所有客户 | list_customers | 完整 |
| 把[客户]升级为 A 级客户 | update_tier | tier 变更 |
| 给[客户]加一个标签：刚需 | add_customer_tag | 标签出现 |
| 查看[客户]的标签 | list_customer_tags | 标签列表 |

### 第二批：客户收尾 + 房源 + 跟进（13 条）
| 指令 | 预期工具 | 验证点 |
|---|---|---|
| 看下客户统计 | customer_stats | 数字与库一致 |
| 查看[客户]的需求变更历史 | customer_change_history | 变更记录完整 |
| 把[房源]月租改成 X 元 / 价格改成 X 万 | update_property | 价格变 + 落库；**单价按新总价÷面积自动变（两位小数）** |
| 搜索[区域] X-Y 万的房源 | search_property | 结果与库一致 |
| 你是什么版本 / 版本号是多少 | get_coco_version | 返回 `Coco v0.21.3-x · 提交 … · 通道` |
| 查 [某套房源] 的详细信息 | get_property_detail | 房源全字段 + **单价(两位小数)** + **业主姓名/完整电话** |
| 查一下[某个库里没有的楼栋房号]的详情 | get_property_detail | 如实说找不到 + 列出候选，**不得拿别的房源充当答案** |
| 查一下房源统计数据 | property_stats | 总数/在售/已售 |
| 给我房源录入模板 | get_property_form | 完整模板 |
| 检查一下有没有重复房源 | deduplicate_properties | 报告重复组 |
| 查看[客户]的跟进记录 | get_followups | 跟进列表 |
| 查一下有哪些逾期未跟进的客户 | get_overdue | 逾期列表 |
| 明天上午 10 点提醒我联系[客户] | schedule_reminder | 提醒落库 |

### 第三批：预警 + 带看 + 成交 + 话术（12 条）
| 指令 | 预期工具 | 验证点 |
|---|---|---|
| 今天的早报是什么 | daily_report | 完整日报 |
| 午间检查一下 | midday_check | 完整 |
| 检查有没有流失风险的客户 | stale_check | 流失预警 |
| 查看 1 号带看的详情 | get_viewing | 详情 |
| 列出所有带看记录 | list_viewings | 列表 |
| 带看统计 | viewing_stats | 统计 |
| 把 1 号成交单推进到签约阶段 | advance_deal | stage 变化（查库 stage=signing） |
| 查看 1 号成交单详情 | get_deal | 详情 |
| 列出所有成交单 | list_deals | 列表 |
| 成交统计 | deal_stats | 统计 |
| 保存一条话术：名字叫"X"，场景是推荐，内容："Y" | save_script | 话术落库 |
| 取一条叫"X"的话术 | get_script_by_name | 取出正确 |

### 第四批：话术收尾 + 分析 + 营销 + 生日（9 条）
| 指令 | 预期工具 | 验证点 |
|---|---|---|
| 列出所有话术 | list_scripts | 列表 |
| 使用"X"话术 | use_template | 返回话术 |
| 删除"X"话术 | delete_script | 库清空 |
| 看下业绩看板 | performance_dashboard | 完整 |
| 渠道统计 | channel_stats | 渠道分组 |
| 生成一份本周经营周报 | generate_report | 周报 |
| 给[在售房源]生成贝壳平台的房源文案 | generate_listing_copy | 文案 |
| 今天有客户过生日吗 | birthday_check | 生日列表 |
| 给[客户]设置生日为 X 月 X 日 | update_birthday | 生日落库 |

### 第五批：模板 + 标签 + 海报 + 对比 + 意向 + 图片（8 条）
| 指令 | 预期工具 | 验证点 |
|---|---|---|
| 给我客户录入模板 | get_customer_form | 完整模板 |
| 把[客户]的"刚需"标签移除 | remove_customer_tag | 标签移除 |
| 给[在售房源]生成一张海报 | generate_property_poster | 出图（MEDIA） |
| 给[在售房源]生成九宫格海报 | generate_poster_grid | 出图 |
| [在售房源]跟同小区在售的比一下价格 | compare_property | 竞品对比表（标注同小区/同区域、该区域均价与样本套数） |
| 给[客户]算一下意向度 | intent_score | 意向度评分（含每一分的来历；没有跟进或带看会标注仅供参考） |
| 看看客户的意向评分排名 | list_intent_scores | 排名（说明共几位在跟客户、列了几位） |
| 查看[在售房源]的图片 | list_property_images | 图片列表 |

### 补测：发图 + 政策（2 条）
| 指令 | 预期工具 | 验证点 |
|---|---|---|
| 给[在售房源]添加这张图片（附一张图） | add_property_images | 查 re_properties.images 字段有路径 |
| 海口现在买房限购政策是什么 | get_loan_policy + web_search | 带来源/建议咨询官方，**禁止编数字** |

## 查库验证模板

```bash
export $(grep DATABASE_URL ~/coco/.env.db) && ~/coco/venv/bin/python -c "
import sqlalchemy, os
e = sqlalchemy.create_engine(os.environ['DATABASE_URL'])
with e.connect() as c:
    # 房源
    p = [dict(r._mapping) for r in c.execute(sqlalchemy.text('select id, title, price, status from re_properties order by id desc limit 3'))]
    # 客户（phone 是 Fernet 密文属正常）
    cus = [dict(r._mapping) for r in c.execute(sqlalchemy.text('select id, name, budget_min, budget_max, tier, source from re_customers order by id desc limit 2'))]
    # 成交（stage 列：deposit/signing/loan/transfer/finalized）
    d = [dict(r._mapping) for r in c.execute(sqlalchemy.text('select id, stage, price, deposit_amount from re_deals order by id desc limit 1'))]
    # 图片（存 re_properties.images 字段，无独立表）
    img = [dict(r._mapping) for r in c.execute(sqlalchemy.text('select id, images from re_properties where id=<房源id>'))]
    print('房源:', p); print('客户:', cus); print('成交:', d); print('图片:', img)
"
```

## 判定标准
- 全部工具跑通 + 每项数据落库一致 = 通过
- 任一回复与库不一致（Coco 报数 ≠ 数据库实际）→ 立即停下查原因，可能是环境/工具问题（参考 SKILL.md 幽灵库事故排查链）
- 注意：re_deals 列名是 stage 不是 status（SQL 别写错）；图片无独立表（在 re_properties.images）
