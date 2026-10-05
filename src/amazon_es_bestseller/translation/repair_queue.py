"""Field-scoped, evidence-bound repair queue for Chinese translation QA."""
from __future__ import annotations
from typing import Any, Iterable, Mapping

from .production_contract import source_text
from .schemas import TRANSLATION_SCHEMA_VERSION
from .service import source_hash as hash_source
from ..quality.chinese import audit_field

_DICTIONARY_CODES = {"NUMERIC_MISMATCH", "UNIT_MISMATCH", "NEGATION_MISMATCH"}
_PROVIDER_CODES = {"SPANISH_RESIDUAL", "PROTECTED_TOKEN_MISSING"}


def _strategy(row: Mapping[str, Any], code: str) -> str:
    if str(row.get("status")) in {"BLOCKED", "MANUAL_REVIEW"}: return "manual_review"
    if code in _DICTIONARY_CODES: return "dictionary_rerender"
    if code in _PROVIDER_CODES: return "provider_retry"
    return "auto_repair"


def build_repair_queue(rows: Iterable[Mapping[str, Any]], *, max_attempts: int = 2) -> list[dict[str, Any]]:
    if max_attempts < 1: raise ValueError("max_attempts must be >= 1")
    queue = []
    for row in rows:
        if str(row.get("status")) == "PASS": continue
        issue = next(iter(row.get("issues") or ()), {"code": "MANUAL_REVIEW"})
        old = row.get("translated_zh")
        queue.append({"asin": row.get("asin"), "field": row.get("field"), "source": row.get("source_es"), "old_translation": old, "oldtranslation": old, "code": str(issue.get("code") or "MANUAL_REVIEW"), "detail": row.get("issues") or [], "strategy": _strategy(row, str(issue.get("code") or "MANUAL_REVIEW")), "attempt": 0, "max_attempts": max_attempts, "provider": None, "model": None, "status": "PENDING", "source_hash": row.get("source_hash"), "candidate_hash": row.get("candidate_hash"), "dictionary_version": row.get("dictionary_version"), "schema_version": row.get("schema_version") or TRANSLATION_SCHEMA_VERSION})
    return queue


def apply_repair(item: Mapping[str, Any], *, source_hash: str, candidate: str, qa_result: Mapping[str, Any], provider: str = "fake", model: str = "fake") -> dict[str, Any]:
    """Accept only a current, exact-tuple PASS; never overwrite good fields."""
    result = dict(item)
    current = hash_source(source_text(result.get("source")))
    if str(source_hash or "") != current or current != str(result.get("source_hash") or ""):
        result.update(status="BLOCKED", code="SOURCE_CHANGED"); return result
    if int(result.get("attempt") or 0) >= int(result.get("max_attempts") or 0):
        result.update(status="MANUAL_REVIEW", code="MAX_ATTEMPTS_REACHED"); return result
    result["attempt"] = int(result.get("attempt") or 0) + 1
    result.update(provider=provider, model=model)
    # Do not promote based on a caller-crafted PASS object.  Re-run the
    # canonical auditor over the candidate immediately before promotion.
    fresh = audit_field(asin=str(result.get("asin") or ""), field=str(result.get("field") or ""),
                        source_es=result.get("source"), translated_zh=str(candidate or ""),
                        source_hash=current, dictionary_version=str(result.get("dictionary_version") or ""),
                        brand=str(result.get("brand") or ""))
    candidate_hash = hash_source(str(candidate or ""))
    exact = (str(fresh.get("source_hash") or "") == current
             and str(fresh.get("candidate_hash") or "") == candidate_hash
             and str(fresh.get("dictionary_version") or "") == str(result.get("dictionary_version") or "")
             and str(fresh.get("schema_version") or "") == str(result.get("schema_version") or ""))
    if str(fresh.get("status") or "") != "PASS" or not exact:
        result.update(status="MANUAL_REVIEW", code="QA_BINDING_MISMATCH", qa=dict(fresh)); return result
    result.update(status="PASS", repaired_translation=str(candidate), qa=dict(fresh)); return result
