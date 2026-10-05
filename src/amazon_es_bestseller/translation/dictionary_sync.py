"""Fail-closed synchronization of reviewed dictionary evidence.

Promotion is deliberately narrower than candidate collection. In particular,
two reviewer rows copied from one product are *one* fact, and a QA PASS is
not reusable for a different source hash, field, context, dictionary version,
or translation schema.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .dictionary_service import DictionaryService, normalize_key

SYNC_SCHEMA_VERSION = "dictionary-sync-v2"
AUTO_FIELD_TYPES = frozenset({"attribute", "unit", "material", "color", "packaging", "boolean", "category", "technical"})
_CONTEXT_KEYS = frozenset({"category", "attribute_label", "product_type", "field"})


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def normalize_context(context: Any) -> tuple[str | None, str | None]:
    """Return the controlled context key, or an explicit rejection reason.

    A bare string is intentionally not accepted: it used to allow ambiguous
    labels such as ``filtro`` to become accidental global dictionary entries.
    The compact mapping is stable, display-independent and easy to audit.
    """
    if not isinstance(context, Mapping):
        return None, "MISSING_OR_UNKNOWN_CONTEXT"
    unknown = set(context) - _CONTEXT_KEYS
    if unknown:
        return None, "MISSING_OR_UNKNOWN_CONTEXT"
    normalized: dict[str, str] = {}
    for key in sorted(_CONTEXT_KEYS):
        value = normalize_key(context.get(key, ""))
        if value:
            normalized[key] = value
    if not normalized or not (normalized.get("category") or normalized.get("product_type") or normalized.get("attribute_label")):
        return None, "MISSING_OR_UNKNOWN_CONTEXT"
    return ";".join("%s=%s" % item for item in sorted(normalized.items())), None


def _independence_key(row: Mapping[str, Any]) -> tuple[str, str] | None:
    asin = str(row.get("asin") or "").strip().upper()
    record_hash = str(row.get("source_record_hash") or "").strip()
    if not asin or not record_hash:
        return None
    return asin, record_hash


def _qa_passes(qa: Any, row: Mapping[str, Any], *, dictionary_version: int,
               translation_schema_version: str, context_key: str) -> bool:
    """Accept only a PASS bound to this exact promotion fact."""
    if not isinstance(qa, Mapping) or str(qa.get("qa_status") or qa.get("status") or "").upper() != "PASS":
        return False
    expected = {
        "source_hash": str(row.get("source_hash") or ""),
        "target": str(row.get("target") or "").strip(),
        "field_type": str(row.get("field_type") or "").strip().lower(),
        "context_key": context_key,
        "dictionary_version": str(dictionary_version),
        "schema_version": str(translation_schema_version),
    }
    return all(str(qa.get(key) or "") == value for key, value in expected.items())


def _change_log(previous: Mapping[str, str], current: Mapping[str, str]) -> list[dict[str, Any]]:
    return [{"key": key, "old": previous.get(key), "new": current.get(key)}
            for key in sorted(set(previous) | set(current))
            if previous.get(key) != current.get(key)]


def dictionary_manifest(service: DictionaryService | None = None, *, source_run_id: str,
                        translation_schema_version: str,
                        sync_result: Mapping[str, Any] | None = None,
                        previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build the sole manifest identity consumed by sync and translation."""
    if sync_result is not None:
        manifest = dict(sync_result.get("manifest") or {})
        if not manifest:
            raise ValueError("DICTIONARY_SYNC_MANIFEST_MISSING")
        return manifest
    service = service or DictionaryService()
    dictionary = {name: getattr(service, name) for name in service.dictionary_counts()}
    # ``dictionary_hash`` always identifies the promoted runtime dictionary.
    # Package inventory gets a separate evidence hash rather than creating a
    # second incompatible definition of the same field.
    content_hash = _hash({"promoted_dictionary": {},
                          "translation_schema_version": translation_schema_version})
    previous_hash = str((previous or {}).get("dictionary_hash") or "")
    previous_version = int((previous or {}).get("dictionary_version", 0) or 0)
    return {
        "schema_version": SYNC_SCHEMA_VERSION,
        "dictionary_version": previous_version + 1 if previous_hash and previous_hash != content_hash else previous_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_run_id": source_run_id,
        "translation_schema_version": translation_schema_version,
        "dictionary_hash": content_hash,
        "previous_hash": previous_hash or None,
        "change_log": [],
        "promoted_dictionary": {},
        "base_dictionary_hash": _hash(dictionary),
        "counts": service.dictionary_counts(),
    }


def sync_evidence(evidence: Iterable[Mapping[str, Any]], *, qa_results: Mapping[str, Any],
                  source_run_id: str, translation_schema_version: str,
                  previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Promote only independent, exact-QA-bound and context-scoped facts."""
    evidence_rows = [dict(row) for row in evidence]
    previous_manifest = dict((previous or {}).get("manifest") or previous or {})
    previous_map = dict(previous_manifest.get("promoted_map") or {})
    base_version = int(previous_manifest.get("dictionary_version", 0) or 0)
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    candidates: list[dict[str, Any]] = []
    for row in evidence_rows:
        source = str(row.get("source") or "").strip()
        target = str(row.get("target") or "").strip()
        field_type = str(row.get("field_type") or "").strip().lower()
        evidence_id = str(row.get("evidence_id") or "").strip()
        context_key, context_reason = normalize_context(row.get("context"))
        if not (source and target and field_type and evidence_id):
            candidates.append({"evidence_ids": [evidence_id] if evidence_id else [], "reason": "MALFORMED_EVIDENCE"})
            continue
        if context_reason:
            candidates.append({"field_type": field_type, "source_normalized": normalize_key(source),
                               "evidence_ids": [evidence_id], "reason": context_reason})
            continue
        grouped[(field_type, normalize_key(source), context_key)].append(row)

    promotions: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for (field_type, normalized, context_key), rows in sorted(grouped.items()):
        targets = {str(row["target"]).strip() for row in rows}
        ids = {str(row["evidence_id"]) for row in rows}
        independent = {_independence_key(row) for row in rows}
        independent.discard(None)
        base = {"field_type": field_type, "source_normalized": normalized,
                "context_key": context_key, "evidence_ids": sorted(ids),
                "targets": sorted(targets), "independent_facts": sorted(independent),
                "affected_fields": sorted({str(row.get("affected_field") or field_type) for row in rows}),
                "evidence": [dict(row) for row in sorted(rows, key=lambda r: str(r["evidence_id"]))]}
        if len(targets) != 1:
            conflicts.append({**base, "reason": "CONTEXT_CONFLICT"})
        elif field_type not in AUTO_FIELD_TYPES:
            candidates.append({**base, "reason": "FIELD_NOT_AUTO_SAFE"})
        elif len(independent) < 2:
            candidates.append({**base, "reason": "INSUFFICIENT_INDEPENDENT_EVIDENCE"})
        elif not all(_qa_passes(qa_results.get(str(row["evidence_id"])), row,
                                dictionary_version=base_version,
                                translation_schema_version=translation_schema_version,
                                context_key=context_key) for row in rows):
            candidates.append({**base, "reason": "CHINESE_QA_NOT_EXACT_BOUND_PASS"})
        else:
            promotions.append({**base, "target": next(iter(targets)), "status": "PROMOTED"})

    cross_context: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in promotions:
        cross_context[(row["field_type"], row["source_normalized"])].append(row)
    cross_context_ambiguities = [
        {"field_type": key[0], "source_normalized": key[1],
         "contexts": sorted({r["context_key"] for r in rows}),
         "targets": sorted({r["target"] for r in rows}), "reason": "CROSS_CONTEXT_AMBIGUITY"}
        for key, rows in sorted(cross_context.items())
        if len({r["context_key"] for r in rows}) > 1 and len({r["target"] for r in rows}) > 1
    ]
    promoted_map = {"%s|%s|%s" % (row["field_type"], row["context_key"], row["source_normalized"]): row["target"]
                    for row in promotions}
    change_log = _change_log(previous_map, promoted_map)
    version = base_version + (1 if change_log else 0)
    promoted_dictionary = {key: promoted_map[key] for key in sorted(promoted_map)}
    dictionary_hash = _hash({"promoted_dictionary": promoted_dictionary,
                             "translation_schema_version": translation_schema_version})
    manifest = {
        "schema_version": SYNC_SCHEMA_VERSION, "dictionary_version": version,
        "created_at": datetime.now(timezone.utc).isoformat(), "source_run_id": source_run_id,
        "translation_schema_version": translation_schema_version, "dictionary_hash": dictionary_hash,
        "previous_hash": previous_manifest.get("dictionary_hash"), "change_log": change_log,
        "promoted_dictionary": promoted_dictionary, "promoted_map": promoted_dictionary,
        "promotions": promotions,
    }
    return {"schema_version": SYNC_SCHEMA_VERSION, "dictionary_version": version,
            "source_run_id": source_run_id, "translation_schema_version": translation_schema_version,
            "evidence_hash": _hash(sorted(evidence_rows, key=lambda row: str(row.get("evidence_id", "")))),
            "dictionary_hash": dictionary_hash, "manifest": manifest,
            "promotions": promotions, "candidates": candidates, "conflicts": conflicts,
            "cross_context_ambiguities": cross_context_ambiguities,
            "promoted_map": promoted_dictionary, "promoted_dictionary": promoted_dictionary,
            "change_log": change_log, "changelog": change_log,
            "impacted_fields": sorted({field for row in promotions for field in row["affected_fields"]}),
            "selective_repair_required": bool(change_log),
            "counts": {"promoted": len(promotions), "candidates": len(candidates), "conflicts": len(conflicts)}}
