"""Canonical field contract at the Production Master/Translation boundary."""
from __future__ import annotations

import json
from typing import Any, Mapping


# The production adapter accepts the frozen research export and a canonical
# English-key Master.  Aliases are resolved once here; downstream stages only
# see the canonical source fields.
PRODUCTION_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "title_es_raw": ("title_es_raw", "title_es", "title", "商品名称（西语）"),
    "brand": ("brand", "brand_es", "品牌"),
    "category_l1": ("category_l1", "category_l1_es", "一级类目"),
    "category_l2": ("category_l2", "category_l2_es", "二级类目"),
    "category_l3": ("category_l3", "category_l3_es", "三级类目"),
    "leaf_category": ("leaf_category", "leaf_category_es", "细分类目"),
    "selected_variation_raw": (
        "selected_variation_raw", "selected_variant_es", "variation_es",
        "当前选中规格 / 变体（西语）", "当前选中规格/变体（西语）",
    ),
    "specification_es": ("specification_es", "spec_v2", "核心规格（西语）"),
    "product_details": (
        "product_details", "product_details_es", "attributes",
        "detail_attributes_raw", "完整商品详情（西语原文）",
    ),
    "feature_bullets": (
        "feature_bullets", "feature_bullets_es", "feature_bullets_raw",
        "features_es", "商品卖点（西语原文）",
    ),
    "product_description": (
        "product_description", "product_description_es", "description_es",
        "product_description_raw", "商品描述（西语原文）",
    ),
}

PRODUCTION_TRANSLATION_FIELDS = tuple(PRODUCTION_FIELD_ALIASES)

RESEARCH_CSV_FIELDS = {
    "ASIN": "asin",
    "商品名称（西语）": "title_es_raw",
    "品牌": "brand",
    "一级类目": "category_l1",
    "二级类目": "category_l2",
    "三级类目": "category_l3",
    "细分类目": "leaf_category",
    "当前选中规格 / 变体（西语）": "selected_variation_raw",
    "核心规格（西语）": "specification_es",
    "完整商品详情（西语原文）": "product_details",
    "商品卖点（西语原文）": "feature_bullets",
    "商品描述（西语原文）": "product_description",
}


def canonical_value(record: Mapping[str, Any], field: str) -> Any:
    """Return the first present source value without guessing across fields."""
    for alias in PRODUCTION_FIELD_ALIASES[field]:
        if alias in record and record[alias] not in (None, "", [], {}):
            return record[alias]
    return ""


def source_text(value: Any) -> str:
    """Stable text representation used for source hashes and provider input."""
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value or "").strip()


def canonical_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project one Master row into the canonical Spanish source contract."""
    asin = str(record.get("asin") or record.get("ASIN") or "").strip().upper()
    out: dict[str, Any] = {"asin": asin}
    for field in PRODUCTION_TRANSLATION_FIELDS:
        out[field] = canonical_value(record, field)
    return out
