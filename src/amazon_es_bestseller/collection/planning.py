# -*- coding: utf-8 -*-
"""详情采集规划（离线）：榜单 ASIN → 补采集策略 + 状态缓存（state/*.json）。

四种策略（计划 B2，纯离线：不联网、不依赖浏览器）：
  - new        新 ASIN 必补：榜单出现但状态缓存无记录 → collect
  - incomplete 缺关键字段补：已有记录但关键字段缺失/为空，或上次为
                CHALLENGE/BLOCKED/RATE_LIMITED（不可信）→ collect
  - refresh    完整按周期刷新：记录完整且超过刷新周期 → collect
  - skip       完整且在刷新周期内 → 不采集（用缓存）

排名页高频（ASIN 出现在 ≥2 个榜单页，或榜单前 N 名）→ 缩短刷新周期，
保证高频商品的新鲜度。周期、阈值全部可配置并带默认值。

状态 JSON 由 ``DetailState`` 读写（``state/details_state.json``），
键 = ASIN（大写），值 = 最近一次详情采集记录。
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from ..models import normalize_asin

#: 关键字段（缺失任一即视为"不完整"）与中文说明（QA_RULES §29 填充率字段）
KEY_FIELDS = ("current_price_raw", "title_es_raw", "rating_raw", "review_count_raw")
KEY_FIELD_NAMES = {
    "current_price_raw": "现价",
    "title_es_raw": "标题",
    "rating_raw": "评分",
    "review_count_raw": "评论数",
}

#: 默认刷新周期：普通 ASIN 7 天，高频 ASIN 1 天
REFRESH_DAYS = 7
HOT_REFRESH_DAYS = 1

#: 高频判定：出现在 ≥2 个不同榜单页，或榜单前 5 名
HOT_APPEARANCES = 2
HOT_TOP_RANK = 5

#: 不可信访问状态：上次如此 → 强制重采，绝不缓存当新
UNRELIABLE_STATES = ("CHALLENGE", "BLOCKED", "RATE_LIMITED")

#: 采集优先级排序（数值越小越先采）
_PRIORITY_RANK = {"new": 0, "incomplete": 1, "refresh": 2}


class DetailState:
    """ASIN → 最近详情采集记录 的离线状态缓存（state/details_state.json）。"""

    def __init__(self, path) -> None:
        self.path = Path(path)
        self._data: Dict[str, dict] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, asin) -> Optional[dict]:
        a = normalize_asin(asin)
        return self._data.get(a) if a else None

    def has(self, asin) -> bool:
        return self.get(asin) is not None

    def update(self, detail_records) -> None:
        """用本次采集结果更新缓存；缺失 collected_at 时盖章当前时间。"""
        now = datetime.now()
        for r in detail_records:
            a = normalize_asin(r.get("asin"))
            if not a:
                continue
            rec = dict(r)
            rec.setdefault("collected_at", now.isoformat(timespec="seconds"))
            self._data[a] = rec

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")

    def records(self) -> List[dict]:
        """全部状态记录（detail records，含 collected_at），按 ASIN 排序。

        cmd_collect 用它重建 details.json：resume 场景下本次 collect 结果只是
        增量，state 是跨 run 的全量权威缓存，details.json 必须反映全量。
        """
        return [dict(v) for _, v in sorted(self._data.items())]

    def __len__(self) -> int:
        return len(self._data)


def _missing_key_fields(rec: dict) -> List[str]:
    return [KEY_FIELD_NAMES[f] for f in KEY_FIELDS if not (rec.get(f) or "").strip()]


def _collected_at(rec: dict) -> Optional[datetime]:
    t = (rec or {}).get("collected_at")
    if not t:
        return None
    try:
        return datetime.fromisoformat(str(t))
    except ValueError:
        return None


def build_plan(ranking_records: List[dict], state: DetailState, now: Optional[datetime] = None,
               refresh_days: int = REFRESH_DAYS,
               hot_refresh_days: int = HOT_REFRESH_DAYS,
               hot_appearances: int = HOT_APPEARANCES,
               hot_top_rank: int = HOT_TOP_RANK) -> dict:
    """榜单记录 + 现有状态 → 采集计划。

    返回::

        {
          "collect": [{"asin", "action", "priority", "reason"}, ...],  # 按优先级+ASIN 排序
          "skip":    [{"asin", "action", "priority", "reason"}, ...],  # 按 ASIN 排序
          "hot_asins": [...],
          "stats": {"total_asins": n, "collect": n, "skip": n},
        }
    """
    now = now or datetime.now()
    pages_seen: Dict[str, set] = defaultdict(set)
    best_rank: Dict[str, int] = {}
    for r in ranking_records:
        a = normalize_asin(r.get("asin"))
        if not a:
            continue
        url = r.get("ranking_source_url") or ""
        if url:
            pages_seen[a].add(url)
        rank = r.get("bestseller_rank")
        try:
            rank = int(rank)
        except (TypeError, ValueError):
            rank = None
        if rank is not None and (a not in best_rank or rank < best_rank[a]):
            best_rank[a] = rank

    hot = set()
    for a, pages in pages_seen.items():
        if len(pages) >= hot_appearances:
            hot.add(a)
    for a, rk in best_rank.items():
        if rk <= hot_top_rank:
            hot.add(a)

    collect: List[dict] = []
    skip: List[dict] = []
    asins = sorted(set(pages_seen))
    for a in asins:
        rec = state.get(a)
        if rec is None:
            collect.append({"asin": a, "action": "collect", "priority": "new",
                            "reason": "新 ASIN 必补"})
            continue
        if (rec.get("access_state") or "").upper() in UNRELIABLE_STATES:
            collect.append({"asin": a, "action": "collect", "priority": "incomplete",
                            "reason": "上次访问 %s 不可信，强制重采" % rec.get("access_state")})
            continue
        missing = _missing_key_fields(rec)
        if missing:
            collect.append({"asin": a, "action": "collect", "priority": "incomplete",
                            "reason": "缺关键字段: %s" % ",".join(missing)})
            continue
        last = _collected_at(rec)
        period = timedelta(days=hot_refresh_days) if a in hot else timedelta(days=refresh_days)
        age = (now - last) if last is not None else None
        if age is None or age > period:
            kind = "高频榜单" if a in hot else "完整"
            collect.append({"asin": a, "action": "collect", "priority": "refresh",
                            "reason": "%s超过 %d 天刷新周期" % (kind, period.days)})
        else:
            skip.append({"asin": a, "action": "skip", "priority": "fresh",
                         "reason": "完整且在刷新周期内（%d 天）" % period.days})

    collect.sort(key=lambda x: (_PRIORITY_RANK[x["priority"]], x["asin"]))
    skip.sort(key=lambda x: x["asin"])
    return {
        "collect": collect,
        "skip": skip,
        "hot_asins": sorted(hot),
        "stats": {
            "total_asins": len(asins),
            "collect": len(collect),
            "skip": len(skip),
        },
    }


def collect_asins(plan: dict) -> List[str]:
    """采集计划 → 需要采集的 ASIN 列表（保持计划顺序，供 collect_details 串行执行）。"""
    return [item["asin"] for item in plan["collect"]]
