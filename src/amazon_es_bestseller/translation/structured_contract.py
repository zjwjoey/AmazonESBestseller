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
from .production_contract import canonical_source_field, target_field_for, translation_candidate_hash
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


def _verified_owner_attribute_eligible(record: Mapping, authority: Mapping | None) -> tuple[list[Mapping], list[dict[str, Any]]]:
    """Accept the new derived attribute view only with its exact raw trail."""
    evidence = record.get("owner_attribute_exclusions")
    if not isinstance(evidence, Mapping) or evidence.get("schema_version") != "owner-current-source-attribute-exclusion-v1":
        raise ValueError("OWNER_ATTRIBUTE_EXCLUSION_BINDING_INVALID")
    raw = record.get("rawattributes_raw")
    eligible = record.get("eligibleattributes")
    excluded = evidence.get("excluded_items")
    if not isinstance(raw, list) or not isinstance(eligible, list) or not isinstance(excluded, list):
        raise ValueError("OWNER_ATTRIBUTE_EXCLUSION_BINDING_INVALID")
    if not isinstance(authority, Mapping) or evidence.get("raw_record_hash") != _hash(dict(authority)):
        raise ValueError("OWNER_ATTRIBUTE_EXCLUSION_AUTHORITY_REQUIRED")
    if authority.get("attributes") != raw:
        raise ValueError("OWNER_ATTRIBUTE_EXCLUSION_BINDING_INVALID")
    if evidence.get("raw_attributes_hash") != _hash(raw):
        raise ValueError("OWNER_ATTRIBUTE_EXCLUSION_BINDING_INVALID")
    if evidence.get("eligible_attributes_hash") != _hash(eligible):
        raise ValueError("OWNER_ATTRIBUTE_EXCLUSION_BINDING_INVALID")
    seen = set()
    trace = []
    for item in excluded:
        locator = item.get("locator") if isinstance(item, Mapping) else None
        if not isinstance(locator, Mapping):
            raise ValueError("OWNER_ATTRIBUTE_EXCLUSION_BINDING_INVALID")
        try:
            position = int(locator.get("position"))
        except (TypeError, ValueError):
            raise ValueError("OWNER_ATTRIBUTE_EXCLUSION_BINDING_INVALID") from None
        if position in seen or position < 0 or position >= len(raw):
            raise ValueError("OWNER_ATTRIBUTE_EXCLUSION_BINDING_INVALID")
        source = raw[position]
        if (not isinstance(source, Mapping) or locator.get("source") != "attributes"
                or source.get("label_raw") != locator.get("label_raw")
                or source.get("value_raw") != locator.get("value_raw")
                or ("section" in locator and source.get("section") != locator.get("section"))
                or ("source_position" in locator and source.get("position") != locator.get("source_position"))):
            raise ValueError("OWNER_ATTRIBUTE_EXCLUSION_BINDING_INVALID")
        if any(dict(candidate) == dict(source) for candidate in eligible if isinstance(candidate, Mapping)):
            raise ValueError("OWNER_ATTRIBUTE_EXCLUSION_BINDING_INVALID")
        seen.add(position)
        trace.append({"item_id": _item_id(source, position, prefix="excluded"), "section": _text(source.get("section")),
                      "position": source.get("position", position), "label_raw": source.get("label_raw"),
                      "value_raw": source.get("value_raw"), "source_hash": _hash({"kind": "owner_attribute_excluded_raw", "item": dict(source)}),
                      "admission": "OWNER_EXCLUDED_RAW_TRACE"})
    return eligible, trace


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


def build_structured_translation_draft(records: Iterable[Mapping], *, review_item_ids: Iterable[str] | None = None,
                                       authority_records: Iterable[Mapping] | None = None) -> dict[str, Any]:
    """Create closure-only item facts; never inspect flat display detail text."""
    review_evidence_complete = review_item_ids is not None
    review_ids = {str(item) for item in (review_item_ids or ())}
    authorities = {str(item.get("asin") or "").upper(): item for item in (authority_records or ()) if isinstance(item, Mapping)}
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
        if isinstance(raw.get("owner_attribute_exclusions"), Mapping):
            eligible, excluded = _verified_owner_attribute_eligible(raw, authorities.get(asin))
        else:
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
        dictionary_manifest: Mapping[str, Any], parent_authority_records: Iterable[Mapping] | None = None) -> dict[str, Any]:
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
    policy_asins = {str(row.get("asin") or "").upper() for row in verified_master.get("records") or []
                    if isinstance(row, Mapping) and isinstance(row.get("owner_attribute_exclusions"), Mapping)}
    authority = list(parent_authority_records or ())
    authority_asins = {str(row.get("asin") or "").upper() for row in authority if isinstance(row, Mapping)}
    if policy_asins and not policy_asins <= authority_asins:
        raise ValueError("STRUCTURED_PARENT_AUTHORITY_MISSING")
    draft = build_structured_translation_draft(
        verified_master.get("records") or [], review_item_ids=review_ids, authority_records=authority)
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
                                  "qa_status": result.get("qa_status"), "qa_issues": result.get("qa_issues") or [],
                                  "provider": result.get("provider"), "provider_alias": result.get("provider_alias"),
                                  "model": result.get("model"), "resolution_source": result.get("resolution_source"),
                                  "attempt_count": result.get("attempt_count"), "last_error": result.get("last_error")})
                elif item.get("admission") == "PRESERVED_IDENTITY":
                    items.append({**item, "translated_text": item.get("value_raw"), "candidate_text": item.get("value_raw"),
                                  "translation_status": "success", "qa_status": "pass", "qa_issues": [],
                                  "provider": "deterministic", "model": "identity-v1",
                                  "resolution_source": "identity", "attempt_count": 0, "last_error": None})
                else:
                    items.append({**item, "translated_text": "", "candidate_text": "", "translation_status": "review_required",
                                  "qa_status": "review_required", "qa_issues": [{"code": item.get("block_code") or "SOURCE_REVIEW_REQUIRED"}],
                                  "provider": None, "model": None, "resolution_source": "source_blocked",
                                  "attempt_count": 0, "last_error": None})
            output_fields[field] = {"field": field, "target_field": field_fact.get("target_field"),
                                    "source_record_hash": record.get("source_record_hash"),
                                    "field_hash": field_fact.get("field_hash"), "items": items,
                                    "excluded_raw_trace": field_fact.get("excluded_raw_trace") or []}
        records[asin] = {"asin": asin, "source_record_hash": record.get("source_record_hash"), "fields": output_fields}
    return {"records": records, "summary": {"records": len(records), "provider_calls": provider_calls},
            "binding": deepcopy(formal.get("binding") or {}),
            "translation_schema_version": STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION,
            "input_hash": _hash(formal.get("records") or [])}


def _item_source_text(item: Mapping[str, Any]) -> str:
    """Return the complete item fact used by its structured source hash."""
    fact = {key: item.get(key) for key in (
        "schema_version", "asin", "field", "item_id", "section", "position",
        "label_raw", "value_raw",
    )}
    return json.dumps(fact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _field_source_text(field: str, field_fact: Mapping[str, Any]) -> str:
    """Keep field QA bound to all label/value facts, never a value-only proxy."""
    payload = {
        "schema_version": STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION,
        "field": field,
        "target_field": field_fact.get("target_field"),
        "items": [_item_source_text(item) for item in field_fact.get("items") or []],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_unique(values: Iterable[str], code: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(code)
        seen.add(value)


def _assert_bound_execution(
        formal: Mapping, execution: Mapping, verified_master: Mapping, source_audit: Mapping,
        source_gate: Mapping, review_snapshot: Mapping, *, prompt_version: str,
        dictionary_manifest: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """Validate results against independently rebuilt formal source facts."""
    validate_formal_structured_translation_input(
        formal, verified_master, source_audit, source_gate, review_snapshot,
        prompt_version=prompt_version, dictionary_manifest=dictionary_manifest)
    if dict(execution.get("binding") or {}) != dict(formal.get("binding") or {}):
        raise ValueError("STRUCTURED_EXECUTION_BINDING_MISMATCH")
    if str(execution.get("translation_schema_version") or "") != STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION:
        raise ValueError("STRUCTURED_EXECUTION_SCHEMA_MISMATCH")
    if str(execution.get("input_hash") or "") != _hash(formal.get("records") or []):
        raise ValueError("STRUCTURED_EXECUTION_INPUT_BINDING_MISMATCH")
    source_records = list(formal.get("records") or [])
    expected_asins = [str(row.get("asin") or "").upper() for row in source_records]
    _require_unique(expected_asins, "STRUCTURED_FORMAL_DUPLICATE_ASIN")
    actual_records = execution.get("records") or {}
    if not isinstance(actual_records, Mapping):
        raise ValueError("STRUCTURED_EXECUTION_RECORDS_INVALID")
    actual_asins = [str(asin or "").upper() for asin in actual_records]
    _require_unique(actual_asins, "STRUCTURED_EXECUTION_DUPLICATE_ASIN")
    if actual_asins != expected_asins:
        raise ValueError("STRUCTURED_EXECUTION_ASIN_ORDER_OR_SET_MISMATCH")
    pairs: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for source_record in source_records:
        asin = str(source_record.get("asin") or "").upper()
        rendered_record = actual_records.get(asin)
        if not isinstance(rendered_record, Mapping):
            raise ValueError("STRUCTURED_EXECUTION_ASIN_SET_MISMATCH")
        if (str(rendered_record.get("asin") or "").upper() != asin
                or str(rendered_record.get("source_record_hash") or "")
                != str(source_record.get("source_record_hash") or "")):
            raise ValueError("STRUCTURED_EXECUTION_RECORD_BINDING_MISMATCH")
        source_fields = source_record.get("fields") or {}
        result_fields = rendered_record.get("fields") or {}
        if list(result_fields) != list(source_fields):
            raise ValueError("STRUCTURED_EXECUTION_FIELD_ORDER_OR_SET_MISMATCH")
        for field, field_fact in source_fields.items():
            result_field = result_fields.get(field)
            expected_target = target_field_for(canonical_source_field(field))
            if (not isinstance(result_field, Mapping)
                    or str(field_fact.get("target_field") or "") != expected_target
                    or str(result_field.get("field") or "") != field
                    or str(result_field.get("target_field") or "") != expected_target
                    or str(result_field.get("source_record_hash") or "")
                    != str(source_record.get("source_record_hash") or "")
                    or str(result_field.get("field_hash") or "") != str(field_fact.get("field_hash") or "")):
                raise ValueError("STRUCTURED_EXECUTION_FIELD_BINDING_MISMATCH")
            source_items = list(field_fact.get("items") or [])
            result_items = list(result_field.get("items") or [])
            if len(result_items) != len(source_items):
                raise ValueError("STRUCTURED_EXECUTION_ITEM_COVERAGE_INCOMPLETE")
            if not all(isinstance(item, Mapping) for item in result_items):
                raise ValueError("STRUCTURED_EXECUTION_ITEM_INVALID")
            _require_unique([str(item.get("item_id") or "") for item in result_items],
                            "STRUCTURED_EXECUTION_DUPLICATE_ITEM_ID")
            for source_item, result_item in zip(source_items, result_items):
                source_fact = {key: source_item.get(key) for key in source_item}
                result_source_fact = {key: result_item.get(key) for key in source_item}
                if (source_fact != result_source_fact
                        or str(source_item.get("source_hash") or "") != source_hash(_item_source_text(source_item))
                        or str(result_item.get("source_hash") or "") != str(source_item.get("source_hash") or "")):
                    raise ValueError("STRUCTURED_EXECUTION_ITEM_BINDING_MISMATCH")
        pairs.append((source_record, rendered_record))
    return pairs


def _label_translation_state(*, asin: str, item: Mapping[str, Any], dictionary_version: str) -> dict[str, Any]:
    """Only an explicit deterministic label mapping may enter Chinese display."""
    from ..quality.chinese import audit_field, canonical_qa_row
    from .full_detail import LABEL_ES_ZH
    from .protection import protect, restore

    label = str(item.get("label_raw") or "")
    protected = protect(label)
    candidate = LABEL_ES_ZH.get(label.casefold(), "")
    restored, restore_issues = restore(protected, candidate)
    context = {"item_id": item.get("item_id"), "position": item.get("position"),
               "kind": "attribute_label", "item_source_hash": item.get("source_hash")}
    if not candidate:
        qa = canonical_qa_row({"asin": asin, "field": "product_details_label",
                               "target_field": "product_details_label_zh", "field_type": "attribute_label",
                               "source_text": label, "source_hash": source_hash(label), "translated_text": "",
                               "context": context, "dictionary_version": dictionary_version,
                               "translation_schema_version": STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION,
                               "status": "MANUAL_REVIEW",
                               "issues": [{"code": "STRUCTURED_LABEL_TRANSLATION_UNAVAILABLE"}]})
    else:
        qa = audit_field(asin=asin, field="product_details_label", source_es=label,
                         translated_zh=restored, source_hash=source_hash(label),
                         dictionary_version=dictionary_version, target_field="product_details_label_zh",
                         field_type="attribute_label", context=context,
                         translation_schema_version=STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION)
        if restore_issues:
            qa = canonical_qa_row({**qa, "status": "MANUAL_REVIEW", "issues": restore_issues})
    return {"label_raw": label, "label_zh": restored if candidate else "", "label_status": qa["status"],
            "label_qa": qa, "label_protection": {"text": protected.text, "tokens": dict(protected.tokens)}}


def build_structured_production_overlay(
        formal: Mapping, execution: Mapping, *, verified_master: Mapping, source_audit: Mapping,
        source_gate: Mapping, review_snapshot: Mapping, prompt_version: str,
        dictionary_manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Build a non-release Chinese overlay from complete structured item facts.

    This is deliberately an adapter, not a second translation path: every
    display candidate is an existing item result and is admitted only after
    item-level canonical Chinese QA passes.  A single non-pass keeps the whole
    display field out of the overlay while retaining all item evidence in the
    repair queue.
    """
    from ..quality.chinese import audit_field, canonical_qa_row
    from .full_detail import LABEL_ES_ZH
    from .repair_queue import build_repair_queue

    bound_pairs = _assert_bound_execution(
        formal, execution, verified_master, source_audit, source_gate, review_snapshot,
        prompt_version=prompt_version, dictionary_manifest=dictionary_manifest)
    dictionary_version = str(dictionary_manifest.get("dictionary_version") or "")
    dictionary_hash = str(dictionary_manifest.get("dictionary_hash") or "")
    qa_rows, repair_rows, overlay_records, state_records, dictionary_impacts = [], [], [], [], []
    for source_record, rendered_record in bound_pairs:
        asin = str(source_record.get("asin") or "").upper()
        overlay, field_states = {"asin": asin}, []
        for field, field_fact in (source_record.get("fields") or {}).items():
            result_fact = (rendered_record.get("fields") or {}).get(field) or {}
            source_items = list(field_fact.get("items") or [])
            result_items = list(result_fact.get("items") or [])
            item_states, display_parts, all_pass = [], [], True
            for position, item in enumerate(source_items):
                item_id = str(item.get("item_id") or "")
                result = result_items[position]
                raw = str(item.get("value_raw") or "")
                item_source_hash = str(item.get("source_hash") or source_hash(raw))
                qa_source_hash = source_hash(raw)
                candidate = str(result.get("translated_text") or "")
                provider_status = str(result.get("translation_status") or "pending")
                identity = item.get("admission") == "PRESERVED_IDENTITY"
                if identity:
                    candidate = raw
                qa = audit_field(asin=asin, field=field, source_es=raw, translated_zh=candidate,
                                 source_hash=qa_source_hash, dictionary_version=dictionary_version,
                                 target_field=field + "_zh", field_type=field,
                                 context={"item_id": item_id, "position": position, "field": field},
                                 translation_schema_version=STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION)
                current_pass = (provider_status in {"success", "cached"}
                                and str(qa.get("status")) == "PASS")
                item_source_text = _item_source_text(item)
                item_state = {**deepcopy(item), "source_text": item_source_text,
                              "source_hash": source_hash(item_source_text), "translated_text": candidate,
                              "provider_status": provider_status, "qa": qa,
                              "promotion_status": "PROMOTED" if current_pass else "QA_BLOCKED",
                              "provider": result.get("provider"), "provider_alias": result.get("provider_alias"),
                              "model": result.get("model"), "resolution_source": result.get("resolution_source"),
                              "attempt_count": result.get("attempt_count"), "last_error": result.get("last_error"),
                              "dictionary_version": dictionary_version,
                              "dictionary_hash": dictionary_hash,
                              "translation_schema_version": STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION}
                if field == "product_details":
                    label_state = _label_translation_state(asin=asin, item=item,
                                                           dictionary_version=dictionary_version)
                    item_state.update(label_state)
                    qa_rows.append(label_state["label_qa"])
                    if label_state["label_status"] != "PASS":
                        current_pass = False
                        item_state["promotion_status"] = "QA_BLOCKED"
                        repair_rows.append({**label_state["label_qa"], "item_id": item_id + ":label",
                                            "position": position, "provider_status": "deterministic"})
                item_states.append(item_state)
                if not current_pass:
                    if str(qa.get("status")) == "PASS":
                        qa = canonical_qa_row({**qa, "status": "MANUAL_REVIEW", "issues": [{
                            "code": "STRUCTURED_PROVIDER_STATUS_NOT_PROMOTABLE",
                            "provider_status": provider_status,
                        }]})
                        item_state["qa"] = qa
                    all_pass = False
                    repair_rows.append({**qa, "item_id": item_id, "position": position,
                                        "provider_status": provider_status,
                                        "candidate_text": str(result.get("candidate_text") or candidate)})
                qa_rows.append(qa)
                dictionary_impacts.append({"asin": asin, "field": field, "item_id": item_id,
                                           "source_hash": item_source_hash, "dictionary_version": dictionary_version,
                                           "dictionary_hash": dictionary_hash})
                if field == "product_details":
                    label = str(item.get("label_raw") or "")
                    display_parts.append("%s��%s" % (LABEL_ES_ZH.get(label.casefold(), label), candidate))
                else:
                    display_parts.append(candidate)
            target = str(field_fact.get("target_field") or "")
            final = "\n".join(display_parts) if all_pass else ""
            full_source_text = _field_source_text(field, field_fact)
            field_states.append({"asin": asin, "field": field, "target_field": target,
                                  "items": item_states, "field_hash": field_fact.get("field_hash"),
                                  "source_text": full_source_text,
                                  "source_hash": source_hash(full_source_text),
                                  "structured_source_hash": field_fact.get("field_hash"),
                                  "translated_text": final,
                                 "promotion_status": "PROMOTED" if all_pass else "QA_BLOCKED",
                                 "final_zh": final, "dictionary_version": dictionary_version,
                                 "dictionary_hash": dictionary_hash,
                                 "translation_schema_version": STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION})
            if all_pass:
                overlay[target] = final
        state_records.append({"asin": asin, "source_record_hash": source_record.get("source_record_hash"),
                              "fields": field_states})
        overlay_records.append(overlay)
    queue = build_repair_queue(repair_rows, max_attempts=2)
    for item, source in zip(queue, repair_rows):
        item["item_id"] = source["item_id"]
        item["position"] = source["position"]
        if item["strategy"] in {"auto_repair", "provider_retry"}:
            item["strategy"] = "manual_review"
        item["auto_provider_repair"] = False
    return {"status": "NONFORMAL_STRUCTURED_OVERLAY", "formal_release": False, "records": state_records,
            "chinese_qa": qa_rows, "repair_queue": queue,
            "chinese_master_overlay": overlay_records,
            "dictionary_rerender_status": "NOT_IMPLEMENTED_ITEM_IMPACT_ONLY",
            "dictionary_rerender_item_impact": dictionary_impacts,
            "dictionary_version": dictionary_version, "dictionary_hash": dictionary_hash}


def build_structured_dictionary_evidence(state: Mapping) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Expose only current PASS structured-detail facts to DictionarySync."""
    from ..quality.chinese import audit_field, canonical_qa_row
    from .dictionary_sync import normalize_context

    def bound_item_qa(qa: Any, *, asin: str, item: Mapping, item_position: int, source: str, target: str,
                      target_field: str, dictionary_version: str, schema_version: str) -> dict[str, Any] | None:
        """Accept only the original, fully-bound QA decision for this item."""
        if not isinstance(qa, Mapping) or not qa.get("qa_row_version"):
            return None
        row = canonical_qa_row(qa)
        context = row.get("context") if isinstance(row.get("context"), Mapping) else {}
        expected_hash = source_hash(source)
        if (str(row.get("status") or "") != "PASS"
                or str(row.get("asin") or "").upper() != asin
                or str(row.get("field") or "") != "product_details"
                or str(row.get("target_field") or "") != target_field
                or str(row.get("source_text") or "") != source
                or str(row.get("source_hash") or "") != expected_hash
                or str(row.get("translated_text") or "") != target
                or str(row.get("dictionary_version") or "") != dictionary_version
                or str(row.get("translation_schema_version") or "") != schema_version
                or str(context.get("item_id") or "") != str(item.get("item_id") or "")
                or str(context.get("field") or "") != "product_details"
                or context.get("position") != item_position
                or str(qa.get("candidate_hash") or "") != translation_candidate_hash(row)):
            return None
        return row

    evidence, qa_results = [], {}
    for record in state.get("records") or []:
        asin = str(record.get("asin") or "").upper()
        for field in record.get("fields") or []:
            if str(field.get("field") or "") != "product_details":
                continue
            for item_position, item in enumerate(field.get("items") or []):
                effective = item.get("effective_render") if isinstance(item.get("effective_render"), Mapping) else {}
                qa = effective.get("qa") if effective else item.get("qa")
                target = str((effective or item).get("translated_text") or "")
                label = str(item.get("label_raw") or "")
                source = str(item.get("value_raw") or "")
                dictionary_version = str((effective or item).get("dictionary_version") or "")
                schema_version = str((effective or item).get("translation_schema_version") or "")
                bound_qa = bound_item_qa(qa, asin=asin, item=item, item_position=item_position,
                                          source=source, target=target,
                                          target_field=str(field.get("target_field") or ""),
                                          dictionary_version=dictionary_version, schema_version=schema_version)
                if (item.get("promotion_status") != "PROMOTED" or not target
                        or bound_qa is None
                        or item.get("admission") == "PRESERVED_IDENTITY"):
                    continue
                normalized_label = label.casefold()
                field_type = ("color" if normalized_label == "color" else
                              "material" if normalized_label in {"material", "materiales"} else
                              "unit" if normalized_label in {"unidad", "unidades"} else "attribute")
                context = {"attribute_label": label, "field": "product_details"}
                context_key, reason = normalize_context(context)
                evidence_id = "%s:product_details:%s" % (asin, item.get("item_id"))
                source_hash_value = source_hash(source)
                row = {"evidence_id": evidence_id, "asin": asin,
                       "source_record_hash": record.get("source_record_hash"), "source": source,
                       "target": target, "source_hash": source_hash_value,
                       "item_source_hash": str(item.get("source_hash") or ""),
                       "field_type": field_type, "context": context,
                       "affected_field": "product_details", "target_field": field.get("target_field"),
                       "item_id": item.get("item_id")}
                evidence.append(row)
                qa_row = audit_field(asin=asin, field="product_details", source_es=source,
                                     translated_zh=target, source_hash=source_hash_value,
                                     dictionary_version=dictionary_version,
                                     target_field=str(field.get("target_field") or ""), field_type=field_type,
                                     context=context, translation_schema_version=schema_version)
                qa_results[evidence_id] = {**qa_row, "qa_status": qa_row.get("status"), "target": target,
                                           "context_key": context_key or "", "context_error": reason}
    return evidence, qa_results


def apply_structured_dictionary_rerender(state: Mapping, manifest: Mapping) -> dict[str, Any]:
    """Apply exact dictionary promotions without re-sending provider content."""
    from ..quality.chinese import audit_field
    from .full_detail import LABEL_ES_ZH
    from .repair_queue import build_repair_queue
    from .rerender import rerender_structured_items

    change_log = list(manifest.get("change_log") or [])
    if not change_log:
        return {"state": deepcopy(dict(state)), "updates": [], "selective_repair": [],
                "status": "NO_CHANGE", "ready": True,
                "dictionary_version": manifest.get("dictionary_version"),
                "dictionary_hash": manifest.get("dictionary_hash")}

    def rerender_qa(*, asin: str, field: str, item: Mapping, position: int, candidate: str,
                    dictionary_version: str, dictionary_hash: str) -> dict[str, Any]:
        raw = str(item.get("value_raw") or "")
        return audit_field(asin=asin, field=field, source_es=raw, translated_zh=candidate,
                           source_hash=source_hash(raw), dictionary_version=dictionary_version,
                           target_field="product_details_zh", field_type=field,
                           context={"item_id": item.get("item_id"), "position": position,
                                    "field": field, "label_raw": item.get("label_raw")},
                           translation_schema_version=STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION)

    rerender = rerender_structured_items(state, manifest, qa_callback=rerender_qa)
    result = rerender["state"]
    blocked = {(str(row.get("asin") or "").upper(), str(row.get("field") or ""),
                str(row.get("item_id") or "")): row for row in rerender["selective_repair"]}
    impacted = {(str(row.get("asin") or "").upper(), str(row.get("field") or ""))
                for row in [*rerender["updates"], *blocked.values()]}
    prior_overlays = {str(row.get("asin") or "").upper(): deepcopy(dict(row))
                      for row in result.get("chinese_master_overlay") or [] if isinstance(row, Mapping)}
    qa_rows, overlay_records = [], []
    for record in result.get("records") or []:
        asin = str(record.get("asin") or "").upper()
        overlay = prior_overlays.get(asin, {"asin": record.get("asin")})
        for field in record.get("fields") or []:
            if (asin, str(field.get("field") or "")) not in impacted:
                continue
            parts, all_pass = [], True
            for item in field.get("items") or []:
                key = (str(record.get("asin") or "").upper(), str(field.get("field") or ""),
                       str(item.get("item_id") or ""))
                effect = item.get("effective_render") if isinstance(item.get("effective_render"), Mapping) else {}
                if key in blocked:
                    item["rerender_status"] = "QA_BLOCKED"
                    item["rerender_qa"] = blocked[key].get("qa") or {}
                candidate = str((effect or item).get("translated_text") or "")
                qa = effect.get("qa") if effect else item.get("qa")
                item_pass = (item.get("promotion_status") == "PROMOTED" and candidate
                             and isinstance(qa, Mapping) and qa.get("status") == "PASS"
                             and (field.get("field") != "product_details" or item.get("label_status") == "PASS")
                             and item.get("rerender_status") != "QA_BLOCKED")
                if not item_pass:
                    all_pass = False
                if field.get("field") == "product_details":
                    label = str(item.get("label_raw") or "")
                    parts.append("%s: %s" % (item.get("label_zh") or LABEL_ES_ZH.get(label.casefold(), label), candidate))
                else:
                    parts.append(candidate)
                if isinstance(qa, Mapping):
                    qa_rows.append(deepcopy(dict(qa)))
            target = str(field.get("target_field") or "")
            field["final_zh"] = "\n".join(parts) if all_pass else ""
            field["promotion_status"] = "PROMOTED" if all_pass else "QA_BLOCKED"
            if all_pass:
                field["dictionary_version"] = rerender["dictionary_version"]
                field["dictionary_hash"] = rerender["dictionary_hash"]
                overlay[target] = field["final_zh"]
            else:
                overlay.pop(target, None)
        overlay_records.append(overlay)
    repair_rows = []
    for row in rerender["selective_repair"]:
        qa = row.get("qa") if isinstance(row.get("qa"), Mapping) else {}
        repair_rows.append({**qa, "asin": row.get("asin"), "field": row.get("field"),
                            "target_field": "product_details_zh", "item_id": row.get("item_id"),
                            "issues": qa.get("issues") or [{"code": row.get("reason")}],
                            "status": qa.get("status") or "MANUAL_REVIEW"})
    queue = build_repair_queue(repair_rows, max_attempts=2)
    for item, source in zip(queue, repair_rows):
        item.update(item_id=source["item_id"], auto_provider_repair=False, strategy="manual_review")
    result["chinese_qa"] = qa_rows
    result["repair_queue"] = queue
    result["chinese_master_overlay"] = overlay_records
    result["dictionary_rerender_status"] = rerender["status"]
    result["dictionary_rerender_item_impact"] = [*(result.get("dictionary_rerender_item_impact") or []),
                                                    *rerender["updates"], *rerender["selective_repair"]]
    result["effective_dictionary_version"] = rerender["dictionary_version"]
    result["effective_dictionary_hash"] = rerender["dictionary_hash"]
    return {**rerender, "state": result, "repair_queue": queue}


def dispatch_structured_translation_tasks(*_args: Any, **_kwargs: Any) -> None:
    """Retired: formal structured work must use ``TranslationService`` only."""
    raise RuntimeError("STRUCTURED_CALLBACK_DISPATCH_RETIRED")


__all__ = ["STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION", "STRUCTURED_TRANSLATION_CACHE_NAMESPACE",
           "build_structured_translation_draft", "formalize_structured_translation_input",
           "bind_formal_structured_translation_input", "validate_formal_structured_translation_input",
           "execute_formal_structured_translation", "build_structured_production_overlay",
           "build_structured_dictionary_evidence", "apply_structured_dictionary_rerender",
           "dispatch_structured_translation_tasks"]
