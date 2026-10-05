"""Conservative, offline field QA for the Chinese research display layer.

This is deliberately a gate, not a translation fallback. Spanish evidence is
passed into deterministic validators unchanged, and uncertain results are
retained for review instead of being guessed or overwritten.
"""
from __future__ import annotations

import re
from typing import Any

from ..translation.protection import protect
from ..translation.validators import validate_translation


FIELDS = (
    "title", "category_l1", "category_l2", "category_l3", "leaf_category",
    "selected_variation", "specification", "product_details", "feature_bullets",
    "description",
)

# Source cue, mistranslated Chinese product type. These are narrow, high-impact
# historical regressions; the checker intentionally does not guess broad types.
_PRODUCT_TYPE_REGRESSIONS = (
    ("bolsa t\u00e9rmica", "\u996d\u76d2"),
    ("pastillas de limpieza", "\u5496\u5561\u624b\u67c4"),
    ("mini motosierra", "\u94fe\u6761\u6cb9"),
    ("hilo de corte", "\u4fee\u526a\u673a"),
    ("portafilter", "\u538b\u7c89\u5668"),
)
_MARKUP_OR_GARBLED = re.compile(r"(?:<[^>]+>|[\ufffd]{2,}|\{\s*[\[{])")
_SPANISH_RESIDUAL = re.compile(
    r"\b(?:sin|no|incluye|libre|filtro|producto|color|material)\b", re.I
)

_REPAIR_CODES = {
    "NUMERIC_MISMATCH", "UNIT_MISMATCH", "NEGATION_MISMATCH",
    "PROTECTED_TOKEN_MISSING", "PRODUCT_TYPE", "EMPTY_TRANSLATION",
    "UNREADABLE_OR_MARKUP", "MISSING_SOURCE_HASH",
}


def audit_field(
    *,
    asin: str,
    field: str,
    source_es: str,
    translated_zh: str,
    source_hash: str,
    dictionary_version: str = "",
    brand: str = "",
) -> dict[str, Any]:
    """Return an evidence-bound QA record for one translated field."""
    source_es = str(source_es or "")
    translated_zh = str(translated_zh or "")
    issues: list[dict[str, Any]] = []
    if not source_es:
        return {
            "asin": asin, "field": field, "source_es": source_es,
            "translated_zh": translated_zh, "source_hash": source_hash,
            "dictionary_version": dictionary_version, "status": "BLOCKED",
            "issues": [{"code": "SOURCE_MISSING"}],
        }
    if not source_hash:
        issues.append({"code": "MISSING_SOURCE_HASH"})
    if not translated_zh:
        issues.append({"code": "EMPTY_TRANSLATION"})
    if _MARKUP_OR_GARBLED.search(translated_zh):
        issues.append({"code": "UNREADABLE_OR_MARKUP"})
    if _SPANISH_RESIDUAL.search(translated_zh):
        issues.append({"code": "SPANISH_RESIDUAL"})

    source_key = source_es.casefold()
    for source_cue, mistranslation in _PRODUCT_TYPE_REGRESSIONS:
        if source_cue in source_key and mistranslation in translated_zh:
            issues.append({"code": "PRODUCT_TYPE", "source_cue": source_cue})

    if translated_zh:
        protected_source = protect(source_es, protected_values=[brand])
        issues.extend(validate_translation(
            protected_source, translated_zh, source_es, field=field, brand=brand,
        ))

    codes = {str(issue.get("code") or "") for issue in issues}
    status = "PASS" if not codes else (
        "REPAIR" if codes & _REPAIR_CODES else "MANUAL_REVIEW"
    )
    return {
        "asin": asin, "field": field, "source_es": source_es,
        "translated_zh": translated_zh, "source_hash": source_hash,
        "dictionary_version": dictionary_version, "status": status, "issues": issues,
    }
