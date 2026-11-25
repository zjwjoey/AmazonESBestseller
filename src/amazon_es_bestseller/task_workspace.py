"""Task configuration and SQLite transaction boundary for incremental collection."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

GIB = 1024 ** 3
AUDIT_CONTRACT = "incremental-audit-v1"
DEFAULT_THRESHOLDS = {
    "parser_missed_warn": 0.05, "parser_missed_pause": 0.10,
    "minimum_source_samples": 10, "batch_title_missing_rate": 0.02,
    "batch_title_missing_minimum": 2, "task_title_missing_rate": 0.01,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as handle:
        temp = Path(handle.name)
        try:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            os.replace(temp, path)
        finally:
            handle.close()
            temp.unlink(missing_ok=True)


class TaskWorkspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.manifest_path = self.root / "task_manifest.json"
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        config = self.manifest
        if (Path(config["workspace_root"]).resolve() != self.root
                or not 0 < config["cache_warning_threshold"] <= config["cache_compact_threshold"]
                <= config["cache_limit_bytes"] <= 2 * GIB
                or config["audit_batch_size"] < 1):
            raise ValueError("INVALID_TASK_WORKSPACE")
        self.database = self.root / "audit" / "workspace.sqlite3"

    @classmethod
    def create(cls, root, task_id: str, *, snapshot_run_id=None, detail_plan_id=None,
               external_snapshot=None, cache_limit_bytes=2 * GIB,
               cache_warning_threshold=None, cache_compact_threshold=None,
               audit_batch_size=50, parser_version="v2", field_quality_thresholds=None):
        root = Path(root).resolve()
        warning = cache_limit_bytes * 8 // 10 if cache_warning_threshold is None else cache_warning_threshold
        compact = cache_limit_bytes * 9 // 10 if cache_compact_threshold is None else cache_compact_threshold
        if (not task_id.strip() or not 0 < warning <= compact <= cache_limit_bytes <= 2 * GIB
                or audit_batch_size < 1 or (external_snapshot and snapshot_run_id)):
            raise ValueError("INVALID_TASK_CONFIGURATION")
        root.mkdir(parents=True, exist_ok=True)
        path = root / "task_manifest.json"
        now = utc_now()
        manifest = dict(
            task_id=task_id, workspace_root=str(root), snapshot_run_id=snapshot_run_id,
            detail_plan_id=detail_plan_id, external_snapshot=external_snapshot,
            task_status="INITIALIZED", cache_limit_bytes=cache_limit_bytes,
            cache_warning_threshold=warning, cache_compact_threshold=compact,
            audit_batch_size=audit_batch_size, audit_contract_version=AUDIT_CONTRACT,
            parser_version=parser_version, field_quality_thresholds=DEFAULT_THRESHOLDS | (field_quality_thresholds or {}),
            created_at=now, updated_at=now,
        )
        with path.open("x", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
        return cls(root)

    @contextmanager
    def transaction(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database, isolation_level=None, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def bind(self, **values):
        allowed = {"snapshot_run_id", "detail_plan_id", "detail_plan_path", "external_snapshot", "task_status"}
        if set(values) - allowed:
            raise ValueError("TASK_BINDING_FIELD")
        with self.transaction():
            current = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            for key, value in values.items():
                if key != "task_status" and current.get(key) not in (None, value):
                    raise ValueError("TASK_BINDING_CHANGED:" + key)
                current[key] = value
            if current.get("snapshot_run_id") and current.get("external_snapshot"):
                raise ValueError("TASK_SNAPSHOT_ORIGIN_CONFLICT")
            current["updated_at"] = utc_now()
            atomic_json(self.manifest_path, current)
            self.manifest = current
