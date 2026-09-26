"""Coco real_estate 工具全量冒烟测试 —— 在 sqlite 上真实调用每个工具的注册 handler

用法（在服务器或本地）:
    在安装目录内执行：python3 scripts/smoke_test_real_estate.py

覆盖: 工具集静态清单里的全部工具 + 出租房附加用例。
输出: 每个工具的 OK/ERR/EXC 汇总 + 未注册/遗漏提示。
"""
import os, sys, json, glob, importlib, traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# ---------- 环境（独立测试库，不碰真实数据） ----------
TEST_DB = '/tmp/coco_smoke_test.db'
os.environ['DATABASE_URL'] = f'sqlite:///{TEST_DB}'
if os.path.exists(TEST_DB):
    os.remove(TEST_DB)
# 用真实 Fernet 密钥走加密路径（服务器上 install.sh 会生成 COCO_ENC_KEY）
from cryptography.fernet import Fernet
os.environ['COCO_ENC_KEY'] = Fernet.generate_key().decode()
os.environ.setdefault('COCO_IMG_DIR', '/tmp/coco_smoke_imgs')
# 隔离 cron 存储（2026-08-13 加）：enable_cron 用例会真实注册任务，指向临时目录避免污染本机/服务器真实 cron
os.environ.setdefault('HERMES_HOME', '/tmp/coco_smoke_cron')

# 会话现场按**真实网关形态**（2026-09-21 加）：时间戳式会话编号 + 由框架绑定的会话上下文。
# 不再手写 agent:main:feishu:dm:oc_xxx 这类格式——那正是 enable_cron 在真实飞书会话里
# 报「无法确定推送会话」却一直冒烟全绿的根因（用假设证明假设）。
from gateway.session_context import set_session_vars
from hermes_state_ids import new_session_id

SMOKE_CHAT_ID = 'oc_smoke_gateway_session'
SMOKE_SESSION_ID = new_session_id()
set_session_vars(platform='feishu', source='feishu', chat_id=SMOKE_CHAT_ID, chat_type='dm',
                 session_key=f'agent:main:feishu:dm:{SMOKE_CHAT_ID}', session_id=SMOKE_SESSION_ID)

from agent.real_estate_db import init_real_estate_db
init_real_estate_db()

# ---------- 导入全部 real_estate 工具模块（自注册） ----------
import tools.registry as registry_mod
for f in sorted(glob.glob(os.path.join(REPO_ROOT, 'tools', 'real_estate_*.py'))):
    mod = os.path.basename(f)[:-3]
    importlib.import_module(f'tools.{mod}')
registry = registry_mod.registry

# ---------- 静态工具清单（与 toolsets.py 一致） ----------
STATIC_TOOLS = [
    "add_customer","update_customer","get_customer","list_customers","update_tier","customer_stats",
    "add_customer_tag","remove_customer_tag","list_customer_tags","get_customer_form","customer_change_history",
    "add_property","update_property","search_property","get_property_detail","match_property","batch_match_report","property_stats","get_property_form","deduplicate_properties",
    "add_followup","get_followups","get_overdue","schedule_reminder","daily_report","midday_check","stale_check",
    "mortgage_calculator","tax_calculator","roi_calculator",
    "get_script","use_template",
    "performance_dashboard","get_loan_policy","list_policy_cities","channel_stats",
    "schedule_viewing","record_viewing","get_viewing","list_viewings","viewing_stats",
    "start_deal","advance_deal","get_deal","list_deals","deal_stats",
    "birthday_check","update_birthday",
    "generate_listing_copy","generate_short_video_script",
    "add_property_images","list_property_images",
    "compare_property","intent_score","list_intent_scores",
    "save_script","get_script_by_name","list_scripts","delete_script",
    "generate_report",
    "generate_property_poster","generate_poster_grid","suggest_poster_titles",
    "save_agent_card","get_agent_card","save_agent_brand","get_agent_brand","get_coco_version",
    "enable_cron","disable_cron",
    "add_owner","update_owner","get_owner","list_owners","owner_portfolio","exclusive_expiring","get_property_owners","find_person_by_name",
    # 2026-09-18：此前注册了却没进工具集（模型看不到），补进清单一起冒烟
    "price_history","price_drop_alerts","update_customer_stage","stage_stagnation",
    "churn_warning","find_alternatives","clear_defect_tag","add_referral","referral_stats",
    "loan_compare","tax_breakdown_report","market_brief",
    "delete_property","delete_customer","purge_data",
]

# ---------- 造一张测试图片 ----------
from PIL import Image
os.makedirs('/tmp/coco_smoke_imgs', exist_ok=True)
TEST_IMG = '/tmp/coco_smoke_imgs/test_house.jpg'
Image.new('RGB', (400, 300), (200, 180, 160)).save(TEST_IMG)


def call(tool, args=None):
    entry = registry.get_entry(tool)
    if entry is None:
        return ('NO_ENTRY', None)
    try:
        # 与网关一致：registry.dispatch 会注入 session_id/task_id 等运行时参数。
        # 裸调用测不出"handler 注册写错（**kw 直传）"这类故障（2026-09-18 get_property_form 事故）。
        raw = entry.handler(args or {}, session_id=SMOKE_SESSION_ID, task_id='smoke')
    except Exception:
        return ('EXC', traceback.format_exc(limit=3))
    try:
        data = json.loads(raw)
        ok = bool(data.get('success'))
        return ('OK' if ok else 'ERR', data)
    except Exception:
        return ('RAW', str(raw)[:120])


results = {}

# ---------- 1. 播种 ----------
r = call('add_customer', {"name":"测试客户张先生","phone":"13800138000","wechat":"zhang138","tier":"S",
    "budget_min":3000000,"budget_max":5000000,"area_pref":"朝阳","layout_pref":"3室2厅","location":"北京朝阳",
    "renovation":"精装","notes":"测试客户","source":"转介绍","customer_type":"buy_second_hand","birthday":"1990-01-01"})
cid = r[1]['customer']['id']; results['add_customer'] = r

r = call('add_property', {"title":"测试小区三居","price":1280000,"area":89.5,"community":"测试小区","district":"朝阳",
    "address":"朝阳路1号","rooms":3,"halls":2,"bathrooms":1,"floor":"5/18","orientation":"南北","renovation":"精装",
    "year_built":2018,"has_elevator":1,"parking":1,"property_type":"second_hand","tags":"地铁房,南北通透"})
pid = r[1]['property']['id']; results['add_property'] = r

# 出租房:月租 1000 元(验证小金额直接存元)
r = call('add_property', {"title":"测试小区单间出租","price":1000,"area":45.0,"community":"测试小区","district":"朝阳",
    "rooms":1,"halls":1,"bathrooms":1,"property_type":"rental","tags":"拎包入住"})
rental_pid = r[1]['property']['id']; results['add_property_rental'] = r

# 第二套在售房源：营销类工具（海报/短视频/传图）用它——播种房源成交后已下架
r = call('add_property', {"title":"在售房源二号","price":2000000,"area":120.0,"community":"和风家园","district":"琼山",
    "rooms":3,"halls":2,"bathrooms":2,"property_type":"second_hand","tags":"南北通透"})
pid2 = r[1]['property']['id']; results['add_property_second'] = r

# 登记业主并关联房源（2026-08-30 补 owner 工具冒烟种子）
r = call('add_owner', {"name":"测试房东","phone":"13900139000","wechat":"owner_wx","id_number":"460005199001011234"})
oid = r[1]['owner']['id']; results['add_owner'] = r
r = call('update_property', {"property_id":pid2,"owner_id":oid,"viewing_note":"钥匙在门店"})
results['update_property_owner'] = r

r = call('save_script', {"name":"议价话术","content":"理解您的预算考虑，这套房可以谈","scenario":"objection_handling"})
sid = r[1]['script']['id']; results['save_script'] = r

# 保存经纪人品牌（海报生成前置条件，2026-08-12 起海报无品牌返回 need_brand）
r = call('save_agent_brand', {"brand_name":"测试房产"})
results['save_agent_brand'] = r

r = call('schedule_viewing', {"customer_id":cid,"property_id":pid,"viewing_time":"2026-08-16 14:00"})
vid = r[1]['viewing']['id']; results['schedule_viewing'] = r

r = call('start_deal', {"customer_id":cid,"property_id":pid,"price":1250000,"deposit_amount":20000,
    "deposit_date":"2026-08-15","notes":"测试成交"})
did = r[1]['deal']['id']; results['start_deal'] = r

# 数据清理冒烟种子（2026-09-23）：一套无牵挂的已售房源 + 一位无牵挂的已关闭客户，
# 直接用 db 层建（不产生跟进/变更历史），专供删除用例——不碰上面那些带历史的种子数据
from agent.real_estate_db import get_real_estate_db as _get_estate_db
_estate_db = _get_estate_db()
purge_pid = _estate_db.add_property(
    title="待清理的已售房源", price=900000, area=60.0, community="测试小区",
    district="朝阳", rooms=2, halls=1, property_type="second_hand", status="sold")["id"]
purge_cid = _estate_db.add_customer(
    name="待清理的已关闭客户", tier="C", customer_type="buy_second_hand", status="closed")["id"]

# ---------- 2. 全部工具按序调用 ----------
CASES = [
    ("update_customer", {"customer_id":cid,"budget_max":900000}),                  # 500万→90万 触发预算漂移预警
    ("get_customer", {"customer_id":cid}),
    ("list_customers", {}),
    ("update_tier", {"customer_id":cid,"tier":"A"}),
    ("customer_stats", {}),
    ("add_customer_tag", {"customer_id":cid,"tag":"刚需"}),
    ("remove_customer_tag", {"customer_id":cid,"tag":"刚需"}),
    ("list_customer_tags", {"customer_id":cid}),
    ("get_customer_form", {}),
    ("customer_change_history", {"customer_id":cid}),
    ("update_property", {"property_id":pid,"price":1250000}),
    ("search_property", {"district":"朝阳","max_price":1500000,"limit":10}),
    ("get_property_detail", {"property_id":pid2}),
    ("get_property_detail", {"title":"在售房源二号"}),
    ("match_property", {"customer_id":cid,"top_n":5}),
    ("batch_match_report", {"top_n":1}),
    ("property_stats", {}),
    ("get_property_form", {}),
    ("deduplicate_properties", {"dry_run":True}),
    ("add_followup", {"customer_id":cid,"content":"电话沟通，客户周末来看房","type":"call",
        "next_date":"2026-08-15","next_time":"10:00"}),
    ("get_followups", {"customer_id":cid}),
    ("get_overdue", {}),
    ("schedule_reminder", {"customer_id":cid,"date":"2026-08-16","time":"09:30","content":"跟进客户意向"}),
    ("daily_report", {}),
    ("midday_check", {}),
    ("stale_check", {}),
    ("mortgage_calculator", {"price":3000000,"down_payment_ratio":0.3,"loan_years":30,"interest_rate":4.5}),
    ("tax_calculator", {"price":3000000,"area":100,"is_first_home":True,"hold_years":2}),
    ("roi_calculator", {"price":2000000,"monthly_rent":5000,"hold_years":5,"expected_appreciation":0.03}),
    ("get_script", {"scenario":"greeting"}),
    ("use_template", {"template_name":"property_recommend",
        "variables":{"community":"测试小区","price":"128","rooms":3,"halls":2,"area":"89.5","highlights":"南北通透"}}),
    ("performance_dashboard", {"period":"week"}),
    ("get_loan_policy", {"city":"北京","policy_type":"首付比例"}),
    ("list_policy_cities", {}),
    ("channel_stats", {}),
    ("record_viewing", {"viewing_id":vid,"status":"done","result":"interested","feedback":"客户觉得价格合适"}),
    ("get_viewing", {"viewing_id":vid}),
    ("list_viewings", {}),
    ("viewing_stats", {}),
    ("advance_deal", {"deal_id":did,"stage":"signing","date":"2026-08-20"}),
    ("get_deal", {"deal_id":did}),
    ("list_deals", {}),
    ("deal_stats", {}),
    ("birthday_check", {}),
    ("update_birthday", {"customer_id":cid,"birthday":"1990-01-01"}),
    ("generate_listing_copy", {"property_id":pid,"platform":"friends"}),
    ("generate_listing_copy", {"property_id":rental_pid,"platform":"beike"}),      # 出租文案应显示"1000元/月"
    ("generate_short_video_script", {"property_id":pid2,"platform":"douyin"}),
    ("add_property_images", {"property_id":pid2,"images":TEST_IMG}),
    ("list_property_images", {"property_id":pid2}),
    ("compare_property", {"property_id":pid2}),
    ("intent_score", {"customer_id":cid}),
    ("list_intent_scores", {}),
    ("get_script_by_name", {"name":"议价话术"}),
    ("list_scripts", {}),
    ("delete_script", {"script_id":sid}),
    ("generate_report", {"period":"week"}),
    ("suggest_poster_titles", {"property_id":pid2}),
    ("get_agent_card", {}),
    ("save_agent_card", {"name":"测试经纪人","phone":"138-0000-0000","wechat":"test-wx","company":"测试房产"}),
    ("generate_property_poster", {"property_id":pid2, "poster_title":"今日主推", "allow_missing":True, "show_room_no":"unit"}),
    ("generate_poster_grid", {"property_ids":str(pid2)}),
    ("get_agent_brand", {}),
    ("get_coco_version", {}),
    ("price_history", {"property_id": pid2}),
    ("price_drop_alerts", {"days": 30}),
    ("update_customer_stage", {"customer_id": cid, "stage": "interested"}),
    ("stage_stagnation", {}),
    ("churn_warning", {}),
    ("find_alternatives", {"property_id": pid2}),
    ("clear_defect_tag", {"property_id": pid2, "tag": "采光差"}),
    ("add_referral", {"referrer_customer_id": cid, "referred_name": "被介绍人甲", "referred_phone": "13700137000"}),
    ("referral_stats", {}),
    ("loan_compare", {"price": 1500000}),
    ("tax_breakdown_report", {"price": 1500000, "area": 100.0}),
    ("market_brief", {"district": "美兰"}),
    ("enable_cron", {}),
    ("disable_cron", {}),
    ("get_owner", {"owner_id":oid}),
    ("list_owners", {}),
    ("owner_portfolio", {"owner_id":oid}),
    ("update_owner", {"owner_id":oid, "trust_note":"价格坚挺，可议价"}),
    ("exclusive_expiring", {"days":30}),
    ("get_property_owners", {"property_ids":[pid2]}),
    ("get_property_owners", {"property_ids":[pid]}),   # 该房源未录业主 => owner=None 不崩
    ("find_person_by_name", {"name":"测试房东"}),
    ("find_person_by_name", {"name":"测试客户张先生"}),
    ("find_person_by_name", {"name":"查无此人"}),
    # 数据清理（2026-09-23 加）：先预演再真删，只用专门的"待清理"种子，不动其它数据
    ("delete_property", {"property_id":purge_pid,"dry_run":True}),
    ("delete_customer", {"customer_id":purge_cid,"dry_run":True}),
    ("delete_property", {"property_id":purge_pid}),
    ("delete_customer", {"customer_id":purge_cid}),
    ("purge_data", {"kind":"all","dry_run":True}),
]

for name, args in CASES:
    results[name] = call(name, args)

# ---------- 2b. 定时任务落点校验（2026-09-21 加）----------
# enable_cron 必须把提醒建到**当前会话**上。旧用例自己传 chat_id，等于绕过了会话寻址，
# 真实飞书里报「无法确定推送会话」它也照绿。这里按真实形态复核一次落点。
from cron.jobs import list_jobs
call('enable_cron', {})
_found = {j.get('name'): j.get('deliver') for j in list_jobs(include_disabled=True)}
_expected = f'feishu:{SMOKE_CHAT_ID}'
if _found and all(v == _expected for v in _found.values()):
    print(f"[OK  ] 定时任务落点校验：{len(_found)} 条任务均指向当前会话")
else:
    print(f"[FAIL] 定时任务落点校验：{_found}（期望全部 {_expected}）")
call('disable_cron', {})

# ---------- 3. 汇总 ----------
covered = set(results.keys())
missing = [t for t in STATIC_TOOLS if t not in covered]
orphan  = [t for t in covered if t not in STATIC_TOOLS]
unregistered = [t for t in STATIC_TOOLS if registry.get_entry(t) is None]

print("=" * 70)
print(f"工具总数(静态清单): {len(STATIC_TOOLS)}  实际调用: {len(covered)}")
if missing: print(f"!! 静态清单里有但未测试: {missing}")
if orphan:  print(f"!! 测试了但不在静态清单: {orphan}")
if unregistered: print(f"!! 静态清单里无注册条目(模型看不到): {unregistered}")
print("-" * 70)
ok = err = exc = 0
for name in STATIC_TOOLS:
    if name not in results: continue
    status, payload = results[name]
    if status == 'OK': ok += 1
    elif status == 'ERR': err += 1; print(f"[{status}] {name} -> success=False: {json.dumps(payload, ensure_ascii=False)[:160]}")
    elif status == 'EXC': exc += 1; print(f"[EXC ] {name} -> 异常:\n{payload}")
    elif status == 'NO_ENTRY': print(f"[MISS] {name} 未注册")
    else: print(f"[RAW ] {name} -> {payload}")
print("-" * 70)
print(f"✅ 正常返回 success=true : {ok}")
print(f"⚠️  返回 success=false   : {err}（工具跑了但业务上没走通，需看原因）")
print(f"❌ 抛异常崩溃            : {exc}")
print(f"✅ 合计通过(OK+ERR 无崩溃): {ok+err}/{len(STATIC_TOOLS)}")
