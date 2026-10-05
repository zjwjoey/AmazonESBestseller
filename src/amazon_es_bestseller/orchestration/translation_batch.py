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


__all__ = ["MAX_TRANSLATION_BATCH_ASINS", "TranslationBatchError", "create_selection_manifest",
           "file_hash", "load_selection_manifest", "write_selection_manifest"]
