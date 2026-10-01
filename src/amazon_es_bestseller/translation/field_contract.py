"""Shared translation-unit field semantics.

This module intentionally has no service/provider imports so the cache, service
and provider pool can agree on deduplication keys without a circular import.
"""
from __future__ import annotations


def canonical_translation_field_type(field: str) -> str:
    key = str(field or "").strip().casefold()
    if key in {"category_l1", "category_l2", "category_l3", "leaf_category",
               "category_l1_es", "category_l2_es", "category_l3_es", "leaf_category_es"}:
        return "category"
    if key in {"title", "title_es", "title_es_raw"}:
        return "title"
    if key in {"selected_variation_raw", "selected_variant_es", "variation_es",
               "selected_variation"}:
        return "variation"
    if key in {"product_details", "product_details_es", "detail_attributes_raw"}:
        return "product_details"
    if key in {"feature_bullets", "feature_bullets_es", "feature_bullets_raw", "features_es"}:
        return "feature_bullets"
    if key in {"brand", "brand_es"}:
        return "brand"
    if key in {"specification_es", "specification"}:
        return "specification"
    if key in {"description_es", "product_description_es", "product_description_raw", "product_description"}:
        return "description"
    return key
