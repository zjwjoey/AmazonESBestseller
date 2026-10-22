"""Explicit copy-on-upgrade and rollback pointers for isolated state only."""
from __future__ import annotations

import json
from pathlib import Path
from .contracts import timestamp
from .storage import atomic_json, digest, immutable_bytes, immutable_json, read_sealed, sealed, file_lock, _atomic_bytes


def upgrade_state(path, *, target_version=1):
    path = Path(path).resolve()
    if target_version != 1:
        raise ValueError("UNSUPPORTED_TARGET_SCHEMA")
    with file_lock(path.parent / "migration.lock"):
        raw = path.read_bytes()
        value = json.loads(raw)
        if value.get("schema_version") == 1:
            read_sealed(path)
            return {"changed": False, "path": str(path)}
        if value.get("schema_version") != 0 or not isinstance(value.get("payload"), dict):
            raise ValueError("UNSUPPORTED_SOURCE_SCHEMA")
        backup = path.parent / "schema_history" / (digest(raw) + ".v0.json")
        immutable_bytes(backup, raw)
        journal = path.parent / "schema_history" / (digest([str(path), digest(raw)]) + ".migration.json")
        record = {"source_version": 0, "target_version": 1, "source_path": str(path),
            "rollback_path": str(backup), "rollback_sha256": digest(raw),
            "upgraded_sha256": digest(sealed(value["payload"])), **timestamp()}
        if journal.exists():
            existing = json.loads(journal.read_text(encoding="utf-8"))
            if any(existing.get(key) != record[key] for key in ("source_version", "target_version", "source_path", "rollback_path", "rollback_sha256", "upgraded_sha256")):
                raise ValueError("IMMUTABLE_MIGRATION_JOURNAL_CONFLICT")
            record = existing
        else:
            immutable_json(journal, record)
        # Publish rollback instructions before replacing the state; interrupted
        # upgrades can be resumed without losing their original bytes.
        atomic_json(path, sealed(value["payload"]), backup=True)
        return {"changed": True, "path": str(path), "journal": str(journal), **record}


def rollback_state(journal_path):
    journal_path = Path(journal_path).resolve()
    record = json.loads(journal_path.read_text(encoding="utf-8"))
    path = Path(record["source_path"])
    backup = Path(record["rollback_path"])
    # Restrict a supplied journal to its own migration tree.
    if path.parent != journal_path.parent.parent or backup.parent != journal_path.parent:
        raise ValueError("ROLLBACK_PATH_OUTSIDE_MIGRATION_TREE")
    with file_lock(path.parent / "migration.lock"):
        raw = backup.read_bytes()
        if digest(raw) != record["rollback_sha256"] or digest(path.read_bytes()) != record["upgraded_sha256"]:
            raise ValueError("ROLLBACK_CHECKSUM_OR_CHANGED_STATE")
        # Exact previous payload, not an attempted lossy down-conversion.
        _atomic_bytes(path, raw)
        return {"rolled_back": True, "path": str(path), "schema_version": 0}
