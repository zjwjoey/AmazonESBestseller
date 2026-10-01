"""Shared translation-unit field semantics.

This module intentionally has no service/provider imports so the cache, service
and provider pool can agree on deduplication keys without a circular import.
"""
from __future__ import annotations

import re
import unicodedata


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


def normalize_translation_label(label: str) -> str:
    """Return a display-independent, import-free detail-label key.

    This deliberately matches the conservative normalization used by the
    dictionary lookup layer without importing it: the field contract is shared
    by Service and Pool and must not create a dependency cycle.
    """
    text = unicodedata.normalize("NFKC", str(label or "")).strip().casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text)


def canonical_translation_unit_field(field: str, *, label: str | None = None) -> str:
    """Return the semantic namespace for TM and ProviderPool deduplication.

    A structured product-detail value is only reusable under the same
    attribute meaning.  For example, ``Color: Natural`` and ``Material:
    Natural`` share their source value but not their translation unit.
    """
    base = canonical_translation_field_type(field)
    if base == "product_details" and label:
        normalized_label = normalize_translation_label(label)
        if normalized_label:
            return "%s:%s" % (base, normalized_label)
    return base
