"""
房产助理 - 数据库模块
Coco（可可）的底层数据存储
"""
import os
import json
import re
import logging
from datetime import datetime, timedelta
from functools import lru_cache
from agent.real_estate_input import (birthday_matches_month_day, clean_tags, customer_type_filter,
                                     norm_money, norm_phone, norm_tags)
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Float, Numeric, BigInteger,
    DateTime, ForeignKey, CheckConstraint, Index, or_, func
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.types import TypeDecorator
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

Base = declarative_base()

# ==================== 敏感字段加密 ====================

def _get_cipher():
    """获取 Fernet 加密器；未配置 COCO_ENC_KEY 时返回 None（明文模式，兼容旧数据）"""
    key = os.getenv('COCO_ENC_KEY', '')
    if not key:
        return None
    try:
        return Fernet(key.encode('utf-8'))
    except Exception:
        return None


class EncryptedString(TypeDecorator):
    """加密字符串类型：配置密钥时自动加密存储，读取时自动解密
    
    无密钥时以明文存取（兼容未加密的旧数据）；解密失败时返回原值（兼容历史明文）。
    """
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        cipher = _get_cipher()
        if cipher is None:
            return value
        return cipher.encrypt(value.encode('utf-8')).decode('utf-8')

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        cipher = _get_cipher()
        if cipher is None:
            return value
        try:
            return cipher.decrypt(value.encode('utf-8')).decode('utf-8')
        except Exception:
            return value  # 旧明文数据原样返回

    def _coerce_compared_value(self, op, value):
        return Text()


# ==================== 房源标题归一化与身份解析（防重复录入） ====================
# 2026-09-21 重做判重（真实事故：同一套房换个写法就会重复建档 —— 只比"整串标题是否完全相同"
# 太脆，"海口美兰区海甸岛恒大美丽沙 3 号楼 1 单元 1602" 与 "恒大美丽沙3栋1单元1602" 判不出来）。
# 现在把标题解析成这套房的**身份要素**再逐项比对：小区主体 + 期数 + 楼栋 + 单元 + 房号 + 面积。
_GEO_TOKENS = ["海口市", "海口", "海南", "美兰区", "龙华区", "秀英区", "琼山区", "桂林洋开发区", "桂林洋"]
# 结构解析用的分隔符（统一成空格，**保留数字边界**）：早期版本先删空格，把 "1802 1 室" 粘成 18021
# → 房号解析成 8021，导致同一套房判成两套（2026-09-21 真实事故：同一套出租房被重发时重复建档）
_SEP_RE = re.compile(r"[\s,，、.。;；:：\-_]+")
# 比对小区名/整串时用（此时 # 也可去掉）
_PUNCT_ALL = re.compile(r"[\s,，、.。;；:：\-_#＃]+")
_BUILDING_RE = re.compile(r"(\d{1,3})\s*(?:号)?\s*(?:楼|栋|幢|座)|(\d{1,3})\s*[#＃]")
_UNIT_RE = re.compile(r"([一二三四五六七八九十\d]{1,2})\s*单元")
_PHASE_RE = re.compile(r"第?([一二三四五六七八九十\d]{1,2})期")
_FLOOR_RE = re.compile(r"\d{1,3}\s*层|\d{1,3}\s*/\s*\d{1,3}\s*层?")
# 首选判据：紧跟在「单元/号楼/栋」后面的 3~4 位数就是房号（最稳，标题尾巴里的数字干扰不到）
_ROOM_AFTER_MARKER_RE = re.compile(r"(?:单元|号楼|楼|栋|幢|座)\s*(\d{3,4}[A-Za-z]?)(?![\dA-Za-z])")
# 兜底判据：3~4 位数字（可带字母后缀，如 1602A），前后一位不能是数字/字母
_ROOM_RE = re.compile(r"(?<!\d)(\d{3,4}[A-Za-z]?)(?:室|房)?(?![\dA-Za-z])")
# 房号后面跟着这些字 → 是面积/价格/楼层/厅卫数，不是房号
_ROOM_TAIL_BLOCK = set("平㎡米万元%年天层楼栋幢座厅卫")
# 房号前面是这些 → 是价格/面积/费用，不是房号
_ROOM_PREFIX_BLOCK = ("租", "价", "费", "面积", "总价")
# 中文数字（「4栋一单元」里的「一」）→ 数字，否则「一单元」与「1单元」会被当成不同单元
_CN_NUM = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
           "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
# 面积同一口径的容差（㎡）：128 与 128.5 是同一套房的两种说法；差 2 ㎡ 以上视为不同房源
AREA_TOLERANCE = 1.0


# 房源类型的中文名（提示文案用）
_PROPERTY_TYPE_LABEL = {"new": "一手房", "second_hand": "二手房", "rental": "出租"}


KEY_MISMATCH_HINT = "读取失败（密钥不一致，请检查备份的密钥文件）"


def looks_like_ciphertext(value) -> bool:
    """值看起来是 Fernet 密文 —— 密钥不一致时解密失败会把密文原样返回，
    这种值绝不能被当成联系方式展示给经纪人（会看到一串乱码）。"""
    if not isinstance(value, str):
        return False
    v = value.strip()
    return len(v) >= 40 and v.startswith("gAAAA") and all(ch.isalnum() or ch in "-_=" for ch in v)


def _name_substring_filter(session, column, text):
    """姓名子串匹配的过滤条件：SQLite/MySQL 用 `instr()`、PostgreSQL 用 `strpos()`。

    为什么不走 LIKE（2026-09-25 拍板）：`%`/`_` 在 LIKE 里是通配符，模型或经纪人随手传一个
    `%` 就能把**全库名单**捞出来（实测 2 位客户 + 2 位业主全命中，大库上就是上万条、8MB）。
    位置函数天然没有通配符语义，读到什么字就按什么字匹配。
    """
    from sqlalchemy import func
    try:
        dialect = session.get_bind().dialect.name
    except Exception:
        dialect = 'sqlite'
    if dialect in ('postgresql', 'postgres'):
        return func.strpos(column, text) > 0
    return func.instr(column, text) > 0


def _change_trace_value(model, field, value):
    """变更留痕里的取值：加密字段只留掩码。

    历史表 `re_customer_changes` 是明文的，而 phone/wechat 在主表里是密文 ——
    把明文号码写进留痕等于让加密白做（备份、导出、看库的人直接读到）。
    """
    if value is None:
        return None
    text = str(value)
    try:
        column = model.__table__.columns.get(field)
    except Exception:
        column = None
    if column is None or not isinstance(column.type, EncryptedString):
        return text
    # 手机号：留前 3 后 4（139****0002，便于核对是哪一位）；短号/微信号：只留前 2 位
    if text.isdigit() and len(text) >= 8:
        return f"{text[:3]}{'*' * 4}{text[-4:]}"
    return f"{text[:2]}{'*' * 4}"


def _only_human_followups(query):
    """只算「经纪人/客户真实互动」的跟进记录（排除系统自动生成的那两条）。

    带看「档 3」（2026-09-25）在带看完成后自动写两条跟进：`type='visit'` 的带看跟进 +
    `type='reminder'` 的回访提醒，两条都带 `source_viewing_id`（迁移 014 的幂等键）。
    它们**不代表经纪人联系过客户**，所以「客户是否被冷落」这一族判定必须把它们排除 ——
    否则带看后没人跟的客户永远不进流失名单、流失预警的「带看后无跟进」信号在生产上永不触发
    （2026-09-26 实测：带看完成的客户风险分 None、不在流失名单；删掉两条自动记录后立刻 60 分 + 该信号）。

    **逾期一族不适用本函数**（`get_overdue` / 逾期计数 / cron 逾期推送 / 带看侧 `_prior_overdue`）：
    那边算的是「哪件待办到点了」，带看自动建的回访提醒正是它要送达的待办（档 3 的回访机制靠它）。
    """
    return query.filter(Followup.source_viewing_id.is_(None))


def _as_money(value, default):
    """把库里的金额读成数字。

    历史脏数据（预算存成「300万」这类文本）也要能读出来 —— 老数据里就可能有，
    读不出就按默认值：绝不让一条脏数据把整批匹配打崩。
    """
    if value is None:
        return default
    n = norm_money(value)
    return default if n is None else n


def _deal_kind(property_type):
    """判重归组（2026-09-24）：同一套房可以既卖又租，但**不可能既是新房又是二手房** ——
    所以出租单算一类，一手房与二手房同属「出售」一类；判重只在同一类内部进行。"""
    return "rental" if (property_type or "second_hand") == "rental" else "sell"


def _normalize_title(t):
    """去地理前缀 + 空白/标点（旧的"整串完全相同"判据，继续作为兜底保留）"""
    if not t:
        return ""
    s = t
    for tok in _GEO_TOKENS:
        s = s.replace(tok, "")
    return _PUNCT_ALL.sub("", s)


def parse_property_identity(title):
    """把房源标题解析成身份要素：{community, phase, building, unit, room}

    解析不出来的一律留空（留空 = 该项不参与比对，绝不臆造）。
    小区名只取**房号段之前**的部分——房号后面的「1室1厅精装出租」这类描述不算小区名。
    """
    t = title or ""
    for tok in _GEO_TOKENS:
        t = t.replace(tok, "")
    t = _SEP_RE.sub(" ", t)                # 分隔符→空格：数字之间保留边界
    nofloor = _FLOOR_RE.sub(" ", t)        # 去掉「16层」这类楼层词，别混进小区名/房号
    b = _BUILDING_RE.search(nofloor)
    building = next((g for g in (b.groups() if b else ()) if g), None)
    u = _UNIT_RE.search(nofloor)
    unit = _CN_NUM.get(u.group(1), u.group(1)) if u else None
    ph = _PHASE_RE.search(nofloor)
    phase = _CN_NUM.get(ph.group(1), ph.group(1)) if ph else None

    room_match = _ROOM_AFTER_MARKER_RE.search(nofloor)
    if room_match is None:                 # 标题没写「X单元/X号楼」时才走兜底
        consumed = [(m.start(), m.end()) for m in (b, u) if m]
        for m in reversed(list(_ROOM_RE.finditer(nofloor))):
            if any(s <= m.start() < e for s, e in consumed):
                continue
            after = nofloor[m.end():].lstrip()[:1]
            if after and after in _ROOM_TAIL_BLOCK:
                continue                    # 后面跟着 平/㎡/万/元/月租/层/楼/厅/卫 → 不是房号
            if any(k in nofloor[max(0, m.start() - 3):m.start()] for k in _ROOM_PREFIX_BLOCK):
                continue                    # 前面是 租/价/费/面积 → 是价格或面积，不是房号
            room_match = m
            break
    room = room_match.group(1) if room_match else None

    spans = [m for m in (b, u, room_match) if m]
    community = nofloor[:min(m.start() for m in spans)] if spans else nofloor
    return {"community": _PUNCT_ALL.sub("", community), "phase": phase,
            "building": building, "unit": unit, "room": room}


def _bigrams(s):
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def community_relation(a, b):
    """小区主体的关系：same / unclear / different（容忍写法差异：包含关系 + 二字切分相似度）"""
    if not a or not b:
        return "unclear"
    if a == b:
        return "same"
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 3 and short in long_:
        return "same"
    jac = len(_bigrams(a) & _bigrams(b)) / max(1, len(_bigrams(a) | _bigrams(b)))
    if jac >= 0.6:
        return "same"
    return "unclear" if jac >= 0.3 else "different"


def _area_close(a, b, tol=AREA_TOLERANCE):
    try:
        return abs(float(a or 0) - float(b or 0)) <= tol
    except (TypeError, ValueError):
        return False


def same_property_identity(a, b):
    """两个身份要素是否指向同一套房：房号两边都有且相同；期数/楼栋/单元明确冲突即不同"""
    if not (a.get("room") and b.get("room")):
        return False
    if a["room"] != b["room"] or a["phase"] != b["phase"]:
        return False
    for key in ("building", "unit"):
        if a.get(key) and b.get(key) and a[key] != b[key]:
            return False
    return community_relation(a["community"], b["community"]) != "different"


def suspected_same_property_identity(a, b):
    """疑似同一套（只提示不拦）：小区同一个、期数/楼栋/单元不冲突，但房号不全"""
    if a.get("room") and b.get("room"):
        return False                      # 两边房号都有 → 交给 same_property_identity 严格判定
    if a["phase"] != b["phase"]:
        return False
    for key in ("building", "unit"):
        if a.get(key) and b.get(key) and a[key] != b[key]:
            return False
    return community_relation(a["community"], b["community"]) == "same"


# ==================== 合并式去重（2026-09-21 加） ====================
# 去重不只会"删多余那条"：被删项常有保留项没有的信息（业主/图片/租客要求/朝向…）。
# 合并时**只补空缺、绝不覆盖**（两边都是历史数据，互相冲会丢信息）。
_PROPERTY_MERGE_FIELDS = (
    "community", "district", "address", "rooms", "halls", "bathrooms", "floor",
    "orientation", "renovation", "year_built", "has_elevator", "parking",
    "tenant_requirements", "viewing_note", "defect_tags",
)


def _is_blank(v):
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (int, float)):
        return v == 0
    return False


def _property_richness(p):
    """信息完整度打分：挑保留项时"信息最全"用的（业主/图片/租客要求权重更高）"""
    score = 0
    if getattr(p, "owner_id", None):
        score += 3
    if (getattr(p, "images", "") or "").strip():
        score += 2
    if (getattr(p, "tenant_requirements", "") or "").strip():
        score += 2
    for f in _PROPERTY_MERGE_FIELDS:
        if not _is_blank(getattr(p, f, None)):
            score += 1
    return score


def _extra_csv_items(keep_value, drop_value):
    """drop 比 keep 多出来的逗号分隔项（图片/标签用），返回逗号串或空串"""
    keep_items = [x.strip() for x in (keep_value or "").split(",") if x.strip()]
    drop_items = [x.strip() for x in (drop_value or "").split(",") if x.strip()]
    extra = [x for x in drop_items if x not in keep_items]
    return ",".join(extra)


# ==================== 数据模型 ====================

# 每日早报里最多列几条「今天要跟进」（其余用计数说明，不再堆满整个早报）
TODAY_TASK_LIMIT = 10


class Customer(Base):
    """客户表"""
    __tablename__ = 're_customers'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    phone = Column(EncryptedString)
    wechat = Column(EncryptedString)
    feishu_id = Column(String(100))
    tier = Column(String(1), default='C')
    budget_min = Column(Integer)  # 预算下限（元，如 300万=3000000）
    budget_max = Column(Integer)  # 预算上限（元）
    area_pref = Column(String(50))
    layout_pref = Column(String(50))
    location = Column(String(200))
    renovation = Column(String(50))
    notes = Column(Text)
    tags = Column(Text)
    source = Column(String(100))
    customer_type = Column(String(20), default="unspecified")  # buy_new/buy_second_hand/rent；unspecified=未细分（历史值 'buy' 同属这一档）
    birthday = Column(String(10))  # YYYY-MM-DD
    stage = Column(String(20), default='lead')  # 生命周期阶段（2026-08-28 功能5）
    status = Column(String(20), default='active')
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    followups = relationship("Followup", back_populates="customer")
    viewings = relationship("Viewing", back_populates="customer")
    deals = relationship("Deal", back_populates="customer")
    changes = relationship("CustomerChange", back_populates="customer")
    
    __table_args__ = (
        CheckConstraint("tier IN ('S', 'A', 'B', 'C')", name='re_check_tier'),
        Index('re_idx_customer_tier', 'tier'),
        Index('re_idx_customer_status', 'status'),
    )
    
    def to_dict(self):
        return {
            'id': self.id, 'name': self.name, 'phone': self.phone,
            'wechat': self.wechat, 'feishu_id': self.feishu_id,
            'tier': self.tier, 'budget_min': self.budget_min,
            'budget_max': self.budget_max, 'area_pref': self.area_pref,
            'layout_pref': self.layout_pref, 'location': self.location,
            'renovation': self.renovation, 'notes': self.notes, 'tags': self.tags,
            'source': self.source, 'customer_type': self.customer_type, 'status': self.status,
            'birthday': self.birthday, 'stage': self.stage,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Property(Base):
    """房源表"""
    __tablename__ = 're_properties'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    community = Column(String(100))
    district = Column(String(100))
    address = Column(String(300))
    price = Column(BigInteger, nullable=False)  # 元（二手房总价如 4000000，出租月租如 1000）
    area = Column(Float, nullable=False)
    rooms = Column(Integer)
    halls = Column(Integer)
    bathrooms = Column(Integer)
    floor = Column(String(50))
    orientation = Column(String(50))
    renovation = Column(String(50))
    year_built = Column(Integer)
    has_elevator = Column(Integer)  # 1=有 / 0=无 / NULL=还没确认（别再默认成"有"）
    property_type = Column(String(20), default="second_hand")  # new/second_hand/rental
    parking = Column(Integer, default=0)
    tags = Column(Text)
    images = Column(Text)
    defect_tags = Column(Text)  # 缺陷标签（带看反馈反哺，2026-08-28 功能3）
    defect_baseline_at = Column(DateTime)  # 整改基线：手动清除标签时刷新，重扫只算之后的新反馈（迁移 015）
    owner_id = Column(Integer, ForeignKey('re_owners.id'))  # 房东（2026-08-28 功能7）
    commission_rate = Column(Float)          # 佣金率（如 0.02 = 2%）
    exclusive_until = Column(DateTime)       # 独家委托到期日
    viewing_note = Column(String(200))       # 看房方式（钥匙/需预约等）
    tenant_requirements = Column(Text)       # 出租房源的租客要求（不吸烟/办居住证/学生优先等，2026-08-29 加）
    status = Column(String(20), default='available')
    agent_id = Column(String(100))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    followups = relationship("Followup", back_populates="property")
    viewings = relationship("Viewing", back_populates="property")
    deals = relationship("Deal", back_populates="property")
    
    __table_args__ = (
        CheckConstraint("status IN ('available', 'sold', 'rented')", name='re_check_prop_status'),
        Index('re_idx_prop_status', 'status'),
        Index('re_idx_prop_price', 'price'),
        Index('re_idx_prop_district', 'district'),
    )
    
    def unit_price_value(self):
        """单价（元/㎡；出租为 元/㎡/月）：按当前 总价÷面积 现算，保留两位小数。

        单价不再存列（历史 unit_price 列已由迁移 009 删除），避免"录入后改价/改面积不重算"
        造成展示层拿到过期值或空值。面积缺失/为 0 时返回 None（无法计算）。
        """
        try:
            price = float(self.price) if self.price is not None else None
            area = float(self.area) if self.area is not None else None
        except (TypeError, ValueError):
            return None
        if not price or not area or area <= 0:
            return None
        return round(price / area, 2)

    def to_dict(self):
        return {
            'id': self.id, 'title': self.title, 'community': self.community,
            'district': self.district, 'address': self.address,
            # price 列读出为 Decimal，必须转 float 否则 json.dumps 崩溃
            'price': float(self.price) if self.price is not None else None,
            'unit_price': self.unit_price_value(),
            'area': self.area, 'rooms': self.rooms, 'halls': self.halls,
            'bathrooms': self.bathrooms, 'floor': self.floor,
            'orientation': self.orientation, 'renovation': self.renovation,
            'year_built': self.year_built, 'has_elevator': self.has_elevator, 'property_type': self.property_type,
            'parking': self.parking, 'tags': self.tags, 'images': self.images,
            'defect_tags': self.defect_tags,
            'tenant_requirements': self.tenant_requirements,
            'owner_id': self.owner_id, 'commission_rate': self.commission_rate,
            'exclusive_until': self.exclusive_until.isoformat() if self.exclusive_until else None,
            'viewing_note': self.viewing_note,
            'status': self.status, 'agent_id': self.agent_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class PriceHistory(Base):
    """房源调价历史表（2026-08-28 功能1）"""
    __tablename__ = 're_price_history'

    id = Column(Integer, primary_key=True, autoincrement=True)
    property_id = Column(Integer, ForeignKey('re_properties.id'), nullable=False)
    old_price = Column(BigInteger)
    new_price = Column(BigInteger, nullable=False)
    change_reason = Column(String(200))
    created_at = Column(DateTime, default=datetime.now)

    property = relationship("Property")

    __table_args__ = (
        Index('re_idx_price_history_prop', 'property_id'),
        Index('re_idx_price_history_created', 'created_at'),
    )

    def to_dict(self):
        return {
            'id': self.id, 'property_id': self.property_id,
            'old_price': self.old_price, 'new_price': self.new_price,
            'change': (self.new_price - self.old_price) if self.old_price else None,
            'reason': self.change_reason,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Owner(Base):
    """房东（业主）表（2026-08-28 功能7）"""
    __tablename__ = 're_owners'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    phone = Column(EncryptedString)
    wechat = Column(EncryptedString)
    id_masked = Column(String(30))     # 脱敏身份证（如 4600**********1234）
    trust_note = Column(String(200))   # 信任度备注
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.now)

    properties = relationship("Property")
    changes = relationship("OwnerChange", back_populates="owner")

    def to_dict(self):
        return {
            'id': self.id, 'name': self.name,
            'phone': self.phone, 'wechat': self.wechat,
            'id_masked': self.id_masked, 'trust_note': self.trust_note,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Referral(Base):
    """转介绍表（2026-08-28 功能6）"""
    __tablename__ = 're_referrals'

    id = Column(Integer, primary_key=True, autoincrement=True)
    referrer_customer_id = Column(Integer, ForeignKey('re_customers.id'), nullable=False)
    referred_customer_id = Column(Integer, ForeignKey('re_customers.id'))
    referred_name = Column(String(100), nullable=False)
    referred_phone = Column(EncryptedString)
    status = Column(String(20), default='registered')  # registered/contacted/viewing/dealing/dealed
    reward_note = Column(String(200))                  # 酬谢备注
    created_at = Column(DateTime, default=datetime.now)

    referrer = relationship("Customer", foreign_keys=[referrer_customer_id])

    __table_args__ = (
        Index('re_idx_referral_referrer', 'referrer_customer_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'referrer_customer_id': self.referrer_customer_id,
            'referrer_name': self.referrer.name if self.referrer else None,
            'referred_customer_id': self.referred_customer_id,
            'referred_name': self.referred_name,
            'referred_phone': self.referred_phone,
            'status': self.status, 'reward_note': self.reward_note,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Followup(Base):
    """跟进记录表"""
    __tablename__ = 're_followups'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey('re_customers.id'))
    property_id = Column(Integer, ForeignKey('re_properties.id'))
    type = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    next_date = Column(DateTime)
    next_time = Column(String(10))
    agent_id = Column(String(100))
    # 这条提醒来自哪一次带看（带看完成自动回访提醒的不重复建用；迁移 014）
    source_viewing_id = Column(Integer, ForeignKey('re_viewings.id'))
    created_at = Column(DateTime, default=datetime.now)
    
    customer = relationship("Customer", back_populates="followups")
    property = relationship("Property", back_populates="followups")
    
    __table_args__ = (
        Index('re_idx_followup_customer', 'customer_id'),
        Index('re_idx_followup_next_date', 'next_date'),
    )
    
    def to_dict(self):
        return {
            'id': self.id, 'customer_id': self.customer_id,
            'property_id': self.property_id, 'type': self.type,
            'content': self.content,
            'next_date': self.next_date.isoformat() if self.next_date else None,
            'next_time': self.next_time, 'agent_id': self.agent_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Reminder(Base):
    """提醒任务表"""
    __tablename__ = 're_reminders'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(50), nullable=False)
    cron_expr = Column(String(100))
    target_chat = Column(String(100))
    content = Column(Text)
    enabled = Column(Integer, default=1)
    last_run = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    
    __table_args__ = (
        Index('re_idx_reminder_type', 'type'),
        Index('re_idx_reminder_enabled', 'enabled'),
    )


class OwnerChange(Base):
    """房东资料变更留痕（2026-09-25 加，配合 update_owner）

    与客户侧的 `re_customer_changes` 同一口径：加密字段（手机号/微信）**只留掩码**，
    否则加密等于白做（备份、导出、看库的人都能读到明文）。
    """
    __tablename__ = 're_owner_changes'

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(Integer, ForeignKey('re_owners.id'))
    field = Column(String(50), nullable=False)
    old_value = Column(Text)
    new_value = Column(Text)
    created_at = Column(DateTime, default=datetime.now)

    owner = relationship("Owner", back_populates="changes")

    __table_args__ = (
        Index('re_idx_owner_change_owner', 'owner_id'),
    )

    def to_dict(self):
        return {
            'id': self.id, 'owner_id': self.owner_id, 'field': self.field,
            'old_value': self.old_value, 'new_value': self.new_value,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Setting(Base):
    """经纪人配置表（key/value，2026-08-12 加）

    存经纪人品牌/公司名等非结构化配置：key 唯一，value 字符串。
    目前唯一 key: brand_name（海报品牌展示）。
    """
    __tablename__ = 're_settings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), nullable=False, unique=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class Viewing(Base):
    """带看记录表"""
    __tablename__ = 're_viewings'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey('re_customers.id'), nullable=False)
    property_id = Column(Integer, ForeignKey('re_properties.id'), nullable=False)
    viewing_time = Column(DateTime, nullable=False)       # 带看时间
    status = Column(String(20), default='scheduled')      # scheduled/done/cancelled
    feedback = Column(Text)                               # 客户反馈
    result = Column(String(20))                           # interested/not_interested/pending
    agent_id = Column(String(100))
    created_at = Column(DateTime, default=datetime.now)
    
    customer = relationship("Customer", back_populates="viewings")
    property = relationship("Property", back_populates="viewings")
    
    __table_args__ = (
        Index('re_idx_viewing_customer', 'customer_id'),
        Index('re_idx_viewing_property', 'property_id'),
        Index('re_idx_viewing_time', 'viewing_time'),
    )
    
    def to_dict(self):
        return {
            'id': self.id, 'customer_id': self.customer_id,
            'property_id': self.property_id,
            'viewing_time': self.viewing_time.isoformat() if self.viewing_time else None,
            'status': self.status, 'feedback': self.feedback,
            'result': self.result, 'agent_id': self.agent_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'customer_name': self.customer.name if self.customer else None,
            'property_title': self.property.title if self.property else None,
        }


class Deal(Base):
    """成交/交易状态表"""
    __tablename__ = 're_deals'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey('re_customers.id'), nullable=False)
    property_id = Column(Integer, ForeignKey('re_properties.id'), nullable=False)
    stage = Column(String(20), default='deposit')  # deposit/signing/loan/transfer/finalized
    price = Column(Integer)                         # 成交价（元，如 400万=4000000）
    deposit_amount = Column(Integer)                # 定金（元）
    deposit_date = Column(DateTime)                 # 定金日期
    signing_date = Column(DateTime)                 # 签约日期
    loan_date = Column(DateTime)                    # 贷款审批日期
    transfer_date = Column(DateTime)                # 过户日期
    finalize_date = Column(DateTime)                # 交房日期
    notes = Column(Text)
    agent_id = Column(String(100))
    referral_id = Column(Integer, ForeignKey('re_referrals.id'))  # 转介绍来源（2026-08-28 功能6）
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    customer = relationship("Customer", back_populates="deals")
    property = relationship("Property", back_populates="deals")
    
    __table_args__ = (
        CheckConstraint("stage IN ('deposit', 'signing', 'loan', 'transfer', 'finalized')", name='re_check_deal_stage'),
        Index('re_idx_deal_customer', 'customer_id'),
        Index('re_idx_deal_stage', 'stage'),
    )
    
    def to_dict(self):
        return {
            'id': self.id, 'customer_id': self.customer_id,
            'property_id': self.property_id, 'stage': self.stage,
            'price': self.price, 'deposit_amount': self.deposit_amount,
            'deposit_date': self.deposit_date.isoformat() if self.deposit_date else None,
            'signing_date': self.signing_date.isoformat() if self.signing_date else None,
            'loan_date': self.loan_date.isoformat() if self.loan_date else None,
            'transfer_date': self.transfer_date.isoformat() if self.transfer_date else None,
            'finalize_date': self.finalize_date.isoformat() if self.finalize_date else None,
            'notes': self.notes, 'agent_id': self.agent_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'customer_name': self.customer.name if self.customer else None,
            'property_title': self.property.title if self.property else None,
        }


class Script(Base):
    """自定义话术表"""
    __tablename__ = 're_scripts'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)       # 话术名称
    scenario = Column(String(50), default='custom')  # greeting/objection_handling/closing/follow_up/custom
    content = Column(Text, nullable=False)           # 话术内容
    agent_id = Column(String(100))
    created_at = Column(DateTime, default=datetime.now)
    
    __table_args__ = (
        Index('re_idx_script_scenario', 'scenario'),
        Index('re_idx_script_name', 'name'),
    )


class CustomerChange(Base):
    """客户需求变更历史表（预算/区域/户型等字段变更留痕）"""
    __tablename__ = 're_customer_changes'

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey('re_customers.id'), nullable=False)
    field = Column(String(50), nullable=False)   # 变更字段：budget_min/budget_max/location/tier...
    old_value = Column(String(200))
    new_value = Column(String(200))
    created_at = Column(DateTime, default=datetime.now)

    customer = relationship("Customer", back_populates="changes")

    __table_args__ = (
        Index('re_idx_change_customer', 'customer_id'),
    )

    def to_dict(self):
        return {
            'id': self.id, 'customer_id': self.customer_id,
            'field': self.field, 'old_value': self.old_value,
            'new_value': self.new_value,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# ==================== 数据库管理 ====================

@lru_cache(maxsize=8192)
def _norm_district_cached(value):
    """区域名归一化的结果缓存（同一批字符串在大匹配里被反复计算上百万次）。

    2026-09-18 加：6 万套房 × 20 位客户的批量匹配原需 71 秒，瓶颈就是这里的正则重复执行。
    值域只有几百个不同字符串，缓存后命中率接近 100%。
    """
    return RealEstateDB._norm_district(value)


# ==================== 客户来源（渠道）归一（2026-09-26 F350） ====================
# 只做**统计侧**归并：同一个渠道的不同写法在渠道统计里合并成一行（实测「贝壳找房」与「贝壳」原先
# 各占一行、各 100% 成交率）。**不改写入**（录入照原样存经纪人说的写法），**也不猜** ——
# 表里没有的写法一律原样成一行，由上层如实说明"这几种写法没合并"。
CHANNEL_ALIASES = {
    # 贝壳
    '贝壳': '贝壳', '贝壳找房': '贝壳', '贝壳网': '贝壳', '贝壳app': '贝壳', 'beike': '贝壳',
    # 安居客
    '安居客': '安居客', '安居客网': '安居客', '安居客app': '安居客', 'anjuke': '安居客',
    # 抖音
    '抖音': '抖音', '抖音短视频': '抖音', '抖音号': '抖音', '抖音直播': '抖音', 'douyin': '抖音',
    # 58
    '58': '58', '58同城': '58', '58网': '58', '58.com': '58',
    # 转介绍
    '转介绍': '转介绍', '老客户介绍': '转介绍', '客户介绍': '转介绍', '推荐': '转介绍',
    '转介': '转介绍', '老带新': '转介绍',
    # 门店
    '门店': '门店', '门店到访': '门店', '上门': '门店', '自然到访': '门店', '进店': '门店',
}
UNSPECIFIED_SOURCE = '未填写'


def normalize_source(value):
    """客户来源（渠道）归并 → 渠道名。空值算「未填写」；表里没有的写法**原样返回**（不猜）。"""
    raw = (value or '').strip()
    if not raw:
        return UNSPECIFIED_SOURCE
    return CHANNEL_ALIASES.get(raw) or CHANNEL_ALIASES.get(raw.lower()) or raw


class RealEstateDB:
    """Coco 的数据库"""
    
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url, echo=False)
        self.SessionLocal = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
    
    def get_session(self):
        return self.SessionLocal()
    
    # ---------- 客户 ----------
    def find_duplicate_customer(self, phone=None, wechat=None, name=None, customer_type=None, exclude_id=None):
        """客户查重：手机号(解密后,主) > 微信(次) > 姓名+客户类型(兜底)。返回 (命中客户dict或None, 警告文本或None)。

        手机号/微信是 EncryptedString，读取时自动解密，此处比较的是明文。
        手机号按写法归一再比（带空格、带横线、加国际区号前缀的写法都算同一个号）。
        防御：若读出的值疑似密文（密钥不一致/错配），不强行判重（避免误报/误合并），
        返回 warning 提示先检查 COCO_ENC_KEY。
        """
        with self.get_session() as s:
            cands = [c.to_dict() for c in s.query(Customer).all()]
        warning = None

        if phone:
            probe = norm_phone(phone)
            for c in cands:
                v = c.get('phone')
                if v is None:
                    continue
                # 疑似密文（密钥不一致）：绝不当成联系方式比较，也不强行判重
                #（2026-09-24 修：原来的"含字母就当密文"启发式会把正常微信号/含字母的联系方式误判成密文，
                #  导致库里一旦有微信客户，后续带微信的建档全被拦死）
                if looks_like_ciphertext(v):
                    warning = ("检测到本机加密密钥与库里的不一致，未强行判重"
                               "（可能把同一个人拆成两条、或把两个人合错）。请先检查加密密钥（COCO_ENC_KEY）。")
                    continue
                if norm_phone(v) == probe:
                    if exclude_id is None or c['id'] != exclude_id:
                        return (c, warning)
            return (None, warning)

        if wechat:
            probe = str(wechat).strip()
            for c in cands:
                v = c.get('wechat')
                if v is None:
                    continue
                if looks_like_ciphertext(v):
                    warning = warning or ("检测到本机加密密钥与库里的不一致，未强行判重"
                                          "（可能把同一个人拆成两条、或把两个人合错）。请先检查加密密钥（COCO_ENC_KEY）。")
                    continue
                if str(v).strip() == probe:
                    if exclude_id is None or c['id'] != exclude_id:
                        return (c, warning)
            return (None, warning)

        if name:
            values = customer_type_filter(customer_type)
            for c in cands:
                # 类型兜底判重按"筛选口径"比：未细分（不传类型/历史 'buy'）算同一档
                if c.get('name') == name and (values is None or (c.get('customer_type') or None) in values):
                    if exclude_id is None or c['id'] != exclude_id:
                        return (c, warning)
        return (None, warning)

    def add_customer(self, **kwargs):
        with self.get_session() as s:
            c = Customer(**kwargs)
            s.add(c); s.commit(); s.refresh(c)
            return c.to_dict()
    
    def update_customer(self, cid, **kwargs):
        with self.get_session() as s:
            c = s.query(Customer).get(cid)
            if not c: return None
            for k, v in kwargs.items():
                if hasattr(c, k):
                    old = getattr(c, k)
                    if old != v:
                        s.add(CustomerChange(
                            customer_id=cid, field=k,
                            old_value=_change_trace_value(Customer, k, old),
                            new_value=_change_trace_value(Customer, k, v),
                        ))
                    setattr(c, k, v)
            s.commit(); s.refresh(c)
            return c.to_dict()
    
    def get_customer_changes(self, customer_id, limit=20, with_total=False):
        """查询客户需求变更历史（预算/区域/户型等）

        2026-09-24 加 with_total=True 时返回 (rows, 变更总条数) —— 供上层区分"本次返回条数"
        与"一共改过多少次"（与 search_properties / list_customers 同一形状）。
        """
        with self.get_session() as s:
            q = (s.query(CustomerChange)
                 .filter(CustomerChange.customer_id == customer_id))
            total = q.count() if with_total else None
            rows = [ch.to_dict() for ch in q
                    .order_by(CustomerChange.created_at.desc(), CustomerChange.id.desc())
                    .limit(limit).all()]
            return (rows, total) if with_total else rows
    
    def get_customer(self, cid):
        with self.get_session() as s:
            c = s.query(Customer).get(cid)
            return c.to_dict() if c else None
    
    def list_customers(self, tier=None, status=None, customer_type=None, limit=50, include_closed=False,
                       with_total=False, tag=None):
        """列客户。默认只列在跟的（active 活跃 / paused 暂缓），**按最新录入优先**。

        2026-09-23 改：此前不传 status 会把已关闭(closed)的客户也列出来，导致"列客户"
        与"客户总数"越用越虚。要看已关闭客户，显式传 status='closed' 或 include_closed=True。
        2026-09-24 改：① 默认按 created_at 倒序（同秒按 id 倒序）—— 原先按主键升序，客户一多
        就只看得到最早录入的那批，"刚录的客户"在默认列表里根本看不见；② 加 with_total=True
        时返回 (rows, 符合条件总数)，供上层区分"本次返回条数"与"总数"（与 search_properties 同形状）。
        """
        with self.get_session() as s:
            q = s.query(Customer)
            if tier: q = q.filter(Customer.tier == tier)
            if status: q = q.filter(Customer.status == status)
            elif not include_closed: q = q.filter(Customer.status != 'closed')
            if customer_type:
                values = customer_type_filter(customer_type)
                if values is None:
                    return ([], 0) if with_total else []
                cols = [v for v in values if v]
                cond = Customer.customer_type.in_(cols)
                if None in values:
                    cond = or_(cond, Customer.customer_type.is_(None))
                q = q.filter(cond)
            ordered = lambda query: query.order_by(Customer.created_at.desc(), Customer.id.desc())
            if tag:
                # 按标签筛（2026-09-24 加，F75）：多个标签=同时包含；LIKE 只是粗筛
                #（'地铁房' 会命中 '近地铁房'），取出后按同一套分隔符精确比对
                wanted = [t for t in norm_tags(tag)[0]]
                if not wanted:
                    return ([], 0) if with_total else []
                for t in wanted:
                    q = q.filter(Customer.tags.contains(t, autoescape=True))
                candidates = [c.to_dict() for c in ordered(q).all()]
                matched = [c for c in candidates
                           if all(t in clean_tags(c.get('tags')) for t in wanted)]
                return (matched[:limit], len(matched)) if with_total else matched[:limit]
            total = q.count() if with_total else None
            rows = [c.to_dict() for c in ordered(q).limit(limit).all()]
            return (rows, total) if with_total else rows

    def get_birthday_customers(self, month=None, day=None):
        """查询指定月/日过生日的客户（用于生日提醒）

        生日库内有两种写法：YYYY-MM-DD 与 MM-DD（经纪人只记得月日），两种都要认，
        否则提醒会静默漏人。
        """
        with self.get_session() as s:
            q = s.query(Customer).filter(Customer.birthday.isnot(None), Customer.status == 'active')
            customers = [c.to_dict() for c in q.all()]
            if month is None and day is None:
                return customers
            return [c for c in customers
                    if birthday_matches_month_day(c.get('birthday'), month, day)]
    
    # ---------- 房源 ----------
    def add_property(self, **kwargs):
        with self.get_session() as s:
            p = Property(**kwargs)
            s.add(p); s.commit(); s.refresh(p)
            return p.to_dict()

    def update_property(self, pid, **kwargs):
        with self.get_session() as s:
            p = s.query(Property).get(pid)
            if not p: return None
            # 调价检测：价格变动自动写入历史（2026-08-28 功能1）
            old_price = p.price
            new_price = kwargs.get('price')
            price_changed = (new_price is not None
                             and old_price is not None
                             and int(new_price) != int(old_price))
            for k, v in kwargs.items():
                if hasattr(p, k): setattr(p, k, v)
            if price_changed:
                ph = PriceHistory(
                    property_id=pid,
                    old_price=int(old_price),
                    new_price=int(new_price),
                    change_reason=kwargs.get('price_change_reason'),
                )
                s.add(ph)
            s.commit(); s.refresh(p)
            d = p.to_dict()
            if price_changed:
                d['price_changed'] = {'old': int(old_price), 'new': int(new_price)}
            return d

    # ---------- 调价历史与反匹配（2026-08-28 功能1） ----------
    def get_price_history_summary(self, property_id):
        """调价总次数与累计变动（2026-09-24 加：原先工具层用"最近 N 条"来数、来加，
        超过返回条数时会报出偏小的次数与累计，模型照抄就说错话）"""
        with self.get_session() as s:
            rows = s.query(PriceHistory).filter(PriceHistory.property_id == property_id).all()
            total = len(rows)
            change = sum((r.new_price - r.old_price) for r in rows
                         if r.old_price is not None and r.new_price is not None)
            return {'count': total, 'change': change}

    def get_price_history(self, property_id, limit=20):
        """房源调价历史（新→旧）"""
        with self.get_session() as s:
            rows = s.query(PriceHistory).filter(
                PriceHistory.property_id == property_id
            ).order_by(PriceHistory.created_at.desc(), PriceHistory.id.desc()).limit(limit).all()
            return [{
                'id': r.id, 'property_id': r.property_id,
                'old_price': r.old_price, 'new_price': r.new_price,
                'change': (r.new_price - r.old_price) if r.old_price else None,
                'reason': r.change_reason,
                'created_at': r.created_at.isoformat() if r.created_at else None,
            } for r in rows]

    def customers_for_drop_pool(self):
        """降价反匹配的候选客户池（一次取出）：active + 填了预算上限 + 没有成交记录

        2026-09-24 加：原先对每套降价房源各查一次客户（N+1），50 套房 × 1000 客户要 30 秒。
        """
        with self.get_session() as s:
            rows = s.query(Customer).filter(Customer.status == 'active',
                                            Customer.budget_max.isnot(None)).all()
            deal_ids = {row[0] for row in s.query(Deal.customer_id).distinct().all()}
            pool = []
            for c in rows:
                if c.id in deal_ids:
                    continue
                bmax = norm_money(c.budget_max)
                if bmax is None:
                    continue    # 预算上限读不出（历史脏数据）：不参与降价反匹配，也不打崩整批
                pool.append({'customer_id': c.id, 'name': c.name, 'tier': c.tier,
                             'budget_min': norm_money(c.budget_min) or 0, 'budget_max': bmax,
                             'phone': c.phone})
            return pool

    def find_customers_for_price_drop(self, property_id, days=7, pool=None, limit=None):
        """降价反匹配：找"预算差一点够得着"的客户

        纳入条件：预算上限 >= 新价*0.95 且 < 旧价（就差一点），
                  排除已成交客户。
        返回按 budget_max 降序（最接近成交的在前）。
        """
        from datetime import datetime, timedelta
        since = datetime.now() - timedelta(days=days)
        with self.get_session() as s:
            drops = s.query(PriceHistory).filter(
                PriceHistory.property_id == property_id,
                PriceHistory.created_at >= since,
            ).order_by(PriceHistory.created_at.desc()).all()
            prop = s.query(Property).get(property_id)
            if not prop or not drops:
                return []
            latest = drops[0]  # 最近一次调价
            new_price = latest.new_price
            old_price = latest.old_price
            if old_price is None or new_price >= old_price:
                return []  # 只反匹配"降价"

            threshold = new_price * 0.95
            if pool is not None:
                # 用预取好的客户池在内存里筛（调用方一次取出，避免每套房各查一次数据库）
                rows = [c for c in pool
                        if (c.get('budget_max') or 0) >= threshold and (c.get('budget_max') or 0) < old_price]
            else:
                rows = s.query(Customer).filter(
                    Customer.status == 'active',
                    Customer.budget_max.isnot(None),
                    Customer.budget_max >= threshold,
                    Customer.budget_max < old_price,
                ).all()
                rows = [{'customer_id': c.id, 'name': c.name, 'tier': c.tier,
                         'budget_min': c.budget_min, 'budget_max': c.budget_max,
                         'phone': c.phone} for c in rows if not self.customer_has_deal(c.id)]
            result = []
            for c in rows:
                bmax = _as_money(c.get('budget_max'), 0)
                result.append({
                    'customer_id': c.get('customer_id'), 'name': c.get('name'), 'tier': c.get('tier'),
                    'budget_min': c.get('budget_min'), 'budget_max': bmax,
                    'phone': c.get('phone'),  # EncryptedString 自动加解密
                    'gap': int(old_price - bmax),  # 之前差多少
                    'now_affordable': bmax >= new_price,
                })
            result.sort(key=lambda x: x['budget_max'], reverse=True)
            return result[:limit] if limit else result

    # ---------- 生命周期阶段（2026-08-28 功能5） ----------
    VALID_STAGES = ['lead', 'interested', 'strong', 'viewed',
                    'negotiating', 'dealing', 'maintain', 'lost']

    def update_stage(self, cid, stage):
        """推进生命周期阶段，写变更历史；非法阶段报错"""
        if stage not in self.VALID_STAGES:
            raise ValueError(f"非法阶段: {stage}，可选 {self.VALID_STAGES}")
        return self.update_customer(cid, stage=stage)

    STAGE_TIMEOUT_RULES = {  # 阶段: (最大停留天数, 超时提示)
        'strong': (7, '强意向超7天未推进，建议安排带看'),
        'viewed': (14, '已看房超14天无动作，建议回访推进'),
        'negotiating': (7, '谈判超7天无进展，建议破冰或调整筹码'),
    }

    def stage_stagnation_report(self):
        """阶段滞留清单：strong/viewed/negotiating 停留超时的客户

        2026-09-25（F174b）：原先对**每一位客户**单独查一条阶段变更（1.2 万客户 = 12003 次 SQL ≈ 7.3 秒），
        改成**一次聚合**拿回"每位客户、每个阶段最近一次变更时间"再在内存里查表 —— 判定口径一行未改
        （有变更记录按变更时间、没有的按客户创建时间兜底）；顺带只取判断要用的四列，不再拉整行客户对象。
        """
        from datetime import datetime
        now = datetime.now()
        alerts = []
        with self.get_session() as s:
            from sqlalchemy import func
            latest_change = {(row[0], row[1]): row[2] for row in
                             s.query(CustomerChange.customer_id, CustomerChange.new_value,
                                     func.max(CustomerChange.created_at))
                             .filter(CustomerChange.field == 'stage')
                             .group_by(CustomerChange.customer_id, CustomerChange.new_value).all()}
            for stage, (max_days, hint) in self.STAGE_TIMEOUT_RULES.items():
                customers = (s.query(Customer.id, Customer.name, Customer.tier, Customer.created_at)
                             .filter(Customer.status == 'active', Customer.stage == stage).all())
                for cid, name, tier, created_at in customers:
                    # 该客户 stage 变为当前阶段的最近一次变更时间；没有变更记录（初始就是该阶段）用创建时间兜底
                    since = latest_change.get((cid, stage)) or created_at
                    if not since:
                        continue
                    days = (now - since).days
                    if days > max_days:
                        alerts.append({
                            'customer_id': cid, 'name': name, 'tier': tier,
                            'stage': stage, 'days_in_stage': days,
                            'hint': hint,
                        })
        alerts.sort(key=lambda x: x['days_in_stage'], reverse=True)
        return alerts

    # ---------- 流失预警（2026-08-28 功能2） ----------
    def churn_risk_customers(self, min_risk=40):
        """流失风险评分：>14天未联系+30 / >30天+50 / 带看后沉默+20 / S级×1.5

        「未联系」与「带看后沉默」都按**人为**跟进算 —— 带看档 3 自动写的带看跟进/回访提醒
        不算经纪人联系过客户（见 `_only_human_followups`）。

        返回 [{customer, risk_score, risk_level, signals, reason_hint}] 按
        风险降序；min_risk 过滤（默认40，即中危起步）。
        """
        from datetime import datetime, timedelta
        now = datetime.now()
        result = []
        with self.get_session() as s:
            from sqlalchemy import func
            # 2026-09-25（F177）：原先对**每一位客户**单独查 3~4 次（有无成交 / 最新跟进 / 带看数 / 带看后跟进数）
            # —— 1.2 万客户 = 36001 次 SQL ≈ 21 秒。这里改成四次聚合一次性拿回，**信号与权重一行未改**：
            #   ① 有成交的客户集合 ② 每客户最后跟进时间 ③ 每客户已完成带看数 ④ 每客户最后一次已完成带看时间
            customers = (s.query(Customer.id, Customer.name, Customer.tier, Customer.phone)
                         .filter(Customer.status == 'active').all())
            if not customers:      # 没有在跟客户就别去跑那四次聚合（空库/新装实例的快路径）
                return []
            dealt_ids = {row[0] for row in s.query(Deal.customer_id).distinct().all()
                         if row[0] is not None}
            # 「最后一次联系」= 最后一次**人为**跟进（排除带看档 3 自动写的两条，见 _only_human_followups）
            last_followup = {row[0]: row[1] for row in
                             _only_human_followups(
                                 s.query(Followup.customer_id, func.max(Followup.created_at))
                                 .filter(Followup.customer_id.isnot(None)))
                             .group_by(Followup.customer_id).all()}
            done_viewing_count = {row[0]: row[1] for row in
                                  s.query(Viewing.customer_id, func.count(Viewing.id))
                                  .filter(Viewing.status == 'done', Viewing.customer_id.isnot(None))
                                  .group_by(Viewing.customer_id).all()}
            last_done_viewing = {row[0]: row[1] for row in
                                 s.query(Viewing.customer_id, func.max(Viewing.created_at))
                                 .filter(Viewing.status == 'done', Viewing.customer_id.isnot(None))
                                 .group_by(Viewing.customer_id).all()}
            for cid, name, tier, phone in customers:
                if cid in dealt_ids:
                    continue
                signals = []
                score = 0
                # 信号1：最后跟进距今
                last_at = last_followup.get(cid)
                days_since = (now - last_at).days if last_at else None
                if days_since is None:
                    score += 40
                    signals.append("从未跟进")
                elif days_since > 30:
                    score += 50
                    signals.append(f"{days_since}天未联系")
                elif days_since > 14:
                    score += 30
                    signals.append(f"{days_since}天未联系")
                # 信号2：带看完成后零跟进（最后一次带看之后没有任何**人为**跟进 ——
                # 带看档 3 自动写的那两条不算，否则这个信号在生产上永不触发，见 _only_human_followups）
                done_viewings = done_viewing_count.get(cid, 0)
                if done_viewings > 0:
                    viewing_at = last_done_viewing.get(cid)
                    has_fp_after = bool(last_at and viewing_at and last_at >= viewing_at)
                    if not has_fp_after:
                        score += 20
                        signals.append("带看后无跟进")
                if not signals:
                    continue
                score = int(score * (1.5 if tier == 'S' else 1.0))
                if score < min_risk:
                    continue
                if days_since is not None and days_since > 30:
                    reason_hint = "winback_long_absence"
                elif done_viewings > 0:
                    reason_hint = "winback_after_viewing"
                else:
                    reason_hint = "winback_long_absence"
                result.append({
                    'customer_id': cid, 'name': name, 'tier': tier,
                    'phone': phone,
                    'risk_score': score,
                    'risk_level': '高危' if score >= 60 else '中危',
                    'signals': signals,
                    'days_since_contact': days_since,
                    'winback_script': reason_hint,
                })
        result.sort(key=lambda x: x['risk_score'], reverse=True)
        return result

    # ---------- 平替推荐（2026-08-28 功能4） ----------
    def find_alternatives(self, property_id, limit=5):
        """一键平替：同小区同户型 > 同小区 > 同区域同价位段 > 同区域近似面积

        返回 [{...property, match_level, diff_price, diff_area}]，
        已售/已租/本尊排除。
        """
        with self.get_session() as s:
            origin = s.query(Property).get(property_id)
            if not origin:
                return []
            origin_d = origin.to_dict()
            candidates = s.query(Property).filter(
                Property.id != property_id,
                Property.status == 'available',
            ).all()
            # 用途隔离（2026-09-24）：出租只推出租、出售只推出售（一手房/二手房可互推）。
            # 原先不分类型，实测一套 2500元/月 的出租房，平替里出现了 150 万的二手房（价差 600 倍）。
            origin_kind = _deal_kind(origin.property_type)
            candidates = [c for c in candidates if _deal_kind(c.property_type) == origin_kind]

        def closeness(c):
            """贴近度评分（越高越像）：小区40/户型25/价位20/面积15"""
            score = 0
            if origin.community and c.community == origin.community:
                score += 40
                if (origin.rooms and c.rooms == origin.rooms
                        and origin.halls and c.halls == origin.halls):
                    score += 25
            if origin.price and c.price:
                ratio = abs(c.price - origin.price) / origin.price
                if ratio <= 0.10:
                    score += 20
                elif ratio <= 0.20:
                    score += 8
            if origin.area and c.area:
                diff = abs((c.area or 0) - origin.area)
                if diff <= 10:
                    score += 15
                elif diff <= 20:
                    score += 6
            # 同区轻微加分
            if origin.district and c.district == origin.district:
                score += 5
            return score

        scored = []
        for c in candidates:
            score = closeness(c)
            if score < 20:   # 至少要有一点相关性
                continue
            d = c.to_dict()
            d['match_level'] = score
            d['diff_price'] = int(c.price - origin.price) if (c.price and origin.price) else None
            d['diff_area'] = round((c.area or 0) - (origin.area or 0), 1)
            scored.append(d)
        scored.sort(key=lambda x: x['match_level'], reverse=True)
        return scored[:limit]

    # ---------- 缺陷标签（2026-08-28 功能3：带看反馈反哺） ----------
    DEFECT_KEYWORDS = {
        '采光差': ['采光差', '采光不好', '采光太差', '光线暗', '不见光', '暗'],
        '临街吵': ['临街', '吵', '噪音', '车流声'],
        '漏水': ['漏水', '渗水', '水印'],
        '物业差': ['物业差', '物业乱', '物业不给力'],
        '电梯久': ['电梯久', '等电梯', '电梯慢'],
        '装修旧': ['装修旧', '破旧', '老旧装修'],
        '异味': ['异味', '味道', '甲醛'],
        '楼层差': ['楼层差', '腰线层', '设备层'],
    }
    DEFECT_THRESHOLD = 2  # 至少 2 位不同客户提及才标记

    def refresh_defect_tags(self, property_id):
        """扫描带看反馈，负面关键词被 **≥2 位不同客户** 提及则写入缺陷标签

        两条口径（2026-09-25 拍板）：
        ① 计数按**不同客户**（同一位客户的两条带看只算一次）—— 这才能说明是"共性差评"；
        ② 只统计该房源**整改时间之后**的新反馈（手动清除标签会刷新整改时间）—— 房东整改过的旧差评
           不再把标签打回来；整改后**新**的反馈仍按同一阈值判，真出新问题照样标。
        实现走 **SQL 聚合**（8 个标签一条语句，`count(distinct case when … then customer_id end)`）：
        原先把整行读进 ORM 再在 Python 里循环，1 万条反馈要 284ms，现在 20ms 量级，名单逐条一致。
        """
        from sqlalchemy import text as _text
        with self.get_session() as s:
            p = s.query(Property).get(property_id)
            if not p:
                return []
            params = {'pid': property_id}
            where = ["property_id = :pid", "status = 'done'", "feedback is not null"]
            if p.defect_baseline_at:
                where.append("created_at > :baseline")
                params['baseline'] = p.defect_baseline_at
            parts = []
            for i, (tag, kws) in enumerate(self.DEFECT_KEYWORDS.items()):
                conds = " or ".join(f"feedback like :kw{i}_{j}" for j in range(len(kws)))
                for j, kw in enumerate(kws):
                    params[f'kw{i}_{j}'] = f'%{kw}%'
                parts.append(f"count(distinct case when ({conds}) then customer_id end) as t{i}")
            sql = (f"select {', '.join(parts)} from re_viewings where " + " and ".join(where))
            row = s.execute(_text(sql), params).fetchone()
            counts = {tag: int(n or 0) for (tag, _kws), n in zip(self.DEFECT_KEYWORDS.items(), row)}
            defects = sorted([tag for tag, n in counts.items() if n >= self.DEFECT_THRESHOLD])
            detail = {tag: counts[tag] for tag in defects}
            p.defect_tags = json.dumps(detail, ensure_ascii=False) if defects else None
            s.commit()
            return defects

    def clear_defect_tag(self, property_id, tag):
        """房东整改后手动清除某缺陷标签，并记下整改时间 → dict 或 None（房源不存在）

        返回值：`{'cleared': True/False, 'remaining': [剩余标签]}` —— 房源不存在返回 None，
        让工具层能把"房源不存在"与"这套房源没有这个标签"分开说（契约 17）。
        整改时间刷新后，该房源整改前的旧差评不再把标签打回来。
        """
        with self.get_session() as s:
            p = s.query(Property).get(property_id)
            if not p:
                return None
            detail = {}
            if p.defect_tags:
                try:
                    detail = json.loads(p.defect_tags) or {}
                except (ValueError, TypeError):
                    detail = {}          # 脏数据（外部改库）不能把整条清除动作崩掉
            if tag not in detail:
                return {'cleared': False, 'remaining': sorted(detail.keys())}
            del detail[tag]
            p.defect_tags = json.dumps(detail, ensure_ascii=False) if detail else None
            p.defect_baseline_at = datetime.now()
            s.commit()
            return {'cleared': True, 'remaining': sorted(detail.keys())}

    # ---------- 转介绍经营（2026-08-28 功能6） ----------
    def find_customer_by_name(self, name):
        """按姓名精确查客户（转介绍登记时判断"这个人是否已在库里"用；不带手机号时才用得上）"""
        if not name:
            return None
        with self.get_session() as s:
            c = (s.query(Customer).filter(Customer.name == name)
                 .order_by(Customer.id.asc()).first())
            return c.to_dict() if c else None

    def has_referral(self, referrer_customer_id, referred_customer_id):
        """同一介绍人是否已经登记过把这位客户介绍进来（避免贡献榜把一个人算两次）"""
        if not referrer_customer_id or not referred_customer_id:
            return False
        with self.get_session() as s:
            return s.query(Referral).filter(
                Referral.referrer_customer_id == referrer_customer_id,
                Referral.referred_customer_id == referred_customer_id).count() > 0

    def add_referral(self, referrer_customer_id, referred_name, referred_phone=None,
                     referred_customer_id=None, reward_note=None):
        """登记转介绍：自动建新客户档案并标记来源"""
        with self.get_session() as s:
            # 自动建被介绍人客户档案（来源标记为转介绍）
            if referred_customer_id is None:
                new_c = Customer(name=referred_name, phone=referred_phone,
                                 source='转介绍', status='active')
                s.add(new_c)
                s.commit(); s.refresh(new_c)
                referred_customer_id = new_c.id
            r = Referral(
                referrer_customer_id=referrer_customer_id,
                referred_customer_id=referred_customer_id,
                referred_name=referred_name,
                referred_phone=referred_phone,
                reward_note=reward_note,
            )
            s.add(r); s.commit(); s.refresh(r)
            return r.to_dict()

    def referral_stats(self, limit=20):
        """转介绍贡献榜：谁介绍了几个、其中几个（人）成交

        2026-09-24 改：`deals_from_referrals` 原先数的是**成交单笔数**（一位被介绍人两笔成交会被算成
        2），文案却说"N 人成交" —— 现在按 `distinct` 客户数计**人数**，与"介绍 N 人"对称。
        介绍人客户已被删除时，不再回 None，而是标注"已删除客户（id=N）" + referrer_missing=True，
        历史贡献保留、文案也不会出现 None。
        """
        with self.get_session() as s:
            from sqlalchemy import func
            rows = s.query(
                Referral.referrer_customer_id,
                func.count(Referral.id).label('total'),
            ).group_by(Referral.referrer_customer_id).order_by(
                func.count(Referral.id).desc(), Referral.referrer_customer_id.asc()).limit(limit).all()
            leaderboard = []
            for cid, total in rows:
                c = s.query(Customer).get(cid)
                referred_ids = s.query(Referral.referred_customer_id).filter(
                    Referral.referrer_customer_id == cid,
                    Referral.referred_customer_id.isnot(None))
                dealed = s.query(Deal.customer_id).filter(
                    Deal.customer_id.in_(referred_ids.subquery())).distinct().count()
                leaderboard.append({
                    'referrer_customer_id': cid,
                    'referrer_name': c.name if c else f"已删除客户（id={cid}）",
                    'tier': c.tier if c else None,
                    'referrer_missing': c is None,
                    'referrals': total,
                    'deals_from_referrals': dealed,
                })
            return leaderboard

    def count_referrers(self):
        """介绍人总数（用于说明榜单是否被截断）"""
        with self.get_session() as s:
            return s.query(Referral.referrer_customer_id).distinct().count()

    # ---------- 房东委托管理（2026-08-28 功能7） ----------
    def add_owner(self, **kwargs):
        with self.get_session() as s:
            o = Owner(**kwargs)
            s.add(o); s.commit(); s.refresh(o)
            return o.to_dict()

    def get_owner(self, oid):
        with self.get_session() as s:
            o = s.query(Owner).get(oid)
            return o.to_dict() if o else None

    def list_owners(self, limit=50, with_total=False, newest_first=True):
        """房东列表（默认最新登记优先，与客户列表口径一致）

        with_total=True 时返回 (rows, total) —— total 用 SQL 聚合求**符合条件的总数**，
        不是本页条数（列表类返回要 count/total/truncated 三件齐，契约见 FINDINGS F94）。
        分页边界（≤0/非数字按默认、上限）留在工具层，这个方法只按给定条数执行。
        """
        with self.get_session() as s:
            q = s.query(Owner)
            if newest_first:
                q = q.order_by(Owner.id.desc())
            rows = [o.to_dict() for o in q.limit(limit).all()]
            if with_total:
                return rows, s.query(Owner).count()
            return rows

    def find_duplicate_owner(self, phone=None, wechat=None, exclude_id=None):
        """房东查重：手机号（归一后，主）> 微信号（次）。返回 (命中房东dict或None, 警告文本或None)。

        **姓名不参与判重**：房东没有"客户类型"那样的维度，同名不同号的两个人必须各建一条
        （2026-09-25）。给了手机号就只按手机号认人 —— 与客户侧同一套口径（F01/F42/F79）。

        只取要比对的那一列（1.2 万条实测 0.30 秒；全字段 0.74 秒）。防御：疑似密文
        （密钥不一致）不参与比对，避免把乱码当成同一个号而误合并。
        """
        with self.get_session() as s:
            if phone:
                probe = norm_phone(phone)
                if probe:
                    for oid, value in s.query(Owner.id, Owner.phone).all():
                        if exclude_id is not None and oid == exclude_id:
                            continue          # 改自己的号时排除自己（2026-09-25 update_owner）
                        if looks_like_ciphertext(value):
                            return (None, "检测到本机加密密钥与库里的不一致，未强行判重"
                                          "（可能把同一个房东拆成两条、或把两位房东合错）。请先检查加密密钥（COCO_ENC_KEY）。")
                        if norm_phone(value) == probe:
                            o = s.query(Owner).get(oid)
                            return (o.to_dict() if o else None, None)
                    return (None, None)
            if wechat:
                probe = str(wechat).strip()
                if probe:
                    for oid, value in s.query(Owner.id, Owner.wechat).all():
                        if exclude_id is not None and oid == exclude_id:
                            continue          # 改自己的号时排除自己（2026-09-25 update_owner）
                        if looks_like_ciphertext(value):
                            return (None, "检测到本机加密密钥与库里的不一致，未强行判重"
                                          "（可能把同一个房东拆成两条、或把两位房东合错）。请先检查加密密钥（COCO_ENC_KEY）。")
                        if str(value).strip() == probe:
                            o = s.query(Owner).get(oid)
                            return (o.to_dict() if o else None, None)
        return (None, None)

    def owner_portfolio(self, owner_id, limit=None, newest_first=True):
        """房东名下房源组合：房源明细 + 全量统计

        - `stats` 永远按名下**全部**房源算（SQL 聚合，不受明细条数影响）；
        - `limit=None` 表示不限条数（内部调用保持原样）；分页边界（≤0/非数字按默认、上限）
          留在工具层，与其它列表工具同一口径；
        - 明细默认**最新登记优先**（原先按主键升序 = 最早优先，刚挂的房要翻到最后才看得见）。
        """
        with self.get_session() as s:
            o = s.query(Owner).get(owner_id)
            if not o:
                return None
            named = s.query(Property).filter(Property.owner_id == owner_id)
            total = named.count()
            available = named.filter(Property.status == 'available').count()
            dealed = named.filter(Property.status.in_(('sold', 'rented'))).count()
            q = named.order_by(Property.id.desc()) if newest_first else named
            if limit is not None:
                q = q.limit(limit)
            return {
                'owner': o.to_dict(),
                'properties': [p.to_dict() for p in q.all()],
                'stats': {'total': total, 'available': available, 'dealed': dealed},
            }

    def get_property_owners(self, property_ids):
        """按房源 ID 批量查业主信息（"房源→业主"反向查询，2026-08-30 加）。

        实测现象：经纪人要求"把这套/几套房源的业主信息给我"，Coco 只能做模糊工具搜索
        （无"按房源查业主"工具），找不到就兜底报"均未录入业主信息"。本方法补上反向能力。

        property_ids: 房源 ID 列表（最多 3，超出截断）。返回每套房源 dict + 其关联业主 dict，
        业主未关联则 owner=None。业主 phone/wechat 读出即明文（EncryptedString 自动解密）。
        """
        if not property_ids:
            return []
        ids = [int(i) for i in list(property_ids)[:3]]
        with self.get_session() as s:
            owners_by_id = {o.id: o for o in s.query(Owner).all()}
            props = s.query(Property).filter(Property.id.in_(ids)).all()
            result = []
            for p in props:
                d = p.to_dict()
                if p.owner_id and p.owner_id in owners_by_id:
                    d['owner'] = owners_by_id[p.owner_id].to_dict()
                else:
                    d['owner'] = None
                result.append(d)
            return result

    def find_person_by_name(self, name, limit=None):
        """按姓名同时查客户表和业主表（2026-08-30 加，治"Coco 只按客户查人找不到业主"）。

        实测：问"某某先生的详细信息"，Coco 默认按客户查，客户列表里没有此人就下结论
        "库内无此客户"，实际欧阳先生是业主（房东）。经纪人隐式假定"找一个人"应同时覆盖
        客户(买家/租客)与业主(房源主人)两类。

        name: 姓名（支持模糊匹配，子串命中即返回）；**通配符按字面处理**（用位置函数匹配，
        2026-09-25 修：原先走 LIKE，`%`/`_` 会被当通配符 —— 传一个 `%` 能把全库名单捞出来）。
        limit: 两张表各自取前 N 条（None = 不限，分页边界留在工具层）。
        返回 dict：customers/owners 为本次明细，total_customers/total_owners 为**符合条件的总数**
        （SQL 聚合，不受 limit 影响）。
        """
        name = (name or '').strip()
        with self.get_session() as s:
            custs = s.query(Customer).filter(_name_substring_filter(s, Customer.name, name))
            owners = s.query(Owner).filter(_name_substring_filter(s, Owner.name, name))
            total_customers, total_owners = custs.count(), owners.count()
            if limit is not None:
                custs, owners = custs.limit(limit), owners.limit(limit)
            return {
                'customers': [c.to_dict() for c in custs.all()],
                'owners': [o.to_dict() for o in owners.all()],
                'total_customers': total_customers,
                'total_owners': total_owners,
            }

    def link_owner_to_property(self, property_id, name=None, phone=None, wechat=None, return_info=False):
        """找到或新建房东并关联到房源（设 owner_id）。电话/微信加密存（EncryptedString）。

        防重复（2026-09-24 改）：**给了手机号就只按手机号认人** —— 号码库里没有就新建房东，
        不再退回按姓名合并（同名不同人 / 同一房东两个号会被合成一条，后给的号还会被静默丢弃）。
        只有没给手机号时才按姓名认人。若读出的值疑似密文（密钥不一致），不参与匹配（避免误合）。
        手机号按写法归一再比（2026-09-25 修：原先精确比字符串，"138 0013 8000" 与
        "13800138000" 会被当成两个人、同一个房东被拆成两条）。
        return_info=True 时返回 (房东 dict, info)：info.created=是否新建、info.matched_by=靠什么认出来的、
        info.same_name_exists=库里是否已有同名房东（给工具层提示"要不要合并"用）。
        """
        name = (name or '').strip()
        phone = norm_phone(phone)
        wechat = (str(wechat).strip() or None) if wechat else None
        if not name and not phone:
            return (None, {}) if return_info else None
        with self.get_session() as s:
            prop = s.query(Property).get(property_id)
            if not prop:
                return (None, {}) if return_info else None
            owners = [o.to_dict() for o in s.query(Owner).all()]

            def _fk(v, probe):
                if not isinstance(v, str):
                    return False
                v = v.strip()
                if not v or v == probe:
                    return False
                phone_chars = set('0123456789 +-()')
                if all(ch in phone_chars for ch in v) and len(v) <= 24:
                    return False
                return True

            owner = None
            matched_by = None
            if phone:
                for o in owners:
                    v = o.get('phone')
                    if v is None or _fk(v, phone):
                        continue
                    if norm_phone(v) == phone:
                        owner = s.query(Owner).get(o['id']); matched_by = 'phone'; break
            elif name:
                for o in owners:
                    if o.get('name') == name:
                        owner = s.query(Owner).get(o['id']); matched_by = 'name'; break
            same_name_exists = bool(name) and any(o.get('name') == name for o in owners)
            created = False
            if not owner:
                owner = Owner(name=name or phone, phone=phone, wechat=wechat)
                s.add(owner); s.flush(); created = True
            prop.owner_id = owner.id
            s.commit()
            info = {'created': created, 'matched_by': matched_by, 'same_name_exists': same_name_exists}
            owner_dict = owner.to_dict()
            return (owner_dict, info) if return_info else owner_dict

    def exclusive_expiring(self, days=30, limit=None):
        """独家委托 N 天内到期清单（重新谈委托/降价的时机）

        limit=None 表示不限条数（分页边界留在工具层）；返回 (items, total)，
        total 是窗口内的**全部**套数（不受 limit 影响）。
        """
        from datetime import datetime, timedelta
        deadline = datetime.now() + timedelta(days=days)
        with self.get_session() as s:
            props = s.query(Property).filter(
                Property.exclusive_until.isnot(None),
                Property.exclusive_until <= deadline,
                Property.status == 'available',
            ).all()
            result = []
            for p in props:
                d = p.to_dict()
                remain = (p.exclusive_until - datetime.now()).days
                d['days_remaining'] = remain
                d['urgency'] = '已过期' if remain < 0 else f'{remain}天'
                result.append(d)
            result.sort(key=lambda x: x['days_remaining'])
            total = len(result)
            if limit is not None:
                result = result[:limit]
            return result, total

    def exclusive_summary(self):
        """独家委托登记概况 → {'registered': 在售且登记了到期日的套数, 'nearest_days': 最近一套还有几天}

        用于空态如实说明"库里到底有没有登记过独家委托"（否则"无到期"分不清是没登记还是没到期）。
        """
        from datetime import datetime
        with self.get_session() as s:
            rows = s.query(Property.exclusive_until).filter(
                Property.exclusive_until.isnot(None),
                Property.status == 'available',
            ).all()
        now = datetime.now()
        days = [(v[0] - now).days for v in rows if v[0] is not None]
        return {'registered': len(days), 'nearest_days': min(days) if days else None}

    def find_duplicate_property(self, title, area, exclude_id=None, property_type=None):
        """查在售房源里是否已有这套房（防重复录入）→ 返回已存在房源 dict；没有则 None

        判定（2026-09-21 重做、2026-09-24 加类型维度）：整串标题归一化后完全相同，或身份要素相同
        （小区主体同一个 + 期数一致 + 楼栋/单元不冲突 + 房号相同）+ 面积差 ≤1㎡ + **同类型**。
        价格不算身份（会被改价/调价），只认 小区 + 房号 + 面积 + 类型。
        """
        return self.find_property_conflict(title=title, area=area, exclude_id=exclude_id,
                                           property_type=property_type)[0]

    def find_suspected_property(self, title, area, exclude_id=None, property_type=None):
        """疑似同一套（只提示、不拦）：小区同一个 + 面积同口径，但标题里房号不全"""
        return self.find_property_conflict(title=title, area=area, exclude_id=exclude_id,
                                           property_type=property_type)[1]

    def find_property_conflict(self, title, area, exclude_id=None, property_type=None):
        """一次扫描同时判定「确定重复」与「疑似重复」，返回 (dup, suspected)

        确定重复：整串标题归一化后相同，或身份要素相同（小区+期数+楼栋+单元+房号）+ 面积差 ≤1㎡，
        **且只跟同一「交易性质」的房源比**（2026-09-24 加）：出租与出售分开（同一套房可以既卖又租），
        一手房与二手房同属出售、同房号仍算同一套。跨性质命中不拦录入，只给「同房号已有另一用途记录」的提示。

        疑似（只提示、不拦录入，四种情形各带 reason）：
          ① 同小区同面积，但标题里房号不全；
          ② 同房号、但库里那条已售/已租（不在售）；
          ③ 同房号、但面积与本次填写不符；
          ④ 同房号、但用途不同（本次出租、库里是出售；或反之）。
        """
        area_value = float(area or 0)
        norm_title = _normalize_title(title)
        ident = parse_property_identity(title)
        want_group = _deal_kind(property_type) if property_type else None
        cands = {}
        with self.get_session() as s:
            for p in s.query(Property).all():
                if exclude_id is not None and p.id == exclude_id:
                    continue
                other = parse_property_identity(p.title or '')
                close = _area_close(p.area, area_value)
                available = (p.status == 'available')
                # 类型维度（2026-09-24）：只跟同一「交易性质」的房源比 ——
                # 出租与出售分开（同一套房可既卖又租）；一手房与二手房同属出售，同房号仍按重复处理
                same_type = want_group is None or _deal_kind(p.property_type) == want_group
                if same_type and available and close:
                    if norm_title and _normalize_title(p.title or '') == norm_title:
                        return p.to_dict(), None
                    if same_property_identity(ident, other):
                        return p.to_dict(), None
                if same_property_identity(ident, other):
                    if not same_type:
                        _other = _PROPERTY_TYPE_LABEL.get(p.property_type, p.property_type)
                        _mine = ("出租" if want_group == "rental"
                                 else f"出售（{_PROPERTY_TYPE_LABEL.get(property_type, property_type)}）")
                        cands.setdefault("other_type", {**p.to_dict(), "reason": (
                            f"库里有一条同房号的{_other}房源（编号 {p.id}，状态 {p.status}），"
                            f"与本次的{_mine}是两条独立记录（同一套房可以既卖又租），已按新记录录入")})
                    elif not available:
                        cands.setdefault("off_market", {**p.to_dict(), "reason": (
                            f"库里有一条同房号的记录（编号 {p.id}，状态 {p.status}），本次同房号重新录入")})
                    elif not close:
                        cands.setdefault("area_mismatch", {**p.to_dict(), "reason": (
                            f"库里有一条同房号的房源（编号 {p.id}，面积 {p.area}㎡），本次填写 {area_value}㎡")})
                elif same_type and available and close and suspected_same_property_identity(ident, other):
                    cands.setdefault("room_unknown", {**p.to_dict(), "reason": (
                        f"库里有一条同小区、同面积的房源（编号 {p.id}），但标题里房号不全，无法确定是不是同一套")})
            suspected = (cands.get("room_unknown") or cands.get("area_mismatch")
                         or cands.get("off_market") or cands.get("other_type"))
            return None, suspected

    def find_duplicate_properties(self):
        """按身份要素找重复房源组，返回 [[keep_id, dup_id, ...], ...]（每组按 id 升序，第一个为保留项）

        写法不同也能认出（小区/期数/楼栋/单元/房号对齐 + 面积差 ≤1㎡）。
        分桶策略：有房号的按「房号 + 面积取整」分桶，桶内再与相邻面积桶两两精判；
        没有房号的按「归一化标题 + 面积」分桶（等同旧规则）——既不漏，也不做 O(n²) 全比。
        """
        with self.get_session() as s:
            rows = [(p.id, p.title or '', float(p.area or 0), parse_property_identity(p.title or ''),
                     _deal_kind(p.property_type))
                    for p in s.query(Property).all()]
        buckets = {}
        for pid, title, area, ident, kind in rows:
            # 分桶键带交易性质（2026-09-24）：出租与出售分开（同一套房可既卖又租）；
            # 一手房与二手房同属出售，同房号仍是同一套
            if ident["room"]:
                buckets.setdefault((kind, ident["room"], int(area)), []).append((pid, area, ident))
            else:
                buckets.setdefault(("title", kind, _normalize_title(title), round(area, 2)), []).append((pid, area, ident))
        parent = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        by_room = {}
        for key, items in buckets.items():
            if key[0] == "title":
                for other in items[1:]:
                    union(items[0][0], other[0])
            else:
                by_room.setdefault((key[0], key[1]), {})[key[2]] = items
        for area_map in by_room.values():
            areas = sorted(area_map)
            for idx, area_key in enumerate(areas):
                group = list(area_map[area_key])
                if idx + 1 < len(areas) and areas[idx + 1] == area_key + 1:
                    group += area_map[areas[idx + 1]]     # 面积跨整数边界（如 128.6 / 129.4）
                for i in range(len(group)):
                    for j in range(i + 1, len(group)):
                        pid_i, area_i, ident_i = group[i]
                        pid_j, area_j, ident_j = group[j]
                        if _area_close(area_i, area_j) and same_property_identity(ident_i, ident_j):
                            union(pid_i, pid_j)
        groups = {}
        for pid in parent:
            groups.setdefault(find(pid), []).append(pid)
        return [sorted(v) for v in groups.values() if len(v) > 1]

    def remove_duplicate_properties(self, dry_run=True, keep=None, keep_id=None, merge=False):
        """去重：每组挑出保留项、其余删除；merge=True 时先把独有信息并到保留项再删

        保留项优先级（2026-09-21 定）：**在售 > 在租 > 已售**，同级比信息完整度
        （业主/图片/租客要求权重更高），再同级取最早 id。
        keep='richest' 只按信息完整度挑、'earliest' 退回旧行为（最早 id）。
        keep_id 直接点名某组保留哪条（该组不用优先级）。
        merge=True 先把被删项的独有字段补到保留项（**只补空缺、绝不覆盖**），再删除。
        跳过（需人工拍板）：有关联带看/成交/跟进；带业主/图片而保留项没有且未开 merge。
        """
        dups = self.find_duplicate_properties()
        removed, skipped, merged_log = [], [], []
        groups_detail = []
        self._dedup_merge_total = 0
        with self.get_session() as s:
            for group in dups:
                keeper_id = self._pick_keeper_id(s, group, keep=keep, keep_id=keep_id)
                keep_row = s.query(Property).get(keeper_id)
                plan_entries = []
                for dup_id in group:
                    if dup_id == keeper_id:
                        continue
                    dup = s.query(Property).get(dup_id)
                    plan = self.merge_plan(keep_row, dup) if (keep_row is not None and dup is not None) else {}
                    if plan:
                        plan_entries.append({'id': dup_id, 'title': dup.title, 'unique': plan})
                    reason = self._dedup_skip_reason(s, keep_row, dup, dup_id, merge=merge)
                    if reason:
                        skipped.append({'id': dup_id, 'reason': reason})
                        continue
                    if merge and dup is not None and keep_row is not None:
                        info = self._apply_merge(s, keep_row, dup)
                        merged_log.append({'keep_id': keeper_id, 'drop_id': dup_id, **info})
                    if not dry_run and dup is not None:
                        s.delete(dup)
                    removed.append(dup_id)
                groups_detail.append({
                    'keep_id': keeper_id, 'keep_title': keep_row.title if keep_row else None,
                    'keep_status': getattr(keep_row, 'status', None),
                    'duplicate_ids': [i for i in group if i != keeper_id],
                    'merge_plan': plan_entries,
                })
            if not dry_run:
                s.commit()
            keep_titles = {g['keep_id']: g['keep_title'] for g in groups_detail}
            for entry in merged_log:
                entry['keep_title'] = keep_titles.get(entry['keep_id'])
        return {
            'duplicate_groups': len(dups),
            'duplicate_total': sum(len(g) - 1 for g in dups),
            'groups': groups_detail,
            'removable': removed, 'skipped': skipped, 'merged': merged_log,
            'merged_count': len(merged_log), 'merge': merge, 'keep': keep or 'available',
            'dry_run': dry_run,
        }

    _STATUS_RANK = {'available': 0, 'rented': 1, 'sold': 2}

    def _pick_keeper_id(self, s, group, keep=None, keep_id=None):
        """在一组重复房源里挑保留项：在售 > 在租 > 已售 → 信息完整度 → 最早 id"""
        if keep_id is not None and keep_id in group:
            return keep_id
        rows = [r for r in (s.query(Property).get(i) for i in group) if r is not None]
        if not rows:
            return group[0]
        if keep == 'earliest':
            return min(r.id for r in rows)
        if keep == 'richest':
            return sorted(rows, key=lambda p: (-_property_richness(p), p.id))[0].id
        return sorted(rows, key=lambda p: (
            self._STATUS_RANK.get(p.status or '', 3), -_property_richness(p), p.id))[0].id

    @staticmethod
    def merge_plan(keep, drop):
        """被删那条有哪些「保留项没有」的独有信息（dry_run 展示 + 决定要不要合并）"""
        if keep is None or drop is None:
            return {}
        unique = {}
        for f in _PROPERTY_MERGE_FIELDS:
            if _is_blank(getattr(keep, f, None)) and not _is_blank(getattr(drop, f, None)):
                unique[f] = getattr(drop, f)
        if not getattr(keep, 'owner_id', None) and getattr(drop, 'owner_id', None):
            unique['owner'] = f'业主（owner_id={drop.owner_id}）'
        images = _extra_csv_items(keep.images, drop.images)
        if images:
            unique['images'] = images
        tags = _extra_csv_items(keep.tags, drop.tags)
        if tags:
            unique['tags'] = tags
        return unique

    @staticmethod
    def _apply_merge(s, keep, drop):
        """把 drop 的独有信息并到 keep（只补空缺、不覆盖），返回本次并了什么"""
        merged, images_added, owner_moved = {}, 0, False
        for f in _PROPERTY_MERGE_FIELDS:
            if _is_blank(getattr(keep, f, None)) and not _is_blank(getattr(drop, f, None)):
                merged[f] = getattr(drop, f)
                setattr(keep, f, getattr(drop, f))
        if not getattr(keep, 'owner_id', None) and getattr(drop, 'owner_id', None):
            keep.owner_id = drop.owner_id
            owner_moved = True
        images = _extra_csv_items(keep.images, drop.images)
        if images:
            keep.images = ','.join([x for x in (keep.images or '').split(',') if x.strip()] + images.split(','))
            merged['images'] = images
            images_added = len(images.split(','))
        tags = _extra_csv_items(keep.tags, drop.tags)
        if tags:
            keep.tags = ','.join([x for x in (keep.tags or '').split(',') if x.strip()] + tags.split(','))
            merged['tags'] = tags
        return {'merged': merged, 'images_added': images_added, 'owner_moved': owner_moved}

    def merge_duplicate_property(self, keep_id, drop_id, dry_run=True):
        """把 drop 的独有信息并到 keep（只补空缺、不覆盖）并删除 drop；dry_run=True 只预演"""
        with self.get_session() as s:
            keep = s.query(Property).get(keep_id)
            drop = s.query(Property).get(drop_id)
            if keep is None or drop is None:
                return {'error': f'房源不存在（keep_id={keep_id} drop_id={drop_id}）'}
            reason = self._dedup_skip_reason(s, keep, drop, drop_id, merge=True)
            if reason:
                return {'error': f'不能自动合并删除：{reason}'}
            info = self.merge_plan(keep, drop)
            if dry_run:
                return {'keep_id': keep_id, 'drop_id': drop_id, 'would_merge': info, 'dry_run': True}
            applied = self._apply_merge(s, keep, drop)
            s.delete(drop)
            s.commit()
            return {'keep_id': keep_id, 'keep_title': keep.title, 'drop_id': drop_id, **applied, 'dry_run': False}

    @staticmethod
    def _dedup_skip_reason(s, keep, dup, dup_id, merge=False):
        """自动清理前必须跳过的理由（None = 可以删）：有牵挂的留给人工，宁可留着不误删"""
        if dup is None:
            return '记录已不存在'
        related = (s.query(Deal).filter(Deal.property_id == dup_id).count()
                   + s.query(Followup).filter(Followup.property_id == dup_id).count()
                   + s.query(Viewing).filter(Viewing.property_id == dup_id).count())
        if related:
            return '有关联带看/成交/跟进记录（需人工处理）'
        if keep is not None and not merge:
            if getattr(dup, 'owner_id', None) and not getattr(keep, 'owner_id', None):
                return f'带着业主信息，而保留的 id={keep.id} 没有 → 先合并再删（merge=true）或人工确认'
            if _extra_csv_items(keep.images, dup.images):
                return f'带房源图片，而保留的 id={keep.id} 没有 → 先合并再删（merge=true）或人工确认'
        return None

    def match_customers_for_property(self, property_id, top_n=5):
        """房源反匹配：新房源 → 扫描活跃客户，按需求匹配推荐（2026-08-29 起放宽到所有活跃客户，不再只 S/A）

        返回命中客户列表（按匹配度排序），用于 add_property 后主动推送。
        """
        with self.get_session() as s:
            prop = s.query(Property).get(property_id)
            if not prop:
                return []
            # 只匹配在售房源
            if prop.status != 'available':
                return []
            candidates = s.query(Customer).filter(
                Customer.status == 'active',
            ).all()
            if not candidates:
                return []
            # 排除已有交易记录的客户（进行中或已完成，不再推新房源）
            candidates = [c for c in candidates if not c.deals]
            if not candidates:
                return []

            results = []
            for c in candidates:
                # 租买互斥（2026-08-13 加）：租房客户只推出租房源，买房客户只推买卖房源
                if prop.property_type == 'rental' and c.customer_type != 'rent':
                    continue
                if prop.property_type in ('new', 'second_hand') and c.customer_type == 'rent':
                    continue
                # 租客要求过滤（2026-08-29 加）：出租房源租客要求与客户资料冲突 → 排除
                if prop.property_type == 'rental' and not self._tenant_req_ok(prop.tenant_requirements, c):
                    continue
                # 类型硬匹配（2026-08-30 加）：客户类型明确(买新/买二手)时，类型不符即排除——买二手不推一手房、买一手房不推二手
                if c.customer_type in ('buy_new', 'buy_second_hand') and not self._match_type(c.customer_type, prop.property_type):
                    continue
                # 户型硬性要求：客户明确 N 室/N 厅而房源不满足 → 跳过
                #（与 match_property 对称，防止"2 室房源推给要 3 室的客户"）
                if c.layout_pref and not self._match_layout(c.layout_pref, prop.rooms, prop.halls):
                    continue
                score = 0
                reasons = []

                # 预算匹配（权重 30）
                budget_min = _as_money(c.budget_min, 0)
                budget_max = _as_money(c.budget_max, 999999999)  # 无预算上限（元制）
                if budget_min <= prop.price <= budget_max:
                    score += 30
                    reasons.append("预算匹配")
                elif prop.price < budget_min:
                    score += 15
                    reasons.append("略低于预算")
                elif prop.price > budget_max:
                    score += 5
                    reasons.append("略超预算")

                # 户型匹配（权重 25）
                pref = c.layout_pref or ""
                import re as _re
                m_room = _re.search(r'(\d+)室', pref)
                if m_room and prop.rooms == int(m_room.group(1)):
                    score += 25
                    reasons.append("户型匹配")

                # 面积匹配（权重 20）
                if c.area_pref:
                    lo, hi = self._parse_area(c.area_pref)
                    if lo <= prop.area <= hi:
                        score += 20
                        reasons.append("面积匹配")

                # 区域匹配（权重 15）：区名归一化优先，原子串兜底
                region_ok = self._match_region(c.location, prop.district, prop.community, reasons)
                if region_ok and c.location:
                    score += 15
                elif c.location and not region_ok:
                    # 客户明确指定区域但房源不在该区：显式标注（2026-08-30 与正向 match_property 对称）
                    reasons.append("区域不符")

                # 类型匹配（一手房/二手房/租房）：不匹配不加分但标注，且不算完全匹配
                type_ok = self._match_type(c.customer_type, prop.property_type)
                if c.customer_type and not type_ok:
                    reasons.append("类型不符")

                # 装修匹配（权重 10）
                if c.renovation and prop.renovation and c.renovation == prop.renovation:
                    score += 10
                    reasons.append("装修匹配")

                if score >= 40:
                    # perfect_match：预算在区间 + 户型硬性匹配 + 区域匹配（或无区域需求）+ 类型匹配
                    perfect_match = (
                        budget_min <= prop.price <= budget_max
                        and self._match_layout(c.layout_pref, prop.rooms, prop.halls)
                        and (region_ok or not c.location)
                        and type_ok
                    )
                    results.append({
                        'customer_id': c.id, 'customer_name': c.name,
                        'tier': c.tier, 'score': score, 'match_reasons': reasons,
                        'budget': [budget_min, budget_max],
                        'area_pref': c.area_pref, 'layout_pref': c.layout_pref,
                        'location': c.location,
                        'perfect_match': perfect_match,
                        '_region_ok': region_ok,
                    })

            # 区域硬优先级 tier（2026-08-30 与正向 match_property 对称）：客户 location 非空时，
            # 区域匹配的客户排在区域不符的前面，同 tier 内按 score 降序。（治：本区客户被其他区客户顶到后面）
            results.sort(key=lambda x: (x['_region_ok'] if x['location'] else True, x['score']), reverse=True)
            for _r in results:
                _r.pop('_region_ok', None)
            return results[:top_n]

    def search_properties(self, **filters):
        """按条件搜房源。默认只看在售、不排序（历史行为不变）。

        2026-09-24 加三个可选参数（不传时行为与以前完全一致）：
          status: available / sold / rented / all（不传 = 只看在售）
          sort: latest（按最新录入倒序）/ price_asc / price_desc（不传 = 不排序）
          with_total: True 时返回 (rows, 匹配总数)，供上层区分"本次返回条数"与"匹配总数"
        """
        with self.get_session() as s:
            status = filters.pop('status', None)
            sort = filters.pop('sort', None)
            with_total = filters.pop('with_total', False)
            q = s.query(Property)
            if status == 'all':
                pass
            elif status:
                q = q.filter(Property.status == status)
            else:
                q = q.filter(Property.status == 'available')
            limit = filters.pop('limit', 50)
            if 'min_price' in filters: q = q.filter(Property.price >= filters['min_price'])
            if 'max_price' in filters: q = q.filter(Property.price <= filters['max_price'])
            if 'min_area' in filters: q = q.filter(Property.area >= filters['min_area'])
            if 'max_area' in filters: q = q.filter(Property.area <= filters['max_area'])
            if 'rooms' in filters: q = q.filter(Property.rooms == filters['rooms'])
            if 'district' in filters: q = q.filter(Property.district.contains(filters['district']))
            if 'renovation' in filters: q = q.filter(Property.renovation == filters['renovation'])
            if 'property_type' in filters: q = q.filter(Property.property_type == filters['property_type'])
            if 'title' in filters: q = q.filter(Property.title.contains(filters['title']))
            if sort == 'latest':
                q = q.order_by(Property.id.desc())
            elif sort == 'price_asc':
                q = q.order_by(Property.price.asc())
            elif sort == 'price_desc':
                q = q.order_by(Property.price.desc())
            total = q.count() if with_total else None
            rows = [p.to_dict() for p in q.limit(limit).all()]
            return (rows, total) if with_total else rows
    
    def get_property(self, property_id):
        """按 id 取单套房源（含现算单价），不存在返回 None"""
        with self.get_session() as s:
            p = s.query(Property).get(property_id)
            return p.to_dict() if p else None

    def iter_available_properties(self, chunk_size: int = 2000, **filters):
        """分块遍历**全部**在售房源（生成器），不做条数截断。

        2026-09-18 修：匹配类逻辑原先用 search_properties(limit=10000)，房源超过 1 万后
        后面的房源永远不参与匹配（存量房源规模大）。这里改成 keyset 分页（按 id 递增），
        内存只占用一块 chunk，且不随总行数增大。
        """
        last_id = 0
        while True:
            with self.get_session() as s:
                q = s.query(Property).filter(Property.status == 'available', Property.id > last_id)
                if filters.get('property_type'):
                    q = q.filter(Property.property_type == filters['property_type'])
                # 户型硬条件下推（客户明确 N 室/N 厅时，命中不了的在循环里也是 continue，交给数据库先筛掉）
                for field, plan in (('rooms', filters.get('rooms')), ('halls', filters.get('halls'))):
                    if not plan:
                        continue
                    col = Property.rooms if field == 'rooms' else Property.halls
                    if plan[0] == 'exact':
                        q = q.filter(col == plan[1])
                    else:
                        q = q.filter(col >= plan[1], col <= plan[2])
                rows = q.order_by(Property.id).limit(chunk_size).all()
                if not rows:
                    return
                last_id = rows[-1].id
                for r in rows:
                    # 只取匹配用得到的字段（不走 to_dict：6 万套实测省 1.7 秒/次，且内存更小）
                    yield {
                        'id': r.id, 'title': r.title, 'community': r.community,
                        'district': r.district, 'price': float(r.price) if r.price is not None else None,
                        'area': r.area, 'rooms': r.rooms, 'halls': r.halls, 'bathrooms': r.bathrooms,
                        'renovation': r.renovation, 'property_type': r.property_type,
                        'tags': r.tags, 'defect_tags': r.defect_tags,
                        'tenant_requirements': r.tenant_requirements, 'status': r.status,
                        'unit_price': r.unit_price_value(),
                    }

    def recent_price_drops(self, days: int = 7, limit: int = None):
        """近期**发生过降价**的在售房源（一次 SQL 查出来，不再对每套房各查一次）

        2026-09-18 修：降价提醒原先遍历 1 万套房、对每套各查一次调价历史（N+1，慢且漏）。
        2026-09-24 修：limit 原写死 200，实测 300 套房降价时后 100 套静默漏掉；默认改为不限（全量扫）。
        """
        from datetime import datetime, timedelta
        since = datetime.now() - timedelta(days=days)
        with self.get_session() as s:
            rows = (s.query(PriceHistory)
                    .filter(PriceHistory.created_at >= since)
                    .order_by(PriceHistory.created_at.desc())
                    .limit(limit).all())
            seen, out = set(), []
            for h in rows:
                if h.property_id in seen:
                    continue
                prop = s.query(Property).filter(Property.id == h.property_id,
                                                Property.status == 'available').first()
                if not prop or h.old_price is None or h.new_price is None or h.new_price >= h.old_price:
                    continue
                seen.add(h.property_id)
                out.append({
                    'property_id': h.property_id,
                    'title': prop.title,
                    'old_price': h.old_price,
                    'new_price': h.new_price,
                    'drop_amount': h.old_price - h.new_price,
                })
            return out

    def recent_properties(self, days: int = 1, limit: int = 200) -> list:
        """近期新录入的在售房源（一次 SQL 查出来，供机会提醒用）

        2026-09-23 加：定时"机会提醒"要拿"今天新录的房源"去匹配 S/A 级客户，
        原先只能遍历全部在售房源（存量房源规模大）再逐条比 created_at。
        """
        from datetime import datetime, timedelta
        since = datetime.now() - timedelta(days=days)
        with self.get_session() as s:
            rows = (s.query(Property)
                    .filter(Property.status == 'available', Property.created_at >= since)
                    .order_by(Property.created_at.desc())
                    .limit(limit).all())
            return [p.to_dict() for p in rows]

    def activity_counts(self, days: int = 1) -> dict:
        """近 N 天的活动量（各表 count，一次会话跑完，供日报/收工小结/周报用）

        2026-09-23 加：定时任务要报"今天/这周做了什么"，原先得遍历客户逐条查跟进（N+1）。
        统计口径：带看按 viewing_time 算，成交按主键创建与交房日期算。
        """
        from datetime import datetime, timedelta
        since = datetime.now() - timedelta(days=days)
        with self.get_session() as s:
            return {
                "new_customers": s.query(Customer).filter(Customer.created_at >= since).count(),
                "new_properties": s.query(Property).filter(Property.created_at >= since).count(),
                "followups": s.query(Followup).filter(Followup.created_at >= since).count(),
                "viewings": s.query(Viewing).filter(Viewing.viewing_time >= since).count(),
                "viewings_done": s.query(Viewing).filter(
                    Viewing.viewing_time >= since, Viewing.status == 'done').count(),
                "viewings_interested": s.query(Viewing).filter(
                    Viewing.viewing_time >= since, Viewing.result == 'interested').count(),
                "deals_created": s.query(Deal).filter(Deal.created_at >= since).count(),
                "deals_updated": s.query(Deal).filter(Deal.updated_at >= since).count(),
                "deals_finalized": s.query(Deal).filter(Deal.finalize_date >= since).count(),
            }

    def viewings_between(self, start, end) -> list:
        """时间段内的带看（按带看时间过滤，供"今天的带看""明天的安排"用）"""
        with self.get_session() as s:
            rows = (s.query(Viewing)
                    .filter(Viewing.viewing_time >= start, Viewing.viewing_time < end)
                    .order_by(Viewing.viewing_time.asc()).all())
            return [v.to_dict() for v in rows]

    def count_available_properties(self) -> int:
        """在售房源总数（SQL count，不受任何 limit 影响）"""
        with self.get_session() as s:
            return s.query(Property).filter(Property.status == 'available').count()

    def get_available_property(self, property_id):
        """按编号取单套**在售**房源（一次命中，不依赖"前 N 条"）

        海报/文案/短视频/图片类工具原先用 search_properties() 捞一把再找编号，
        而 search_properties 默认只返回 50 条 —— 房源一多（实测 6 万条）就报"房源不存在"。
        """
        if property_id is None:
            return None
        with self.get_session() as s:
            p = s.query(Property).filter(Property.id == property_id,
                                         Property.status == 'available').first()
            return p.to_dict() if p else None

    def find_available_property_by_title(self, title, limit=10):
        """按标题包含匹配取在售房源列表（供"按标题找那套房"的场景，不再受默认 50 条限制）"""
        title = (title or '').strip()
        if not title:
            return []
        with self.get_session() as s:
            rows = s.query(Property).filter(Property.status == 'available',
                                            Property.title.contains(title)).limit(limit).all()
            return [p.to_dict() for p in rows]

    def find_property_by_title(self, title):
        """按标题定位单套房源：先"完全一致"，再"包含"。

        返回 {'exact': [...], 'contains': [...]}。分开两类是为了让上层能区分
        "库里确实没有" 与 "标题写法不完全一样"，从而如实回答而不是拿别的房源顶替。
        """
        title = (title or '').strip()
        if not title:
            return {'exact': [], 'contains': []}
        with self.get_session() as s:
            exact = [p.to_dict() for p in
                     s.query(Property).filter(Property.title == title).limit(10).all()]
            contains = [p.to_dict() for p in
                        s.query(Property).filter(Property.title.contains(title)).limit(10).all()]
        return {'exact': exact, 'contains': contains}

    def similar_properties_by_title(self, title, limit=5):
        """标题查不到时的候选：把输入尾部逐字去掉再找（最多退 4 级），返回第一批命中的在售房源"""
        title = (title or '').strip()
        for cut in range(0, 5):
            probe = title[:len(title) - cut] if cut else title
            if len(probe) < 2:
                break
            rows = self.search_properties(title=probe, limit=limit)
            if rows:
                return rows
        return []

    def customer_has_deal(self, customer_id) -> bool:
        """客户是否已有交易记录（进行中或已完成）"""
        with self.get_session() as s:
            return s.query(Deal).filter(Deal.customer_id == customer_id).first() is not None

    def match_property(self, customer_id, top_n=5, pool=None):
        customer = self.get_customer(customer_id)
        if not customer: return []
        # 已有交易记录（进行中或已完成）的客户不再推送房源
        # （真实案例 2026-08-11：客户过户完成仍被推荐房源）
        if self.customer_has_deal(customer_id):
            return []
        
        
        min_area, max_area = self._parse_area(customer.get('area_pref'))
        budget_min = _as_money(customer.get('budget_min'), 0)
        budget_max = _as_money(customer.get('budget_max'), 999999999)  # 无预算上限（元制）
        loc = customer.get('location') or ''
        layout_pref = customer.get('layout_pref')
        layout_plan = self._parse_layout_pref(layout_pref)   # 每客户解析一次（循环里只做整数比较）
        ren_pref = customer.get('renovation')
        ctype = customer.get('customer_type')

        # 候选池：全量在售房源（此前写死 limit=10000，房源多时后面的一律不参与匹配）。
        # 客户类型 / 户型是硬过滤（命中不了直接 continue），下推到 SQL 只为"少捞回来"，
        # 最终判定仍在循环里做，语义不变。
        pushdown = {}
        want_type = self._CUSTOMER_TYPE_TO_PROP_TYPE.get(ctype or '')
        if want_type:
            pushdown['property_type'] = want_type
        if layout_plan:
            for token in ('rooms', 'halls'):
                if layout_plan.get(token):
                    pushdown[token] = layout_plan[token]
        if pool is not None:
            properties = pool
        else:
            properties = list(self.iter_available_properties(**pushdown))
        if not properties: return []

        scores = []
        for prop in properties:
            score = 0; reasons = []
            
            prop_type = prop.get('property_type')
            # 租买互斥硬过滤（2026-08-13 加）：租房客户只推出租房源，买房客户只推买卖房源
            #（真实教训：月租 1500-2500 的客户被推 78 万总价二手房；反之亦然）
            if prop_type == 'rental' and ctype != 'rent':
                continue
            if prop_type in ('new', 'second_hand') and ctype == 'rent':
                continue
            # 租客要求过滤（2026-08-29 加）：出租房源租客要求与客户资料冲突 → 排除
            if prop_type == 'rental' and not self._tenant_req_ok(prop.get('tenant_requirements'), customer):
                continue
            # 类型硬匹配（2026-08-30 加）：客户类型明确(买新/买二手)时，类型不符即排除——买二手不推一手房、买一手房不推二手
            if ctype in ('buy_new', 'buy_second_hand') and not self._match_type(ctype, prop_type):
                continue
            
            price = prop.get('price', 0)
            if budget_min <= price <= budget_max:
                score += 30; reasons.append("价格匹配")
            elif price < budget_min:
                score += 15; reasons.append("略低于预算")
            else:
                # 超预算：不加分但必须显式标注，防止把超预算房源标成"完全匹配"
                #（2026-08-13 真实案例：280 万房源被推给 230 万预算客户并标完全匹配）
                reasons.append("超预算")
            
            area = prop.get('area', 0)
            if min_area <= area <= max_area:
                score += 20; reasons.append("面积匹配")
            
            # 户型硬性要求：客户明确 N 室/N 厅而房源不满足 → 直接排除
            #（真实案例 2026-08-11：客户要 3 室却被推 2 室房源并标"匹配度较高"）
            layout_ok = self._layout_ok(layout_plan, prop.get('rooms'), prop.get('halls'))
            if layout_pref and not layout_ok:
                continue
            if layout_ok:
                score += 25; reasons.append("户型匹配")
            
            # 区域匹配（权重 15）：区名归一化优先，原子串兜底
            #（2026-08-13 真实案例：客户"美兰区"命中不了"美兰-海甸岛"格式，同房差 15 分）
            region_ok = self._match_region(loc, prop.get('district'), prop.get('community'), reasons)
            if region_ok and loc:
                score += 15
            elif loc and not region_ok:
                # 客户明确指定区域但房源不在该区：显式标注，给模型信号，防把错区误标"完全匹配"
                #（2026-08-30 真实案例：周女士要秀英区，4 套其他区被排前面且被误判完全匹配）
                reasons.append("区域不符")
            
            # 类型匹配（一手房/二手房/租房）：不匹配不加分但标注，且不算完全匹配
            #（2026-08-13 真实案例：要买一手房的客户被推二手房并标完全匹配）
            type_ok = self._match_type(ctype, prop.get('property_type'))
            if ctype and not type_ok:
                reasons.append("类型不符")
            
            if ren_pref and ren_pref == prop.get('renovation'):
                score += 10; reasons.append("装修匹配")
            
            # 缺陷降权（2026-08-28 功能3）：有缺陷标签的房源评分×0.8，并在理由里透明标注
            if prop.get('defect_tags'):
                try:
                    defects = json.loads(prop['defect_tags'])
                except (ValueError, TypeError):
                    defects = {}
                if defects:
                    score = int(score * 0.8)
                    reasons.append("客户反馈:" + ",".join(f"{k}({v}次)" for k, v in defects.items()))

            if score > 0:
                # perfect_match：预算在区间 + 户型硬性匹配 + 区域匹配（或无区域需求）+ 类型匹配
                #（2026-08-13 加：判定标准写死在代码里，模型只能引用此字段标"完全匹配"，
                #   禁止自定口径——此前模型把 150 万标成 160-200 万预算客户的"完全匹配"）
                perfect_match = (
                    budget_min <= price <= budget_max
                    and layout_ok
                    and (region_ok or not loc)
                    and type_ok
                )
                scores.append({**prop, 'score': score, 'match_reasons': reasons,
                               'perfect_match': perfect_match, '_region_ok': region_ok})

        # 区域硬优先级 tier（2026-08-30 加，治"客户明确要某区但其他区房源排在前面"）：
        # 客户 location 非空时，区域匹配(region_ok)的房源一律排在区域不符的前面，同 tier 内再按 score 降序。
        # 真实案例：周女士要秀英区，4 套其他区(各75分)排唯一真秀英区(138万,60分)前面。
        if loc:
            scores.sort(key=lambda x: (x['_region_ok'], x['score']), reverse=True)
        else:
            scores.sort(key=lambda x: x['score'], reverse=True)
        # 排序用完即剥临时键，不污染返回给模型的结果
        for _s in scores:
            _s.pop('_region_ok', None)
        return scores[:top_n]
    
    def match_all_customers(self, top_n=1, customer_type=None, tier=None, district=None):
        """批量匹配：为全部 active 客户（排除有交易的）逐客户生成匹配明细与汇总

        每个客户必有一行（无匹配显式标注"无匹配"），汇总统计由代码生成。
        2026-08-13 真实教训：模型自行汇总必错（完全匹配 42 vs 明细 41、漏 4 位客户），
        批量匹配汇报必须用本方法输出，禁止模型口算。
        """
        with self.get_session() as s:
            customers = s.query(Customer).filter(Customer.status == 'active').all()
        if tier:
            customers = [c for c in customers if c.tier == tier]
        if customer_type:
            values = customer_type_filter(customer_type)
            customers = [c for c in customers if values is not None and (c.customer_type or None) in values]
        customers = [c for c in customers if not self.customer_has_deal(c.id)]
        if district:
            dnorm = self._norm_district(district)
            customers = [c for c in customers
                         if c.location and (dnorm == self._norm_district(c.location) or dnorm in c.location)]

        # 候选池按客户类型分桶、整批只从库拉一次（2026-09-24 修：原先每个客户各自
        # iter_available_properties 全量拉一遍，实测 12000 套 × 100 客户要 41 秒）。
        # 户型/租买/类型/价格/区域判定全在 match_property 的评分循环里，与"池是否预筛"无关，
        # 所以复用同一份候选不会改变结果（改前改后逐客户比对完全一致，见 tests/real_estate/test_batch_match_pool.py）。
        pool_cache = {}

        def _pool_for(ctype):
            key = self._CUSTOMER_TYPE_TO_PROP_TYPE.get(ctype or '') or '__all__'
            if key not in pool_cache:
                pushdown = {}
                want = self._CUSTOMER_TYPE_TO_PROP_TYPE.get(ctype or '')
                if want:
                    pushdown['property_type'] = want
                pool_cache[key] = list(self.iter_available_properties(**pushdown))
            return pool_cache[key]

        rows = []
        for c in customers:
            matches = self.match_property(c.id, top_n, pool=_pool_for(c.customer_type))
            has_perfect = any(m.get('perfect_match') for m in matches)
            status = '完全匹配' if has_perfect else ('接近匹配' if matches else '无匹配')
            rows.append({
                'customer_id': c.id,
                'customer_name': c.name,
                'tier': c.tier,
                'customer_type': c.customer_type,
                'budget_min': c.budget_min,
                'budget_max': c.budget_max,
                'location': c.location,
                'layout_pref': c.layout_pref,
                'match_status': status,
                'matches': matches,
            })
        summary = {
            'total': len(rows),
            'perfect_match': sum(1 for r in rows if r['match_status'] == '完全匹配'),
            'close_match': sum(1 for r in rows if r['match_status'] == '接近匹配'),
            'no_match': sum(1 for r in rows if r['match_status'] == '无匹配'),
        }
        return {'customers': rows, 'summary': summary}
    
    def _parse_area(self, area_pref):
        if not area_pref: return (0, 999999)
        m = re.search(r'(\d+)[\-\~到](\d+)', area_pref)
        if m: return (float(m.group(1)), float(m.group(2)))
        m = re.search(r'(\d+)', area_pref)
        if m:
            v = float(m.group(1))
            return (v * 0.9, v * 1.1)
        return (0, 999999)
    
    @staticmethod
    def _parse_layout_pref(layout_pref):
        """把客户户型偏好解析成可复用的判定计划（每客户解析一次，循环里只做整数比较）

        2026-09-18 加：原实现每套房跑 3 次正则（硬过滤 / 加分 / 完全匹配判定），
        6 万套 × 3 次 = 18 万次正则，是批量匹配的主要耗时来源。
        """
        if not layout_pref:
            return None

        def _parse(token):
            m = re.search(r'(\d+)\s*[-~到至]\s*(\d+)' + token, layout_pref)
            if m:
                return ('range', int(m.group(1)), int(m.group(2)))
            m = re.search(r'(\d+)' + token, layout_pref)
            if m:
                return ('exact', int(m.group(1)))
            return None

        plan = {'rooms': _parse('室'), 'halls': _parse('厅')}
        return plan if (plan['rooms'] or plan['halls']) else None

    @staticmethod
    def _layout_ok(plan, rooms, halls):
        """按计划判定房源户型是否符合（纯整数比较，无正则）"""
        if plan is None:
            return True
        if rooms is None and halls is None:
            return True
        rp = plan.get('rooms')
        if rp:
            if rp[0] == 'range':
                if rooms is not None and not (rp[1] <= rooms <= rp[2]):
                    return False
            elif rooms is not None and rooms != rp[1]:
                return False
        hp = plan.get('halls')
        if hp:
            if hp[0] == 'range':
                if halls is not None and not (hp[1] <= halls <= hp[2]):
                    return False
            elif halls is not None and halls != hp[1]:
                return False
        return True

    def _match_layout(self, layout_pref, rooms, halls):
        """兼容入口（内部先用解析计划再判定）"""
        return self._layout_ok(self._parse_layout_pref(layout_pref), rooms, halls)
    
    # ---------- 匹配辅助（2026-08-13 加） ----------
    @staticmethod
    def _norm_district(s):
        """区域名归一化：'海口美兰区海甸岛'/'美兰-海甸岛'/'美兰区' → '美兰'

        2026-08-13 真实案例：存量区域字段三种写法并存（海口美兰区…/美兰-海甸岛/美兰区），
        裸子串匹配（客户"美兰区" in "美兰-海甸岛"=False）导致同套房区域分差 15 分。
        """
        if not s:
            return ''
        s = str(s).strip()
        # 2026-08-28 修：原 {2,3}? 非贪婪遇到城市前缀（"海口美兰区"，真实数据无"市"字）
        # 会截出"口美兰"。改为两级策略：①"XX市XX区"形式先剥市名取区名；
        # ②其余取"区"字紧邻前 2 字（国内市辖区名以 2 字为主：美兰/秀英/龙华/琼山/吉阳）。
        # 已知限制：3 字区名（如重庆沙坪坝区）无市字前缀时会截出"坪坝"，
        # 但 _match_region 的子串兜底保证端到端匹配仍正确。
        m = re.search(r'[\u4e00-\u9fa5]{2,3}市([\u4e00-\u9fa5]{2,3})区', s)
        if m:
            return m.group(1)
        m = re.search(r'([\u4e00-\u9fa5]{2})区', s)
        if m:
            return m.group(1)
        return s.split('-')[0].strip()

    @staticmethod
    def _match_region(loc, prop_district, prop_community, reasons=None):
        """区域/小区匹配：区名归一化优先，原子串兜底（兼容客户直接填小片区如"海甸岛"）"""
        if not loc:
            return True  # 客户无区域需求视为区域满足（是否计入 perfect 由调用方决定）
        prop_district = prop_district or ''
        prop_community = prop_community or ''
        norm_loc = _norm_district_cached(loc)
        if norm_loc and (norm_loc == _norm_district_cached(prop_district) or norm_loc in prop_district):
            if reasons is not None: reasons.append("区域匹配")
            return True
        # 客户 location 含房源所在区名（如客户"秀英大道" vs 房源"秀英-秀英小街"）→ 区域级匹配
        #（2026-08-13 加：解决小片区粒度差异，客户写小片区、房源存"区-小片区"）
        prop_norm = _norm_district_cached(prop_district)
        if prop_norm and prop_norm in loc:
            if reasons is not None: reasons.append("区域匹配")
            return True
        if loc in prop_district:
            if reasons is not None: reasons.append("区域匹配")
            return True
        if loc in prop_community:
            if reasons is not None: reasons.append("小区匹配")
            return True
        return False

    _CUSTOMER_TYPE_TO_PROP_TYPE = {
        'buy_new': 'new',            # 买一手房 → 一手房
        'buy_second_hand': 'second_hand',  # 买二手房 → 二手房
        'rent': 'rental',            # 租房 → 出租
    }

    @classmethod
    def _match_type(cls, customer_type, prop_type):
        """客户购房类型 ↔ 房源类型；未细分类型（buy）不限"""
        want = cls._CUSTOMER_TYPE_TO_PROP_TYPE.get(customer_type or '')
        if not want:
            return True
        return want == prop_type

    def _tenant_req_ok(self, tenant_requirements, customer):
        """租客要求过滤（2026-08-29 加，功能：出租房源租客要求真实用于匹配）。

        租客要求（如"不吸烟/办居住证/学生优先"）不支持直接结构化匹配，
        此处做关键词级置信过滤：对否定要求（"不X"），若客户资料(备注/标签)含"X"→ 冲突→False；
        否则不排除（归 True，交由评分）。无要求或客户无资料 → True。
        """
        if not tenant_requirements:
            return True
        text = ''
        if isinstance(customer, dict):
            text = (customer.get('notes') or '') + ' ' + (customer.get('tags') or '')
        else:
            text = (getattr(customer, 'notes', '') or '') + ' ' + (getattr(customer, 'tags', '') or '')
        if not text:
            return True
        import re as _re
        negs = _re.findall(r'不([\u4e00-\u9fa5]{1,4})', str(tenant_requirements))
        for neg in negs:
            # 负向断言：客户资料里该词"前面不是'不'"才算冲突（"不吸烟"不算，独立"吸烟"才算）
            if _re.search(r'(?<!不)' + _re.escape(neg), text):
                return False
        return True

    # ---------- 经纪人配置 ----------
    def get_setting(self, key: str) -> str:
        """读取配置；不存在返回 None"""
        with self.get_session() as s:
            row = s.query(Setting).filter(Setting.key == key).first()
            return row.value if row else None

    def set_setting(self, key: str, value: str) -> str:
        """写入/更新配置，返回当前值"""
        with self.get_session() as s:
            row = s.query(Setting).filter(Setting.key == key).first()
            if row:
                row.value = value
                row.updated_at = datetime.now()
            else:
                row = Setting(key=key, value=value)
                s.add(row)
            s.commit()
            return value

    # ---------- 跟进 ----------
    def add_followup(self, **kwargs):
        with self.get_session() as s:
            f = Followup(**kwargs)
            s.add(f); s.commit(); s.refresh(f)
            return f.to_dict()
    
    def get_followups(self, customer_id, limit=20, with_total=False):
        """某位客户的跟进历史（最新在前）

        with_total=True 时返回 (本页明细, 该客户跟进总条数) —— 总数走 SQL 聚合，
        别拿本页条数当总数（2026-09-25 F136）。同一秒写入的多条按 id 兜底排序，保证顺序确定。
        """
        with self.get_session() as s:
            query = (s.query(Followup)
                     .filter(Followup.customer_id == customer_id)
                     .order_by(Followup.created_at.desc(), Followup.id.desc()))
            total = query.count() if with_total else None
            items = [f.to_dict() for f in query.limit(limit).all()]
        return (items, total) if with_total else items

    def get_latest_followup(self, customer_id):
        """该客户最新一条**人为**跟进（按 created_at，同一时刻按 id 最大）→ dict 或 None

        「设提醒会不会顶掉旧的逾期」这类判断要用它（口径与 get_overdue 一致）。
        2026-09-26（F215）：带看档 3 自动写的那两条不算（`source_viewing_id` 非空），
        否则"最新一条"会被系统自造记录占住，判断跟着错。
        """
        with self.get_session() as s:
            row = (_only_human_followups(
                       s.query(Followup).filter(Followup.customer_id == customer_id))
                   .order_by(Followup.created_at.desc(), Followup.id.desc())
                   .first())
            return row.to_dict() if row else None

    def find_reminder(self, customer_id, next_date):
        """同客户 + 同一时刻的提醒（type='reminder'）→ dict 或 None（重复设置判定用）"""
        with self.get_session() as s:
            row = (s.query(Followup)
                   .filter(Followup.customer_id == customer_id,
                           Followup.type == 'reminder',
                           Followup.next_date == next_date)
                   .order_by(Followup.id.desc())
                   .first())
            return row.to_dict() if row else None

    def find_followup_by_viewing(self, viewing_id, followup_type=None):
        """某次带看自动生成的那条记录 → dict 或 None

        带看重复记「完成」时要靠它精确找到那一条（有就不重复建）。`followup_type`
        用来区分同一条带看产生的两类记录：'reminder'（回访提醒）与 'visit'（带看跟进）。
        """
        with self.get_session() as s:
            query = s.query(Followup).filter(Followup.source_viewing_id == viewing_id)
            if followup_type:
                query = query.filter(Followup.type == followup_type)
            row = query.order_by(Followup.id.asc()).first()
            return row.to_dict() if row else None

    def update_followup(self, followup_id, **kwargs):
        """改一条跟进（只写传进来的字段）→ 更新后的 dict 或 None"""
        with self.get_session() as s:
            row = s.query(Followup).get(followup_id)
            if not row:
                return None
            for k, val in kwargs.items():
                if hasattr(row, k):
                    setattr(row, k, val)
            s.commit()
            s.refresh(row)
            return row.to_dict()

    def update_followup_content(self, followup_id, content):
        """改一条跟进的正文（只用于「同时间的提醒改了内容」）→ 更新后的 dict 或 None"""
        with self.get_session() as s:
            row = s.query(Followup).get(followup_id)
            if not row:
                return None
            row.content = content
            s.commit()
            s.refresh(row)
            return row.to_dict()

    def get_property_titles(self, property_ids):
        """按 id 批量取房源标题 → {id: 标题}（只取要展示的两列，缺号不补占位）"""
        ids = [int(i) for i in set(property_ids or []) if i is not None]
        if not ids:
            return {}
        with self.get_session() as s:
            return {row[0]: row[1] for row in
                    s.query(Property.id, Property.title).filter(Property.id.in_(ids)).all()}
    
    def get_overdue(self, before=None):
        """逾期跟进：按客户取最新一条跟进，其 next_date 已过期才算逾期

        2026-08-12 修复：原逻辑返回所有 next_date 过期的跟进记录，导致
        record_viewing 自动生成的"带看后回访"提醒（1小时后过期）永远挂在
        逾期列表——经纪人记了新跟进也不消解，cron 每 30 分钟重复提醒。
        现改为：每个客户只看最新一条跟进（created_at 最大），只有它也过期
        才报逾期；客户有过更新的跟进记录说明已处理，旧提醒不再报。

        before: 截止时间，默认当前时刻。传"明天零点"即"今天要跟进或已逾期"
        （早报 09:00 要提醒今天到期的客户，而那些 next_date 还没到点，
        只用 now 当截止会漏掉——2026-09-23 加）。
        """
        with self.get_session() as s:
            # 2026-09-25（F140c）：改成 SQL 侧取"每位客户最新一条跟进"（created_at 最大、
            # 同一时刻按 id 最大），不再把全表跟进读进内存再聚合（1.2 万客户 + 3.6 万跟进：
            # 0.6s → 预计 0.05s）。口径与原来一致：每客户只看最新一条。
            # 2026-09-26（F215，拍板全库统一）：只认**人为**跟进 —— 带看档 3 自动写的带看跟进/
            # 回访提醒不算"联系过"（见 `_only_human_followups`）。**两处子查询与主查询必须同一口径**，
            # 否则主查询按人为过滤、子查询却取到自动记录的时间戳 → 一条都匹配不上、逾期清单整片变空。
            from sqlalchemy import func
            from sqlalchemy.orm import aliased
            other = aliased(Followup)
            max_created = (s.query(func.max(other.created_at))
                           .filter(other.customer_id == Followup.customer_id,
                                   other.source_viewing_id.is_(None))
                           .scalar_subquery())
            max_id = (s.query(func.max(other.id))
                      .filter(other.customer_id == Followup.customer_id,
                              other.created_at == Followup.created_at,
                              other.source_viewing_id.is_(None))
                      .scalar_subquery())
            now = before or datetime.now()
            rows = (_only_human_followups(s.query(Followup))
                    .filter(Followup.customer_id.isnot(None),
                            Followup.created_at == max_created,
                            Followup.id == max_id,
                            Followup.next_date.isnot(None),
                            Followup.next_date < now)
                    # 逾期最久在前（同一时刻按 id 升序，顺序确定）—— 工具层直接照此展示
                    .order_by(Followup.next_date.asc(), Followup.id.asc())
                    .all())
            return [f.to_dict() for f in rows]
    
    def get_stale_customers(self):
        """流失预警：按最后互动时间计算超期客户
        
        S级>5天 / A级>10天 / B级>30天 / C级>60天 无互动 → 预警。
        最后互动时间 = 最后一次**人为**跟进（带看档 3 自动写的带看跟进/回访提醒不算，见
        `_only_human_followups` —— 它们会把「带看后没人跟」这件事掩盖掉）；无跟进则取客户创建时间。
        """
        with self.get_session() as s:
            # 2026-09-25（F140a）：原先"每位活跃客户单独查一次最新跟进"= 1.2 万次查询（≈7.7 秒），
            # 改成一次 group by 聚合取每客户最新跟进时间再合并（≈0.05 秒），口径不变。
            from sqlalchemy import func
            last_followup = {row[0]: row[1] for row in
                             _only_human_followups(
                                 s.query(Followup.customer_id, func.max(Followup.created_at))
                                 .filter(Followup.customer_id.isnot(None)))
                             .group_by(Followup.customer_id).all()}
            now = datetime.now()
            result = []
            # 只取判断要用的四列：拉整行 Customer 会把 1.2 万个手机号解密一遍（≈0.7 秒）
            for cid, name, tier, created_at in (s.query(Customer.id, Customer.name,
                                                        Customer.tier, Customer.created_at)
                                                .filter(Customer.status == 'active').all()):
                last_time = last_followup.get(cid) or created_at
                if not last_time:
                    continue
                days = (now - last_time).days
                threshold = {'S': 5, 'A': 10, 'B': 30}.get(tier, 60)
                if days > threshold:
                    result.append({
                        'customer_id': cid, 'name': name, 'tier': tier,
                        'days_inactive': days, 'threshold': threshold,
                        'last_contact': last_time.isoformat() if last_time else None,
                    })
            # 最久没互动的在前（调用方直接取前 N 位就是"最该跟进的"）
            result.sort(key=lambda x: x['days_inactive'], reverse=True)
            return result
    
    def auto_downgrade_stale_customers(self, stale=None):
        """流失自动降级：S级超5天→A，A级超10天→B，B级超30天→C（**同一天最多降一级**）
        
        降级同时写入变更历史。返回降级明细和仍超期的客户数。

        2026-09-25 两处修复：
        - **F141 一天最多降一级**：原先每次调用都按"当前等级"重算阈值，而早报、午间检查、
          经纪人手动查逾期都会调它 → 同一个客户一天内 S→A→B→C 连降三级（等级是给经纪人看的
          优先级别，掉了会直接影响跟进排序）。现在该客户**今天已经有一次等级变更**
          （自动降级或人工调整都算）就不再降。
        - **F140b 复用快照**：可传入已算好的 stale 列表，避免同一次请求里重复全库扫描。
        """
        stale = self.get_stale_customers() if stale is None else stale
        downgrades = []
        today = datetime.now().date()
        with self.get_session() as s:
            from sqlalchemy import func
            last_tier_change = {row[0]: row[1] for row in
                                s.query(CustomerChange.customer_id, func.max(CustomerChange.created_at))
                                .filter(CustomerChange.field == 'tier')
                                .group_by(CustomerChange.customer_id).all()}
            # 2026-09-25（F160）：原先逐客户 query(Customer).get(id)（1.2 万流失客户 = 1.2 万次查询 ≈ 11 秒），
            # 改成分批一次取出来再改 —— 判定口径一行未动，只去 N+1。
            for start in range(0, len(stale), 500):
                chunk = stale[start:start + 500]
                by_id = {c.id: c for c in s.query(Customer)
                         .filter(Customer.id.in_([i['customer_id'] for i in chunk])).all()}
                for item in chunk:
                    c = by_id.get(item['customer_id'])
                    if not c:
                        continue
                    changed_at = last_tier_change.get(c.id)
                    if changed_at and changed_at.date() == today:
                        continue
                    mapping = {'S': 'A', 'A': 'B', 'B': 'C'}
                    new_tier = mapping.get(c.tier)
                    if new_tier and c.tier != new_tier:
                        old = c.tier
                        c.tier = new_tier
                        s.add(CustomerChange(customer_id=c.id, field='tier',
                                             old_value=old, new_value=new_tier))
                        downgrades.append({
                            'customer_id': c.id, 'name': c.name,
                            'from': old, 'to': new_tier, 'days_inactive': item['days_inactive'],
                            'threshold': item.get('threshold'),
                        })
            s.commit()
        return {'downgrades': downgrades, 'still_stale': len(stale) - len(downgrades)}

    def update_owner(self, owner_id, **kwargs):
        """改房东资料（每次改动写一条留痕；加密字段只留掩码）→ 更新后的 dict，房东不存在返回 None

        与客户侧 `update_customer` 同一套口径：只写传进来的字段、值没变就不留痕。
        """
        with self.get_session() as s:
            o = s.query(Owner).get(owner_id)
            if not o:
                return None
            for k, v in kwargs.items():
                if not hasattr(o, k):
                    continue
                old = getattr(o, k)
                if old != v:
                    s.add(OwnerChange(owner_id=owner_id, field=k,
                                      old_value=_change_trace_value(Owner, k, old),
                                      new_value=_change_trace_value(Owner, k, v)))
                setattr(o, k, v)
            s.commit()
            s.refresh(o)
            return o.to_dict()

    def count_customers(self, status=None):
        """客户数（轻量 count，空态判断用）"""
        with self.get_session() as s:
            query = s.query(Customer)
            if status:
                query = query.filter(Customer.status == status)
            return query.count()

    def get_customer_labels(self, customer_ids):
        """批量取客户的姓名与等级 → {id: {name, tier}}（只取展示要用的两列，缺号不补）

        分批查询：`IN` 里的变量数在 sqlite 上有上限（999），万级清单一次塞进去会报错。
        """
        ids = [int(i) for i in set(customer_ids or []) if i is not None]
        if not ids:
            return {}
        labels = {}
        with self.get_session() as s:
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                for row in (s.query(Customer.id, Customer.name, Customer.tier)
                            .filter(Customer.id.in_(chunk)).all()):
                    labels[row[0]] = {'name': row[1], 'tier': row[2]}
        return labels
    
    # ---------- 数据清理（彻底删除 / 归档 / 恢复，2026-09-23 加） ----------
    # 背景：经纪人要求"删除/清空"数据时，系统此前只能把状态改成已售/已租/已关闭，
    # 记录连同电话、价格、业主信息一直留在库里，而"列客户""客户总数"又会把已关闭的
    # 算进去 → 数据越积越"虚"。这里补上真正的物理删除能力；保护口径与去重一致：
    # 有关联带看/成交/跟进的记录默认不删（宁可留着不误删），明确要求才连历史一起删。

    _PURGE_LABELS = {
        'followups': '跟进', 'viewings': '带看', 'deals': '成交',
        'changes': '需求变更', 'referrals': '转介绍', 'price_history': '调价记录',
    }

    @classmethod
    def parse_purge_before(cls, before):
        """清理用的"某个时间点之前"解析 → (datetime 或 None, 中文提示或 None)

        与共用件 `norm_date` 的分工：`norm_date` 面向"未来的到期/提醒日"（裸月日按未来那天算），
        而清理的 before 是"过去的截止点"——裸月日要按**最近一次已经过去的那天**算（今天 9-27 写
        「9月23日」= 今年 9-23）。所以这里单独一套解析，不去动共用件的语义（免得影响到期日类工具）。
        支持：2026-01-01 / 2026/1/1 / 2026年1月1日 / 9月23日（最近一次）/ 今天·昨天·前天·大前天 /
        上周 / 上个月 / 3天前 / 2周前 / 3个月前 / 带时间的 2026-09-27 10:00。
        """
        if before in (None, ''):
            return None, None
        if isinstance(before, datetime):
            return before, None
        raw = str(before).strip()
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
            try:
                return datetime.strptime(raw, fmt), None
            except ValueError:
                continue
        text = re.sub(r'[\s\u3000]+', '', raw)
        today = datetime.now()
        day0 = datetime.combine(today.date(), datetime.min.time())
        past_words = {'今天': 0, '昨天': 1, '前天': 2, '大前天': 3}
        if text in past_words:
            return day0 - timedelta(days=past_words[text]), None
        if text in ('上周', '上星期', '上个星期'):
            return day0 - timedelta(days=7), None
        if text in ('上个月', '上月'):
            month_index = today.year * 12 + (today.month - 1) - 1
            year, month = divmod(month_index, 12)
            return datetime(year, month + 1, min(today.day, 28)), None
        m = re.fullmatch(r'(\d+)(天|日|周|个星期|星期|个月|月)前', text)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            if unit in ('天', '日'):
                return day0 - timedelta(days=n), None
            if unit in ('周', '个星期', '星期'):
                return day0 - timedelta(weeks=n), None
            month_index = today.year * 12 + (today.month - 1) - n
            year, month = divmod(month_index, 12)
            return datetime(year, month + 1, min(today.day, 28)), None
        normalized = (text.replace('年', '-').replace('月', '-').replace('日', '')
                          .replace('/', '-').replace('.', '-'))
        parts = [p for p in normalized.split('-') if p != '']
        try:
            if len(parts) == 3:
                return datetime(int(parts[0]), int(parts[1]), int(parts[2])), None
            if len(parts) == 2:                     # 只有月日 → 最近一次（今年已过就算今年）
                candidate = datetime(today.year, int(parts[0]), int(parts[1]))
                if candidate > day0:
                    candidate = datetime(today.year - 1, int(parts[0]), int(parts[1]))
                return candidate, None
        except ValueError:
            return None, f'日期没认出来：{before}'
        return None, f'日期没认出来：{before}'

    def _purge_boundary(self, before):
        """把日期写法统一成"创建时间早于它"的比较值（None = 不限）

        2026-09-27（第十二组 F433）：此前只认 `YYYY-MM-DD`，写「昨天」这类相对说法会抛异常逃出工具层。
        现在走 `parse_purge_before`（过去截止点口径）；工具层会先用它做校验并给中文提示，
        这里的抛错只兜底（脚本直接调库时）。
        """
        parsed, problem = self.parse_purge_before(before)
        if problem:
            raise ValueError(f'{problem}（可以写 2026-01-01，也可以说「昨天」「上个月」）')
        return parsed

    def _purge_related_counts(self, s, kind, oid):
        """一条记录挂了多少关联历史（决定物理删除是否安全）"""
        if kind == 'property':
            return {
                'price_history': s.query(PriceHistory).filter(PriceHistory.property_id == oid).count(),
                'followups': s.query(Followup).filter(Followup.property_id == oid).count(),
                'viewings': s.query(Viewing).filter(Viewing.property_id == oid).count(),
                'deals': s.query(Deal).filter(Deal.property_id == oid).count(),
            }
        return {
            'followups': s.query(Followup).filter(Followup.customer_id == oid).count(),
            'viewings': s.query(Viewing).filter(Viewing.customer_id == oid).count(),
            'deals': s.query(Deal).filter(Deal.customer_id == oid).count(),
            'changes': s.query(CustomerChange).filter(CustomerChange.customer_id == oid).count(),
            'referrals': s.query(Referral).filter(or_(
                Referral.referrer_customer_id == oid,
                Referral.referred_customer_id == oid)).count(),
        }

    def _purge_skip_reason(self, related, kind):
        """不可以直接物理删除的理由（None = 可以删）

        调价记录属于房源自身明细，随房源一起删，不算"牵挂"。
        """
        protected = ('followups', 'viewings', 'deals') if kind == 'property' \
            else ('followups', 'viewings', 'deals', 'changes', 'referrals')
        hit = {k: related.get(k) for k in protected if related.get(k)}
        if hit:
            detail = '、'.join(f'{self._PURGE_LABELS[k]}{v}条' for k, v in hit.items())
            return f'有关联记录（{detail}），需要明确要求"连历史一起删"'
        return None

    def _purge_targets(self, s, kind, statuses=None, before=None):
        """按状态 + 创建时间挑出候选记录（只读）

        2026-09-27（第十二组 F432）：状态参数先归一 —— 此前传中文「已售」匹配不到任何记录、
        还静默返回 0 条，经纪人会以为清干净了。现在中文别名与英文值等价（认不出的由工具层报出来）。
        """
        cutoff = self._purge_boundary(before)
        statuses, _bad = self.norm_purge_statuses(statuses)
        targets = []
        if kind in ('property', 'all'):
            q = s.query(Property).filter(Property.status.in_(statuses or ['sold', 'rented']))
            if cutoff:
                q = q.filter(Property.created_at < cutoff)
            targets += [('property', p) for p in q.order_by(Property.id).all()]
        if kind in ('customer', 'all'):
            q = s.query(Customer).filter(Customer.status.in_(statuses or ['closed']))
            if cutoff:
                q = q.filter(Customer.created_at < cutoff)
            targets += [('customer', c) for c in q.order_by(Customer.id).all()]
        return targets

    def _purge_entry(self, s, kind, obj):
        """把一条候选记录整理成"给经纪人看的清单条目"（含关联条数与不能删的原因）"""
        related = self._purge_related_counts(s, kind, obj.id)
        status_word = self.purge_status_label(kind, obj.status)
        if kind == 'property':
            entry = {'kind': '房源', 'id': obj.id, 'title': obj.title,
                     'status': status_word, 'district': obj.district,
                     'price': float(obj.price) if obj.price is not None else None,
                     'area': obj.area}
        else:
            entry = {'kind': '客户', 'id': obj.id, 'name': obj.name,
                     'status': status_word, 'tier': obj.tier, 'source': obj.source}
        entry['related'] = related
        entry['related_total'] = sum(related.values())
        entry['skip_reason'] = self._purge_skip_reason(related, kind)
        return entry

    def purge_preview(self, kind='all', statuses=None, before=None):
        """清理预演：只列"会删什么、会跳过什么、为什么跳过"，绝不修改数据"""
        with self.get_session() as s:
            targets = self._purge_targets(s, kind, statuses, before)
            entries = [self._purge_entry(s, k, o) for k, o in targets]
        deletable = [e for e in entries if not e['skip_reason']]
        skipped = [e for e in entries if e['skip_reason']]
        return {
            'success': True, 'kind': kind, 'before': before,
            'matched': len(entries), 'deletable': len(deletable), 'skipped': len(skipped),
            'deletable_ids': [e['id'] for e in deletable],
            'skipped_entries': skipped,
            'entries': entries,
            'related_total': sum(e['related_total'] for e in deletable),
        }

    def _purge_delete(self, s, kind, oid):
        """连带删除一条记录及其关联行（调用方负责确认与提交）"""
        if kind == 'property':
            s.query(PriceHistory).filter(PriceHistory.property_id == oid).delete(synchronize_session=False)
            s.query(Followup).filter(Followup.property_id == oid).delete(synchronize_session=False)
            s.query(Viewing).filter(Viewing.property_id == oid).delete(synchronize_session=False)
            s.query(Deal).filter(Deal.property_id == oid).delete(synchronize_session=False)
            row = s.query(Property).get(oid)
        else:
            s.query(Followup).filter(Followup.customer_id == oid).delete(synchronize_session=False)
            s.query(Viewing).filter(Viewing.customer_id == oid).delete(synchronize_session=False)
            s.query(Deal).filter(Deal.customer_id == oid).delete(synchronize_session=False)
            s.query(CustomerChange).filter(CustomerChange.customer_id == oid).delete(synchronize_session=False)
            ref_ids = [r[0] for r in s.query(Referral.id).filter(or_(
                Referral.referrer_customer_id == oid,
                Referral.referred_customer_id == oid)).all()]
            if ref_ids:
                # 别人的成交单可能还挂在这条转介绍上：只摘链接，不动成交单
                s.query(Deal).filter(Deal.referral_id.in_(ref_ids)).update(
                    {'referral_id': None}, synchronize_session=False)
                s.query(Referral).filter(Referral.id.in_(ref_ids)).delete(synchronize_session=False)
            row = s.query(Customer).get(oid)
        if row is not None:
            s.delete(row)

    # 关联记录的中文标签（回执里给经纪人看）
    _RELATED_LABELS = (('viewings', '带看'), ('deals', '成交'), ('followups', '跟进'),
                       ('changes', '需求变更'), ('referrals', '转介绍'), ('price_history', '调价'))

    @staticmethod
    def _delete_target_text(kind, entry) -> str:
        """回执里点名删的是哪一条（编号 + 名字，便于经纪人核对）

        注意 kind 必须显式传：清单条目里的 kind 值从 2026-09-27 起是中文（房源/客户），
        不能再拿它当内部码判断（否则会读 entry["name"] 报 KeyError）。
        """
        if kind == 'property':
            return f'「{entry["title"]}」（房源编号 {entry["id"]}）'
        return f'「{entry["name"]}」（客户编号 {entry["id"]}）'

    @classmethod
    def _purge_related_text(cls, entry) -> str:
        """关联明细（只列非零项；全零就说"无关联记录"）"""
        parts = []
        for key, label in cls._RELATED_LABELS:
            count = entry['related'].get(key)
            if count:
                parts.append(f'{label} {count}')
        return ' / '.join(parts) if parts else '无关联记录'

    @staticmethod
    def _status_suggestion(kind) -> str:
        return '把它标成已售/已租' if kind == 'property' else '把它标成已关闭'

    @staticmethod
    def _where_text(kind) -> str:
        return '房源列表、报表、匹配' if kind == 'property' else '客户列表、跟进提醒、报表'

    # ---- 批量清理（purge_data）的口径件（2026-09-27 第十二组）----
    # 参数认中文别名（经纪人嘴里的说法），回执与清单里的值一律中文，不外露英文枚举。
    PURGE_KIND_ALIASES = {
        'property': 'property', 'properties': 'property', 'house': 'property',
        '房源': 'property', '房子': 'property', '房': 'property',
        'customer': 'customer', 'customers': 'customer', 'client': 'customer',
        '客户': 'customer', '客': 'customer',
        'all': 'all', 'both': 'all', '两者': 'all', '全部': 'all', '都要': 'all',
        '房源和客户': 'all', '客户和房源': 'all', '所有': 'all',
    }
    PURGE_MODE_ALIASES = {
        'delete': 'delete', 'purge': 'delete', 'remove': 'delete',
        '彻底删除': 'delete', '删除': 'delete', '真删': 'delete', '清掉': 'delete',
        'archive': 'archive', 'mark': 'archive', 'archive_only': 'archive',
        '只改状态': 'archive', '归档': 'archive', '改状态': 'archive', '标记': 'archive',
    }
    PROPERTY_STATUS_LABELS = {'available': '在售', 'sold': '已售', 'rented': '已租'}
    CUSTOMER_STATUS_LABELS = {'active': '在跟', 'paused': '暂缓', 'closed': '已关闭',
                              'potential': '潜在', 'lost': '已流失'}
    PURGE_STATUS_ALIASES = {
        'available': 'available', 'available_for_sale': 'available', '在售': 'available', '待售': 'available',
        'sold': 'sold', '已售': 'sold', '已成交': 'sold', '卖掉': 'sold',
        'rented': 'rented', '已租': 'rented', '已出租': 'rented',
        'active': 'active', '在跟': 'active', '活跃': 'active',
        'paused': 'paused', '暂缓': 'paused',
        'closed': 'closed', '已关闭': 'closed', '流失': 'closed', '已流失': 'closed',
        'potential': 'potential', '潜在': 'potential',
    }

    @classmethod
    def purge_kind_word(cls, kind) -> str:
        return '房源' if kind == 'property' else '客户'

    @classmethod
    def purge_status_label(cls, kind, status):
        table = cls.PROPERTY_STATUS_LABELS if kind == 'property' else cls.CUSTOMER_STATUS_LABELS
        return table.get(status, status)

    @classmethod
    def norm_purge_kind(cls, value):
        return cls.PURGE_KIND_ALIASES.get(str(value).strip().lower()) if value not in (None, '') else None

    @classmethod
    def norm_purge_mode(cls, value):
        return cls.PURGE_MODE_ALIASES.get(str(value).strip().lower()) if value not in (None, '') else None

    @classmethod
    def norm_purge_statuses(cls, statuses):
        """状态参数归一 → (规范值列表或 None, 认不出的原文列表)"""
        if statuses in (None, '', []):
            return None, []
        if isinstance(statuses, str):
            statuses = [p for p in re.split(r'[,，、/\\s]+', statuses) if p.strip()]
        good, bad = [], []
        for item in statuses:
            key = str(item).strip().lower()
            mapped = cls.PURGE_STATUS_ALIASES.get(key)
            if mapped:
                good.append(mapped)
            else:
                bad.append(str(item).strip())
        return (good or None), bad

    def _delete_one(self, kind, property_id=None, title=None, customer_id=None,
                    name=None, phone=None, force=False, dry_run=False):
        label = '房源' if kind == 'property' else '客户'
        model = Property if kind == 'property' else Customer
        oid = property_id if kind == 'property' else customer_id
        with self.get_session() as s:
            if oid is not None:
                row = s.query(model).get(oid)
                if row is None:
                    return {'success': False, 'error': 'not_found',
                            'message': f'库里没有 id={oid} 的{label}'}
            else:
                key = title if kind == 'property' else name
                if not key:
                    return {'success': False, 'error': 'missing_key',
                            'message': f'请提供{label}编号或{label}名称'}
                col = Property.title if kind == 'property' else Customer.name
                rows = s.query(model).filter(col == key).all()
                if kind == 'customer' and phone:
                    rows = [r for r in rows if (r.phone or '') == phone]
                if not rows:
                    return {'success': False, 'error': 'not_found',
                            'message': f'没找到叫"{key}"的{label}，可先列一下确认名称或编号'}
                if len(rows) > 1:
                    return {'success': False, 'error': 'ambiguous',
                            'message': f'有 {len(rows)} 条叫"{key}"的{label}，请报编号确认',
                            'candidates': [self._purge_entry(s, kind, r) for r in rows]}
                row = rows[0]
            entry = self._purge_entry(s, kind, row)
            entry['dry_run'] = dry_run
            target = self._delete_target_text(kind, entry)
            related_text = self._purge_related_text(entry)
            if entry['skip_reason'] and not force:
                entry.update(success=False, error='has_history', message=(
                    f'没有删除{target}：{entry["skip_reason"]}。'
                    f'如果只是想让它别再出现，我可以{self._status_suggestion(kind)}（随时能改回来）；'
                    f'确实要连历史一起删掉、再也不会恢复，就跟我说「连历史一起删」。'))
                return entry
            if dry_run:
                entry.update(success=True, message=(
                    f'预演：可以删掉{target}（连同 {entry["related_total"]} 条关联记录：{related_text}）。'
                    f'这一步不动任何数据。'))
                return entry
            self._purge_delete(s, kind, row.id)
            s.commit()
            entry.update(success=True, deleted_related=entry['related_total'], message=(
                f'已彻底删除{target}及其 {entry["related_total"]} 条关联记录（{related_text}）。'
                f'删除后取不回来，之后在{self._where_text(kind)}里都不会再出现。'))
            return entry

    def delete_property(self, property_id=None, title=None, force=False, dry_run=False):
        """彻底删除一套房源（含调价记录；有关联带看/成交/跟进时默认拒删）

        force=True 连关联历史一起删；dry_run=True 只报告不动手。
        """
        return self._delete_one('property', property_id=property_id, title=title,
                                force=force, dry_run=dry_run)

    def delete_customer(self, customer_id=None, name=None, phone=None, force=False, dry_run=False):
        """彻底删除一位客户（含跟进/带看/成交/需求变更/转介绍引用；有历史时默认拒删）"""
        return self._delete_one('customer', customer_id=customer_id, name=name, phone=phone,
                                force=force, dry_run=dry_run)

    _PURGE_LIST_LIMIT = 20      # 清单里最多列几条（上万条时别把明细撑进对话）

    def _purge_where_text(self, kind) -> str:
        """删完之后"哪里都不会再出现"（按清理对象说）"""
        if kind == 'property':
            return '房源列表、报表、匹配'
        if kind == 'customer':
            return '客户列表、跟进提醒、报表'
        return '房源列表、客户列表、报表'

    @staticmethod
    def _purge_count_text(entries) -> str:
        """回执里说清删掉的是几套房 / 几位客户"""
        house = sum(1 for e in entries if e['kind'] == '房源')
        people = sum(1 for e in entries if e['kind'] == '客户')
        if house and people:
            return f'{house} 套房源、{people} 位客户'
        if house:
            return f'{house} 套房源'
        if people:
            return f'{people} 位客户'
        return '0 条记录'

    def purge_data(self, kind='all', statuses=None, before=None, mode='delete',
                   dry_run=True, force=False):
        """批量清理：默认只删"没有关联历史"的，有历史的跳过并列出原因

        mode='delete' 彻底删除；mode='archive' 只改状态（房源→已售/已租，客户→已关闭）。
        2026-09-27（第十二组 F430–F437）：模式/状态认中文别名、清单封顶 20 条并标明截断、
        回执一律中文（删掉的用中文说清"取不回来"，归档的用中文说清"记录都还在"）。
        """
        if mode not in ('delete', 'archive'):
            return {'success': False,
                    'message': f'处理方式只支持：彻底删除（默认）、只改状态。收到的是「{mode}」。'}
        with self.get_session() as s:
            targets = self._purge_targets(s, kind, statuses, before)
            if mode == 'archive':
                archived, already = [], []
                for (k, obj), entry in zip(targets, [self._purge_entry(s, k, o) for k, o in targets]):
                    new_status = 'closed' if k == 'customer' else (
                        'rented' if obj.property_type == 'rental' else 'sold')
                    if obj.status == new_status:
                        already.append(entry)
                        continue
                    if not dry_run:
                        obj.status = new_status
                    archived.append({**entry, 'new_status': new_status})
                if not dry_run:
                    s.commit()
                limit = self._PURGE_LIST_LIMIT
                truncated = len(archived) > limit
                head = ('预演：将把' if dry_run else '已把')
                tail = '这一步不动数据。' if dry_run else '这只是改状态，记录都还在，随时能改回来。'
                return {'success': True, 'mode': '只改状态', 'dry_run': dry_run,
                        'matched': len(targets), 'archived': len(archived),
                        'already_marked': len(already), 'entries': archived[:limit],
                        'truncated': truncated,
                        'message': (f'{head} {len(archived)} 条标记为已售/已租（客户标为已关闭）'
                                    f'（另有 {len(already)} 条已经是该状态）。{tail}'
                                    + (f'列表只列了前 {limit} 条，完整清单没放进对话'
                                       f'（要逐条看我可以分页列）。' if truncated else ''))}
            done, skipped = [], []
            entries = [self._purge_entry(s, k, o) for k, o in targets]
            for (k, obj), entry in zip(targets, entries):
                if entry['skip_reason'] and not force:
                    skipped.append(entry)
                    continue
                if not dry_run:
                    self._purge_delete(s, k, obj.id)
                done.append(entry)
            if not dry_run:
                s.commit()
            limit = self._PURGE_LIST_LIMIT
            related_total = sum(e['related_total'] for e in done)
            count_text = self._purge_count_text(done)
            if dry_run:
                message = f'预演：将彻底删除 {count_text}（连同 {related_total} 条关联记录）。这一步不动数据。'
            else:
                message = (f'已彻底删除 {count_text}（连同 {related_total} 条关联记录）。'
                           f'删除后取不回来，之后在{self._purge_where_text(kind)}里都不会再出现。')
            if skipped:
                message += f'另有 {len(skipped)} 条因有关联历史被跳过（要连历史一起删，请单独跟我说）。'
            truncated = len(done) > limit or len(skipped) > limit
            if truncated:
                message += f'列表只列了前 {limit} 条，完整清单没放进对话（要逐条看我可以分页列）。'
            return {'success': True, 'mode': '彻底删除', 'dry_run': dry_run,
                    'matched': len(targets), 'deleted': len(done), 'skipped_count': len(skipped),
                    'deleted_entries': done[:limit], 'skipped_entries': skipped[:limit],
                    'truncated': truncated,
                    'deleted_related': related_total, 'message': message}

    def restore_status(self, kind='all', statuses=None, before=None, dry_run=True):
        """撤销"为了清空而误标"的状态：房源已售/已租 → 在售，客户已关闭 → 在跟

        原始状态没有留痕，这里只能整体复位；只改状态，不新增或删除任何记录。
        """
        with self.get_session() as s:
            targets = self._purge_targets(s, kind, statuses, before)
            restored = []
            for k, obj in targets:
                new_status = 'active' if k == 'customer' else 'available'
                if obj.status == new_status:
                    continue
                if not dry_run:
                    obj.status = new_status
                if k == 'property':
                    restored.append({'kind': k, 'id': obj.id, 'title': obj.title, 'new_status': new_status})
                else:
                    restored.append({'kind': k, 'id': obj.id, 'name': obj.name, 'new_status': new_status})
            if not dry_run:
                s.commit()
            return {'success': True, 'dry_run': dry_run, 'restored': len(restored), 'entries': restored,
                    'message': f'{"预演：" if dry_run else ""}将把 {len(restored)} 条恢复为在售/在跟'}

    # ---------- 统计 ----------
    def count_overdue_followups(self):
        """逾期跟进数（SQL 聚合；口径与 get_overdue 完全一致：每位客户只看最新一条**人为**跟进且已过期）

        2026-09-24 改：统计里原先用 len(self.get_overdue())，会把**全部跟进记录**拉进内存再数
        —— 1.2 万客户 / 2.4 万跟进下约 0.5 秒，而同一口径的 SQL 聚合只要 0.014 秒。
        2026-09-26（F215）：排除带看档 3 自动写的两条（`source_viewing_id` 非空），
        子查询与主查询同一口径，与 `get_overdue` 保持一致。
        """
        from sqlalchemy import text as _text
        with self.get_session() as s:
            row = s.execute(_text(
                "SELECT COUNT(*) FROM re_followups f"
                " WHERE f.customer_id IS NOT NULL AND f.next_date IS NOT NULL AND f.next_date < :now"
                "   AND f.source_viewing_id IS NULL"
                "   AND f.id = (SELECT x.id FROM re_followups x WHERE x.customer_id = f.customer_id"
                "               AND x.source_viewing_id IS NULL"
                "               ORDER BY x.created_at DESC, x.id DESC LIMIT 1)"),
                {"now": datetime.now()}).fetchone()
            return int(row[0]) if row else 0

    def get_stats(self):
        """统计。客户数按"在跟"口径（活跃+暂缓），已关闭单列不计入

        2026-09-23 改：客户数此前把已关闭(closed)的也算了进去，客户越多、关掉的越多，
        报表数字就越"虚"。现在 total_customers=在跟客户数，已关闭放 closed_customers。
        """
        with self.get_session() as s:
            tracking = s.query(Customer).filter(Customer.status != 'closed').count()
            closed = s.query(Customer).filter(Customer.status == 'closed').count()
            active = s.query(Customer).filter(Customer.status == 'active').count()
            paused = s.query(Customer).filter(Customer.status == 'paused').count()
            tiers = {t: s.query(Customer).filter(
                Customer.tier == t, Customer.status != 'closed').count() for t in ['S','A','B','C']}
            # 客户类型分布（2026-09-24 加，与房源侧 available_by_type 对称）：经纪人问"多少租房客户"
            # 要一次答出来，而不是逐个类型调 list_customers。未细分（含历史 'buy'/空值）单列一档。
            type_counts = {t: s.query(Customer).filter(
                Customer.status != 'closed', Customer.customer_type == t).count()
                for t in ('buy_new', 'buy_second_hand', 'rent')}
            unsp_vals = customer_type_filter('unspecified')
            unsp_cols = [v for v in unsp_vals if v]
            unsp_cond = Customer.customer_type.in_(unsp_cols)
            if None in unsp_vals:
                unsp_cond = or_(unsp_cond, Customer.customer_type.is_(None))
            type_counts['unspecified'] = s.query(Customer).filter(
                Customer.status != 'closed', unsp_cond).count()
            typed = sum(type_counts.values())
            if typed < tracking:
                # 库里若有既不属于任何类型也不属于未分组的脏值（历史遗留），如实单列，别让分布对不上总数
                type_counts['other'] = tracking - typed
            props = s.query(Property).filter(Property.status == 'available').count()
            # 房源侧多维统计（2026-09-24 加）：原先只报一个"在售数"，经纪人问"一共多少套/卖了几套/
            # 多少套在出租"都答不了，而客户侧却有 4 个维度。
            total_props = s.query(Property).count()
            sold_props = s.query(Property).filter(Property.status == 'sold').count()
            rented_props = s.query(Property).filter(Property.status == 'rented').count()
            avail_by_type = {t: s.query(Property).filter(Property.status == 'available',
                                                        Property.property_type == t).count()
                             for t in ('new', 'second_hand', 'rental')}
            overdue = self.count_overdue_followups()
            return {
                'total_customers': tracking, 'closed_customers': closed,
                'active_customers': active, 'paused_customers': paused,
                'customer_count_note': ('客户数按"在跟"统计（活跃+暂缓），已关闭单列不计入；'
                                        'active_customers/paused_customers 是在跟客户的细分'),
                'tier_counts': tiers,
                'customer_type_counts': type_counts,
                'customer_type_note': ('客户类型分布按"在跟"口径：buy_new=买一手房 / buy_second_hand=买二手房 / '
                                       'rent=租房 / unspecified=未细分（没确认买新房还是买二手房的；含历史值）。'
                                       '按来源看渠道分布请用 channel_stats'),
                'total_properties': total_props,
                'available_properties': props,
                'sold_properties': sold_props,
                'rented_properties': rented_props,
                'available_by_type': avail_by_type,
                'property_count_note': ('房源：total=全部（含已售已租）；available=在售；sold/rented 已售已租；'
                                        'available_by_type 只统计在售（new=一手房 / second_hand=二手房 / rental=出租）'),
                'overdue_followups': overdue,
            }

    def period_stats(self, start):
        """某个时间点之后的**活动计数**（经营报告"本期"那段用）→ dict

        - `new_customers`：本期新增客户（按建档时间，含已关闭）
        - `new_properties`：本期新增房源（含已售已租）
        - `viewings_done` / `viewings_interested`：本期**已完成**的带看数 / 其中感兴趣的位数
          （按带看时间算；待带看的时间通常在未来，所以只统计已完成的，避免把预约算成"
          本期看过 N 次"）
        - `new_deals`：本期新开的成交单

        2026-09-26 加（F337）：经营报告此前把**累计**数字挂在"周报"标题下报，经纪人会把累计
        当成本期业绩（t74a 实测：本周成交 1 单、报告写 2 单）。口径与展示都在
        `tools/real_estate_report.py` 一处，别在别处重写一套。
        """
        with self.get_session() as s:
            return {
                'new_customers': s.query(Customer).filter(Customer.created_at >= start).count(),
                'new_properties': s.query(Property).filter(Property.created_at >= start).count(),
                'viewings_done': s.query(Viewing).filter(
                    Viewing.status == 'done', Viewing.viewing_time >= start).count(),
                'viewings_interested': s.query(Viewing).filter(
                    Viewing.status == 'done', Viewing.result == 'interested',
                    Viewing.viewing_time >= start).count(),
                'new_deals': s.query(Deal).filter(Deal.created_at >= start).count(),
            }

    def get_channel_stats(self):
        """渠道线索统计：按客户来源分组统计客户数、分级、成交数、成交率

        2026-09-23 改：已关闭客户不再算进渠道来客数（该渠道单列 closed）——客户关了就不再
        跟进，继续算进"这个渠道来了多少人"会让渠道量越用越虚。

        2026-09-26 改（F350）：来源先过 `normalize_source()` **归并同义写法**（「贝壳找房」与「贝壳」
        原先是两行），并发回该行合并了哪些写法（`spellings`，给上层如实说明用）；
        认不出的写法原样成一行。`deals` = 该渠道**有成交的客户数**，`成交单数` = 该渠道的**成交单数**
        （一位客户开两张单时两者不同），`conversion_rate` = deals / customers。
        """
        from sqlalchemy import func
        with self.get_session() as s:
            # 2026-09-18 修：原先每客户查一次成交（N+1），现改为一次取出"有成交的客户集合"
            deal_customer_ids = {row[0] for row in s.query(Deal.customer_id).distinct().all()}
            # 2026-09-26（F359）：每客户的**成交单数** —— `deals` 只是"有成交的客户数"，
            # 一位客户开两张单时两者不同（周报要报"共 N 张单"就用这个）。一次 group by 拿回，别在下面逐客户查。
            deal_orders = {row[0]: row[1] for row in
                           s.query(Deal.customer_id, func.count(Deal.id))
                           .group_by(Deal.customer_id).all()}
            channels = {}
            for c in s.query(Customer).all():
                raw_src = (c.source or '').strip()
                src = normalize_source(raw_src)
                ch = channels.setdefault(src, {
                    'source': src, 'customers': 0, 'closed': 0,
                    'tiers': {'S': 0, 'A': 0, 'B': 0, 'C': 0}, 'deals': 0,
                    '成交单数': 0, 'spellings': set(),
                })
                if raw_src and raw_src != src:
                    ch['spellings'].add(raw_src)
                ch['成交单数'] += deal_orders.get(c.id, 0)      # 已关闭客户的单也算进渠道产出
                if c.status == 'closed':
                    ch['closed'] += 1
                    continue
                ch['customers'] += 1
                tier = c.tier or 'C'
                ch['tiers'][tier] = ch['tiers'].get(tier, 0) + 1
                if c.id in deal_customer_ids:
                    ch['deals'] += 1
            result = sorted(channels.values(), key=lambda x: -x['customers'])
            for ch in result:
                ch['spellings'] = sorted(ch['spellings'])
                ch['conversion_rate'] = round(ch['deals'] / ch['customers'] * 100, 1) if ch['customers'] else 0
            return result
    
    def daily_report(self):
        stats = self.get_stats()
        overdue = self.get_overdue()
        today = datetime.now().date()
        with self.get_session() as s:
            # 2026-09-25（F159）：客户名走 join 一起查出来，**在会话内**把要展示的内容拼好 ——
            # 原先在 with 块外访问 f.customer.name，session 已关 → DetachedInstanceError，
            # 只要有客户今天要跟进，早报就整条失败。
            rows = (s.query(Followup, Customer.name)
                    .join(Customer, Followup.customer_id == Customer.id)
                    .filter(Followup.next_date >= datetime.combine(today, datetime.min.time()),
                            Followup.next_date < datetime.combine(today + timedelta(days=1),
                                                                  datetime.min.time()))
                    .order_by(Followup.next_date.asc(), Followup.id.asc())
                    .all())
            today_total = len(rows)
            today_tasks = [{'customer': name or '未知', 'time': f.next_time, 'content': f.content}
                           for f, name in rows[:TODAY_TASK_LIMIT]]
        return {
            'date': today.isoformat(),
            'customer_stats': stats.get('tier_counts', {}),
            'total_customers': stats.get('total_customers', 0),
            'available_properties': stats.get('available_properties', 0),
            'overdue_count': len(overdue),
            'today_followups': today_total,
            'today_tasks': today_tasks,
        }
    
    def midday_check(self):
        stats = self.get_stats()
        overdue = self.get_overdue()
        return {
            'overdue_count': len(overdue),
            'overdue_customers': [
                {'customer_id': f['customer_id'], 'content': f['content'], 'next_date': f['next_date']}
                for f in overdue[:5]
            ],
            'total_customers': stats.get('total_customers', 0),
            'available_properties': stats.get('available_properties', 0),
        }

    # ---------- 带看 ----------
    def add_viewing(self, customer_id, property_id, viewing_time, **kwargs):
        with self.get_session() as s:
            v = Viewing(customer_id=customer_id, property_id=property_id,
                        viewing_time=viewing_time, **kwargs)
            s.add(v); s.commit(); s.refresh(v)
            return v.to_dict()

    def update_viewing(self, vid, **kwargs):
        with self.get_session() as s:
            v = s.query(Viewing).get(vid)
            if not v: return None
            for k, val in kwargs.items():
                if hasattr(v, k): setattr(v, k, val)
            s.commit(); s.refresh(v)
            return v.to_dict()

    def get_viewing(self, vid):
        with self.get_session() as s:
            v = s.query(Viewing).get(vid)
            return v.to_dict() if v else None

    def list_viewings(self, customer_id=None, property_id=None, status=None, limit=50,
                      with_total=False):
        """带看记录（按带看时间倒序）

        with_total=True 时返回 (本页明细, 符合条件总数) —— 总数走 SQL 聚合，别拿本页条数当总数
        （2026-09-25 F196）。
        `customer_name`/`property_title` 走**一次联表**取回：原先每条记录各懒加载一次客户与房源
        （20 条 = 43 次查询，实测 F199），联表后与条数无关。
        """
        from sqlalchemy.orm import joinedload
        with self.get_session() as s:
            q = (s.query(Viewing)
                 .options(joinedload(Viewing.customer), joinedload(Viewing.property)))
            if customer_id: q = q.filter(Viewing.customer_id == customer_id)
            if property_id: q = q.filter(Viewing.property_id == property_id)
            if status: q = q.filter(Viewing.status == status)
            total = q.count() if with_total else None
            rows = [v.to_dict() for v in
                    q.order_by(Viewing.viewing_time.desc(), Viewing.id.desc()).limit(limit).all()]
        return (rows, total) if with_total else rows

    def find_followups_by_viewings(self, viewing_ids, followup_type='reminder'):
        """一次取回"多条带看各自自动生成的记录" → {带看编号: 记录 dict}

        列表页要给每条带看显示"已安排回访提醒"时用它 —— 别在循环里一条一查（N+1）。
        """
        ids = [int(i) for i in set(viewing_ids or []) if i is not None]
        if not ids:
            return {}
        with self.get_session() as s:
            rows = (s.query(Followup)
                    .filter(Followup.source_viewing_id.in_(ids))
                    .order_by(Followup.id.asc()).all())
            out = {}
            for row in rows:
                if followup_type and row.type != followup_type:
                    continue
                out.setdefault(row.source_viewing_id, row.to_dict())
            return out

    def find_scheduled_viewing(self, customer_id, property_id, viewing_time):
        """同一客户同一房源同一时间是否已约了还没完成的带看（用于「不重复登记」）"""
        with self.get_session() as s:
            v = (s.query(Viewing)
                 .filter(Viewing.customer_id == customer_id,
                         Viewing.property_id == property_id,
                         Viewing.viewing_time == viewing_time,
                         Viewing.status == 'scheduled')
                 .order_by(Viewing.id.asc()).first())
            return v.to_dict() if v else None

    def viewing_stats(self, period='month'):
        """带看统计：总数、已看、取消、感兴趣客户 + 客户意向分布

        `intent_breakdown`（2026-09-25 增强）只统计**已看**的带看：感兴趣/不感兴趣/再考虑/未记意向
        四项之和 = `done`（对账用）。认不出的意向值归到 `unknown`，不静默丢。
        """
        with self.get_session() as s:
            from sqlalchemy import func
            total = s.query(Viewing).count()
            done = s.query(Viewing).filter(Viewing.status == 'done').count()
            scheduled = s.query(Viewing).filter(Viewing.status == 'scheduled').count()
            cancelled = s.query(Viewing).filter(Viewing.status == 'cancelled').count()
            interested = s.query(Viewing).filter(Viewing.status == 'done', Viewing.result == 'interested').count()
            breakdown = {'interested': 0, 'not_interested': 0, 'pending': 0, 'unknown': 0}
            for result, n in (s.query(Viewing.result, func.count())
                              .filter(Viewing.status == 'done')
                              .group_by(Viewing.result).all()):
                key = result if result in ('interested', 'not_interested', 'pending') else 'unknown'
                breakdown[key] += n
            return {
                'total_viewings': total, 'done': done, 'scheduled': scheduled,
                'cancelled': cancelled, 'interested': interested,
                'interest_rate': round(interested / done * 100, 1) if done else 0,
                'intent_breakdown': breakdown,
            }

    def viewing_top_properties(self, limit=5):
        """带看最多的房源 → (明细, 有过带看的房源总数)

        按带看次数降序、次数相同按感兴趣数降序（2026-09-25 增强）。房源已被删的给可读标注。
        """
        with self.get_session() as s:
            from sqlalchemy import case, func
            count_col = func.count(Viewing.id)
            interested_col = func.sum(case((Viewing.result == 'interested', 1), else_=0))
            rows = (s.query(Viewing.property_id, count_col, interested_col)
                    .group_by(Viewing.property_id)
                    .order_by(count_col.desc(), interested_col.desc(), Viewing.property_id.asc())
                    .limit(limit).all())
            total = s.query(func.count(func.distinct(Viewing.property_id))).scalar() or 0
        titles = self.get_property_titles([row[0] for row in rows])
        items = [{'property_id': pid,
                  'property_title': titles.get(pid) or f"已删除房源（id={pid}）",
                  'viewings': n, 'interested': int(i or 0)} for pid, n, i in rows]
        return items, total

    # ---------- 成交 ----------
    def add_deal(self, customer_id, property_id, **kwargs):
        with self.get_session() as s:
            d = Deal(customer_id=customer_id, property_id=property_id, **kwargs)
            s.add(d)
            # 成交后房源不再对外在售：二手房/一手房 → sold，出租 → rented
            # （真实案例 2026-08-11：阳光花园过户完成仍显示在售 21 套）
            p = s.query(Property).get(property_id)
            if p and p.status == 'available':
                p.status = 'rented' if p.property_type == 'rental' else 'sold'
            cust = s.query(Customer).get(customer_id)
            has_customer = cust is not None
            stage_before = getattr(cust, 'stage', None) if cust else None
            s.commit(); s.refresh(d)
            result = d.to_dict()
        # 生命周期联动：开单自动推进到 dealing（2026-08-28 功能5，防忘）
        # 走 update_stage 而不是直接赋值 —— 「客户变更历史」里要能看到这次推进（与带看档 3 同一套留痕）
        if has_customer and stage_before not in ('dealing', 'maintain'):
            self.update_stage(customer_id, 'dealing')
        return result

    def find_open_deal(self, customer_id, property_id):
        """同一客户同一房源是否已有还没走到「交房完成」的成交单（用于「不重复开单」）"""
        with self.get_session() as s:
            d = (s.query(Deal)
                 .filter(Deal.customer_id == customer_id,
                         Deal.property_id == property_id,
                         Deal.stage != 'finalized')
                 .order_by(Deal.id.asc()).first())
            return d.to_dict() if d else None

    def update_deal(self, did, **kwargs):
        with self.get_session() as s:
            d = s.query(Deal).get(did)
            if not d: return None
            for k, val in kwargs.items():
                if hasattr(d, k): setattr(d, k, val)
            s.commit(); s.refresh(d)
            return d.to_dict()

    def get_deal(self, did):
        with self.get_session() as s:
            d = s.query(Deal).get(did)
            return d.to_dict() if d else None

    def list_deals(self, stage=None, customer_id=None, limit=50, with_total=False):
        """成交单列表（按登记时间倒序，最新在前）

        `with_total=True` 时返回 (本页明细, 符合条件总数) —— 总数走 SQL 聚合，别拿本页条数当总数（2026-09-26 F226）。
        `customer_name`/`property_title` 走**一次联表**取回：原先每条各懒加载一次客户与房源
        （20 条 = 41 次查询，实测 F229），联表后与条数无关。
        """
        from sqlalchemy.orm import joinedload
        with self.get_session() as s:
            q = (s.query(Deal)
                 .options(joinedload(Deal.customer), joinedload(Deal.property)))
            if stage: q = q.filter(Deal.stage == stage)
            if customer_id: q = q.filter(Deal.customer_id == customer_id)
            total = q.count() if with_total else None
            rows = [d.to_dict() for d in
                    q.order_by(Deal.created_at.desc(), Deal.id.desc()).limit(limit).all()]
            return (rows, total) if with_total else rows

    def deal_stats(self):
        """成交统计：各阶段数量"""
        with self.get_session() as s:
            stages = {st: s.query(Deal).filter(Deal.stage == st).count()
                      for st in ['deposit', 'signing', 'loan', 'transfer', 'finalized']}
            return {
                'total_deals': sum(stages.values()),
                'stages': stages,
                'finalized': stages.get('finalized', 0),
            }

    # ---------- 竞品对比 ----------
    def compare_properties(self, property_id, limit=5):
        """同小区/同区域竞品对比（**只比同类型**：卖房比卖房、租房比租房）。

        返回（房源不存在给 None）：
        - `target`：目标房源（原样，展示口径由 `_property_display` 补）
        - `competitors`：竞品明细，每条带 `scope`（`same_community`/`same_district`）
          —— 同小区优先，同小区不足 3 套时补同区域，两个来源必须能分辨；
        - `competitor_total`：符合条件的竞品**总数**（与本次列出条数分开，供上层说"列了 N 套、共 M 套"）
        - `district_avg_price` / `avg_sample` / `avg_district` / `avg_property_type` /
          `avg_is_global`：均价口径（同区域 + 同类型；房源没填区域时按全库同类型，`avg_is_global=True`）

        2026-09-26 改（探针 t64a 实测）：① 原先均价是**全库在售均价**（含别的区域、含出租房），
        却叫 `district_avg_price` —— 朝阳区与海淀区两套房源拿到同一个数；② 原先竞品里
        出售房与出租房混在一张表（160 万与 2500 元/月并排）；③ 原先没有总数，被截断也不说。
        """
        from sqlalchemy import func
        with self.get_session() as s:
            target = s.query(Property).get(property_id)
            if not target:
                return None
            target_dict = target.to_dict()
            base = s.query(Property).filter(Property.status == 'available',
                                            Property.property_type == target.property_type,
                                            Property.id != property_id)
            # 同小区竞品（按价格从低到高，便于"贵在哪"一眼看清；同价按编号）
            comm_q = base.filter(Property.community == target.community) if target.community else None
            competitors = []
            if comm_q is not None:
                competitors = [dict(p.to_dict(), scope='same_community')
                               for p in comm_q.order_by(Property.price, Property.id).limit(limit).all()]
            dist_q = base.filter(Property.district == target.district) if target.district else None
            if len(competitors) < 3 and dist_q is not None:
                seen = {p['id'] for p in competitors}
                for p in dist_q.order_by(Property.price, Property.id).limit(limit).all():
                    if p.id not in seen:
                        competitors.append(dict(p.to_dict(), scope='same_district'))
                        seen.add(p.id)
            competitors = competitors[:limit]

            # 竞品总数 = 同小区 ∪ 同区域（两层各自计数后扣掉交集，别把两批拼起来数长度）
            n_comm = comm_q.count() if comm_q is not None else 0
            n_dist = dist_q.count() if dist_q is not None else 0
            n_both = (comm_q.filter(Property.district == target.district).count()
                      if (comm_q is not None and target.district) else 0)
            competitor_total = n_comm + n_dist - n_both

            # 均价：同区域 + 同类型（没填区域 → 全库同类型，上层必须如实说明这是全库口径）
            avg_q = base.filter(Property.district == target.district) if target.district else base
            avg_sample = avg_q.count()
            avg_price = avg_q.with_entities(func.avg(Property.price)).scalar()
            return {
                'target': target_dict,
                'competitors': competitors,
                'competitor_total': competitor_total,
                'district_avg_price': round(float(avg_price)) if avg_price is not None else None,
                'avg_sample': avg_sample,
                'avg_district': target.district,
                'avg_property_type': target.property_type,
                'avg_is_global': target.district is None,
                'target_unit_price': target_dict.get('unit_price'),
            }

    # ---------- 客户意向度评分 ----------
    def intent_components(self, customer_id=None, tier=None):
        """意向度评分的**原始分项**（一次聚合拿回；计分与分项文案在
        `tools/real_estate_intent.py::_score_intent` 一处，别在这里再写一套权重）。

        返回 [{customer_id, customer_name, tier, status, budget_min, budget_max,
              viewing_count, recent_followups, deal_count, last_followup_at}]

        - 不给 `customer_id`：全部**在跟**客户（status='active'，与流失预警族同一口径），
          可按 `tier` 筛。原先 `list_intent_scores` 逐客户调 `customer_intent_score`
          （200 位 = 801 次 SQL / 0.49 秒），且名单先被"最新 N 位"截断 → 实测库里
          分数最高的 3 位老客户在默认调用里一位都不出现。
        - 给 `customer_id`：只回那一位（含已关闭客户 —— 经纪人直接问某位客户的意向仍要能答）；
          不存在回 `[]`。
        - `last_followup_at` / `recent_followups` 都只看**人为**跟进（口径见 `_only_human_followups`）：
          带看档 3 自动写的带看跟进/回访提醒不算"经纪人联系过客户"（与流失预警族、F215 同一口径）。
          `last_followup_at` 只用于同分排序、**不参与计分**。
        """
        from sqlalchemy import func
        with self.get_session() as s:
            q = s.query(Customer.id, Customer.name, Customer.tier, Customer.status,
                        Customer.budget_min, Customer.budget_max)
            if customer_id is not None:
                q = q.filter(Customer.id == customer_id)
            else:
                q = q.filter(Customer.status == 'active')
                if tier:
                    q = q.filter(Customer.tier == tier)
            rows = q.all()
            if not rows:
                return []
            ids = [r[0] for r in rows]
            viewings = {r[0]: r[1] for r in
                        s.query(Viewing.customer_id, func.count(Viewing.id))
                        .filter(Viewing.status == 'done', Viewing.customer_id.in_(ids))
                        .group_by(Viewing.customer_id).all()}
            week_ago = datetime.now() - timedelta(days=7)
            # 「近 7 天跟进」只算**人为**跟进：带看档 3 自动写的带看跟进/回访提醒不算
            # （口径与「最后一次联系」F215 同一处 `_only_human_followups`）—— 否则带看一完成
            # 就算成"经纪人联系过"，分项文案还会写"近7天跟进2次"（其实一次都没联系）。
            recent = {r[0]: r[1] for r in
                      _only_human_followups(
                          s.query(Followup.customer_id, func.count(Followup.id))
                          .filter(Followup.customer_id.in_(ids), Followup.created_at >= week_ago))
                      .group_by(Followup.customer_id).all()}
            deals = {r[0]: r[1] for r in
                     s.query(Deal.customer_id, func.count(Deal.id))
                     .filter(Deal.customer_id.in_(ids))
                     .group_by(Deal.customer_id).all()}
            last_fu = {r[0]: r[1] for r in
                       _only_human_followups(
                           s.query(Followup.customer_id, func.max(Followup.created_at))
                           .filter(Followup.customer_id.in_(ids)))
                       .group_by(Followup.customer_id).all()}
            return [{
                'customer_id': r[0], 'customer_name': r[1], 'tier': r[2], 'status': r[3],
                'budget_min': r[4], 'budget_max': r[5],
                'viewing_count': viewings.get(r[0], 0),
                'recent_followups': recent.get(r[0], 0),
                'deal_count': deals.get(r[0], 0),
                'last_followup_at': last_fu.get(r[0]),
            } for r in rows]

    # ---------- 话术库 ----------
    def add_script(self, name, content, scenario='custom'):
        with self.get_session() as s:
            sc = Script(name=name, content=content, scenario=scenario)
            s.add(sc); s.commit(); s.refresh(sc)
            return {'id': sc.id, 'name': sc.name, 'scenario': sc.scenario,
                    'content': sc.content, 'created_at': sc.created_at.isoformat() if sc.created_at else None}

    def list_scripts(self, scenario=None, limit=100, with_total=False, scenario_values=None):
        """列话术（最新登记优先）

        `with_total=True` 时返回 `(rows, total)`（total 是**符合条件**的总数，一次聚合）；
        `scenario_values`：该档位在库里的**全部历史写法**（英文键 / 中文原样 / 大小写变体 / 自定义的空串），
        大小写不敏感匹配 —— 契约：筛选要把历史值归到同一档（改前存进去的「逼定」也要筛得到）。
        """
        with self.get_session() as s:
            q = s.query(Script)
            if scenario_values is not None:
                conds = []
                lowered = [str(v).lower() for v in scenario_values if v not in (None, '')]
                if lowered:
                    conds.append(func.lower(Script.scenario).in_(lowered))
                if '' in scenario_values:
                    conds.append(Script.scenario == '')
                if None in scenario_values:
                    conds.append(Script.scenario.is_(None))
                if conds:
                    q = q.filter(or_(*conds))
            elif scenario:
                q = q.filter(Script.scenario == scenario)
            ordered = q.order_by(Script.created_at.desc())
            total = ordered.count() if with_total else None
            rows = [{'id': sc.id, 'name': sc.name, 'scenario': sc.scenario,
                     'content': sc.content,
                     'created_at': sc.created_at.isoformat() if sc.created_at else None}
                    for sc in ordered.limit(limit).all()]
            return (rows, total) if with_total else rows

    def get_script_by_name(self, name):
        with self.get_session() as s:
            sc = s.query(Script).filter(Script.name == name).first()
            if sc:
                return {'id': sc.id, 'name': sc.name, 'scenario': sc.scenario,
                        'content': sc.content,
                        'created_at': sc.created_at.isoformat() if sc.created_at else None}
            return None

    def get_script(self, sid):
        """按编号取一条话术（含正文）→ dict 或 None —— 删除前要能核对删的是哪条"""
        with self.get_session() as s:
            sc = s.get(Script, sid)
            if not sc:
                return None
            return {'id': sc.id, 'name': sc.name, 'scenario': sc.scenario,
                    'content': sc.content,
                    'created_at': sc.created_at.isoformat() if sc.created_at else None}

    def list_scripts_by_name(self, name):
        """按名字取**全部**同名的（编号升序）—— 存量数据可能重名，读的时候要能说清有几份

        （`get_script_by_name` 只取第一条，是写入侧查重用的；读取侧的份数走这里。）
        """
        with self.get_session() as s:
            rows = s.query(Script).filter(Script.name == name).order_by(Script.id.asc()).all()
            return [{'id': sc.id, 'name': sc.name, 'scenario': sc.scenario,
                     'content': sc.content,
                     'created_at': sc.created_at.isoformat() if sc.created_at else None}
                    for sc in rows]

    def update_script(self, sid, name=None, content=None, scenario=None):
        """改一条话术（只改给进来的字段）→ 更新后的 dict；找不到返回 None

        覆盖同名话术走这里，**编号不变**（删了重建会换编号，别的引用就断了）。
        """
        with self.get_session() as s:
            sc = s.query(Script).get(sid)
            if not sc:
                return None
            if name is not None:
                sc.name = name
            if content is not None:
                sc.content = content
            if scenario is not None:
                sc.scenario = scenario
            s.commit(); s.refresh(sc)
            return {'id': sc.id, 'name': sc.name, 'scenario': sc.scenario,
                    'content': sc.content,
                    'created_at': sc.created_at.isoformat() if sc.created_at else None}

    def delete_script(self, sid):
        with self.get_session() as s:
            sc = s.query(Script).get(sid)
            if not sc:
                return False
            s.delete(sc); s.commit()
            return True


# ==================== 全局实例 ====================

_db_instance = None

def init_real_estate_db(database_url: str = None) -> RealEstateDB:
    global _db_instance
    if database_url is None:
        database_url = os.getenv('DATABASE_URL')
        # 2026-08-12 真实事故治本方案：无 DATABASE_URL 时显式报错，禁止静默回退 sqlite。
        # 背景：gateway 用户服务（hermes-gateway.service）默认不带 EnvironmentFile，
        # 进程无 DATABASE_URL 时旧代码静默回退 sqlite:///real_estate.db，写入
        # ~/.hermes/real_estate.db 幽灵库——Coco 回复"添加成功"但 PostgreSQL 查不到。
        # 现在改为：未配置环境变量直接抛错，工具调用返回明确错误，模型无法编造成功。
        # 显式传参（本地测试 sqlite）不受影响；healthcheck/install.sh 都显式设置环境变量。
        if not database_url:
            raise RuntimeError(
                "未配置 DATABASE_URL 环境变量，拒绝初始化数据库。"
                "请先运行 `coco check` 做体检（会检查 .env.db 与网关服务的数据库环境）；"
                "若是网关服务没加载 .env.db，运维可在服务器上补 EnvironmentFile（systemctl --user edit "
                "hermes-gateway.service 添加 EnvironmentFile=/root/hermes-agent/.env.db），改完 `coco restart` 生效；"
                "也可以手动 export DATABASE_URL=postgresql://... 后重试。"
                "这是 2026-08-12 幽灵库事故的防复发机制：禁止静默回退 sqlite。"
            )
    _db_instance = RealEstateDB(database_url)
    return _db_instance

def get_real_estate_db() -> RealEstateDB:
    global _db_instance
    if _db_instance is None:
        # 惰性初始化：gateway 启动不走 init_agent（CLI 专用），首次工具调用
        # 时自动建表，避免重装后新库无表（2026-08-11 真实事故）
        init_real_estate_db()
    return _db_instance
