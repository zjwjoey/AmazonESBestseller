# -*- coding: utf-8 -*-
"""
数据模型契约（对应 docs/DATA_MODEL.md / docs/ARCHITECTURE.md）。

商品/排行榜记录/原始详情均为 **plain dict**（与现有脚本一致），
本模块只提供：状态枚举、校验问题结构、规范键集常量、榜单×详情合并函数。
"""
from __future__ import annotations

from collections import namedtuple
from enum import Enum
from typing import List, Mapping, Optional


class QAStatus(str, Enum):
    """QA 状态（docs/QA_RULES.md §59）。"""
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"


class AccessState(str, Enum):
    """访问状态（docs/ARCHITECTURE.md §5）。"""
    NORMAL = "NORMAL"
    BLOCKED = "BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    CHALLENGE = "CHALLENGE"
    NETWORK_ERROR = "NETWORK_ERROR"
    UNKNOWN = "UNKNOWN"


#: 严重度（docs/QA_RULES.md §2）：P0 关键 / P1 高 / P2 中 / P3 低
QaIssue = namedtuple("QaIssue", ["code", "severity", "field", "message"])


#: 排行榜记录规范键（docs/ARCHITECTURE.md §12 / DATA_MODEL.md）
RANKING_KEYS = (
    "index",
    "asin",
    "category_l1",
    "category_l2",
    "category_l3",
    "leaf_category",
    "browse_node_id",
    "bestseller_rank",
    "monthly_bought_raw",
    "monthly_bought_min",
    "ranking_source_url",
    "collected_at",
)

#: 商品表规范键（docs/ARCHITECTURE.md §23 / DATA_MODEL.md）
PRODUCT_KEYS = (
    "asin",
    "parent_asin",
    "title_es_raw",
    "brand",
    "product_url",
    "image_url",
    "specification",
    "details_json",
    "date_first_available",
)

#: 详情原始证据键（docs/ARCHITECTURE.md §21 / DATA_MODEL §4-§8）
DETAIL_RAW_KEYS = (
    "asin",
    "parent_asin",
    "title_es_raw",
    "current_price_raw",
    "original_price_raw",
    "rating_raw",
    "review_count_raw",
    "brand_raw",
    "seller_raw",
    "availability_raw",
    "selected_variation_raw",
    "details_json",
    "date_first_available_raw",
    "detail_bsr_raw",
    "image_url",
    "product_url",
    # 无损全量详情（DATA_MODEL §4-§8）
    "attributes",
    "feature_bullets_raw",
    "product_description_raw",
    "detail_bullets_raw",
)


def normalize_asin(asin: Optional[str]) -> str:
    """ASIN 统一为大写去空白；无效输入返回 ''。"""
    if not asin:
        return ""
    return str(asin).strip().upper()


def merge_ranking_and_detail(
    ranking_records: List[Mapping],
    details: List[Mapping],
) -> List[dict]:
    """按 ASIN 合并榜单记录与详情记录为商品表（每 ASIN 一行）。

    关键规则（docs/DATA_MODEL.md / QA_RULES.md §9）：
      - 连接键为 ASIN（大小写不敏感，统一大写）。
      - ``bestseller_rank`` 只来自 ``ranking_records``，绝不被详情 BSR 覆盖；
        详情 BSR 单独落到 ``detail_bsr_raw`` / ``detail_bsr_segments``。
      - 商品表每 ASIN 一行：该 ASIN 的第一条榜单记录提供榜单上下文，
        其余榜单上下文保留在排行榜记录表（本函数不折叠、不去重）。
      - 仅出现在详情、未出现在榜单的 ASIN 也进入商品表（详情字段为准）。
    """
    details_by_asin: dict = {}
    for d in details:
        a = normalize_asin(d.get("asin"))
        if a:
            details_by_asin.setdefault(a, d)

    products: dict = {}
    for r in ranking_records:
        a = normalize_asin(r.get("asin"))
        if not a:
            continue
        prod = products.setdefault(a, {"asin": a})
        # 第一条榜单记录提供榜单上下文，后续不覆盖
        if "bestseller_rank" not in prod:
            for k in ("bestseller_rank", "ranking_source_url", "collected_at",
                      "leaf_category", "browse_node_id", "category_l1",
                      "category_l2", "category_l3", "monthly_bought_raw",
                      "monthly_bought_min", "index"):
                if k in r:
                    prod[k] = r[k]
        if a in details_by_asin:
            _merge_detail_fields(prod, details_by_asin[a])

    # 仅出现在详情、未出现在榜单的 ASIN
    for a, d in details_by_asin.items():
        if a not in products:
            prod = {"asin": a}
            _merge_detail_fields(prod, d)
            products[a] = prod

    # Product URL is deterministic identity evidence; retain ranking/detail
    # URLs when present, otherwise derive the canonical Amazon.es /dp URL from
    # the validated ASIN so ranking-only products remain actionable.
    for prod in products.values():
        if not str(prod.get("product_url") or "").strip():
            asin = normalize_asin(prod.get("asin"))
            if asin:
                prod["product_url"] = "https://www.amazon.es/dp/%s" % asin

    return list(products.values())


def _merge_detail_fields(prod: dict, detail: Mapping) -> None:
    """把详情字段并入商品记录；不覆盖已存在的榜单上下文键。"""
    for k, v in detail.items():
        if k == "asin":
            continue
        if k not in prod:
            prod[k] = v
