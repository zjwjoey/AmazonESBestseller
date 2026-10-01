"""Translation V2 field QA and report generation."""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List

from .schemas import TranslationFieldResult
from .validators import validate_translation


def qa_field(protected, translated: str, source: str, *, allowed_residual: Iterable[str] = ()) -> Dict[str, Any]:
    issues = validate_translation(protected, translated, source, allowed_residual=allowed_residual)
    return {"qa_status": "success" if not issues else "qa_failed", "issues": issues}


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
    return {"schema_version": "translation-v2.1", "counts": dict(counts),
            "issues": issues, "status": "pass" if not issues else "qa_failed"}
