"""Translation V2 field QA and report generation."""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List

from .schemas import TranslationFieldResult
from .validators import validate_translation


def qa_field(protected, translated: str, source: str, *, field: str = "",
             allowed_residual: Iterable[str] = ()) -> Dict[str, Any]:
    issues = validate_translation(protected, translated, source, field=field,
                                  allowed_residual=allowed_residual)
    return {"qa_status": "pass" if not issues else "qa_failed", "issues": issues}


def build_qa_report(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    counts = Counter()
    issues: List[Dict[str, Any]] = []
    for record in records:
        for field, value in (record.get("fields") or {}).items():
            status = value.get("translation_status", "pending")
            counts[status] += 1
            for issue in value.get("qa_issues") or []:
                code = issue.get("code", "UNKNOWN")
                counts[code] += 1
                issues.append({"asin": record.get("asin"), "field": field, **issue})
    # Stable zero-filled keys make dashboards and batch reports comparable even
    # when a run has no failures of a particular class.
    aliases = {
        "numeric_errors": "NUMERIC_MISMATCH",
        "unit_errors": "UNIT_MISMATCH",
        "protected_token_missing": "PROTECTED_TOKEN_MISSING",
        "brand_abnormal": "BRAND_ABNORMAL",
        "empty_translation": "EMPTY_TRANSLATION",
        "spanish_residual": "SPANISH_RESIDUAL",
        "added_numbers": "NUMERIC_MISMATCH",
    }
    for key in ("success", "cached", "partial", "failed", "qa_failed",
                "numeric_errors", "unit_errors", "protected_token_missing",
                "brand_abnormal", "empty_translation", "spanish_residual",
                "added_numbers"):
        counts.setdefault(key, 0)
    for alias, code in aliases.items():
        if alias not in counts:
            counts[alias] = counts.get(code, 0)
    return {"schema_version": "translation-v2.1", "counts": dict(counts),
            "issues": issues, "status": "pass" if not issues else "qa_failed"}
