"""Bounded Quick Selection / DetailBridge command line. Preparation is offline."""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..multisource.storage import digest, immutable_json, immutable_bytes, read_sealed, sealed
from .quick_selection import audit_snapshot, file_sha, select_candidates, write_csv, SELECTION_FIELDS
from .detail_bridge5500 import DetailBridge5500, cache_index, create_plan


def prepare(args):
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(args.snapshot)
    if args.certificate:
        certificate = json.loads(Path(args.certificate).read_text(encoding="utf8"))
        if certificate["manifest_sha256"] != file_sha(directory / "manifest.json"):
            raise ValueError("CERTIFICATE_SNAPSHOT_MISMATCH")
    else:
        certificate = audit_snapshot(directory, args.campaign)
    immutable_json(root / "source_input_certificate.json", certificate)
    with (directory / "三来源统一去重候选主表.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    selected, reserve, report, review, excluded = select_candidates(rows, certificate)
    if len(selected) != 5500:
        immutable_json(root / "selection_shortfall.json", report)
        raise ValueError("QUALIFIED_SELECTION_SHORTFALL: " + str(report["shortfall"]))
    # Keep the requested identity, field and provenance contract; do not carry
    # the historical CSV's failed QA marker as an approval of this new input.
    selected = [{k: row.get(k) for k in SELECTION_FIELDS} for row in selected]
    reserve = [{k: row.get(k) for k in SELECTION_FIELDS} for row in reserve]
    immutable_json(root / "selected_5500.json", selected)
    write_csv(root / "selected_5500.csv", selected, SELECTION_FIELDS)
    write_csv(root / "reserve_1000.csv", reserve, SELECTION_FIELDS)
    immutable_json(root / "selection_review_queue.json", review)
    immutable_json(root / "selection_exclusions.json", excluded)
    plan = create_plan(selected, run_id=args.run_id,
        selection_sha256=file_sha(root / "selected_5500.json"), source_certificate_sha256=digest(certificate),
        shared_gate_path=args.shared_gate)
    plan.update(task_book_reference={"path": str(Path(args.task_book).resolve()), "sha256": file_sha(args.task_book)},
        source_snapshot_reference={"directory": str(directory.resolve()), "manifest_sha256": certificate["manifest_sha256"]})
    bridge = DetailBridge5500(root, plan)
    index = cache_index(args.cache_root)
    reused = bridge.apply_caches(index)
    report.update(run_id=args.run_id, reserve_count=len(reserve),
        selected_sha256=file_sha(root / "selected_5500.json"), plan_sha256=digest(plan),
        source_snapshot_accepted=False, narrower_input_certificate=True,
        valid_cache_reused=reused, cache_scan_roots=args.cache_root,
        cache_url_keys=len(index), detail_network_needed=5500 - reused)
    immutable_json(root / "quick_selection_report.json", report)
    immutable_json(root / "selection_report.json", report)
    now = datetime.now(timezone.utc)
    proposal = {"approved": False, "scope": "DETAIL_5500_INDEPENDENT", "run_id": args.run_id,
        "runtime_root": str(root.resolve()),
        "plan_sha256": digest(plan), "selected_sha256": file_sha(root / "selected_5500.json"),
        "max_requests": 6050, "max_page_requests": 6050, "max_other_requests": 0,
        "max_attempts_per_task": 1, "request_start_interval_seconds": 3,
        "valid_cache_reused": reused, "first_network_tasks": 5500 - reused,
        "estimated_page_requests_without_failures": 5500 - reused,
        "estimated_hours": [round((5500 - reused) * 8 / 3600, 2), round((5500 - reused) * 15 / 3600, 2)],
        "deadline_policy": "requests stop 34 hours after approval; final review deadline 36 hours after approval; both fixed before first request",
        "proposed_at": now.isoformat(), "starts_at": None, "request_deadline": None, "deadline": None,
        "approved_by": None, "approval_id": None, "code_sha": None, "ci_success_evidence": None,
        "offline_acceptance_reference": None,
        "T1_cumulative_tasks": 30, "T2_cumulative_tasks": 100, "formal_tasks": 5500,
        "retry_policy": "no automatic retries; extra cap is not an extra task authorization",
        "old_ranking_budget_not_reused": True}
    immutable_json(root / "authorization.pending.json", proposal)
    output_files = [p for p in root.iterdir() if p.is_file() and p.name != "preparation_manifest.json"]
    immutable_json(root / "preparation_manifest.json", {"run_id": args.run_id,
        "code_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "status": "OFFLINE_PREPARATION_NETWORK_AUTHORIZATION_REQUIRED",
        "files": [{"name": p.name, "sha256": file_sha(p)} for p in sorted(output_files)]})
    return report


def export(root):
    root = Path(root)
    plan = json.loads((root / "detail_plan_5500.json").read_text(encoding="utf8"))
    bridge = DetailBridge5500(root, plan)
    rows, reviews, failed = [], [], []
    for task in plan["tasks"]:
        p = root / "tasks" / task["task_id"] / "checkpoint.json"
        if not p.exists():
            continue
        outcome = read_sealed(p)
        if outcome["status"] in {"SUCCESS", "REUSED_VALID_CACHE"}:
            bridge.verify_saved_success(task, outcome)
            rows.append(outcome["record"])
        elif outcome["status"] == "IDENTITY_REVIEW":
            reviews.append({**task, **outcome})
        else:
            failed.append({**task, **outcome})
    # Live state is atomically rebuilt, original per-task evidence is immutable.
    from ..multisource.storage import _atomic_bytes
    _atomic_bytes(root / "details_5500_raw.jsonl", b"".join(
        (json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n").encode("utf8") for r in rows))
    import tempfile
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        temporary = Path(temporary)
        for name, values, fields in [
            ("details_5500.csv", rows, sorted({k for r in rows for k in r})),
            ("identity_review_queue.csv", reviews, ["task_id", "candidate_id", "request_url", "status", "reason"]),
            ("failed_details.csv", failed, ["task_id", "candidate_id", "request_url", "status", "access_state", "reason"])]:
            write_csv(temporary / name, values, fields or ["candidate_id"])
            _atomic_bytes(root / name, (temporary / name).read_bytes())
    return bridge.progress()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    p = subs.add_parser("prepare")
    for key in ("root", "snapshot", "campaign", "run-id", "shared-gate", "task-book"):
        p.add_argument("--" + key, required=True)
    p.add_argument("--certificate")
    p.add_argument("--cache-root", action="append", default=[])
    e = subs.add_parser("export")
    e.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(prepare(args) if args.action == "prepare" else export(args.root), ensure_ascii=True))


if __name__ == "__main__":
    main()
