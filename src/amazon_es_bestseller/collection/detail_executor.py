# -*- coding: utf-8 -*-
"""Plan-driven, resumable detail execution.

Offline actions never open a browser. Network results are merged into the
authoritative :class:`DetailState`; ``details.json`` is rebuilt from that
state and is never the source of truth for an incremental run.
"""
from __future__ import annotations

import csv
import hashlib
import inspect
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from ..access.detector import AccessStopError
from ..monitoring.detail_planner import _cache_classification, validate_detail_plan
from ..models import is_valid_asin, normalize_asin
from .checkpoints import read_checkpoint, write_checkpoint
from .detail import collect_details, reparse_saved_details
from .planning import DetailState
from .runtime_io import atomic_json, read_json

NETWORK_ACTIONS = frozenset({"FETCH_NEW", "REFETCH_INVALID_CACHE", "RETRY_TRANSIENT_FAILURE"})
OFFLINE_ACTIONS = frozenset({"REUSE_VALID_CACHE", "REPARSE_SAVED_HTML", "VERIFY_IDENTITY",
                             "BLOCK_ACCESS_STATE", "BLOCK_LINK_IDENTITY", "BLOCK_CODE_FIX"})


def _atomic_json(path: Path, value: object) -> None:
    atomic_json(path, value)


def _checkpoint_record(checkpoint: Mapping | None) -> dict | None:
    if not isinstance(checkpoint, Mapping) or checkpoint.get("status") != "success":
        return None
    record = checkpoint.get("record")
    return dict(record) if isinstance(record, Mapping) else None


def _asin(row: Mapping) -> str:
    return str(row.get("ranking_asin") or row.get("asin") or "").strip().upper()


def _usable_record(record, asin: str) -> bool:
    return (isinstance(record, Mapping)
            and str(record.get("asin") or "").upper() == asin
            and bool(str(record.get("title_es_raw") or "").strip())
            and _cache_classification(record) == "VALID_PRODUCT_PAGE"
            and not record.get("is_captcha")
            and not any(str(record.get(key) or "").upper() in {
                "BLOCKED", "CHALLENGE", "RATE_LIMITED", "NETWORK_ERROR", "UNKNOWN"}
                for key in ("access_state", "final_access_state"))
            and not any(str(record.get(key) or "").upper() in {
                "IDENTITY_MISMATCH", "IDENTITY_UNCONFIRMED", "IDENTITY_REVIEW"}
                for key in ("identity_status", "identity_status_code"))
            and str(record.get("resolved_asin") or asin).upper() == asin)


def _checkpoint_matches(checkpoint, row) -> bool:
    return (isinstance(checkpoint, Mapping)
            and checkpoint.get("snapshot_id") == row.get("snapshot_id")
            and checkpoint.get("action") == row.get("detail_action"))


def _call_collector(collector, asins, session, root, **kwargs):
    # Negotiate old adapter signatures before invocation. Retrying on TypeError
    # could issue the same request twice after an internal parser failure.
    parameters = inspect.signature(collector).parameters
    if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        kwargs = {key: value for key, value in kwargs.items() if key in parameters}
    return collector(asins, session, str(root), **kwargs)


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
    state.ensure_valid()
    if len(state) == 0 and (root / "details.json").exists():
        # A damaged/unreadable legacy authority file is not an empty dataset.
        # Fail before requests or writes rather than rebuilding it as [].
        legacy = read_json(root / "details.json")
        if not isinstance(legacy, list) or any(
                not isinstance(row, Mapping) or not is_valid_asin(normalize_asin(row.get("asin")))
                for row in legacy):
            raise ValueError("LEGACY_DETAIL_RECORDS_INVALID")
        state.update(legacy)

    # A verified candidate selection may carry a parser result bound to saved
    # HTML. Revalidate that file at execution time, then merge the parsed fact
    # into the normal DetailState without issuing a request.
    verified_handoffs = []
    for row in records:
        detail_record = row.get("detail_record")
        evidence_path = Path(str(row.get("detail_evidence_path") or ""))
        expected_hash = str(row.get("detail_evidence_sha256") or "").lower()
        if not isinstance(detail_record, Mapping) or not expected_hash:
            continue
        asin = _asin(row)
        try:
            actual_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        except OSError:
            actual_hash = ""
        if (actual_hash != expected_hash
                or str(detail_record.get("asin") or "").upper() != asin
                or str(detail_record.get("identity_status") or "").upper() != "MATCH"
                or str(detail_record.get("resolved_asin") or "").upper() != asin):
            raise ValueError("DETAIL_HANDOFF_EVIDENCE_CHANGED_OR_IDENTITY_INVALID:%s" % asin)
        verified_handoffs.append(dict(detail_record))
    if verified_handoffs:
        state.update(verified_handoffs)

    skipped: list[dict] = []
    pending: list[dict] = []
    reparse_rows: list[dict] = []
    verify_rows: list[dict] = []
    reuse_replays = {}
    for row in records:
        action = str(row.get("detail_action") or "")
        asin = _asin(row)
        if action == "REPARSE_SAVED_HTML":
            reparse_rows.append(row)
            continue
        if action == "VERIFY_IDENTITY":
            verify_rows.append(row)
            continue
        if action == "REUSE_VALID_CACHE":
            html_root = saved_html or row.get("saved_html_dir") or (root / "html")
            replay_key = str(html_root)
            if replay_key not in reuse_replays:
                reuse_asins = [_asin(r) for r in records if r.get("detail_action") == "REUSE_VALID_CACHE"
                    and str(saved_html or r.get("saved_html_dir") or (root / "html")) == replay_key]
                reparsed = reparse_saved_details(html_root, state, asins=reuse_asins,
                                                parser_version=parser_version)
                reuse_replays[replay_key] = {r["asin"]: r for r in reparsed}
            # Contradictory/unreadable evidence must not be hidden by an older
            # state record. Missing data stays pending without issuing a request.
            roots = html_root if isinstance(html_root, (list, tuple)) else [html_root]
            evidence_present = any((Path(p) / (asin + ".html")).exists() for p in roots)
            record = reuse_replays[replay_key].get(asin)
            if record is None and not evidence_present:
                record = state.get(asin)
            if not _usable_record(record, asin):
                record = (None if evidence_present else
                          _checkpoint_record(read_checkpoint(checkpoint_dir, asin)))
            if _usable_record(record, asin):
                state.update([record])
                status = "REUSED"
            else:
                status = "PENDING"
            skipped.append({"asin": asin, "ranking_asin": asin, "action": action,
                            "status": status})
            continue
        if action in {"BLOCK_ACCESS_STATE", "BLOCK_LINK_IDENTITY", "BLOCK_CODE_FIX"}:
            skipped.append({"asin": asin, "ranking_asin": asin, "action": action,
                            "status": "BLOCKED"})
            continue
        if action not in NETWORK_ACTIONS:
            skipped.append({"asin": asin, "ranking_asin": asin, "action": action,
                            "status": "PLAN_SKIPPED"})
            continue
        checkpoint = read_checkpoint(checkpoint_dir, asin)
        if (_checkpoint_matches(checkpoint, row) and checkpoint.get("status") == "success"):
            recovered = _checkpoint_record(checkpoint)
            if _usable_record(recovered, asin):
                state.update([recovered])
                skipped.append({"asin": asin, "ranking_asin": asin, "action": action,
                                "status": "ALREADY_SUCCESS"})
                continue
        pending.append(row)

    if reparse_rows:
        # Plans may bind different ASINs to different saved-evidence roots.
        # Group by root so every row is replayed from its own evidence once.
        grouped_reparse = {}
        for row in reparse_rows:
            html_root = saved_html or row.get("saved_html_dir") or (root / "html")
            grouped_reparse.setdefault(str(html_root), (html_root, []))[1].append(row)
        reparsed_by_asin = {}
        for html_root, group in grouped_reparse.values():
            reparsed = reparse_saved_details(
                html_root, state, asins=[_asin(row) for row in group],
                parser_version=parser_version)
            reparsed_by_asin.update({str(row.get("asin") or "").upper(): row for row in reparsed})
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
            details = _call_collector(collector, [_asin(row) for row in pending], session, root,
                                request_urls=request_urls, execution_context=context,
                                write_summary=False, parser_version=parser_version,
                                checkpoint_root=checkpoint_dir)
        except AccessStopError as exc:
            access_stop = str(exc)

    for row in pending:
        checkpoint = read_checkpoint(checkpoint_dir, _asin(row))
        recovered = _checkpoint_record(checkpoint) if _checkpoint_matches(checkpoint, row) else None
        if _usable_record(recovered, _asin(row)):
            state.update([recovered])
    if details:
        expected = {_asin(row) for row in pending}
        if any(str(record.get("asin") or "").upper() not in expected
               or not _usable_record(record, str(record.get("asin") or "").upper())
               for record in details):
            raise ValueError("DETAIL_COLLECTOR_RETURNED_INVALID_OR_UNPLANNED_RECORD")
        state.update(details)
    state.save()
    details_full = state.records()
    _atomic_json(root / "details.json", details_full)
    _atomic_json(root / "details_delta.json", details)

    detail_by_asin = {str(row.get("asin") or "").upper(): row for row in details
                      if isinstance(row, Mapping)}
    outcomes = []
    candidate_provenance = {
        _asin(row): {
            "candidate_ids": list(row.get("candidate_ids") or []),
            "candidate_observation_ids": list(row.get("candidate_observation_ids") or []),
            "saved_html_path": row.get("detail_evidence_path") or "",
            "html_sha256": row.get("detail_evidence_sha256") or "",
            "candidate_evidence_path": row.get("candidate_evidence_path") or "",
            "candidate_evidence_file_path": row.get("candidate_evidence_file_path") or "",
            "candidate_evidence_sha256": row.get("candidate_evidence_sha256") or "",
        }
        for row in records
    }
    for row in pending:
        asin = _asin(row)
        checkpoint = read_checkpoint(checkpoint_dir, asin) or {}
        status = checkpoint.get("status") if _checkpoint_matches(checkpoint, row) else None
        if (str(status or "").lower() == "success"
                and not _usable_record(_checkpoint_record(checkpoint), asin)):
            status = None
        status = status or ("SUCCESS" if asin in detail_by_asin else "PENDING")
        outcomes.append({"snapshot_id": row.get("snapshot_id"), "ranking_asin": asin,
                         **candidate_provenance.get(asin, {}),
                         "action": row.get("detail_action"), "status": status,
                         "requested_url": row.get("preferred_request_url") or "",
                         "requested_asin": row.get("requested_asin") or asin,
                         "final_url": checkpoint.get("final_url") or "",
                         "resolved_asin": checkpoint.get("resolved_asin") or "",
                         "identity_status": checkpoint.get("identity_status") or "",
                         "access_state": checkpoint.get("access_state") or "",
                         "error": checkpoint.get("error") or ""})
    for outcome in skipped:
        enriched = dict(outcome)
        enriched.update(candidate_provenance.get(_asin(outcome), {}))
        outcomes.append(enriched)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex[:8]
    run_root = root / "detail_runs" / run_id
    completed_count = sum(str(row.get("status") or "").upper() in {
        "SUCCESS", "REUSED", "ALREADY_SUCCESS"} for row in outcomes)
    execution_status = ("ACCESS_STOP" if access_stop else
                        "COMPLETE" if completed_count == len(outcomes) else "INCOMPLETE")
    manifest = {"run_id": run_id, "snapshot_id": str(plan.get("snapshot_id") or ""),
                "plan_id": plan.get("plan_id"), "plan_hash": plan.get("plan_hash"),
                "records": outcomes, "details_delta": details, "requested_count": len(pending),
                "access_stop": access_stop, "state_record_count": len(details_full),
                "parser_version": parser_version, "execution_status": execution_status,
                "completed_count": completed_count,
                "incomplete_count": len(outcomes) - completed_count}
    _atomic_json(run_root / "manifest.json", manifest)
    _atomic_json(root / "latest_detail_run.json", {"run_id": run_id,
                                                     "path": str(run_root.relative_to(root))})
    _atomic_json(root / "detail_execution_manifest.json", manifest)
    if access_stop:
        raise AccessStopError(access_stop)
    return {"snapshot_id": manifest["snapshot_id"], "records": outcomes, "details": details,
            "details_delta": details, "skipped": skipped, "requested_count": len(pending),
            "access_stop": access_stop, "run_id": run_id, "state_record_count": len(details_full),
            "execution_status": execution_status, "completed_count": completed_count,
            "incomplete_count": len(outcomes) - completed_count}
