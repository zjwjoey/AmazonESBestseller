"""Final reconciliation of task scope, persisted audits and original evidence."""
from __future__ import annotations

import json
from collections import Counter
from contextlib import closing
from pathlib import Path

from .audit_ledger import AuditLedger, canonical_json
from .collection.checkpoints import read_checkpoint
from .collection.detail_batch_audit import checkpoint_payload
from .collection.field_completeness import FieldCompletenessMonitor
from .collection.runtime_io import read_json
from .evidence_store import CacheCapacityExceeded, sha256
from .monitoring.detail_planner import validate_detail_plan
from .multisource.storage import digest, read_sealed
from .task_workspace import TaskWorkspace, atomic_json


def _snapshot_receipt_errors(store, plan, pages, requests):
    run_id = plan["run_id"]
    directory = store.root / "runs" / run_id
    plan_digest = digest(plan)
    reservations = {r["request_id"]: r for r in requests if r["status"] != "RESERVED"}
    indexed = {r["request_id"]: r for r in store.iter_responses(run_id)}
    errors = []
    if indexed.keys() != reservations.keys():
        errors.append("SNAPSHOT_RECEIPT_SCOPE_MISMATCH")
    for request_id, indexed_item in indexed.items():
        try:
            root = directory / "responses" / (request_id + ".json")
            receipt = read_sealed(root)
            reservation = read_sealed(directory / "requests" / (request_id + ".reserved.json"))
            expected = {key: value for key, value in indexed_item.items() if key != "evidence_path"}
            actual = {key: value for key, value in receipt.items() if key != "evidence_path"}
            if (expected != actual or reservation["request_id"] != request_id
                    or reservation["run_id"] != run_id or reservation["plan_sha256"] != plan_digest
                    or receipt["run_id"] != run_id or receipt["request_id"] != request_id
                    or reservation["kind"] != receipt["kind"]
                    or reservations[request_id]["status"] != receipt["access_state"]
                    or reservation["source_id"] != receipt["source_id"]
                    or reservation["url"] != receipt["request_url"]):
                raise ValueError("RECEIPT_BINDING_MISMATCH")
        except (ValueError, KeyError, TypeError):
            errors.append("SNAPSHOT_RECEIPT_INVALID:" + request_id)
    for page in pages:
        reference = page.get("capture_receipt_reference") or {}
        path = Path(reference.get("path") or "")
        try:
            if (path.resolve().parent != (directory / "responses").resolve()
                    or path.stem not in indexed
                    or sha256(path.read_bytes()) != reference.get("sha256")):
                raise ValueError("PAGE_RECEIPT_REFERENCE_MISMATCH")
        except (OSError, ValueError):
            errors.append("SNAPSHOT_PAGE_RECEIPT_INVALID:" + page["page_key"])
    return errors


def _audit_managed_snapshot(workspace, snapshot_id, errors):
    from .highspeed.incremental_audit import SnapshotPageAudit
    from .highspeed.store import ReadOnlyRunStore
    with closing(ReadOnlyRunStore(workspace.root / "store")) as store:
        run = store.get_run(snapshot_id)
        plan = run["plan"]
        pages = store.pages(snapshot_id)
        references = {(src["source_id"], url) for src in plan["sources"] for url in src["page_urls"]}
        completed = {(r["source_id"], r["page_url"]) for r in pages}
        requests = store.requests(snapshot_id)
        unfinished = [r["request_id"] for r in requests if r["status"] == "RESERVED"]
        audit = SnapshotPageAudit(workspace).coverage(plan, pages)
        receipt_errors = _snapshot_receipt_errors(store, plan, pages, requests)
        snapshot = {
            "mode": "WORKSPACE", "run_id": snapshot_id, "run_status": run["status"],
            "stop_reason": run.get("stop_reason"), "planned_base_pages": len(references),
            "completed_base_pages": len(references & completed), "additional_pages": len(completed - references),
            "missing_base_pages": [list(key) for key in sorted(references - completed)],
            "unfinished_requests": unfinished, "request_statuses": dict(Counter(r["status"] for r in requests)),
            "audit": audit, "receipt_errors": receipt_errors,
        }
        if receipt_errors:
            errors.append("SNAPSHOT_RECEIPT_VERIFICATION_FAILED")
        if run["status"] != "STOPPED" or references - completed or unfinished:
            errors.append("SNAPSHOT_SCOPE_INCOMPLETE")
        if not audit["central_full_dom_review_passed"]:
            errors.append("SNAPSHOT_AUDIT_INCOMPLETE")
    return snapshot


def finalize_task(root):
    workspace = TaskWorkspace(root)
    manifest = workspace.manifest
    ledger = AuditLedger(workspace)
    errors = []
    snapshot = {"mode": "NOT_CONFIGURED"}
    snapshot_id = manifest.get("snapshot_run_id")
    if snapshot_id and not (workspace.root / "store").is_dir():
        snapshot = {"mode": "WORKSPACE", "run_id": snapshot_id, "run_status": "NOT_STARTED"}
        errors.append("SNAPSHOT_SCOPE_INCOMPLETE")
    elif snapshot_id:
        try:
            snapshot = _audit_managed_snapshot(workspace, snapshot_id, errors)
        except CacheCapacityExceeded:
            snapshot = {"mode": "WORKSPACE", "run_id": snapshot_id, "run_status": "AUDIT_CAPACITY_BLOCKED"}
            errors.extend(("SNAPSHOT_SCOPE_INCOMPLETE", "SNAPSHOT_AUDIT_CACHE_CAPACITY"))
    elif manifest.get("external_snapshot"):
        snapshot = {"mode": "EXTERNAL", "reference": manifest["external_snapshot"]}
        import hashlib
        try:
            with Path(snapshot["reference"]["path"]).open("rb") as handle:
                current_hash = hashlib.file_digest(handle, "sha256").hexdigest()
            if current_hash != snapshot["reference"]["sha256"]:
                errors.append("EXTERNAL_SNAPSHOT_CHANGED")
        except OSError:
            errors.append("EXTERNAL_SNAPSHOT_MISSING")

    detail_root = workspace.root / "detail"
    plan_path = Path(manifest.get("detail_plan_path") or (detail_root / "plan" / "detail_plan.json"))
    plan = validate_detail_plan(read_json(plan_path)) if plan_path.is_file() else None
    execution = read_json(detail_root / "detail_execution_manifest.json", {}) or {}
    expected = {r.get("ranking_asin") or r.get("asin") for r in plan["records"]} if plan else set()
    current = ledger.current("detail")
    terminal = {r["unit_id"]: r for r in current if r["status"] != "ACCESS_STOP"
                and r["parser_version"] == manifest["parser_version"]
                and r["qa_version"] == ledger.qa_version("detail")}
    passed = {asin for asin, row in terminal.items() if row["status"] in {"PASS", "WARN"}}
    output = read_json(detail_root / "details.json", []) or []
    written = [r.get("asin") for r in output if r.get("title_es_raw") and r.get("identity_status") == "MATCH"]
    coverage = {asin for asin, row in terminal.items() if row["batch_id"]}
    if not plan or not expected or expected != terminal.keys():
        errors.append("DETAIL_SCOPE_INCOMPLETE")
    if plan and (plan.get("task_id") != manifest["task_id"]
                 or plan.get("plan_id") != manifest.get("detail_plan_id")
                 or plan.get("plan_hash") != execution.get("plan_hash")):
        errors.append("DETAIL_PLAN_BINDING_MISMATCH")
    outcomes = execution.get("records") or []
    successful_statuses = {"ALREADY_SUCCESS", "SUCCESS", "REUSED"}
    closed_statuses = successful_statuses | {"FAILED", "INVALID", "ASIN_MISMATCH",
                                             "QUARANTINED", "TERMINAL_FAILURE"}
    closed_outcomes = [row for row in outcomes if str(row.get("status")).upper() in closed_statuses]
    reported_successes = {row.get("ranking_asin") for row in closed_outcomes
                          if str(row.get("status")).upper() in successful_statuses}
    if plan and (len(outcomes) != len(expected) or len(closed_outcomes) != len(expected)
                 or {row.get("ranking_asin") for row in closed_outcomes} != expected
                 or reported_successes != passed):
        errors.append("DETAIL_EXECUTION_SCOPE_MISMATCH")
    coverage_matches = expected == coverage
    if coverage:
        expected_batch_ids = {row["batch_id"] for row in terminal.values() if row["batch_id"]}
        with workspace.transaction() as conn:
            batches = {row["batch_id"]: (row["status"], set(json.loads(row["ids"])))
                       for row in conn.execute("SELECT * FROM detail_batches")}
        coverage_matches = coverage_matches and expected_batch_ids <= batches.keys()
        coverage_matches = coverage_matches and all(
            batches[row["batch_id"]][0] == "COMMITTED"
            and row["audit_id"] in batches[row["batch_id"]][1] for row in terminal.values()
        )
    if not coverage_matches:
        errors.append("DETAIL_BATCH_COVERAGE_INCOMPLETE")
    if set(written) != passed or len(written) != len(output) or len(set(written)) != len(written):
        errors.append("DETAIL_OUTPUT_AUDIT_MISMATCH")
    invalid_checkpoints = []
    edited_output = []
    output_by_asin = {item["asin"]: item for item in output if item.get("asin")}
    checkpoint_root = Path(execution.get("checkpoint_root") or (detail_root / "checkpoints"))
    for asin, row in terminal.items():
        checkpoint = read_checkpoint(checkpoint_root, asin)
        if not checkpoint or sha256(canonical_json(checkpoint_payload(checkpoint)).encode("utf-8")) != row["record_digest"]:
            invalid_checkpoints.append(asin)
            continue
        if asin in passed and asin in output_by_asin:
            original = checkpoint["record"]
            written_record = dict(output_by_asin[asin])
            for derived in ("collected_at", "detail_schema_version"):
                if derived not in original:
                    written_record.pop(derived, None)
            if written_record != original:
                edited_output.append(asin)
    if invalid_checkpoints:
        errors.append("DETAIL_CHECKPOINT_AUDIT_MISMATCH")
    if edited_output and "DETAIL_OUTPUT_AUDIT_MISMATCH" not in errors:
        errors.append("DETAIL_OUTPUT_AUDIT_MISMATCH")
    completeness = FieldCompletenessMonitor(manifest["field_quality_thresholds"]).summarize(terminal.values())
    if terminal and read_json(workspace.root / "audit" / "field_completeness.json") != completeness:
        errors.append("FIELD_COMPLETENESS_MISMATCH")

    archive = ledger.evidence.archive_stats(verify=True)
    cache = ledger.evidence.capacity()
    if archive["errors"]:
        errors.append("ARCHIVE_VERIFICATION_FAILED")
    if cache["active_bytes"]:
        errors.append("ACTIVE_EVIDENCE_REMAINS")
    if cache["peak_bytes"] > manifest["cache_limit_bytes"]:
        errors.append("ACTIVE_CACHE_LIMIT_EXCEEDED")
    with workspace.transaction() as conn:
        uncovered = conn.execute("""SELECT count(*) FROM evidence WHERE archive_path IS NULL
                                    OR NOT EXISTS (SELECT 1 FROM evidence_audits WHERE evidence.digest=evidence_audits.digest)""").fetchone()[0]
    if uncovered:
        errors.append("UNINDEXED_OR_UNARCHIVED_EVIDENCE")
    if any(r["status"] == "ACCESS_STOP" for r in current) or execution.get("access_stop"):
        errors.append("DETAIL_ACCESS_STOP")
    if manifest["task_status"] == "QUALITY_PAUSED":
        errors.append("DETAIL_QUALITY_PAUSED")

    collection_complete = bool(expected and expected == terminal.keys()
                               and not set(errors) & {"SNAPSHOT_SCOPE_INCOMPLETE", "DETAIL_ACCESS_STOP",
                                                       "DETAIL_EXECUTION_SCOPE_MISMATCH"})
    reviewed = collection_complete and not errors
    unresolved = sorted(asin for asin, row in terminal.items() if row["anomalies"] or asin not in passed)
    research_ready = reviewed and not unresolved and snapshot["mode"] != "NOT_CONFIGURED"
    state = ("RESEARCH_READY" if research_ready else "QUALITY_REVIEW_COMPLETE" if reviewed
             else "COLLECTION_COMPLETE" if collection_complete else "INCOMPLETE")
    summary = {"task_id": manifest["task_id"], "state": state, "collection_complete": collection_complete,
               "quality_review_complete": reviewed, "research_ready": research_ready, "errors": errors,
               "snapshot": snapshot, "detail": {
                   "planned": len(expected), "terminal": len(terminal), "passed": len(passed),
                   "batch_covered": len(coverage), "statuses": dict(Counter(r["status"] for r in current)),
                   "field_completeness": completeness, "unresolved_asins": unresolved,
                   "mismatched_checkpoint_asins": invalid_checkpoints,
                   "mismatched_output_asins": edited_output},
               "archive": archive, "cache": cache}
    atomic_json(workspace.root / "reports" / "task_quality_summary.json", summary)
    return summary
