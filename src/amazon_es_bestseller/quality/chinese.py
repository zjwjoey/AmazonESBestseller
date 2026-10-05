"""Conservative, evidence-bound QA for Chinese research fields."""
from __future__ import annotations

import re
from typing import Any

from ..translation.production_contract import source_text
from ..translation.protection import protect
from ..translation.schemas import TRANSLATION_SCHEMA_VERSION
from ..translation.service import source_hash
from ..translation.validators import validate_translation

FIELDS = ("title", "category_l1", "category_l2", "category_l3", "leaf_category", "selected_variation", "specification", "product_details", "feature_bullets", "description")
_PRODUCT_TYPE_REGRESSIONS = (("bolsa térmica", "饭盒"), ("recipiente reutilizable", "一次性"), ("pastillas de limpieza", "咖啡手柄"), ("mini motosierra", "链条油"), ("hilo de corte", "修剪机"), ("portafilter", "压粉器"))
_MARKUP_OR_GARBLED = re.compile(r"(?:<[^>]+>|[�]{2,}|\{\s*[\[{])")
_SPANISH_RESIDUAL = re.compile(r"\b(?:sin|no|incluye|libre|filtro|producto|color|material)\b", re.I)
_REPAIR_CODES = {"NUMERIC_MISMATCH", "UNIT_MISMATCH", "NEGATION_MISMATCH", "PROTECTED_TOKEN_MISSING", "PRODUCT_TYPE", "EMPTY_TRANSLATION", "UNREADABLE_OR_MARKUP", "MISSING_SOURCE_HASH", "SOURCE_HASH_MISMATCH"}

# High-signal additions are flagged, not corrected.  Rules cannot prove all
# product semantics; anything uncertain remains a manual-review decision.
_UNSUPPORTED_CLAIMS = (("防水", ("impermeable", "resistente al agua", "waterproof")), ("防火", ("ignifugo", "ignífugo", "fireproof")), ("抗菌", ("antibacter",)), ("无毒", ("no tox", "atox")), ("食品级", ("grado aliment", "food grade")))


def audit_field(*, asin: str, field: str, source_es: Any, translated_zh: str,
                source_hash: str, dictionary_version: str = "", brand: str = "") -> dict[str, Any]:
    """Return a result bound to ASIN/field/current source/candidate/version."""
    raw_source = source_text(source_es)
    target = str(translated_zh or "")
    computed_hash = source_hash_fn(raw_source)
    candidate_hash = source_hash_fn(target)
    base = {"asin": str(asin), "field": str(field), "source_es": raw_source,
            "translated_zh": target, "source_hash": computed_hash,
            "candidate_hash": candidate_hash, "dictionary_version": str(dictionary_version or ""),
            "schema_version": TRANSLATION_SCHEMA_VERSION}
    if not raw_source:
        return {**base, "status": "BLOCKED", "issues": [{"code": "SOURCE_MISSING"}]}
    issues: list[dict[str, Any]] = []
    if not source_hash:
        issues.append({"code": "MISSING_SOURCE_HASH"})
    elif str(source_hash) != computed_hash:
        issues.append({"code": "SOURCE_HASH_MISMATCH", "provided": str(source_hash), "computed": computed_hash})
    if not target:
        issues.append({"code": "EMPTY_TRANSLATION"})
    if _MARKUP_OR_GARBLED.search(target):
        issues.append({"code": "UNREADABLE_OR_MARKUP"})
    if _SPANISH_RESIDUAL.search(target):
        issues.append({"code": "SPANISH_RESIDUAL"})
    source_key = raw_source.casefold()
    for source_cue, mistranslation in _PRODUCT_TYPE_REGRESSIONS:
        if source_cue in source_key and mistranslation in target:
            issues.append({"code": "PRODUCT_TYPE", "source_cue": source_cue})
    for target_claim, source_evidence in _UNSUPPORTED_CLAIMS:
        if target_claim in target and not any(cue in source_key for cue in source_evidence):
            issues.append({"code": "UNSUPPORTED_CLAIM", "claim": target_claim})
    if target:
        issues.extend(validate_translation(protect(raw_source, protected_values=[brand]), target, raw_source, field=field, brand=brand))
    codes = {str(issue.get("code") or "") for issue in issues}
    status = "PASS" if not codes else ("REPAIR" if codes & _REPAIR_CODES else "MANUAL_REVIEW")
    return {**base, "status": status, "issues": issues}


source_hash_fn = source_hash
