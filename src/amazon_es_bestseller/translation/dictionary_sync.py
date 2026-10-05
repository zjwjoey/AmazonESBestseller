"""Fail-closed, offline dictionary-evidence synchronization.

This module proposes or promotes compact lexical mappings only from supplied,
already-reviewed evidence.  It never calls a translation provider and never
guesses that a candidate passed Chinese QA.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .dictionary_service import DictionaryService, normalize_key

SYNC_SCHEMA_VERSION = "dictionary-sync-v1"
AUTO_FIELD_TYPES = frozenset({"attribute", "unit", "material", "color", "packaging", "boolean", "category", "technical"})


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def dictionary_manifest(service: DictionaryService, *, source_run_id: str,
                        translation_schema_version: str, previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    dictionaries = {name: getattr(service, name) for name in service.dictionary_counts()}
    content_hash = _hash(dictionaries)
    previous_hash = (previous or {}).get("dictionary_hash")
    changed = bool(previous_hash and previous_hash != content_hash)
    previous_version = int((previous or {}).get("dictionary_version", 0) or 0)
    return {"schema_version": SYNC_SCHEMA_VERSION, "dictionary_version": previous_version + 1 if changed else previous_version,
            "created_at": datetime.now(timezone.utc).isoformat(), "source_run_id": source_run_id,
            "translation_schema_version": translation_schema_version, "dictionary_hash": content_hash,
            "previous_hash": previous_hash, "counts": service.dictionary_counts()}


def sync_evidence(evidence: Iterable[Mapping[str, Any]], *, qa_results: Mapping[str, Any],
                  source_run_id: str, translation_schema_version: str,
                  previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Generate promotion/candidate/conflict artifacts from explicit QA PASS evidence."""
    evidence_rows = [dict(row) for row in evidence]
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in evidence_rows:
        row = dict(raw)
        source = str(row.get("source") or "").strip()
        target = str(row.get("target") or "").strip()
        field_type = str(row.get("field_type") or "").strip().lower()
        context_key = str(row.get("context_key") or "").strip()
        evidence_id = str(row.get("evidence_id") or "").strip()
        if source and target and field_type and context_key and evidence_id:
            grouped[(field_type, normalize_key(source), context_key)].append(row)
    promotions, candidates, conflicts = [], [], []
    for (field_type, normalized, context_key), rows in sorted(grouped.items()):
        targets = {str(row["target"]).strip() for row in rows}
        qa_pass = all(str((qa_results.get(str(row["evidence_id"])) or {}).get("qa_status") or "").upper() == "PASS"
                      for row in rows)
        ids = {str(row["evidence_id"]) for row in rows}
        base = {"field_type": field_type, "source_normalized": normalized, "context_key": context_key,
                "evidence_ids": sorted(ids), "targets": sorted(targets),
                "affected_fields": sorted({str(row.get("affected_field") or field_type) for row in rows})}
        if len(targets) != 1:
            conflicts.append({**base, "reason": "CONTEXT_CONFLICT"})
        elif field_type not in AUTO_FIELD_TYPES:
            candidates.append({**base, "reason": "FIELD_NOT_AUTO_SAFE"})
        elif len(ids) < 2:
            candidates.append({**base, "reason": "INSUFFICIENT_INDEPENDENT_EVIDENCE"})
        elif not qa_pass:
            candidates.append({**base, "reason": "CHINESE_QA_NOT_PASS"})
        else:
            promotions.append({**base, "target": next(iter(targets)), "status": "PROMOTED"})
    promoted_map = {f"{row['field_type']}|{row['context_key']}|{row['source_normalized']}": row["target"]
                    for row in promotions}
    previous_map = dict((previous or {}).get("promoted_map") or {})
    changed = promoted_map != previous_map
    version = int((previous or {}).get("dictionary_version", 0) or 0) + (1 if changed else 0)
    changelog = [{"key": key, "old": previous_map.get(key), "new": value}
                 for key, value in sorted(promoted_map.items()) if previous_map.get(key) != value]
    return {"schema_version": SYNC_SCHEMA_VERSION, "dictionary_version": version,
            "source_run_id": source_run_id, "translation_schema_version": translation_schema_version,
            "evidence_hash": _hash(sorted(evidence_rows, key=lambda row: str(row.get("evidence_id", "")))),
            "promotions": promotions, "candidates": candidates, "conflicts": conflicts,
            "promoted_map": promoted_map, "changelog": changelog,
            "impacted_fields": sorted({field for row in promotions for field in row["affected_fields"]}),
            "selective_repair_required": bool(changed),
            "counts": {"promoted": len(promotions), "candidates": len(candidates), "conflicts": len(conflicts)}}
