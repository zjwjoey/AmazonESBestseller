"""Ranking-to-detail reconciliation for reviewed task runs.

This module is deliberately offline.  It compares the frozen candidate set
with a prior ``details.json``/detail-state JSON and delegates the field-level
classification to the existing Detail Planner.  No browser or network code is
imported here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from ..monitoring.detail_planner import build_detail_plan, write_detail_plan

NETWORK_ACTIONS = frozenset({
    "FETCH_NEW", "REFETCH_INVALID_CACHE", "RETRY_TRANSIENT_FAILURE",
})


def load_detail_records(path: str | Path | None) -> list[dict]:
    """Load a prior detail table/state JSON without mutating it."""
    if not path:
        return []
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(value, Mapping):
        if isinstance(value.get("records"), list):
            value = value["records"]
        elif isinstance(value.get("details"), list):
            value = value["details"]
        else:
            value = [dict(row, asin=key) for key, row in value.items()
                     if isinstance(row, Mapping)]
    if not isinstance(value, list):
        raise ValueError("previous details 必须是 JSON 数组、records/details 对象或 ASIN 映射")
    return [dict(row) for row in value if isinstance(row, Mapping)]


def build_reconciliation(candidate_rows: list[Mapping], previous_details: list[Mapping],
                         *, snapshot_id: str, saved_html: str | Path | None = None,
                         current_access_state: str = "UNKNOWN") -> dict:
    """Build the immutable detail plan for the frozen candidate set."""
    snapshot = {
        "snapshot_status": "AUTHORITATIVE",
        "snapshot_id": snapshot_id,
        "rankings": [dict(row) for row in candidate_rows],
    }
    plan = build_detail_plan(
        snapshot, detail_cache=previous_details, saved_html=saved_html,
        current_access_state=current_access_state,
    )
    previous_by_asin = {
        str(row.get("asin") or row.get("ranking_asin") or "").strip().upper(): dict(row)
        for row in previous_details if isinstance(row, Mapping)
    }
    for row in plan.get("records") or []:
        if str(row.get("detail_action") or "") == "REUSE_VALID_CACHE":
            cached = previous_by_asin.get(
                str(row.get("ranking_asin") or row.get("asin") or "").strip().upper())
            if cached is not None:
                row["existing_detail_record"] = cached
    return plan


def write_reconciliation(plan: Mapping, output_dir: str | Path, *,
                         previous_details_path: str | Path | None = None) -> dict[str, Path]:
    """Write operator-facing reconciliation, queue, and stable detail plan."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    records = [dict(row) for row in (plan.get("records") or [])
               if isinstance(row, Mapping)]
    actions: dict[str, int] = {}
    for row in records:
        action = str(row.get("detail_action") or "")
        actions[action] = actions.get(action, 0) + 1
    network = [row for row in records
               if str(row.get("detail_action") or "") in NETWORK_ACTIONS]
    non_reuse = [row for row in records
                 if str(row.get("detail_action") or "") != "REUSE_VALID_CACHE"]
    summary = {
        "snapshot_id": str(plan.get("snapshot_id") or ""),
        "candidate_count": len(records),
        "previous_details_path": str(previous_details_path or ""),
        "actions": actions,
        "reuse_count": actions.get("REUSE_VALID_CACHE", 0),
        "reextract_count": len(network),
        "manual_review_count": len(non_reuse) - len(network),
        "network_actions": sorted(NETWORK_ACTIONS),
    }
    payload = {**summary, "records": records}
    (root / "detail_reconciliation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "detail_reextract_queue.json").write_text(
        json.dumps({**summary, "records": network}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (root / "detail_backfill_queue.json").write_text(
        json.dumps({**summary, "records": non_reuse}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    plan_payload = dict(plan)
    plan_payload["summary"] = summary
    paths = write_detail_plan(plan_payload, root / "detail_plan")
    paths.update({
        "reconciliation": root / "detail_reconciliation.json",
        "reextract_queue": root / "detail_reextract_queue.json",
        "backfill_queue": root / "detail_backfill_queue.json",
    })
    return paths


__all__ = ["NETWORK_ACTIONS", "build_reconciliation", "load_detail_records",
           "write_reconciliation"]
