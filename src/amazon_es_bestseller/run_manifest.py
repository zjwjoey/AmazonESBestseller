# -*- coding: utf-8 -*-
"""Small JSON-serializable run metadata foundation.

The manifest records workflow observability only; it is not a source of
Amazon product evidence and is intentionally independent from the CLI
orchestrator (which is deferred to a later milestone).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


_DEFAULTS: dict[str, Any] = {
    "run_id": "",
    "started_at": "",
    "finished_at": "",
    "git_commit": "",
    "status": "created",
    "config_hash": "",
    "ranking_pages_requested": 0,
    "ranking_pages_completed": 0,
    "ranking_records": 0,
    "unique_asins": 0,
    "detail_cached": 0,
    "detail_offline_reparsed": 0,
    "detail_planned": 0,
    "detail_requested": 0,
    "detail_success": 0,
    "detail_failed": 0,
    "translation_cached": 0,
    "translation_requested": 0,
    "translation_success": 0,
    "translation_partial": 0,
    "translation_failed": 0,
    "qa_p0": 0,
    "qa_p1": 0,
    "qa_p2": 0,
    "qa_p3": 0,
    "closure_source_missing": 0,
    "closure_parser_missed": 0,
    "closure_mapping_missed": 0,
    "closure_derived_missing": 0,
    "closure_translation_incomplete": 0,
    "export_status": "",
    "final_workbook": "",
    "error_stage": "",
    "error_message": "",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_manifest(run_id: str, **initial: Any) -> dict[str, Any]:
    """Create a manifest with stable defaults and optional initial values."""
    result = dict(_DEFAULTS)
    result["run_id"] = str(run_id or "")
    result["started_at"] = initial.pop("started_at", None) or _now()
    result.update(initial)
    return result


def _as_dict(manifest: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(manifest, Mapping):
        return dict(manifest)
    if hasattr(manifest, "to_dict"):
        value = manifest.to_dict()
        if isinstance(value, Mapping):
            return dict(value)
    raise TypeError("manifest must be a mapping")


def update_manifest(manifest: Mapping[str, Any], **updates: Any) -> dict[str, Any]:
    """Return a copy with stage counters/status updates applied."""
    result = _as_dict(manifest)
    result.update(updates)
    return result


def finalize_manifest(manifest: Mapping[str, Any], **updates: Any) -> dict[str, Any]:
    """Mark a run finished, defaulting open states to successful completion."""
    result = _as_dict(manifest)
    result.update(updates)
    result.setdefault("finished_at", "")
    if not result["finished_at"]:
        result["finished_at"] = _now()
    if result.get("status") in {None, "", "created", "running"}:
        result["status"] = "success"
    return result


def write_manifest(manifest: Mapping[str, Any], path: str | Path) -> Path:
    """Write UTF-8 deterministic JSON and return the output path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(_as_dict(manifest), ensure_ascii=False,
                                    indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return target


def load_manifest(path: str | Path) -> dict[str, Any]:
    """Load a manifest JSON object, failing clearly on malformed input."""
    target = Path(path)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid run manifest: %s" % target) from exc
    if not isinstance(value, dict):
        raise ValueError("run manifest must be a JSON object: %s" % target)
    return value


__all__ = ["create_manifest", "update_manifest", "finalize_manifest",
           "write_manifest", "load_manifest"]
