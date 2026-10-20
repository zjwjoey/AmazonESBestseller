"""Canonical field contract at the Production Master/Translation boundary."""
from __future__ import annotations

import hashlib
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

# This is the one canonical source-to-display mapping used by production
# input, provider envelopes, QA and release.  Aliases remain accepted at the
# boundary through ``PRODUCTION_FIELD_ALIASES`` only; downstream code must not
# invent another target-field map.
CANONICAL_FIELD_TARGETS: dict[str, str] = {
    "title_es_raw": "title_zh",
    "brand": "brand_zh",
    "category_l1": "category_l1_zh",
    "category_l2": "category_l2_zh",
    "category_l3": "category_l3_zh",
    "leaf_category": "leaf_category_zh",
    "selected_variation_raw": "selected_variation_zh",
    "specification_es": "specification_zh",
    "product_details": "product_details_zh",
    "feature_bullets": "feature_bullets_zh",
    "product_description": "description_zh",
}


def canonical_source_field(field: Any) -> str:
    """Resolve a compatibility alias once at the production boundary."""
    name = str(field or "")
    for canonical, aliases in PRODUCTION_FIELD_ALIASES.items():
        if name == canonical or name in aliases:
            return canonical
    return name


def target_field_for(field: Any) -> str:
    """Return the frozen Chinese display field for a known source field."""
    canonical = canonical_source_field(field)
    return CANONICAL_FIELD_TARGETS.get(canonical, canonical)


def translation_candidate_hash(candidate: Mapping[str, Any]) -> str:
    """Hash the immutable field facts that a Chinese QA decision must bind."""
    payload = {
        "asin": str(candidate.get("asin") or "").upper(),
        "field": canonical_source_field(candidate.get("field")),
        "target_field": str(candidate.get("target_field") or ""),
        "field_type": str(candidate.get("field_type") or ""),
        "source_text": str(candidate.get("source_text") or ""),
        "source_hash": str(candidate.get("source_hash") or ""),
        "target_value": str(candidate.get("target_value") or ""),
        "context": candidate.get("context") if isinstance(candidate.get("context"), Mapping) else {},
        "dictionary_version": str(candidate.get("dictionary_version") or ""),
        "translation_schema_version": str(candidate.get("translation_schema_version") or ""),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()

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


def normalize_asin_filter(value: Any) -> list[str]:
    """Normalize a CLI/file ASIN selection without silently accepting junk."""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("INVALID_ASIN_FILTER_JSON:%s" % exc.msg) from exc
            if not isinstance(parsed, list):
                raise ValueError("INVALID_ASIN_FILTER: JSON value must be an array")
            values = parsed
        else:
            values = [item for item in text.split(",") if item.strip()]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        raise ValueError("INVALID_ASIN_FILTER: expected JSON array or comma-separated string")
    normalized: list[str] = []
    invalid: list[Any] = []
    for item in values:
        if isinstance(item, str):
            asin = item.strip().upper()
        elif isinstance(item, Mapping):
            raw = item.get("asin") or item.get("ASIN")
            asin = str(raw).strip().upper() if isinstance(raw, str) else ""
        else:
            asin = ""
        if not asin:
            invalid.append(item)
            continue
        if asin not in normalized:
            normalized.append(asin)
    if invalid:
        raise ValueError("INVALID_ASIN_FILTER_ELEMENT:%s" % json.dumps(invalid, ensure_ascii=False))
    return normalized


def matches_category_filter(record: Mapping[str, Any], query: str) -> bool:
    """Match a query against every canonical category level."""
    needle = str(query or "").strip().casefold()
    if not needle:
        return True
    return any(needle in str(record.get(field) or "").casefold()
               for field in ("category_l1", "category_l2", "category_l3", "leaf_category"))


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
