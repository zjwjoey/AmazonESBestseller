"""Canonical, evidence-bound QA rows for Chinese research fields."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from ..translation.production_contract import (canonical_source_field, source_text,
                                               target_field_for, translation_candidate_hash)
from ..translation.protection import protect
from ..translation.schemas import TRANSLATION_SCHEMA_VERSION
from ..translation.service import source_hash
from ..translation.validators import validate_translation


QA_ROW_VERSION = "chinese-qa-row-v1"
_PRODUCT_TYPE_REGRESSIONS = (("bolsa t\u00e9rmica", "\u996d\u76d2"),
                             ("recipiente reutilizable", "\u4e00\u6b21\u6027"),
                             ("pastillas de limpieza", "\u5496\u5561\u624b\u67c4"),
                             ("mini motosierra", "\u94fe\u6761\u6cb9"),
                             ("hilo de corte", "\u4fee\u526a\u673a"),
                             ("portafilter", "\u538b\u7c89\u5668"))
_MARKUP_OR_GARBLED = re.compile(r"(?:<[^>]+>|[?]{2,}|\{\s*[\[{])")
_SPANISH_RESIDUAL = re.compile(r"\b(?:sin|no|incluye|libre|filtro|producto|color|material)\b", re.I)
_REPAIR_CODES = {"NUMERIC_MISMATCH", "UNIT_MISMATCH", "NEGATION_MISMATCH", "PROTECTED_TOKEN_MISSING",
                 "PRODUCT_TYPE_ERROR", "EMPTY_TRANSLATION", "UNREADABLE_OR_MARKUP", "MISSING_SOURCE_HASH",
                 "SOURCE_HASH_MISMATCH"}
_UNSUPPORTED_CLAIMS = (("\u9632\u6c34", ("impermeable", "resistente al agua", "waterproof")),
                       ("\u9632\u706b", ("ignifugo", "ign\u00edfugo", "fireproof")),
                       ("\u6297\u83cc", ("antibacter",)), ("\u65e0\u6bd2", ("no tox", "atox")),
                       ("\u98df\u54c1\u7ea7", ("grado aliment", "food grade")))


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def qa_payload_hash(rows: Iterable[Mapping[str, Any]]) -> str:
    """Hash canonical QA evidence, never a caller-supplied ready flag."""
    canonical = [canonical_qa_row(row) for row in rows if isinstance(row, Mapping)]
    return hashlib.sha256(_canonical(canonical).encode("utf-8")).hexdigest()


def canonical_qa_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize legacy aliases into the one row contract consumed downstream."""
    field = canonical_source_field(row.get("field") or row.get("source_field"))
    target_field = str(row.get("target_field") or target_field_for(field))
    source = str(row.get("source_text") if row.get("source_text") is not None else row.get("source_es") or "")
    target = str(row.get("target_value") if row.get("target_value") is not None else row.get("translated_text")
                 if row.get("translated_text") is not None else row.get("translated_zh") or "")
    context = row.get("context") if isinstance(row.get("context"), Mapping) else {
        "field": field, "target_field": target_field,
    }
    result = {
        "qa_row_version": QA_ROW_VERSION,
        "asin": str(row.get("asin") or "").upper(),
        "field": field,
        "target_field": target_field,
        "field_type": str(row.get("field_type") or target_field),
        "source_text": source,
        "source_hash": str(row.get("source_hash") or ""),
        "translated_text": target,
        "target_value": target,
        "context": dict(context),
        "dictionary_version": str(row.get("dictionary_version") or ""),
        "translation_schema_version": str(row.get("translation_schema_version") or row.get("schema_version") or ""),
        "status": str(row.get("status") or "BLOCKED").upper(),
        "issues": [dict(item) if isinstance(item, Mapping) else {"code": str(item)}
                   for item in row.get("issues") or row.get("qa_issues") or []],
    }
    result["candidate_hash"] = str(row.get("candidate_hash") or translation_candidate_hash(result))
    # Compatibility aliases are presentation-only; canonical consumers use the
    # keys above and do not need to guess which spelling a caller supplied.
    result["source_es"] = result["source_text"]
    result["translated_zh"] = result["translated_text"]
    result["schema_version"] = result["translation_schema_version"]
    return result


def audit_field(*, asin: str, field: str, source_es: Any, translated_zh: str,
                source_hash: str, dictionary_version: str = "", brand: str = "",
                target_field: str = "", field_type: str = "", context: Mapping[str, Any] | None = None,
                translation_schema_version: str = TRANSLATION_SCHEMA_VERSION) -> dict[str, Any]:
    """Audit one immutable source/target pair and return a canonical QA row."""
    raw_source = source_text(source_es)
    target = str(translated_zh or "")
    computed_hash = source_hash_fn(raw_source)
    canonical_field = canonical_source_field(field)
    canonical_target = str(target_field or target_field_for(canonical_field))
    issues: list[dict[str, Any]] = []
    if not raw_source:
        issues.append({"code": "SOURCE_MISSING"})
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
    target_key = target.casefold()
    for source_cue, mistranslation in _PRODUCT_TYPE_REGRESSIONS:
        if source_cue in source_key and mistranslation in target_key:
            issues.append({"code": "PRODUCT_TYPE_ERROR", "source_cue": source_cue})
    for target_claim, source_evidence in _UNSUPPORTED_CLAIMS:
        if target_claim in target_key and not any(cue in source_key for cue in source_evidence):
            issues.append({"code": "UNSUPPORTED_CLAIM", "claim": target_claim})
    if raw_source and target:
        issues.extend(validate_translation(protect(raw_source, protected_values=[brand]), target, raw_source,
                                           field=canonical_field, brand=brand))
    codes = {str(issue.get("code") or "") for issue in issues}
    status = "BLOCKED" if "SOURCE_MISSING" in codes else ("PASS" if not codes
             else "REPAIR" if codes & _REPAIR_CODES else "MANUAL_REVIEW")
    return canonical_qa_row({
        "asin": asin, "field": canonical_field, "target_field": canonical_target,
        "field_type": field_type or canonical_target, "source_text": raw_source,
        "source_hash": computed_hash, "translated_text": target,
        "context": context or {"field": canonical_field, "target_field": canonical_target},
        "dictionary_version": dictionary_version,
        "translation_schema_version": translation_schema_version,
        "status": status, "issues": issues,
    })


source_hash_fn = source_hash


__all__ = ["QA_ROW_VERSION", "audit_field", "canonical_qa_row", "qa_payload_hash"]
