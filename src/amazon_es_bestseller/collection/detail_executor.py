# -*- coding: utf-8 -*-
"""Plan-driven thin detail executor.

The planner owns the decision.  This module filters only the explicitly
networkable actions, then delegates serial collection to the existing detail
collector and checkpoint/access gate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Mapping

from ..access.detector import AccessStopError
from .checkpoints import read_checkpoint
from .detail import collect_details

NETWORK_ACTIONS = frozenset({
    "FETCH_NEW", "REFETCH_INVALID_CACHE", "RETRY_TRANSIENT_FAILURE",
})


def execute_detail_plan(plan: Mapping, session, out_dir: str,
                        *, collector: Callable = collect_details,
                        checkpoint_root=None) -> dict:
    """Execute only network actions from a detail plan, serially and resumably."""
    records = [dict(row) for row in (plan.get("records") or [])
               if isinstance(row, Mapping)]
    root = Path(out_dir)
    checkpoint_dir = Path(checkpoint_root or (root / "checkpoints"))
    skipped = []
    pending = []
    for row in records:
        action = str(row.get("detail_action") or "")
        asin = str(row.get("ranking_asin") or row.get("asin") or "").strip().upper()
        if action not in NETWORK_ACTIONS:
            skipped.append({"asin": asin, "ranking_asin": asin,
                            "action": action, "status": "PLAN_SKIPPED"})
            continue
        checkpoint = read_checkpoint(checkpoint_dir, asin)
        if (checkpoint and checkpoint.get("status") == "success"
                and checkpoint.get("snapshot_id") == row.get("snapshot_id")
                and checkpoint.get("action") == action):
            skipped.append({"asin": asin, "ranking_asin": asin,
                            "action": action, "status": "ALREADY_SUCCESS"})
            continue
        pending.append(row)

    request_urls = {
        str(row.get("ranking_asin") or row.get("asin") or "").upper():
        str(row.get("preferred_request_url") or "")
        for row in pending
    }
    context = {
        str(row.get("ranking_asin") or row.get("asin") or "").upper(): {
            "snapshot_id": row.get("snapshot_id"),
            "ranking_asin": row.get("ranking_asin") or row.get("asin"),
            "ranking_product_url_raw": row.get("ranking_product_url_raw") or "",
            "preferred_request_url": row.get("preferred_request_url") or "",
            "action": row.get("detail_action"),
            "attempt": int((read_checkpoint(checkpoint_dir,
                                             str(row.get("ranking_asin") or row.get("asin") or ""))
                             or {}).get("attempt", 0) or 0) + 1,
        }
        for row in pending
    }
    details = []
    access_stop = None
    if pending:
        try:
            details = collector(
                [str(row.get("ranking_asin") or row.get("asin") or "").upper()
                 for row in pending],
                session,
                str(root),
                request_urls=request_urls,
                execution_context=context,
            )
        except TypeError as exc:
            # Only support legacy injectable test doubles that have the old
            # signature; production collector always accepts the evidence args.
            if "unexpected keyword argument" not in str(exc):
                raise
            details = collector(
                [str(row.get("ranking_asin") or row.get("asin") or "").upper()
                 for row in pending], session, str(root))
        except AccessStopError as exc:
            access_stop = str(exc)

    outcomes = []
    detail_by_asin = {
        str(row.get("asin") or "").upper(): row for row in details
        if isinstance(row, Mapping)
    }
    for row in pending:
        asin = str(row.get("ranking_asin") or row.get("asin") or "").upper()
        checkpoint = read_checkpoint(checkpoint_dir, asin) or {}
        status = checkpoint.get("status")
        if not status:
            status = "SUCCESS" if asin in detail_by_asin else "PENDING"
        outcomes.append({
            "snapshot_id": row.get("snapshot_id"),
            "ranking_asin": asin,
            "action": row.get("detail_action"),
            "status": status,
            "requested_url": row.get("preferred_request_url") or "",
            "requested_asin": asin,
            "final_url": checkpoint.get("final_url") or "",
            "resolved_asin": checkpoint.get("resolved_asin") or "",
            "identity_status": checkpoint.get("identity_status") or "",
            "access_state": checkpoint.get("access_state") or "",
            "error": checkpoint.get("error") or "",
        })
    outcomes.extend(skipped)
    plan_snapshot_id = plan.get("snapshot_id") or next(
        (row.get("snapshot_id") for row in records if row.get("snapshot_id")), "")
    result = {"snapshot_id": plan_snapshot_id,
              "records": outcomes, "details": details, "skipped": skipped,
              "requested_count": len(pending), "access_stop": access_stop}
    root.mkdir(parents=True, exist_ok=True)
    (root / "detail_execution_manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if access_stop:
        raise AccessStopError(access_stop)
    return result
