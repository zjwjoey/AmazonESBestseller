"""Immutable, offline Spanish Master promotion artifact.

This is deliberately a small boundary object: orchestration supplies already
collected rows and the source audit; this module neither collects nor
translates.  A record is promoted only when the gate is demonstrably derived
from that exact audit.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable, Mapping

from ..collection.detail import CURRENT_DETAIL_SCHEMA_VERSION, CURRENT_DETAIL_PARSER_VERSION
from ..models import is_valid_asin, normalize_asin
from ..quality.source_gate import canonical_audit_hash, evaluate_source_gate, verify_source_gate


MASTER_SCHEMA_VERSION = "spanish-master-v1"


class MasterPromotionError(ValueError):
    """Promotion is refused because source evidence or its gate is invalid."""


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _nonempty(value) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _contexts(record: Mapping) -> list[dict]:
    value = record.get("ranking_contexts") or []
    if not isinstance(value, list):
        return []
    return [copy.deepcopy(dict(item)) for item in value if isinstance(item, Mapping)]


def _context_key(value: Mapping) -> str:
    return _canonical(value)


def _immutable_raw(record: Mapping) -> dict:
    """Capture Spanish/raw evidence without derived Chinese or human fields."""
    excluded = {"notes", "remark", "remarks", "备注", "title_zh", "specification_zh",
                "product_details_zh", "feature_bullets_zh", "ranking_contexts", "audit_status",
                "source_hash", "artifact_hash", "raw_source", "observed_source"}
    return {str(key): copy.deepcopy(value) for key, value in record.items() if key not in excluded}


def _note(record: Mapping) -> object:
    for key in ("notes", "remark", "remarks", "备注"):
        if key in record:
            return copy.deepcopy(record[key])
    return ""


def _existing_by_asin(existing_master: Mapping | None) -> dict[str, Mapping]:
    records = existing_master.get("records", []) if isinstance(existing_master, Mapping) else []
    return {normalize_asin(row.get("asin")): row for row in records
            if isinstance(row, Mapping) and is_valid_asin(row.get("asin"))}


def _schema_metadata(record: Mapping, run_id: str) -> dict:
    metadata = {
        "source_hash": _hash(_immutable_raw(record)),
        "detail_schema_version": record.get("detail_schema_version", CURRENT_DETAIL_SCHEMA_VERSION),
        "ranking_schema_version": record.get("ranking_schema_version", record.get("ranking_parser_version", "ranking-v1")),
        "parser_version": record.get("parser_version", record.get("detail_parser_version", CURRENT_DETAIL_PARSER_VERSION)),
        "collected_at": record.get("collected_at", record.get("collection_time", "")),
        "observed_at": record.get("observed_at", record.get("collection_time", record.get("collected_at", ""))),
        "run_id": run_id or record.get("run_id", ""),
    }
    # Compact aliases are retained for consumers of the reviewed Production V1
    # contract; the explicit snake-case keys remain the Python-facing form.
    metadata.update({
        "sourcehash": metadata["source_hash"],
        "detailschema": metadata["detail_schema_version"],
        "rankingschema": metadata["ranking_schema_version"],
        "parserversion": metadata["parser_version"],
        "collected": metadata["collected_at"],
        "observed": metadata["observed_at"],
        "runid": metadata["run_id"],
    })
    return metadata


def _merge_by_asin(records: Iterable[Mapping], existing: Mapping | None, run_id: str) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in records or ():
        if not isinstance(row, Mapping):
            continue
        asin = normalize_asin(row.get("asin"))
        if not is_valid_asin(asin):
            raise MasterPromotionError("cannot master an invalid ASIN")
        grouped.setdefault(asin, []).append(copy.deepcopy(dict(row)))
    prior = _existing_by_asin(existing)
    output = []
    for asin in sorted(grouped):
        candidates = grouped[asin]
        seed = candidates[0]
        source = _immutable_raw(seed)
        all_contexts = []
        seen = set()
        for candidate in candidates:
            for context in _contexts(candidate):
                key = _context_key(context)
                if key not in seen:
                    seen.add(key)
                    all_contexts.append(context)
        all_contexts.sort(key=_context_key)
        old = prior.get(asin)
        if old and isinstance(old.get("raw_source"), Mapping):
            raw_source = copy.deepcopy(dict(old["raw_source"]))
            observed_source = source
        else:
            raw_source = source
            observed_source = source
        # Do not silently make two conflicting values canonical.  The first
        # accepted evidence remains raw; a refresh is available separately in
        # observed_source for investigation/explicit reconciliation.
        canonical = copy.deepcopy(seed)
        canonical["asin"] = asin
        canonical["ranking_contexts"] = all_contexts
        canonical["raw_source"] = raw_source
        canonical["observed_source"] = observed_source
        canonical["notes"] = _note(old) if old and _nonempty(_note(old)) else _note(seed)
        canonical.update(_schema_metadata(seed, run_id))
        output.append(canonical)
    return output


def _artifact_payload(records: list[dict], audit_hash: str, gate: Mapping, run_id: str) -> dict:
    return {"master_schema_version": MASTER_SCHEMA_VERSION, "source_audit_hash": audit_hash,
            "source_gate_status": gate.get("status"), "run_id": run_id, "records": records}


def build_spanish_master(records: Iterable[Mapping], source_audit: Mapping, source_gate: Mapping | None = None,
                         *, existing_master: Mapping | None = None, run_id: str = "") -> dict:
    """Promote one canonical record per ASIN after a bound source-gate check.

    ``source_gate`` is optional for ergonomic use but is always recomputed and
    verified.  A hand-written `{status: PASS}` cannot bypass field facts.
    """
    if not isinstance(source_audit, Mapping) or source_audit.get("check") != "source_fields":
        raise MasterPromotionError("Spanish Master requires a source_fields audit")
    calculated_gate = evaluate_source_gate(source_audit)
    gate = source_gate or calculated_gate
    if not verify_source_gate(source_audit, gate):
        raise MasterPromotionError("source gate is not bound to this audit")
    if not calculated_gate["ready"]:
        raise MasterPromotionError("P0/P1 source findings or review evidence block Spanish Master promotion")
    master_records = _merge_by_asin(records, existing_master, run_id)
    if not master_records:
        raise MasterPromotionError("empty input cannot enter Spanish Master")
    statuses = source_audit.get("sku_status") or {}
    for record in master_records:
        asin = record["asin"]
        if statuses.get(asin) != "SOURCE_READY":
            raise MasterPromotionError("record is not SOURCE_READY: %s" % asin)
        if not any(_nonempty(record.get(key)) for key in ("product_url", "ranking_contexts", "title_es_raw", "current_price", "rating")):
            raise MasterPromotionError("empty input cannot enter Spanish Master: %s" % asin)
        record["audit_status"] = "SOURCE_READY"
        record["auditstatus"] = "SOURCE_READY"
    audit_hash = canonical_audit_hash(source_audit)
    effective_run_id = run_id or (master_records[0].get("run_id", "") if master_records else "")
    payload = _artifact_payload(master_records, audit_hash, gate, effective_run_id)
    artifact = {**payload, "artifact_hash": _hash(payload)}
    return copy.deepcopy(artifact)


def verify_artifact_hash(artifact: Mapping) -> bool:
    """Return whether a Spanish Master artifact has not changed in transit."""
    if not isinstance(artifact, Mapping) or artifact.get("master_schema_version") != MASTER_SCHEMA_VERSION:
        return False
    payload = {key: artifact.get(key) for key in ("master_schema_version", "source_audit_hash", "source_gate_status", "run_id", "records")}
    return bool(artifact.get("artifact_hash")) and artifact.get("artifact_hash") == _hash(payload)


__all__ = ["MASTER_SCHEMA_VERSION", "MasterPromotionError", "build_spanish_master", "verify_artifact_hash"]
