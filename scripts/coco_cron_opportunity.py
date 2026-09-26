"""Coco 机会提醒数据收集（脚本筛数据，有匹配才叫模型组织语言）

设计要点（2026-09-23 拍板）：
- 每天 12:30 跑一次，只在**真有值得联系的机会**时才输出；
- 没有任何匹配 → **空输出 = 不叫模型**（官方调度器"脚本无输出则跳过 AI 调用"）；
- 只用库内已有的房源与客户数据，不去网上爬房源、不编造客户需求；
- 同一个「客户 × 房源」组合 7 天内只推一次。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coco_cron_common import (  # noqa: E402
    ensure_import_path, get_db, load_state, save_state, today_str,
)

_STATE_NAME = "opportunity"
_WINDOW_DAYS = 7
_LOOKBACK_DAYS = 1  # 只看昨天到现在新出现的降价/新上房源
_MIN_MATCH_SCORE = 50  # 新上房源匹配低于这个分不推（避免"硬凑"）


def _seen_recently(state: dict, key: str, now: datetime) -> bool:
    """该机会是否在去重窗口内已推过"""
    stamp = (state.get("seen") or {}).get(key)
    when = None
    if stamp:
        try:
            when = datetime.fromisoformat(stamp)
        except Exception:
            when = None
    return bool(when and (now - when) < timedelta(days=_WINDOW_DAYS))


def collect_opportunities(db, state: dict, now: datetime = None) -> tuple:
    """返回 (新机会列表, 新状态)；每条机会带 kind/property_id/描述行

    kind: drop（降价捞回）/ new（新上房源匹配）
    """
    now = now or datetime.now()
    seen = {
        k: v for k, v in (state.get("seen") or {}).items()
        if _seen_recently({"seen": {k: v}}, k, now)
    }
    fresh = []

    for drop in db.recent_price_drops(days=_LOOKBACK_DAYS, limit=50):
        pid = drop.get("property_id")
        key = f"drop:{pid}:{drop.get('new_price')}"
        if _seen_recently({"seen": seen}, key, now):
            continue
        try:
            matches = [m for m in db.find_customers_for_price_drop(pid, days=_LOOKBACK_DAYS)
                       if m.get("now_affordable")]
        except Exception:
            matches = []
        if not matches:
            continue
        matches.sort(key=lambda m: abs(m.get("gap") or 0))
        fresh.append({
            "kind": "drop",
            "property_id": pid,
            "key": key,
            "title": drop.get("title"),
            "old_price": drop.get("old_price"),
            "new_price": drop.get("new_price"),
            "drop_amount": drop.get("drop_amount"),
            "matches": matches[:3],
        })

    drop_ids = {i["property_id"] for i in fresh if i["kind"] == "drop"}
    try:
        new_props = db.recent_properties(days=_LOOKBACK_DAYS, limit=50)
    except Exception:
        new_props = []
    for prop in new_props:
        pid = prop.get("id")
        # 刚降价的房源已经按"降价捞回"报过了（本轮或近 7 天内），别换个名头再报一遍
        if pid in drop_ids or _seen_recently({"seen": seen}, f"prop:{pid}", now):
            continue
        try:
            raw = db.match_customers_for_property(pid, top_n=3)
        except Exception:
            raw = []
        matches = [m for m in raw
                   if m.get("tier") in ("S", "A") and (m.get("score") or 0) >= _MIN_MATCH_SCORE]
        if not matches:
            continue
        keys = [(f"match:{pid}:{m.get('customer_id')}", m) for m in matches]
        keys = [(k, m) for k, m in keys if not _seen_recently({"seen": seen}, k, now)]
        if not keys:
            continue
        fresh.append({
            "kind": "new",
            "property_id": pid,
            "key": keys[0][0],
            "extra_keys": [k for k, _ in keys[1:]],
            "title": prop.get("title"),
            "price": prop.get("price"),
            "area": prop.get("area"),
            "matches": [m for _, m in keys],
        })

    for item in fresh:
        keys = [item["key"]] + list(item.get("extra_keys") or [])
        keys.append(f"prop:{item['property_id']}")  # 同一套房 7 天内不许换渠道再推
        for key in keys:
            seen[key] = now.isoformat()
    return fresh, {"seen": seen}


def _match_name(m: dict) -> str:
    """匹配结果里的客户名（不同接口字段名不一致：customer_name / name）"""
    return m.get("customer_name") or m.get("name") or f"客户{m.get('customer_id')}"


def _match_reasons(m: dict) -> list:
    return m.get("match_reasons") or m.get("reasons") or []


def format_data(items: list) -> str:
    """把机会列表排成给模型看的数据块（不含任何编造的解读）"""
    drops = [i for i in items if i["kind"] == "drop"]
    news = [i for i in items if i["kind"] == "new"]
    lines = []
    if drops:
        lines.append(f"【降价捞回】{len(drops)} 套")
        for i in drops:
            amount = f"{round((i['drop_amount'] or 0) / 10000)}万" if i.get("drop_amount") else "?"
            new_price = f"{round((i['new_price'] or 0) / 10000)}万" if i.get("new_price") else "?"
            lines.append(f"· {i['title']}（编号{i['property_id']}）降价 {amount} → 现价 {new_price}")
            for m in i["matches"]:
                gap = m.get("gap")
                gap_txt = f"，原先差 {round(gap / 10000)}万" if isinstance(gap, int) and gap > 0 else ""
                budget = m.get("budget_max")
                budget_txt = f"，预算上限 {round(budget / 10000)}万" if budget else ""
                lines.append(
                    f"   → 可联系：{m.get('name')}（{m.get('tier')}级{budget_txt}{gap_txt}，现在够得着）")
    if news:
        lines.append(f"【新上房源】{len(news)} 套")
        for i in news:
            area = f"{i['area']}㎡" if i.get("area") else ""
            price = f"{round((i['price'] or 0) / 10000)}万" if i.get("price") else "?"
            lines.append(f"· {i['title']}（编号{i['property_id']}，{area} {price}）")
            for m in i["matches"]:
                reason = "/".join(_match_reasons(m)) or "匹配"
                lines.append(
                    f"   → 匹配：{_match_name(m)}（{m.get('tier')}级，{reason}，匹配分 {m.get('score')}）")
    return "\n".join(lines)


def main() -> int:
    ensure_import_path()
    now = datetime.now()
    try:
        fresh, new_state = collect_opportunities(get_db(), load_state(_STATE_NAME), now)
    except Exception as exc:
        print(f"⚠️ 机会提醒数据收集失败：{type(exc).__name__}: {exc}")
        return 0
    save_state(_STATE_NAME, new_state)
    if not fresh:
        return 0  # 没机会 → 静默，不叫模型
    print(format_data(fresh))
    return 0


if __name__ == "__main__":
    sys.exit(main())
