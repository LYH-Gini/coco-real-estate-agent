---
name: real-estate-excel-import
description: "Excel批量导入房源数据到Coco房产系统：解析多级表头、字段映射、批量导入"
version: 1.1.0
author: Coco
tags: [real-estate, excel, import, batch, property]
---

# Excel批量导入房源数据

当用户上传Excel文件要求导入房源数据时使用此技能。

## When to Use
- 用户上传.xlsx/.xls文件要求导入
- 用户说"导入房源""批量添加房源""Excel导入"
- 用户提供包含房源信息的表格文件

## Excel常见结构（房产经纪人系统）
- **多级表头**：第5行为分类（编号、房屋信息、房屋价格...），第6行为具体字段名
- **数据起始**：第7行
- **常见Sheet**：2#可售、3#可售、2#出租、3#出租
- **公式字段**：每平方/元等含Excel公式，需`data_only=True`

## 执行流程

### Step 0: 源文件留档（导入前先做）
经纪人发来的表放在消息缓存目录里，**第二天会被系统自动清理**（清理是上游行为，我们不改），所以先复制一份到留档目录，随时能翻出来。

```python
import hashlib, os, shutil, datetime
src = file_path                      # 经纪人发来的 Excel/合同路径
home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
docs = os.path.join(home, "real_estate_docs")
os.makedirs(docs, exist_ok=True)
with open(src, "rb") as f:
    digest = hashlib.sha1(f.read()).hexdigest()[:8]
target = os.path.join(docs, f"{datetime.datetime.now():%Y%m%d}_{digest}_{os.path.basename(src)}")
if not os.path.exists(target):
    shutil.copy2(src, target)
print("源文件已留档：", target)
```

### Step 1: 安装依赖
```python
import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install", "pandas", "openpyxl"])
```

### Step 2: 分析Excel结构
```python
from openpyxl import load_workbook
wb = load_workbook(file_path, data_only=True)
for sheet_name in wb.sheetnames:
    ws = wb[sheet_name]
    # 查看第5-6行表头，第7行起数据
```

### Step 3: 数据处理（execute_code中）
- 逐Sheet读取，解析户型、转换价格
- 过滤无效记录（无价格/无面积）
- 保存为JSON供批量导入

### Step 4: 批量导入
- 用 `delegate_task` 派子代理
- 子代理读取JSON，循环调用 `add_property`
- 每10条打印进度

## 字段映射

### 可售→second_hand
| Excel列 | 系统字段 | 转换 |
|---------|----------|------|
| 房号+户型+面积 | title | 拼接字符串，可追加景观 |
| 出售总价/元 | price | 直接存（系统单位即元） |
| 面积m² | area | float |
| 户型 | rooms+halls | 解析 |
| 景观 | tags | 存为"景观:海景"，不进district |
| 是否已售 | status | 未售出→available |

### 出租→rental
| Excel列 | 系统字段 | 转换 |
|---------|----------|------|
| 年租价（col12） | price | 直接存（元/月，系统单位即元） |
| 6个月租价（col10） | price备选 | 年租为空时用，同样直接存 |

## 关键代码片段

### 户型解析
```python
def parse_layout(s):
    if '开间' in s: return 1, 0
    if '一房一厅' in s: return 1, 1
    if '两房一厅' in s: return 2, 1
    if '三房两厅' in s: return 3, 2
    if '四房两厅' in s: return 4, 2
    import re
    nums = re.findall(r'\d+', s)
    return (int(nums[0]), int(nums[1])) if len(nums)>=2 else (int(nums[0]) if nums else 1, 0)
```

### 安全转换
```python
def safe_float(val):
    if val is None: return 0
    s = str(val).strip()
    if '万' in s: return float(s.replace('万',''))*10000
    if s in ('','0','/'): return 0
    try: return float(s)
    except: return 0
```

## 批量导入执行模式

### 模式A：parallel add_property（推荐，200条以内）
每轮并行调用20个add_property，约10轮完成200条。无需delegate_task。
```
第1轮: add_property × 20 (并行)
第2轮: add_property × 20 (并行)
...直到完成
```
进度格式：`进度：{已完成}/{总数} 成功`

### 模式B：delegate_task子代理（200条以上或需要隔离）
用delegate_task派子代理，子代理读JSON循环导入。

## 数据质量检查（导入前）
导入前用execute_code预检，标记异常数据：
- **价格异常**：二手房价格<5万或>500万 → 可能是月租金混入或录入错误
- **标题为空**：title字段缺失或只有空格
- **区域为空**：district为空字符串（Excel无区域列时留空即可，禁止用景观填充）
- **租赁价格**：rental的price单位是元，Excel月租（元）直接存（1000元/月→1000）

## JSON文件批量导入

当数据已处理为JSON文件（如从Excel转换后）需要批量导入时：

### 读取大JSON文件
`read_file` 在约100K字符处截断，大JSON文件必须用terminal读取：
```python
# 在execute_code中
from hermes_tools import terminal
result = terminal("python3 -c \"import json; data = json.load(open('/tmp/file.json')); print(len(data))\"")
```

### 分批保存策略
将JSON按20条一组保存为独立文件，避免单次输出超限：
```python
terminal("""python3 << 'EOF'
import json
data = json.load(open('/tmp/source.json'))
remaining = data[SKIP:]
for i in range(0, len(remaining), 20):
    batch = remaining[i:i+20]
    with open(f'/tmp/batch_{i//20:03d}.json', 'w') as f:
        json.dump(batch, f, ensure_ascii=False)
EOF""")
```

### JSON解析注意事项
terminal输出的JSON可能包含控制字符，解析时必须用 `strict=False`：
```python
data = json.loads(result["output"], strict=False)
```

### 导入流程
1. 读取JSON总数，确定跳过已导入的数量
2. 分批保存为 `/tmp/batch_NNN.json`
3. 每轮读取一个batch文件，发起20个并行 `add_property` 调用
4. 每20条打印进度：`进度：{已完成}/{总数}`
5. 记录失败条目但继续
6. 完成后调用 `property_stats` 验证

## 数据处理完整模板（execute_code）

参考 `references/batch-import-script.py`（v1.1.0 修正版）：
- 景观 → tags（`景观:海景`），不填 district
- district/community/renovation 无真实数据时留空，禁止编造
- 出租价格：元/月 直接存（系统单位即元）
- 出售总价：元 直接存（系统单位即元）

## Pitfalls
- **openpyxl必须单独安装**：pandas读xlsx报`Import openpyxl failed`，需先`pip install openpyxl`
- 合并单元格读取为None，需跳过空行（检查row[2]即编号列是否为空）
- `data_only=False`时公式字段返回字符串而非值，必须用`data_only=True`
- 价格为"暂定""/"时需安全转换返回0
- 子代理批量导入时注意add_property的price必须是数字类型
- 出租Sheet列结构与可售不同（6个月租价在col10，年租价在col12）
- **大批量导入（>50条）建议用delegate_task**：单会话并行add_property过多会导致超时
- **租金单位问题**（历史）：旧系统price存"万元"，Excel月租1000元需÷10000，曾出现存成1000万的事故；2026-08 起系统存"元"，Excel 元直接入库不再转换
- **景观≠区域**：Excel景观列（园区/海景/园林）是卖点不是行政区，存tags不存district（历史bug：423条房源district全被填成景观）
- **价格异常检测**：导入前检查price范围（二手房建议50000-5000000元，租房建议500-10000元/月）
- **批次大小**：delegate_task子代理建议每批导入50-100条，打印进度
- read_file读大JSON会在100K字符处截断，必须用terminal+python3读取完整文件
- terminal输出的JSON含控制字符时，json.loads需加strict=False
- 批量导入时每轮并行20个add_property调用效率最高（不是10个也不是50个）
