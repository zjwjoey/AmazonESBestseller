# -*- coding: utf-8 -*-
"""Plan-driven, resumable detail execution.

Offline actions never open a browser. Network results are merged into the
authoritative :class:`DetailState`; ``details.json`` is rebuilt from that
state and is never the source of truth for an incremental run.
"""
from __future__ import annotations

import csv
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from ..access.detector import AccessStopError
from ..monitoring.detail_planner import validate_detail_plan
from .checkpoints import read_checkpoint, write_checkpoint
from .detail import collect_details, reparse_saved_details
from .planning import DetailState

NETWORK_ACTIONS = frozenset({"FETCH_NEW", "REFETCH_INVALID_CACHE", "RETRY_TRANSIENT_FAILURE"})
OFFLINE_ACTIONS = frozenset({"REUSE_VALID_CACHE", "REPARSE_SAVED_HTML", "VERIFY_IDENTITY",
                             "BLOCK_ACCESS_STATE", "BLOCK_LINK_IDENTITY", "BLOCK_CODE_FIX"})


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _checkpoint_record(checkpoint: Mapping | None) -> dict | None:
    if not isinstance(checkpoint, Mapping) or checkpoint.get("status") != "success":
        return None
    record = checkpoint.get("record")
    return dict(record) if isinstance(record, Mapping) else None


def _asin(row: Mapping) -> str:
    return str(row.get("ranking_asin") or row.get("asin") or "").strip().upper()


def _write_identity_queue(root: Path, rows: list[dict]) -> None:
    payload = []
    for row in rows:
        payload.append({
            "ranking_asin": row.get("ranking_asin") or row.get("asin") or "",
            "requested_asin": row.get("requested_asin") or row.get("ranking_asin") or "",
            "resolved_asin": row.get("resolved_asin") or "",
            "parent_asin": row.get("parent_asin") or "",
            "identity_status": row.get("identity_status") or "IDENTITY_UNCONFIRMED",
            "identity_evidence": row.get("identity_evidence") or [],
            "ranking_url": row.get("ranking_product_url_raw") or "",
            "final_url": row.get("final_url") or "",
            "reason": row.get("action_reason") or row.get("error") or "需要人工身份复核",
        })
    _atomic_json(root / "identity_review_queue.json", payload)
    with (root / "identity_review_queue.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["ranking_asin", "requested_asin", "resolved_asin", "parent_asin",
                  "identity_status", "identity_evidence", "ranking_url", "final_url", "reason"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in payload:
            row = dict(row)
            row["identity_evidence"] = json.dumps(row["identity_evidence"], ensure_ascii=False)
            writer.writerow(row)


def execute_detail_plan(plan: Mapping, session, out_dir: str, *, collector: Callable = collect_details,
                        checkpoint_root=None, saved_html=None, offline: bool = False,
                        parser_version: str = "v1") -> dict:
    """Execute a validated plan and atomically merge its delta into state."""
    if isinstance(plan, list):
        plan = {"records": plan,
                "snapshot_id": next((row.get("snapshot_id") for row in plan
                                      if isinstance(row, Mapping) and row.get("snapshot_id")), "")}
    plan = validate_detail_plan(plan)
    records = [dict(row) for row in (plan.get("records") or []) if isinstance(row, Mapping)]
    root = Path(out_dir)
    checkpoint_dir = Path(checkpoint_root or (root / "checkpoints"))
    state = DetailState(root / "state" / "details_state.json")
    if len(state) == 0 and (root / "details.json").exists():
        try:
            legacy = json.loads((root / "details.json").read_text(encoding="utf-8"))
            if isinstance(legacy, list):
                state.update(legacy)
        except (OSError, ValueError):
            pass

    skipped: list[dict] = []
    pending: list[dict] = []
    reparse_rows: list[dict] = []
    verify_rows: list[dict] = []
    for row in records:
        action = str(row.get("detail_action") or "")
        asin = _asin(row)
        if action == "REPARSE_SAVED_HTML":
            reparse_rows.append(row)
            continue
        if action == "VERIFY_IDENTITY":
            verify_rows.append(row)
            continue
        if action in {"REUSE_VALID_CACHE", "BLOCK_ACCESS_STATE", "BLOCK_LINK_IDENTITY", "BLOCK_CODE_FIX"}:
            skipped.append({"asin": asin, "ranking_asin": asin, "action": action,
                            "status": "REUSED" if action == "REUSE_VALID_CACHE" else "BLOCKED"})
            continue
        if action not in NETWORK_ACTIONS:
            skipped.append({"asin": asin, "ranking_asin": asin, "action": action,
                            "status": "PLAN_SKIPPED"})
            continue
        checkpoint = read_checkpoint(checkpoint_dir, asin)
        if (checkpoint and checkpoint.get("status") == "success"
                and checkpoint.get("snapshot_id") == row.get("snapshot_id")
                and checkpoint.get("action") == action):
            recovered = _checkpoint_record(checkpoint)
            if recovered:
                state.update([recovered])
            skipped.append({"asin": asin, "ranking_asin": asin, "action": action,
                            "status": "ALREADY_SUCCESS"})
            continue
        pending.append(row)

    if reparse_rows:
        html_root = saved_html or next((row.get("saved_html_dir") for row in reparse_rows
                                        if row.get("saved_html_dir")), root / "html")
        reparsed = reparse_saved_details(
            html_root, state, asins=[_asin(row) for row in reparse_rows],
            parser_version=parser_version)
        reparsed_by_asin = {str(row.get("asin") or "").upper(): row for row in reparsed}
        for row in reparse_rows:
            asin = _asin(row)
            rec = reparsed_by_asin.get(asin)
            if rec:
                write_checkpoint(checkpoint_dir, asin, {"asin": asin, "status": "success",
                    "action": "REPARSE_SAVED_HTML", "snapshot_id": row.get("snapshot_id"),
                    "record": rec})
                skipped.append({"asin": asin, "ranking_asin": asin,
                                "action": "REPARSE_SAVED_HTML", "status": "SUCCESS"})
            else:
                skipped.append({"asin": asin, "ranking_asin": asin,
                                "action": "REPARSE_SAVED_HTML", "status": "PENDING"})
    if verify_rows:
        _write_identity_queue(root, verify_rows)
        skipped.extend({"asin": _asin(row), "ranking_asin": _asin(row),
                        "action": "VERIFY_IDENTITY", "status": "REVIEW_REQUIRED"}
                       for row in verify_rows)

    request_urls = {_asin(row): str(row.get("preferred_request_url") or "") for row in pending}
    context = {
        _asin(row): {"snapshot_id": row.get("snapshot_id"),
                     "ranking_asin": row.get("ranking_asin") or row.get("asin"),
                     "requested_asin": row.get("requested_asin") or _asin(row),
                     "ranking_product_url_raw": row.get("ranking_product_url_raw") or "",
                     "preferred_request_url": row.get("preferred_request_url") or "",
                     "action": row.get("detail_action"),
                     "attempt": int((read_checkpoint(checkpoint_dir, _asin(row)) or {}).get("attempt", 0) or 0) + 1}
        for row in pending
    }
    details: list[dict] = []
    access_stop = None
    if pending:
        if offline:
            raise ValueError("offline detail execution contains network actions")
        try:
            details = collector([_asin(row) for row in pending], session, str(root),
                                request_urls=request_urls, execution_context=context,
                                write_summary=False, parser_version=parser_version)
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            details = collector([_asin(row) for row in pending], session, str(root),
                                request_urls=request_urls, execution_context=context)
        except AccessStopError as exc:
            access_stop = str(exc)

    for row in pending:
        recovered = _checkpoint_record(read_checkpoint(checkpoint_dir, _asin(row)))
        if recovered:
            state.update([recovered])
    if details:
        state.update(details)
    state.save()
    details_full = state.records()
    _atomic_json(root / "details.json", details_full)
    _atomic_json(root / "details_delta.json", details)

    detail_by_asin = {str(row.get("asin") or "").upper(): row for row in details
                      if isinstance(row, Mapping)}
    outcomes = []
    for row in pending:
        asin = _asin(row)
        checkpoint = read_checkpoint(checkpoint_dir, asin) or {}
        status = checkpoint.get("status") or ("SUCCESS" if asin in detail_by_asin else "PENDING")
        outcomes.append({"snapshot_id": row.get("snapshot_id"), "ranking_asin": asin,
                         "action": row.get("detail_action"), "status": status,
                         "requested_url": row.get("preferred_request_url") or "",
                         "requested_asin": row.get("requested_asin") or asin,
                         "final_url": checkpoint.get("final_url") or "",
                         "resolved_asin": checkpoint.get("resolved_asin") or "",
                         "identity_status": checkpoint.get("identity_status") or "",
                         "access_state": checkpoint.get("access_state") or "",
                         "error": checkpoint.get("error") or ""})
    outcomes.extend(skipped)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex[:8]
    run_root = root / "detail_runs" / run_id
    manifest = {"run_id": run_id, "snapshot_id": str(plan.get("snapshot_id") or ""),
                "plan_id": plan.get("plan_id"), "plan_hash": plan.get("plan_hash"),
                "records": outcomes, "details_delta": details, "requested_count": len(pending),
                "access_stop": access_stop, "state_record_count": len(details_full),
                "parser_version": parser_version}
    _atomic_json(run_root / "manifest.json", manifest)
    _atomic_json(root / "latest_detail_run.json", {"run_id": run_id,
                                                     "path": str(run_root.relative_to(root))})
    _atomic_json(root / "detail_execution_manifest.json", manifest)
    if access_stop:
        raise AccessStopError(access_stop)
    return {"snapshot_id": manifest["snapshot_id"], "records": outcomes, "details": details,
            "details_delta": details, "skipped": skipped, "requested_count": len(pending),
            "access_stop": access_stop, "run_id": run_id, "state_record_count": len(details_full)}
