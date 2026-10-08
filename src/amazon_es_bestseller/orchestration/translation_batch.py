"""Hash-bound selection manifests for bounded Production V1 translation.

The collection master may be much larger than the reviewed translation
batch.  This module makes that difference explicit: a translation batch is a
strict ASIN subset of one immutable ``spanish-master`` producer artifact.  It
is deliberately filesystem-only so operators can review the selected ASINs
before any billable provider is constructed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


MAX_TRANSLATION_BATCH_ASINS = 1500


class TranslationBatchError(ValueError):
    """A selection does not bind to the current Spanish Master evidence."""


def file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _asins(values: Iterable[object]) -> list[str]:
    result: set[str] = set()
    for value in values:
        asin = str(value or "").strip().upper()
        if not asin:
            continue
        if len(asin) != 10 or not asin.isalnum():
            raise TranslationBatchError("TRANSLATION_SELECTION_ASIN_INVALID:%s" % asin)
        result.add(asin)
    if not result:
        raise TranslationBatchError("TRANSLATION_SELECTION_EMPTY")
    if len(result) > MAX_TRANSLATION_BATCH_ASINS:
        raise TranslationBatchError("TRANSLATION_SELECTION_ASIN_CAP_EXCEEDED")
    return sorted(result)


def create_selection_manifest(*, master_artifact_path: str | Path,
                              selected_asins: Iterable[object],
                              selection_id: str = "") -> dict[str, Any]:
    """Return a reviewed, portable manifest for one <=1500-ASIN batch."""
    artifact_path = Path(master_artifact_path)
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TranslationBatchError("SPANISH_MASTER_ARTIFACT_INVALID:%s" % artifact_path) from exc
    master = artifact.get("master") if isinstance(artifact, Mapping) else None
    if not isinstance(master, Mapping) or not isinstance(master.get("records"), list):
        raise TranslationBatchError("SPANISH_MASTER_ARTIFACT_REQUIRED")
    available = {str(row.get("asin") or "").upper()
                 for row in master["records"] if isinstance(row, Mapping)}
    selected = _asins(selected_asins)
    missing = sorted(set(selected) - available)
    if missing:
        raise TranslationBatchError("TRANSLATION_SELECTION_ASIN_NOT_IN_MASTER:%s" % ",".join(missing))
    return {
        "selection_schema_version": "translation-batch-selection-v1",
        "selection_id": str(selection_id or "translation-batch").strip() or "translation-batch",
        "parent_spanish_master_artifact_hash": file_hash(artifact_path),
        "parent_spanish_master_content_hash": str(master.get("artifact_hash") or ""),
        "selected_asins": selected,
        "selected_count": len(selected),
        "max_unique_asins": MAX_TRANSLATION_BATCH_ASINS,
    }


def write_selection_manifest(path: str | Path, manifest: Mapping[str, Any]) -> Path:
    """Write an operator-reviewable manifest without introducing a database."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(manifest), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return target


def load_selection_manifest(path: str | Path, *, parent_artifact_hash: str,
                            parent_content_hash: str,
                            available_asins: Iterable[object]) -> dict[str, Any]:
    """Verify and resolve the selected subset against the current producer."""
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TranslationBatchError("TRANSLATION_SELECTION_MANIFEST_INVALID:%s" % source) from exc
    if not isinstance(value, Mapping) or value.get("selection_schema_version") != "translation-batch-selection-v1":
        raise TranslationBatchError("TRANSLATION_SELECTION_SCHEMA_INVALID")
    if str(value.get("parent_spanish_master_artifact_hash") or "") != str(parent_artifact_hash or ""):
        raise TranslationBatchError("TRANSLATION_SELECTION_PARENT_HASH_MISMATCH")
    if str(value.get("parent_spanish_master_content_hash") or "") != str(parent_content_hash or ""):
        raise TranslationBatchError("TRANSLATION_SELECTION_PARENT_CONTENT_MISMATCH")
    selected = _asins(value.get("selected_asins") or ())
    if int(value.get("selected_count") or 0) != len(selected):
        raise TranslationBatchError("TRANSLATION_SELECTION_COUNT_MISMATCH")
    if int(value.get("max_unique_asins") or 0) != MAX_TRANSLATION_BATCH_ASINS:
        raise TranslationBatchError("TRANSLATION_SELECTION_CAP_MISMATCH")
    allowed = {str(asin or "").strip().upper() for asin in available_asins}
    missing = sorted(set(selected) - allowed)
    if missing:
        raise TranslationBatchError("TRANSLATION_SELECTION_ASIN_NOT_IN_MASTER:%s" % ",".join(missing))
    return {**dict(value), "selected_asins": selected, "manifest_file_hash": file_hash(source)}


def load_parent_authority(reference: Mapping[str, Any] | None, *, artifact_dir: str | Path,
                          available_asins: Iterable[object], selected_asins: Iterable[object]) -> list[dict]:
    """Resolve producer-owned raw parents, never derived records or inline data."""
    if not isinstance(reference, Mapping) or not reference.get("artifact_path"):
        raise TranslationBatchError("TRANSLATION_PARENT_AUTHORITY_MISSING")
    path = Path(artifact_dir) / str(reference["artifact_path"])
    try:
        content = path.read_bytes()
        payload = json.loads(content)
    except (OSError, ValueError) as exc:
        raise TranslationBatchError("TRANSLATION_PARENT_AUTHORITY_ARTIFACT_INVALID") from exc
    if hashlib.sha256(content).hexdigest() != reference.get("artifact_file_hash"):
        raise TranslationBatchError("TRANSLATION_PARENT_AUTHORITY_FILE_HASH_MISMATCH")
    records = payload.get("records") if isinstance(payload, Mapping) else None
    if not isinstance(records, list) or not records or any(not isinstance(row, Mapping) for row in records):
        raise TranslationBatchError("TRANSLATION_PARENT_AUTHORITY_RECORDS_INVALID")
    digest = hashlib.sha256(json.dumps(records, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    if digest != reference.get("dataset_canonical_hash"):
        raise TranslationBatchError("TRANSLATION_PARENT_AUTHORITY_CONTENT_HASH_MISMATCH")
    asins = [str(row.get("asin") or "").strip().upper() for row in records]
    allowed = {str(asin or "").strip().upper() for asin in available_asins}
    if (len(asins) != len(set(asins)) or sorted(asins) != reference.get("asins")
            or set(asins) != allowed):
        raise TranslationBatchError("TRANSLATION_PARENT_AUTHORITY_SCOPE_MISMATCH")
    if any(row.get("owner_attribute_exclusions") is not None for row in records):
        raise TranslationBatchError("TRANSLATION_PARENT_AUTHORITY_DERIVED_RECORD")
    selected = set(selected_asins)
    return [dict(row) for row in records if str(row.get("asin") or "").upper() in selected]


def load_source_candidate(path: str | Path, *, manifest_hash: str,
                          available_asins: Iterable[object]) -> dict[str, Any]:
    """Verify an immutable closure candidate before normalizing its eligible view."""
    from ..production.spanish_source_closure import (
        _hash, _audit_eligible_attribute_view, SPANISH_SOURCE_CLOSURE_SCHEMA_VERSION,
        CURRENT_SOURCE_GATE_SCHEMA_VERSION,
    )
    from ..quality.source_gate import verify_source_gate
    from ..translation.structured_contract import build_structured_translation_draft

    source = Path(path)
    try:
        if file_hash(source) != manifest_hash:
            raise TranslationBatchError("SOURCE_CANDIDATE_MANIFEST_HASH_MISMATCH")
        manifest = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(manifest, Mapping) or manifest.get("schema_version") != SPANISH_SOURCE_CLOSURE_SCHEMA_VERSION:
            raise TranslationBatchError("SOURCE_CANDIDATE_MANIFEST_SCHEMA_INVALID")
        master_path = source.parent / "spanish_master_5480.json"
        if file_hash(master_path) != (manifest.get("artifacts") or {}).get(master_path.name):
            raise TranslationBatchError("SOURCE_CANDIDATE_MASTER_FILE_HASH_MISMATCH")
        candidate = json.loads(master_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError) as exc:
        raise TranslationBatchError("SOURCE_CANDIDATE_ARTIFACT_INVALID:%s" % exc) from exc
    records = candidate.get("records") if isinstance(candidate, Mapping) else None
    if not isinstance(candidate, Mapping) or candidate.get("schema_version") not in {
            SPANISH_SOURCE_CLOSURE_SCHEMA_VERSION, CURRENT_SOURCE_GATE_SCHEMA_VERSION}:
        raise TranslationBatchError("SOURCE_CANDIDATE_MASTER_SCHEMA_INVALID")
    if not isinstance(records, list) or not records or any(not isinstance(row, Mapping) for row in records):
        raise TranslationBatchError("SOURCE_CANDIDATE_RECORDS_INVALID")
    if _hash(records) != manifest.get("dataset_canonical_hash"):
        raise TranslationBatchError("SOURCE_CANDIDATE_MASTER_CANONICAL_HASH_MISMATCH")
    asins = [str(row.get("asin") or "").strip().upper() for row in records]
    scope = manifest.get("binding_scope") or {}
    allowed = {str(asin or "").strip().upper() for asin in available_asins}
    if (len(asins) != len(set(asins)) or any(len(asin) != 10 or not asin.isalnum() for asin in asins)
            or sorted(asins) != scope.get("asins") or len(asins) != scope.get("count")
            or candidate.get("binding_scope") != scope or not set(asins) <= allowed):
        raise TranslationBatchError("SOURCE_CANDIDATE_ASIN_SCOPE_MISMATCH")
    reference = candidate.get("parent_authority_artifact")
    policy = candidate.get("source_exclusion_policy")
    policy_records = any(isinstance(row.get("owner_attribute_exclusions"), Mapping) for row in records)
    if reference != manifest.get("parent_authority_artifact"):
        raise TranslationBatchError("SOURCE_CANDIDATE_PARENT_AUTHORITY_REFERENCE_MISMATCH")
    if reference is not None or policy_records:
        if isinstance(reference, Mapping) and (manifest.get("artifacts") or {}).get("parent_authority.json") != reference.get("artifact_file_hash"):
            raise TranslationBatchError("SOURCE_CANDIDATE_PARENT_AUTHORITY_MANIFEST_HASH_MISMATCH")
        authority = load_parent_authority(reference, artifact_dir=source.parent,
            available_asins=asins, selected_asins=asins)
        if (not isinstance(policy, Mapping) or policy.get("raw_dataset_canonical_hash") != reference.get("dataset_canonical_hash")
                or policy.get("derived_dataset_canonical_hash") != _hash(records)):
            raise TranslationBatchError("SOURCE_CANDIDATE_PARENT_AUTHORITY_POLICY_MISMATCH")
        try:
            build_structured_translation_draft(records, review_item_ids=set(), authority_records=authority)
        except ValueError as exc:
            raise TranslationBatchError("SOURCE_CANDIDATE_PARENT_AUTHORITY_POLICY_BINDING_INVALID:%s" % exc) from exc
    audit, gate = candidate.get("source_audit") or {}, candidate.get("source_gate") or {}
    if not verify_source_gate(audit, gate) or not gate.get("ready") or manifest.get("source_gate") != gate:
        raise TranslationBatchError("SOURCE_CANDIDATE_SOURCE_GATE_NOT_READY_OR_UNVERIFIED")
    return {"records": _audit_eligible_attribute_view(records),
            "parent_authority_artifact": reference,
            "source_candidate_manifest_hash": manifest_hash}


__all__ = ["MAX_TRANSLATION_BATCH_ASINS", "TranslationBatchError", "create_selection_manifest", "load_parent_authority", "load_source_candidate",
           "file_hash", "load_selection_manifest", "write_selection_manifest"]
