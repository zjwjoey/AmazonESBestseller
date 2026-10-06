"""Fail-closed, repository-approved detail code-fingerprint migrations.

Normal resume remains strict.  This module is the narrow escape hatch for one
explicitly reviewed *detail-only* upgrade; it never infers compatibility from
Git history and never alters business evidence.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .checkpoint import write_json_atomic
from .phases import asins


_ARTIFACT_NAME = "detail_code_migration.json"
DETAIL_RUNTIME_FILES = (
    "src/amazon_es_bestseller/collection/detail.py",
    "src/amazon_es_bestseller/collection/detail_request_plan.py",
    "src/amazon_es_bestseller/orchestration/worker.py",
    "src/amazon_es_bestseller/orchestration/scheduler.py",
    "src/amazon_es_bestseller/orchestration/detail_reconciliation.py",
    "src/amazon_es_bestseller/orchestration/detail_code_migration.py",
    "src/amazon_es_bestseller/orchestration/state.py",
    "src/amazon_es_bestseller/quality/detail_identity.py",
    "src/amazon_es_bestseller/identity.py",
)
_REQUIRED_CONTRACT_KEYS = (
    "schema_version", "task_id", "phase", "from_code_head", "observed_to_code_head",
    "target_detail_runtime_sha256", "observed_detail_runtime_sha256",
    "plan_sha256", "candidate_manifest_sha256", "ranking_status",
    "snapshot_status", "candidate_count", "reason", "approved_by", "status",
)


def detail_runtime_sha256(project_root: str | Path | None) -> str:
    """Hash the explicit Detail production allowlist with UTF-8/LF bytes."""
    root = Path(project_root).expanduser().resolve() if project_root else Path.cwd()
    digest = hashlib.sha256()
    for relative in DETAIL_RUNTIME_FILES:
        path = root / relative
        try:
            content = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
        except OSError as exc:
            raise ValueError("DETAIL_RUNTIME_FINGERPRINT_FILE_MISSING") from exc
        digest.update(relative.encode("utf-8")); digest.update(b"\n")
        digest.update(content.encode("utf-8")); digest.update(b"\n<FILE_END>\n")
    return digest.hexdigest()


def _registry_entries(project_root: str | Path | None, task_id: str) -> list[dict[str, Any]]:
    root = Path(project_root).expanduser().resolve() if project_root else Path.cwd()
    path = root / "configs" / "tasks" / "detail_code_migrations.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("DETAIL_CODE_MIGRATION_REGISTRY_INVALID") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("DETAIL_CODE_MIGRATION_REGISTRY_INVALID")
    entries = payload.get(task_id, [])
    if not isinstance(entries, list):
        raise ValueError("DETAIL_CODE_MIGRATION_REGISTRY_INVALID")
    return [dict(entry) for entry in entries if isinstance(entry, Mapping)]


def _approved_entry(*, project_root: str | Path | None, task_id: str,
                    from_code_head: str, runtime_sha256: str) -> dict[str, Any] | None:
    for entry in _registry_entries(project_root, task_id):
        if (str(entry.get("phase") or "") == "detail"
                and str(entry.get("from_code_head") or "") == from_code_head
                and str(entry.get("target_detail_runtime_sha256") or "") == runtime_sha256):
            return entry
    return None


def _artifact_contract(*, task_id: str, from_code_head: str, observed_to_code_head: str, target_detail_runtime_sha256: str,
                       plan_sha256: str, candidate_manifest_sha256: str,
                       candidate_count: int, reason: str) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "task_id": task_id,
        "phase": "detail",
        "from_code_head": from_code_head,
        "observed_to_code_head": observed_to_code_head,
        "target_detail_runtime_sha256": target_detail_runtime_sha256,
        "observed_detail_runtime_sha256": target_detail_runtime_sha256,
        "plan_sha256": plan_sha256,
        "candidate_manifest_sha256": candidate_manifest_sha256,
        "candidate_count": candidate_count,
        "ranking_status": "COMPLETE",
        "snapshot_status": "AUTHORITATIVE",
        "reason": reason,
        "approved_by": "repository_migration_registry",
        "status": "APPROVED",
    }


def _write_or_validate_artifact(output: Path, contract: Mapping[str, Any]) -> str:
    path = output / _ARTIFACT_NAME
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("DETAIL_CODE_MIGRATION_CONFLICT") from exc
        if not isinstance(current, Mapping) or any(
                current.get(key) != contract.get(key) for key in _REQUIRED_CONTRACT_KEYS):
            raise ValueError("DETAIL_CODE_MIGRATION_CONFLICT")
        return _ARTIFACT_NAME
    payload = dict(contract)
    payload["migrated_at"] = datetime.now().isoformat(timespec="seconds")
    write_json_atomic(path, payload)
    return _ARTIFACT_NAME


def apply_approved_detail_code_migration(*, output: Path, runtime: Any, plan: Mapping,
                                         candidates: list[dict], candidate_hash: str,
                                         ranking_report: Mapping, snapshot_manifest: Mapping,
                                         current_code_head: str, project_root: str | Path | None,
                                         allow_approved_code_migration: bool) -> bool:
    """Apply one exact audited detail migration before any worker can exist."""
    current = runtime.phase_fingerprints.get("detail")
    if not isinstance(current, Mapping):
        return False
    stored_head = str(current.get("code_head") or "UNKNOWN")
    if stored_head == current_code_head:
        return False
    # Registry presence alone is not authority: retain normal bind_phase block
    # unless the CLI explicitly opted into this reviewed migration.
    if not allow_approved_code_migration:
        return False
    runtime_sha256 = detail_runtime_sha256(project_root)
    entry = _approved_entry(project_root=project_root, task_id=str(plan["task_id"]),
                            from_code_head=stored_head, runtime_sha256=runtime_sha256)
    if entry is None:
        entries = _registry_entries(project_root, str(plan["task_id"]))
        if any(str(item.get("phase") or "") == "detail"
               and str(item.get("from_code_head") or "") == stored_head for item in entries):
            raise ValueError("DETAIL_RUNTIME_FINGERPRINT_MISMATCH")
        raise ValueError("CODE_FINGERPRINT_MISMATCH")
    plan_hash = str(plan.get("canonical_plan_sha256") or "")
    if (str(current.get("plan_sha256") or "") != plan_hash
            or str(entry.get("plan_sha256") or "") != plan_hash):
        raise ValueError("PLAN_FINGERPRINT_MISMATCH")
    if (str(current.get("candidate_manifest_sha256") or "") != candidate_hash
            or str(entry.get("candidate_manifest_sha256") or "") != candidate_hash):
        raise ValueError("CANDIDATE_FINGERPRINT_MISMATCH")
    if len(candidates) != 5500 or len(asins(candidates)) != 5500:
        raise ValueError("CANDIDATE_QUOTA_NOT_MET")
    if str(ranking_report.get("status") or "") != "COMPLETE":
        raise ValueError("DETAIL_REQUIRES_COMPLETE_RANKING")
    if str(snapshot_manifest.get("snapshot_status") or "") != "AUTHORITATIVE":
        raise ValueError("DETAIL_REQUIRES_AUTHORITATIVE_SNAPSHOT")

    # A persisted active worker after an interruption is stale.  The existing
    # TaskRuntimeState resume normalization already changed RUNNING categories
    # to PENDING; only then may migration write a new checkpoint with none.
    active_workers = list(runtime.raw.get("active_workers") or [])
    if active_workers:
        if any(str(value.get("status") or "") == "RUNNING"
               for value in runtime.category_states.values() if isinstance(value, Mapping)):
            raise ValueError("DETAIL_CODE_MIGRATION_ACTIVE_WORKERS")
        runtime.raw["active_workers"] = []

    contract = _artifact_contract(
        task_id=str(plan["task_id"]), from_code_head=stored_head,
        observed_to_code_head=current_code_head, target_detail_runtime_sha256=runtime_sha256, plan_sha256=plan_hash,
        candidate_manifest_sha256=candidate_hash,
        candidate_count=len(candidates),
        reason=str(entry.get("reason") or ""),
    )
    artifact = _write_or_validate_artifact(output, contract)
    runtime.phase_fingerprints["detail"] = {
        **dict(current),
        "phase": "detail",
        "plan_sha256": plan_hash,
        "candidate_manifest_sha256": candidate_hash,
        "code_head": current_code_head,
        "detail_runtime_sha256": runtime_sha256,
        "previous_code_head": stored_head,
        "code_migration": {
            "from": stored_head, "observed_to": current_code_head, "detail_runtime_sha256": runtime_sha256,
            "reason": contract["reason"], "artifact": artifact,
        },
        "migration_count": int(current.get("migration_count") or 0) + 1,
        "schema_version": 2,
    }
    runtime.save(str(runtime.raw.get("run_status") or "PENDING"), [])
    return True


__all__ = ["DETAIL_RUNTIME_FILES", "apply_approved_detail_code_migration", "detail_runtime_sha256"]
