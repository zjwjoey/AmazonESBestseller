"""Strict structured-detail facts for the closure Translation V2 boundary.

This adapter is deliberately separate from the legacy flat-input profile.  It
does not reconstruct Amazon facts from display strings and it does not call a
provider.  Formal admission and dispatch require a bound ready SourceGate.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Callable, Iterable, Mapping

from .dictionary_service import is_identity_attribute
from .preclean import clean_text
from .production_contract import canonical_source_field, target_field_for
from .service import source_hash


STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION = "translation-structured-input-v2"
STRUCTURED_TRANSLATION_CACHE_NAMESPACE = "structured-details-item-v2"
_CJK_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def _hash(value: Any) -> str:
    return source_hash(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"), default=str))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _item_id(item: Mapping, position: int, *, prefix: str) -> str:
    return _text(item.get("item_id")) or f"{prefix}-{position}"


def _excluded_trace(record: Mapping) -> list[dict[str, Any]]:
    exclusion = record.get("owner_optional_exclusion")
    raw = exclusion.get("excluded_attributes") if isinstance(exclusion, Mapping) else []
    trace = []
    for position, item in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(item, Mapping):
            continue
        trace.append({
            "item_id": _item_id(item, position, prefix="excluded"),
            "section": _text(item.get("section")), "position": item.get("position", position),
            "label_raw": item.get("label_raw"), "value_raw": item.get("value_raw"),
            "source_hash": _hash({"kind": "owner_excluded_raw", "item": dict(item)}),
            "admission": "OWNER_EXCLUDED_RAW_TRACE",
        })
    return trace


def _detail_item(asin: str, item: Mapping, ordinal: int, *, review_item_ids: set[str], review_evidence_complete: bool) -> dict[str, Any]:
    item_id = _item_id(item, ordinal, prefix="detail")
    label, value = _text(item.get("label_raw")), _text(item.get("value_raw"))
    fact = {
        "schema_version": STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION,
        "asin": asin, "field": "product_details", "item_id": item_id,
        "section": _text(item.get("section")), "position": item.get("position", ordinal),
        "label_raw": item.get("label_raw"), "value_raw": item.get("value_raw"),
    }
    result = {**fact, "source_hash": _hash(fact), "label_admission": "RULE_OR_DICTIONARY"}
    if not label or not value:
        return {**result, "admission": "STRUCTURED_EVIDENCE_MISSING", "translate_allowed": False,
                "value_for_translation": "", "block_code": "STRUCTURED_EVIDENCE_MISSING"}
    if f"{asin}:{item_id}" in review_item_ids:
        return {**result, "admission": "REVIEW_BLOCKED", "translate_allowed": False,
                "value_for_translation": "", "block_code": "SOURCE_REVIEW_REQUIRED"}
    if _CJK_RE.search(value) or _CJK_RE.search(label):
        return {**result, "admission": "CJK_BLOCKED", "translate_allowed": False,
                "value_for_translation": "", "block_code": "MULTILINGUAL_ATTRIBUTE_REVIEW"}
    if is_identity_attribute(label):
        return {**result, "admission": "PRESERVED_IDENTITY", "translate_allowed": False,
                "value_for_translation": value, "block_code": None}
    if not review_evidence_complete:
        return {**result, "admission": "REVIEW_EVIDENCE_MISSING", "translate_allowed": False,
                "value_for_translation": "", "block_code": "SOURCE_REVIEW_EVIDENCE_MISSING"}
    cleaned = clean_text(value, field="detail_value")
    if not cleaned.get("translate_allowed"):
        return {**result, "admission": "PRECLEAN_BLOCKED", "translate_allowed": False,
                "value_for_translation": "", "block_code": "PRECLEAN_REVIEW_REQUIRED",
                "preclean": cleaned}
    return {**result, "admission": "TRANSLATION_TASK", "translate_allowed": True,
            "value_for_translation": cleaned["clean_text"], "block_code": None, "preclean": cleaned}


def _bullet_item(asin: str, value: Any, ordinal: int, *, review_item_ids: set[str], review_evidence_complete: bool) -> dict[str, Any]:
    item_id, raw = f"bullet-{ordinal}", _text(value)
    fact = {"schema_version": STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION, "asin": asin,
            "field": "feature_bullets", "item_id": item_id, "section": "",
            "position": ordinal, "label_raw": None, "value_raw": value}
    result = {**fact, "source_hash": _hash(fact), "label_admission": "NOT_APPLICABLE"}
    if not raw:
        return {**result, "admission": "STRUCTURED_EVIDENCE_MISSING", "translate_allowed": False,
                "value_for_translation": "", "block_code": "STRUCTURED_EVIDENCE_MISSING"}
    if f"{asin}:{item_id}" in review_item_ids:
        return {**result, "admission": "REVIEW_BLOCKED", "translate_allowed": False,
                "value_for_translation": "", "block_code": "SOURCE_REVIEW_REQUIRED"}
    if _CJK_RE.search(raw):
        return {**result, "admission": "CJK_BLOCKED", "translate_allowed": False,
                "value_for_translation": "", "block_code": "MULTILINGUAL_ATTRIBUTE_REVIEW"}
    if not review_evidence_complete:
        return {**result, "admission": "REVIEW_EVIDENCE_MISSING", "translate_allowed": False,
                "value_for_translation": "", "block_code": "SOURCE_REVIEW_EVIDENCE_MISSING"}
    cleaned = clean_text(raw, field="feature_bullet")
    if not cleaned.get("translate_allowed"):
        return {**result, "admission": "PRECLEAN_BLOCKED", "translate_allowed": False,
                "value_for_translation": "", "block_code": "PRECLEAN_REVIEW_REQUIRED", "preclean": cleaned}
    return {**result, "admission": "TRANSLATION_TASK", "translate_allowed": True,
            "value_for_translation": cleaned["clean_text"], "block_code": None, "preclean": cleaned}


def _field_fact(field: str, items: list[dict[str, Any]], *, excluded_raw_trace: list[dict[str, Any]] | None = None,
                evidence_status: str = "STRUCTURED") -> dict[str, Any]:
    payload = {"schema_version": STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION, "field": field,
               "items": items, "excluded_raw_trace": excluded_raw_trace or [], "evidence_status": evidence_status}
    return {
        **payload, "target_field": target_field_for(canonical_source_field(field)),
        "field_hash": _hash(payload), "translation_tasks": [deepcopy(item) for item in items if item.get("translate_allowed")],
    }


def build_structured_translation_draft(records: Iterable[Mapping], *, review_item_ids: Iterable[str] | None = None) -> dict[str, Any]:
    """Create closure-only item facts; never inspect flat display detail text."""
    review_evidence_complete = review_item_ids is not None
    review_ids = {str(item) for item in (review_item_ids or ())}
    output = []
    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        asin = _text(raw.get("asin")).upper()
        if not asin:
            raise ValueError("STRUCTURED_TRANSLATION_MISSING_ASIN")
        eligible = raw.get("eligibleattributes")
        excluded = _excluded_trace(raw)
        if not isinstance(eligible, list):
            details = _field_fact("product_details", [], excluded_raw_trace=excluded,
                                  evidence_status="STRUCTURED_EVIDENCE_MISSING")
        else:
            details = _field_fact("product_details", [
                _detail_item(asin, item, position, review_item_ids=review_ids,
                             review_evidence_complete=review_evidence_complete)
                for position, item in enumerate(eligible) if isinstance(item, Mapping)
            ], excluded_raw_trace=excluded)
        bullets_raw = raw.get("feature_bullets_raw")
        if not isinstance(bullets_raw, list):
            bullets = _field_fact("feature_bullets", [], evidence_status="STRUCTURED_EVIDENCE_MISSING")
        else:
            bullets = _field_fact("feature_bullets", [
                _bullet_item(asin, value, position, review_item_ids=review_ids,
                             review_evidence_complete=review_evidence_complete)
                for position, value in enumerate(bullets_raw)
            ])
        output.append({"asin": asin, "source_record_hash": _hash(dict(raw)),
                       "fields": {"product_details": details, "feature_bullets": bullets}})
    return {"schema_version": STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION,
            "cache_namespace": STRUCTURED_TRANSLATION_CACHE_NAMESPACE,
            "review_evidence_complete": review_evidence_complete,
            "records": output,
            "dataset_hash": _hash([item["source_record_hash"] for item in output]),
            "formal": False}


def _require_ready_source_gate(source_gate: Mapping) -> None:
    if not isinstance(source_gate, Mapping) or source_gate.get("status") != "SOURCE_READY" or not source_gate.get("ready"):
        raise ValueError("SOURCE_GATE_NOT_READY")


def formalize_structured_translation_input(draft: Mapping, source_gate: Mapping) -> dict[str, Any]:
    """Promote a draft to a formal V2 input only with a ready SourceGate."""
    _require_ready_source_gate(source_gate)
    if draft.get("schema_version") != STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION:
        raise ValueError("STRUCTURED_TRANSLATION_SCHEMA_MISMATCH")
    result = deepcopy(dict(draft))
    result["formal"] = True
    result["source_gate"] = {"status": source_gate["status"], "audit_hash": source_gate.get("audit_hash", "")}
    return result


def dispatch_structured_translation_tasks(draft: Mapping, source_gate: Mapping,
                                          dispatch: Callable[[Mapping], Any]) -> list[Any]:
    """Dispatch only formally admitted value tasks; gate rejection precedes callbacks."""
    formal = formalize_structured_translation_input(draft, source_gate)
    return [dispatch(task) for record in formal.get("records", [])
            for field in (record.get("fields") or {}).values()
            for task in field.get("translation_tasks", [])]


__all__ = ["STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION", "STRUCTURED_TRANSLATION_CACHE_NAMESPACE",
           "build_structured_translation_draft", "formalize_structured_translation_input",
           "dispatch_structured_translation_tasks"]
