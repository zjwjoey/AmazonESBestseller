"""Read-only evidence grading for detail-run artifacts.

This adapter deliberately does not create request receipts. It records what
the saved run artifacts can prove and delegates content checks to Quality Gate.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


EVIDENCE_LEVELS = ("REQUEST_EVIDENCE", "VERIFIED_SAVED_EVIDENCE", "INSUFFICIENT")


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    if isinstance(value, Mapping):
        for key in ("records", "outcomes", "details"):
            if isinstance(value.get(key), list):
                return [dict(item) for item in value[key] if isinstance(item, Mapping)]
        return [dict(item, asin=item.get("asin") or key)
                for key, item in value.items() if isinstance(item, Mapping)]
    return []


def _has_request_evidence(row: Mapping[str, Any]) -> bool:
    """Require actual request/response facts; success status alone is not enough."""
    return bool(
        row.get("requested_url") or row.get("request_url") or row.get("preferred_request_url")
    ) and bool(row.get("status_code") or row.get("http_status")) and bool(
        row.get("response_sha256") or row.get("html_sha256") or row.get("evidence_sha256")
    )


def audit_detail_run_evidence(run_dir: str | Path) -> dict[str, Any]:
    """Grade detail outcomes against immutable files already present in run_dir."""
    root = Path(run_dir)
    manifest_path = root / "detail_execution_manifest.json"
    manifest = _json(manifest_path)
    outcomes = _rows(manifest)
    checkpoint_root = root / "checkpoints"
    state = _json(root / "state" / "details_state.json")
    state_rows = _rows(state)
    state_by_asin = {str(row.get("asin") or "").upper(): row for row in state_rows}
    if not outcomes:
        outcomes = [dict(row, ranking_asin=row.get("ranking_asin") or row.get("asin"))
                    for row in state_rows]
    graded = []
    for outcome in outcomes:
        asin = str(outcome.get("ranking_asin") or outcome.get("asin") or "").upper()
        checkpoint = _json(checkpoint_root / (asin + ".json")) if asin else None
        checkpoint = checkpoint if isinstance(checkpoint, Mapping) else {}
        detail = state_by_asin.get(asin, {})
        checkpoint_record = checkpoint.get("record") if isinstance(checkpoint.get("record"), Mapping) else {}
        merged = {**detail, **checkpoint_record, **checkpoint, **outcome}
        if _has_request_evidence(merged):
            level = "REQUEST_EVIDENCE"
            reason = "request/response metadata and response hash are present"
        else:
            html_path_value = (merged.get("saved_html_path") or merged.get("html_path")
                               or merged.get("evidence_path"))
            html_path = Path(str(html_path_value)) if html_path_value else None
            if html_path and not html_path.is_absolute():
                html_path = root / html_path
            expected_hash = str(merged.get("html_sha256") or merged.get("evidence_sha256") or "").lower()
            if html_path and html_path.is_file() and expected_hash:
                actual_hash = hashlib.sha256(html_path.read_bytes()).hexdigest()
                verified = actual_hash == expected_hash
            else:
                verified = False
            if verified:
                level = "VERIFIED_SAVED_EVIDENCE"
                reason = "saved HTML exists and its recorded SHA-256 matches"
            else:
                level = "INSUFFICIENT"
                reason = "no verifiable response receipt or hash-bound saved HTML"
        graded.append({"asin": asin, "candidate_ids": outcome.get("candidate_ids", []),
                       "candidate_observation_ids": outcome.get("candidate_observation_ids", []),
                       "candidate_evidence_path": outcome.get("candidate_evidence_path", ""),
                       "candidate_evidence_file_path": outcome.get("candidate_evidence_file_path", ""),
                       "candidate_evidence_sha256": outcome.get("candidate_evidence_sha256", ""),
                       "status": outcome.get("status", ""), "evidence_level": level,
                       "evidence_reason": reason})
    counts = {level: sum(row["evidence_level"] == level for row in graded)
              for level in EVIDENCE_LEVELS}
    return {
        "audit_type": "READ_ONLY_DETAIL_RUN_EVIDENCE",
        "manifest_present": manifest_path.is_file(),
        "record_count": len(graded),
        "evidence_counts": counts,
        "records": graded,
        "source_audit_compatibility": {
            "request_receipt_validation": "SUPPORTED_ONLY_WHEN_ACTUAL_REQUEST_FIELDS_EXIST",
            "repair_or_network_replay": "NOT_PERFORMED",
            "translation_preparation": "NOT_PERFORMED",
            "formal_source_audit_pass": False,
        },
    }
