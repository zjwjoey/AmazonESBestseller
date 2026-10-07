"""Strict structured-detail facts for the closure Translation V2 boundary.

This adapter is deliberately separate from the legacy flat-input profile.  It
does not reconstruct Amazon facts from display strings and it does not call a
provider.  Formal admission and dispatch require a bound ready SourceGate.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Iterable, Mapping

from .dictionary_service import is_identity_attribute
from .preclean import clean_text
from .production_contract import canonical_source_field, target_field_for
from .service import source_hash
from ..production.spanish_master import verify_artifact_hash
from ..quality.source_gate import canonical_audit_hash, verify_source_gate


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
        # Owner-approved exclusions may consume only the explicitly derived
        # eligible list.  Ordinary records must carry the canonical structured
        # source; neither branch ever parses a flat display field or raw owner
        # exclusions as a fallback.
        exclusion = raw.get("owner_optional_exclusion")
        eligible = (raw.get("eligibleattributes") if isinstance(exclusion, Mapping)
                    else raw.get("canonicalstructuredsource"))
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


def _review_snapshot_hash(snapshot: Mapping) -> str:
    payload = {"schema_version": snapshot.get("schema_version"),
               "item_ids": sorted(str(value) for value in snapshot.get("item_ids") or ()),
               "source_audit_hash": snapshot.get("source_audit_hash"),
               "item_decisions": sorted(snapshot.get("item_decisions") or [], key=lambda item: str(item.get("item_id") if isinstance(item, Mapping) else item))}
    return _hash(payload)


def _all_item_ids(draft: Mapping) -> list[str]:
    return sorted("%s:%s" % (record.get("asin"), item.get("item_id"))
                  for record in draft.get("records") or []
                  for field in (record.get("fields") or {}).values()
                  for item in field.get("items") or [])


def _verify_master_gate(master: Mapping, source_audit: Mapping, source_gate: Mapping) -> None:
    if not verify_artifact_hash(master):
        raise ValueError("VERIFIED_MASTER_ARTIFACT_INVALID")
    if not verify_source_gate(source_audit, source_gate):
        raise ValueError("SOURCE_GATE_UNVERIFIED")
    if source_gate.get("status") != "SOURCE_READY" or not source_gate.get("ready"):
        raise ValueError("SOURCE_GATE_NOT_READY")
    if master.get("source_audit_hash") != canonical_audit_hash(source_audit):
        raise ValueError("VERIFIED_MASTER_SOURCE_AUDIT_MISMATCH")
    if master.get("source_gate_status") != "SOURCE_READY":
        raise ValueError("VERIFIED_MASTER_SOURCE_GATE_MISMATCH")


def bind_formal_structured_translation_input(
        verified_master: Mapping, source_audit: Mapping, source_gate: Mapping,
        review_snapshot: Mapping, *, prompt_version: str,
        dictionary_manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Bind closure details to the existing verified Master/SourceGate chain."""
    _verify_master_gate(verified_master, source_audit, source_gate)
    if not isinstance(review_snapshot, Mapping) or review_snapshot.get("schema_version") != "structured-review-snapshot-v1":
        raise ValueError("STRUCTURED_REVIEW_SNAPSHOT_INVALID")
    if review_snapshot.get("source_audit_hash") != canonical_audit_hash(source_audit):
        raise ValueError("STRUCTURED_REVIEW_AUDIT_BINDING_MISMATCH")
    decisions = review_snapshot.get("item_decisions")
    if not isinstance(decisions, list):
        raise ValueError("STRUCTURED_REVIEW_DECISIONS_MISSING")
    review_ids = {str(item.get("item_id")) for item in decisions if isinstance(item, Mapping)
                  and str(item.get("status") or "").upper() != "APPROVED"}
    draft = build_structured_translation_draft(
        verified_master.get("records") or [], review_item_ids=review_ids)
    item_ids = _all_item_ids(draft)
    if sorted(str(value) for value in review_snapshot.get("item_ids") or ()) != item_ids:
        raise ValueError("STRUCTURED_REVIEW_ITEM_SET_MISMATCH")
    expected_decisions = sorted([{"item_id": "%s:%s" % (record["asin"], item["item_id"]),
                                  "source_hash": item["source_hash"]}
                                 for record in draft["records"] for field in record["fields"].values()
                                 for item in field["items"]], key=lambda item: item["item_id"])
    supplied_decisions = sorted([{"item_id": str(item.get("item_id") or ""), "source_hash": str(item.get("source_hash") or "")}
                                 for item in decisions if isinstance(item, Mapping)], key=lambda item: item["item_id"])
    if supplied_decisions != expected_decisions:
        raise ValueError("STRUCTURED_REVIEW_DECISION_BINDING_MISMATCH")
    result = deepcopy(draft)
    master_records = {str(row.get("asin") or "").upper(): row for row in verified_master.get("records") or []}
    for record in result["records"]:
        source = master_records[record["asin"]]
        record["brand"] = _text(source.get("brand"))
    result.update({
        "formal": True,
        "binding": {
            "verified_master_artifact_hash": verified_master.get("artifact_hash"),
            "source_audit_hash": canonical_audit_hash(source_audit),
            "source_gate_audit_hash": source_gate.get("audit_hash"),
            "dataset_hash": draft["dataset_hash"],
            "asins": [record["asin"] for record in result["records"]],
            "record_hashes": {record["asin"]: record["source_record_hash"] for record in result["records"]},
            "review_snapshot_hash": _review_snapshot_hash(review_snapshot),
            "review_item_ids_hash": _hash(sorted(review_ids)),
            "translation_schema_version": STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION,
            "prompt_version": str(prompt_version),
            "dictionary_version": str(dictionary_manifest.get("dictionary_version") or ""),
            "dictionary_hash": str(dictionary_manifest.get("dictionary_hash") or ""),
        },
    })
    return result


def validate_formal_structured_translation_input(
        formal: Mapping, verified_master: Mapping, source_audit: Mapping, source_gate: Mapping,
        review_snapshot: Mapping, *, prompt_version: str,
        dictionary_manifest: Mapping[str, Any]) -> None:
    """Rebuild formal facts from independent current authority before provider use."""
    expected = bind_formal_structured_translation_input(
        verified_master, source_audit, source_gate, review_snapshot,
        prompt_version=prompt_version, dictionary_manifest=dictionary_manifest)
    if sorted(str(row.get("asin") or "") for row in formal.get("records") or ()) != expected["binding"]["asins"]:
        raise ValueError("STRUCTURED_BINDING_ASIN_SET_MISMATCH")
    if formal.get("schema_version") != STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION:
        raise ValueError("STRUCTURED_TRANSLATION_SCHEMA_MISMATCH")
    if formal.get("binding") != expected["binding"]:
        raise ValueError("STRUCTURED_BINDING_MISMATCH")
    if _hash(formal.get("records") or []) != _hash(expected.get("records") or []):
        raise ValueError("STRUCTURED_RECORD_OR_ITEM_HASH_MISMATCH")


def _service_records(formal: Mapping) -> list[dict[str, Any]]:
    rows = []
    for record in formal.get("records") or []:
        fields = record.get("fields") or {}
        detail_items = [item for item in (fields.get("product_details") or {}).get("items") or []
                        if item.get("admission") in {"TRANSLATION_TASK", "PRESERVED_IDENTITY"}]
        bullet_items = [item for item in (fields.get("feature_bullets") or {}).get("items") or []
                        if item.get("admission") == "TRANSLATION_TASK"]
        rows.append({"asin": record.get("asin"), "brand": record.get("brand", ""),
                     "product_details": [{"label_raw": item.get("label_raw"), "value_raw": item.get("value_raw")}
                                         for item in detail_items],
                     "feature_bullets": [item.get("value_raw") for item in bullet_items]})
    return rows


def execute_formal_structured_translation(
        formal: Mapping, verified_master: Mapping, source_audit: Mapping, source_gate: Mapping,
        review_snapshot: Mapping, service: Any, *, prompt_version: str,
        dictionary_manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Use TranslationService's existing cache/TM/QA path for admitted items only."""
    validate_formal_structured_translation_input(formal, verified_master, source_audit, source_gate,
                                                 review_snapshot, prompt_version=prompt_version,
                                                 dictionary_manifest=dictionary_manifest)
    # Avoid mutating a potentially shared pool/service while binding the
    # formal structured-input contract into cache derivation. The execution
    # view shares provider/cache semantics (including inflight dedupe) but its
    # derived keys include this immutable structured schema version.
    structured_service = service.with_structured_schema_version(
        STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION)
    translated = structured_service.translate_records(
        _service_records(formal), fields=["product_details", "feature_bullets"])
    records: dict[str, Any] = {}
    provider_calls = 0
    for record in formal.get("records") or []:
        asin = record["asin"]
        provider_fields = (translated.get("records") or {}).get(asin, {}).get("fields") or {}
        output_fields = {}
        for field, field_fact in (record.get("fields") or {}).items():
            provider_envelope = provider_fields.get(target_field_for(field), {}) or {}
            provider_items = list(provider_envelope.get("items") or [])
            field_cached = provider_envelope.get("translation_status") == "cached"
            allowed = [item for item in field_fact.get("items") or []
                       if item.get("admission") in ({"TRANSLATION_TASK", "PRESERVED_IDENTITY"} if field == "product_details" else {"TRANSLATION_TASK"})]
            rendered = iter(provider_items)
            translated_by_id = {item["item_id"]: next(rendered, {}) for item in allowed}
            items = []
            for item in field_fact.get("items") or []:
                result = translated_by_id.get(item.get("item_id"))
                if result:
                    provider_calls += (int(result.get("attempt_count") or 0)
                                       if not field_cached and result.get("resolution_source") == "provider" else 0)
                    items.append({**item, "translated_text": result.get("translated_text", ""),
                                  "candidate_text": result.get("candidate_text", ""),
                                  "translation_status": result.get("translation_status"),
                                  "qa_status": result.get("qa_status"), "qa_issues": result.get("qa_issues") or []})
                elif item.get("admission") == "PRESERVED_IDENTITY":
                    items.append({**item, "translated_text": item.get("value_raw"), "candidate_text": item.get("value_raw"),
                                  "translation_status": "success", "qa_status": "pass", "qa_issues": []})
                else:
                    items.append({**item, "translated_text": "", "candidate_text": "", "translation_status": "review_required",
                                  "qa_status": "review_required", "qa_issues": [{"code": item.get("block_code") or "SOURCE_REVIEW_REQUIRED"}]})
            output_fields[field] = {"field_hash": field_fact.get("field_hash"), "items": items,
                                    "excluded_raw_trace": field_fact.get("excluded_raw_trace") or []}
        records[asin] = {"asin": asin, "source_record_hash": record.get("source_record_hash"), "fields": output_fields}
    return {"records": records, "summary": {"records": len(records), "provider_calls": provider_calls},
            "binding": deepcopy(formal.get("binding") or {})}


def dispatch_structured_translation_tasks(*_args: Any, **_kwargs: Any) -> None:
    """Retired: formal structured work must use ``TranslationService`` only."""
    raise RuntimeError("STRUCTURED_CALLBACK_DISPATCH_RETIRED")


__all__ = ["STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION", "STRUCTURED_TRANSLATION_CACHE_NAMESPACE",
           "build_structured_translation_draft", "formalize_structured_translation_input",
           "bind_formal_structured_translation_input", "validate_formal_structured_translation_input",
           "execute_formal_structured_translation", "dispatch_structured_translation_tasks"]
