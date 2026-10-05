"""Production Translation V2 adapter, state and promotion gate.

This module is deliberately provider-agnostic.  It turns an immutable Spanish
Master into a traceable input, then combines Pre-Clean and Translation V2
results into a derived state.  No function writes the collection Master.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .production_contract import (
    PRODUCTION_TRANSLATION_FIELDS, canonical_record, source_text,
)
from .service import DEFAULT_FIELD_MAP
from .schemas import TRANSLATION_SCHEMA_VERSION


PRODUCTION_INPUT_VERSION = "production-translation-input-v1"
PRODUCTION_STATE_VERSION = "production-translation-state-v1"
PROMPT_VERSION = "amazon-es-retail-v2"
RELEASE_STATUSES = {"READY", "PARTIAL", "REVIEW_REQUIRED", "BLOCKED"}
NON_RELEASE_PROMOTION_STATUSES = {
    "QA_BLOCKED", "PRECLEAN_BLOCKED", "PROVIDER_FAILED", "POLICY_BLOCKED",
    "PENDING", "MANUAL_REVIEW", "SOURCE_CHANGED",
}


def _hash(value: Any) -> str:
    if isinstance(value, str):
        raw = value
    else:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_record_map(translations: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None) -> dict[str, dict[str, Any]]:
    if not translations:
        return {}
    values = translations.values() if isinstance(translations, Mapping) else translations
    result: dict[str, dict[str, Any]] = {}
    for item in values:
        if not isinstance(item, Mapping):
            continue
        asin = str(item.get("asin") or "").strip().upper()
        if asin:
            result[asin] = deepcopy(dict(item))
    return result


def merge_translation_shards(shards: Sequence[Mapping[str, Any]], *,
                             legacy_results: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """Aggregate immutable shard records by ASIN and target field.

    A field is only replaced when its source hash is unchanged.  A changed
    source is retained as a non-publishable candidate with explicit evidence,
    so an old translation can never silently become current.
    """
    aggregate = _as_record_map(legacy_results)
    for shard in shards:
        records = shard.get("records") if isinstance(shard, Mapping) else None
        incoming = _as_record_map(records or {})
        for asin, new_record in incoming.items():
            current = aggregate.setdefault(asin, {"asin": asin, "fields": {}})
            current_fields = current.setdefault("fields", {})
            for field, value in (new_record.get("fields") or {}).items():
                if not isinstance(value, Mapping):
                    continue
                candidate = deepcopy(dict(value))
                if isinstance(shard, Mapping) and shard.get("batch_id"):
                    candidate.setdefault("batch_id", shard.get("batch_id"))
                old = current_fields.get(field)
                if not candidate.get("source_hash"):
                    candidate.update(translation_status="source_changed", qa_status="review_required",
                                     promotion_status="SOURCE_CHANGED", last_error={
                                         "code": "MISSING_SOURCE_HASH"})
                if old and old.get("source_hash") and candidate.get("source_hash") \
                        and old.get("source_hash") != candidate.get("source_hash"):
                    history = list(old.get("history") or [])
                    history.append({"source_hash": old.get("source_hash"),
                                    "translation_status": old.get("translation_status"),
                                    "candidate_text": old.get("candidate_text"),
                                    "updated_at": old.get("updated_at")})
                    candidate["history"] = history
                    candidate["translation_status"] = "source_changed"
                    candidate["qa_status"] = "review_required"
                    candidate["promotion_status"] = "SOURCE_CHANGED"
                    candidate["last_error"] = {
                        "code": "SOURCE_CHANGED",
                        "old_source_hash": old.get("source_hash"),
                        "new_source_hash": candidate.get("source_hash"),
                    }
                elif old:
                    merged = deepcopy(dict(old))
                    merged.update(candidate)
                    candidate = merged
                current_fields[field] = candidate
            for key in ("source_record_hash", "source_record"):
                if key in new_record:
                    current[key] = deepcopy(new_record[key])
            current["asin"] = asin
    return aggregate


def shard_batch_id(*, dataset_hash: str, selection: Mapping[str, Any],
                   prompt_version: str, schema_version: str, provider: str = "",
                   execution_mode: str = "") -> str:
    payload = {"dataset_hash": dataset_hash, "selection": dict(selection),
               "prompt_version": prompt_version, "schema_version": schema_version,
               "provider": provider, "execution_mode": execution_mode}
    return "batch_" + _hash(payload)[:16]


def shard_records_equal(left: Any, right: Any) -> bool:
    """Compare retry output while ignoring timestamps that do not change facts."""
    volatile = {"created_at", "updated_at", "translated_at"}

    def scrub(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: scrub(item) for key, item in value.items() if key not in volatile}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return scrub(left) == scrub(right)


def shard_immutable_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Require every immutable batch identity component to match on retry."""
    keys = ("run_id", "batch_id", "selection", "input_dataset_hash",
            "translation_schema_version", "prompt_version", "provider", "execution_mode")
    return all(left.get(key) == right.get(key) for key in keys) and shard_records_equal(
        left.get("records"), right.get("records"))


def compute_release_status(statuses: Iterable[str]) -> str:
    """Derive the global release gate from every field promotion status."""
    values = {str(status or "PENDING") for status in statuses}
    values.discard("SOURCE_MISSING")
    if not values:
        # An empty dataset, or one with no translatable source at all, is not a
        # production release.  READY means that evidence was actually checked.
        return "BLOCKED"
    if values & {"QA_BLOCKED", "MANUAL_REVIEW"}:
        return "REVIEW_REQUIRED"
    if values & NON_RELEASE_PROMOTION_STATUSES:
        return "BLOCKED"
    return "READY" if values <= {"PROMOTED"} else "BLOCKED"


def release_gate(release_candidate: Mapping[str, Any]) -> tuple[bool, str]:
    status = str(release_candidate.get("release_status") or "BLOCKED")
    return status == "READY", status


def build_production_input(records: Iterable[Mapping[str, Any]], *,
                           source_run_id: str = "", source_schema_version: str = "master-v1",
                           translation_schema_version: str = TRANSLATION_SCHEMA_VERSION,
                           run_id: str = "") -> dict[str, Any]:
    """Build a standalone, hash-bound Production Translation Input."""
    canonical: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in records:
        record = canonical_record(raw)
        asin = record["asin"]
        if not asin:
            raise ValueError("PRODUCTION_INPUT_MISSING_ASIN")
        if asin in seen:
            raise ValueError("PRODUCTION_INPUT_DUPLICATE_ASIN:%s" % asin)
        seen.add(asin)
        # Record hash binds the complete Spanish Master row. Field hashes below
        # still provide the field-level incremental boundary.
        record_hash = _hash(dict(raw))
        fields = {}
        for field in PRODUCTION_TRANSLATION_FIELDS:
            raw_value = record.get(field, "")
            text = source_text(raw_value)
            fields[field] = {"source_text": text, "source_hash": _hash(text),
                             "raw_value": raw_value}
        preserved_master = deepcopy(dict(raw))
        # Keep the complete source row for the eventual release/export stage;
        # the canonical fields below are the only values consumed by V2.
        preserved_master.update(deepcopy(record))
        canonical.append({
            "asin": asin,
            "source_record_hash": record_hash,
            "source_schema_version": source_schema_version,
            "translation_schema_version": translation_schema_version,
            "fields": fields,
            "source_record": preserved_master,
        })
    dataset_hash = _hash([row["source_record_hash"] for row in canonical])
    manifest = {
        "manifest_version": PRODUCTION_INPUT_VERSION,
        "run_id": run_id,
        "source_run_id": source_run_id,
        "record_count": len(canonical),
        "unique_asin_count": len(seen),
        "dataset_hash": dataset_hash,
        "created_at": _now(),
        "translation_schema_version": translation_schema_version,
    }
    return {"manifest": manifest, "records": canonical}


def records_for_preclean(production_input: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expose only canonical Spanish fields to the existing Pre-Clean engine."""
    rows = []
    for item in production_input.get("records", []):
        source = item.get("source_record") or {}
        row = deepcopy(source)
        row["asin"] = str(item.get("asin") or "").upper()
        rows.append(row)
    return rows


def _is_policy_error(error: Any) -> bool:
    text = str(error or "").casefold()
    return any(token in text for token in ("content_safety", "data_inspection", "content policy", "safety policy"))


def _field_state(*, asin: str, source_field: str, source: Mapping[str, Any],
                 preclean: Mapping[str, Any] | None,
                 result: Mapping[str, Any] | None) -> dict[str, Any]:
    target = DEFAULT_FIELD_MAP.get(source_field, source_field)
    source_value = str(source.get("source_text") or "")
    base = {
        "asin": asin, "field": source_field, "target_field": target,
        "source_text": source_value, "source_hash": source.get("source_hash", ""),
        "preclean_status": (preclean or {}).get("clean_status", "SOURCE_MISSING"),
        "translate_allowed": bool((preclean or {}).get("translate_allowed", False)),
        "resolution_method": None, "translated_text": "", "candidate_text": "",
        "qa_status": "pending", "qa_issues": [], "promotion_status": "PENDING",
        "final_zh": None, "last_error": None, "updated_at": _now(),
    }
    if not source_value:
        base.update(translation_status="source_missing", qa_status="source_missing",
                    promotion_status="SOURCE_MISSING")
        return base
    if not base["translate_allowed"]:
        base.update(translation_status="preclean_blocked", qa_status="review_required",
                    promotion_status="PRECLEAN_BLOCKED",
                    qa_issues=[{"code": item} for item in (preclean or {}).get("issues", [])])
        return base
    if not result:
        base.update(translation_status="pending", promotion_status="PENDING")
        return base
    result_source_hash = str(result.get("source_hash") or "")
    expected_source_hash = str(source.get("source_hash") or "")
    if not result_source_hash:
        base.update(translation_status="source_changed", qa_status="review_required",
                    promotion_status="SOURCE_CHANGED", candidate_text=result.get("candidate_text") or
                    result.get("translated_text") or "", last_error={
                        "code": "MISSING_SOURCE_HASH", "expected_source_hash": expected_source_hash,
                    })
        return base
    if result_source_hash != expected_source_hash:
        base.update(translation_status="source_changed", qa_status="review_required",
                    promotion_status="SOURCE_CHANGED", candidate_text=result.get("candidate_text") or
                    result.get("translated_text") or "", last_error={
                        "code": "SOURCE_CHANGED",
                        "expected_source_hash": source.get("source_hash"),
                        "result_source_hash": result_source_hash,
                    })
        return base
    base.update({key: result.get(key) for key in (
        "translated_text", "candidate_text", "qa_status", "qa_issues", "last_error",
        "resolution_source", "provider", "provider_alias", "model", "attempt_count",
        "batch_id", "schema_version", "prompt_version",
    ) if key in result})
    base["candidate_text"] = base.get("candidate_text") or base.get("translated_text") or ""
    base["resolution_method"] = result.get("resolution_source") or result.get("provider")
    status = str(result.get("translation_status") or "pending")
    base["translation_status"] = status
    if status in {"success", "cached"} and result.get("qa_status") == "pass" and not result.get("qa_issues"):
        base["promotion_status"] = "PROMOTED"
        base["final_zh"] = result.get("translated_text") or ""
    elif status == "qa_failed" or result.get("qa_status") == "qa_failed":
        base["promotion_status"] = "QA_BLOCKED"
    elif status == "failed":
        base["promotion_status"] = "POLICY_BLOCKED" if _is_policy_error(result.get("last_error")) else "PROVIDER_FAILED"
    elif status == "preclean_blocked":
        base["promotion_status"] = "PRECLEAN_BLOCKED"
    else:
        base["promotion_status"] = "PENDING"
    return base


def build_production_state(production_input: Mapping[str, Any],
                           preclean_records: Sequence[Mapping[str, Any]],
                           translations: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]], *,
                           qa_version: str = "translation-qa-v1",
                           prompt_version: str = PROMPT_VERSION) -> dict[str, Any]:
    """Create field states and a release candidate without mutating source."""
    preclean_by_asin = {str(row.get("asin") or "").upper(): row for row in preclean_records}
    if isinstance(translations, Mapping):
        translation_by_asin = {str(k).upper(): v for k, v in translations.items()}
    else:
        translation_by_asin = {str(v.get("asin") or "").upper(): v for v in translations}
    states: list[dict[str, Any]] = []
    release_records: list[dict[str, Any]] = []
    field_counts: Counter[str] = Counter()
    sku_counts: Counter[str] = Counter()
    for item in production_input.get("records", []):
        asin = str(item.get("asin") or "").upper()
        preclean = preclean_by_asin.get(asin, {})
        translated_record = translation_by_asin.get(asin, {}) or {}
        translated_fields = translated_record.get("fields") or {}
        source_record = item.get("source_record") or {}
        field_states = []
        release = {"asin": asin, "source_record_hash": item.get("source_record_hash"), "fields": {}}
        for source_field in PRODUCTION_TRANSLATION_FIELDS:
            source = (item.get("fields") or {}).get(source_field) or {"source_text": "", "source_hash": ""}
            clean = (preclean.get("fields") or {}).get(source_field) or {}
            target = DEFAULT_FIELD_MAP.get(source_field, source_field)
            result = translated_fields.get(target) or translated_fields.get(source_field)
            state = _field_state(asin=asin, source_field=source_field, source=source,
                                 preclean=clean, result=result)
            state["qa_version"] = qa_version
            state["prompt_version"] = prompt_version
            field_states.append(state)
            field_counts[state["promotion_status"]] += 1
            if state["final_zh"] is not None:
                release["fields"][target] = state["final_zh"]
        statuses = {state["promotion_status"] for state in field_states}
        if "MANUAL_REVIEW" in statuses or "QA_BLOCKED" in statuses:
            release_status = "REVIEW_REQUIRED"
        elif ("PROVIDER_FAILED" in statuses or "POLICY_BLOCKED" in statuses or
              "PRECLEAN_BLOCKED" in statuses or "SOURCE_CHANGED" in statuses):
            release_status = "PARTIAL" if "PROMOTED" in statuses else "BLOCKED"
        elif "PENDING" in statuses:
            release_status = "PARTIAL" if "PROMOTED" in statuses else "BLOCKED"
        else:
            release_status = "READY"
        sku_counts[release_status] += 1
        states.append({"asin": asin, "source_record_hash": item.get("source_record_hash"),
                       "source_record": deepcopy(source_record), "fields": field_states,
                       "release_status": release_status})
        release["release_status"] = release_status
        release["field_statuses"] = {state["target_field"]: state["promotion_status"] for state in field_states}
        release_records.append(release)
    summary = {
        "record_count": len(states),
        "unique_asin_count": len({row["asin"] for row in states}),
        "field_counts": dict(field_counts),
        "sku_counts": dict(sku_counts),
        "qa_version": qa_version,
        "prompt_version": prompt_version,
        "master_writes": 0,
        "production_apply": False,
    }
    repair_queue = [
        {"asin": row["asin"], "field": field["field"], "target_field": field["target_field"],
         "status": field["promotion_status"], "source_hash": field["source_hash"],
         "candidate_text": field.get("candidate_text", ""), "last_error": field.get("last_error")}
        for row in states for field in row["fields"]
        if field["promotion_status"] in NON_RELEASE_PROMOTION_STATUSES
    ]
    all_statuses = [field["promotion_status"] for row in states for field in row["fields"]]
    global_release_status = (compute_release_status(all_statuses)
                             if states else "BLOCKED")
    repair_kinds = {
        "QA_BLOCKED": "QA_REPAIR", "MANUAL_REVIEW": "QA_REPAIR",
        "PRECLEAN_BLOCKED": "PRE_CLEAN_REVIEW", "PROVIDER_FAILED": "PROVIDER_RETRY",
        "POLICY_BLOCKED": "POLICY_MANUAL", "PENDING": "PENDING",
        "SOURCE_CHANGED": "SOURCE_CHANGED",
    }
    field_lookup = {(row["asin"], field["field"]): field
                    for row in states for field in row["fields"]}
    for item in repair_queue:
        item["repair_kind"] = repair_kinds.get(item["status"], "REVIEW")
        field = field_lookup.get((item["asin"], item["field"]), {})
        item["translation_status"] = field.get("translation_status", "pending")
        item["qa_status"] = field.get("qa_status", "pending")
        item["qa_issues"] = field.get("qa_issues", [])
        item["resolution_method"] = field.get("resolution_method")
        item["provider"] = field.get("provider")
        item["batch_id"] = field.get("batch_id")
    return {
        "state_version": PRODUCTION_STATE_VERSION,
        "created_at": _now(),
        "input_manifest": deepcopy(production_input.get("manifest") or {}),
        "summary": summary,
        "records": states,
        "repair_queue": repair_queue,
        "release_candidate": {"records": release_records, "release_status": global_release_status,
                              "release_status_reason": sorted(set(all_statuses)),
                              "repair_queue_count": len(repair_queue)},
    }
