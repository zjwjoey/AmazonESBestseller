"""Field-scoped offline repair queue for Chinese translation QA findings."""
from __future__ import annotations

from typing import Any, Iterable, Mapping


_DICTIONARY_CODES = {"NUMERIC_MISMATCH", "UNIT_MISMATCH", "NEGATION_MISMATCH"}
_PROVIDER_CODES = {"SPANISH_RESIDUAL", "PROTECTED_TOKEN_MISSING"}


def _strategy(row: Mapping[str, Any], code: str) -> str:
    if str(row.get("status")) in {"BLOCKED", "MANUAL_REVIEW"}:
        return "manual_review"
    if code in _DICTIONARY_CODES:
        return "dictionary_rerender"
    if code in _PROVIDER_CODES:
        return "provider_retry"
    return "auto_repair"


def build_repair_queue(
    rows: Iterable[Mapping[str, Any]], *, max_attempts: int = 2,
) -> list[dict[str, Any]]:
    """Build immutable, field-level repair work without including good fields."""
    queue: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("status")) == "PASS":
            continue
        issue = next(iter(row.get("issues") or ()), {"code": "MANUAL_REVIEW"})
        code = str(issue.get("code") or "MANUAL_REVIEW")
        old_translation = row.get("translated_zh")
        queue.append({
            "asin": row.get("asin"), "field": row.get("field"),
            "source": row.get("source_es"), "old_translation": old_translation,
            # Kept only for consumers not migrated to the canonical spelling.
            "oldtranslation": old_translation,
            "code": code, "detail": row.get("issues") or [],
            "strategy": _strategy(row, code), "attempt": 0,
            "max_attempts": max_attempts, "provider": None, "model": None,
            "status": "PENDING", "source_hash": row.get("source_hash"),
            "dictionary_version": row.get("dictionary_version"),
        })
    return queue


def apply_repair(
    item: Mapping[str, Any], *, source_hash: str, candidate: str,
    qa_result: Mapping[str, Any], provider: str = "fake", model: str = "fake",
) -> dict[str, Any]:
    """Accept a repaired field only after hash binding and fresh PASS QA."""
    result = dict(item)
    if str(source_hash or "") != str(result.get("source_hash") or ""):
        result.update(status="BLOCKED", code="SOURCE_CHANGED")
        return result
    if int(result.get("attempt") or 0) >= int(result.get("max_attempts") or 0):
        result.update(status="MANUAL_REVIEW", code="MAX_ATTEMPTS_REACHED")
        return result
    result["attempt"] = int(result.get("attempt") or 0) + 1
    result.update(provider=provider, model=model)
    if str(qa_result.get("status") or "") != "PASS":
        result.update(status="MANUAL_REVIEW", qa=dict(qa_result))
        return result
    result.update(status="PASS", repaired_translation=str(candidate), qa=dict(qa_result))
    return result
