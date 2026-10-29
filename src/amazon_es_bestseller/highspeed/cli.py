"""Explicit V2 CLI. Help, planning, replay and reporting never open a browser."""
from __future__ import annotations
import json
import hashlib
from pathlib import Path
import time
import uuid
from .budgets import utc_text, validate_plan, budget_reason
from .authorization import validate_authorization, verified_stage_artifact
from .contracts import DEFAULT_CONFIG, STAGES, SOURCE_ROOTS
from ..multisource.storage import immutable_json, immutable_bytes, canonical_bytes, read_sealed
from ..multisource.storage import file_lock
from contextlib import ExitStack

def load_json(path):
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return read_sealed(path) if "payload" in value else value

def reference(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

def write_rows(path, rows):
    immutable_bytes(path, b"".join(canonical_bytes(row)+b"\n" for row in rows))
    return reference(path)

def save_native_contract(store, plan, value):
    """Attach an immutable proof reference to the transport's captured object."""
    directory = store.root / "native_acp_contracts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (uuid.uuid4().hex+".json")
    payload = value.get("payload", "")
    observed = {**value, "run_id": plan["run_id"], "captured_at": utc_text(),
        "request_payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "request_contract_parent_page_url": value.get("parent_page_url"),
        "note": "Actual native browser contract held or aborted before network; not a successful response."}
    immutable_json(path, observed)
    value.update(request_contract_parent_page_url=value.get("parent_page_url"),
        request_payload_sha256=observed["request_payload_sha256"], run_id=plan["run_id"],
        native_contract_evidence_path=str(path.resolve()),
        native_contract_evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return reference(path)

def export_gate(store, run_id, output, *, authorization_path=None):
    from .quality import audit_run
    from .reports import eligible_candidate_ids
    run = store.get_run(run_id)
    stage = run["stage"]
    quality = audit_run(store, run_id)
    candidates = list(store.iter_candidates(run_id))
    directory = Path(output) / ("gate_"+uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    manifest_path = directory / "manifest.json"
    immutable_json(manifest_path, {"run_id": run_id, "stage": stage, "origin": run.get("origin"),
        "plan": run["plan"], "pages": store.pages(run_id), "run": run, "store_root": str(store.root)})
    quality_path = directory / "quality.json"
    immutable_json(quality_path, quality)
    refs = {"manifest": reference(manifest_path), "quality_report": reference(quality_path),
        "observations": write_rows(directory / "observations.jsonl", store.iter_observations(run_id)),
        "candidates": write_rows(directory / "candidates.jsonl", candidates)}
    if authorization_path:
        auth_path = directory / "authorization.json"
        immutable_json(auth_path, load_json(authorization_path))
        refs["authorization"] = reference(auth_path)
    receipts = [reference(item["evidence_path"]) for item in store.iter_responses(run_id)
        if item.get("origin") == "LIVE_CAPTURE" and item.get("access_state") == "NORMAL"]
    passed = quality["gate_passed"]
    record = {"schema_version": 2, "stage": stage, "origin": run.get("origin"), "run_id": run_id,
        "passed": passed, "quality_gate_passed": passed, "unique_valid_links": len(eligible_candidate_ids(candidates)),
        **refs, "artifacts": list(refs.values()), "request_receipts": receipts}
    if passed:
        # Run the same independent evidence check the next stage will execute.
        provisional = directory / "gate.precheck.json"
        immutable_json(provisional, record)
        try:
            verified_stage_artifact(reference(provisional), stage)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            record.update(passed=False, quality_gate_passed=False, gate_validation_error=str(exc))
    gate_path = directory / "stage_gate.json"
    immutable_json(gate_path, record)
    return {"gate": reference(gate_path), "passed": record["passed"], "quality": quality,
        "directory": str(directory), "gate_validation_error": record.get("gate_validation_error")}

def run_live(args, store, plan, approval, *, entry_time):
    """Compatibility rejection only; the old browser execution body is deleted."""
    if plan.get('snapshot_contract_version') == 4:
        raise ValueError('V4_REQUIRES_EXPLICIT_V4_CLI_AND_BOUNDED_PROTOCOL_DIAGNOSTICS')
    raise ValueError('LEGACY_HIGHSPEED_LIVE_RETIRED: use the reviewed PAGE snapshot entry')


def dispatch(args):
    entry_time = utc_text()
    operation = args.operation
    if operation == "baseline-v3":
        from .v3_baseline import export_baseline
        return export_baseline(args.csv, args.out, candidate_jsonl=args.candidate_jsonl)
    if operation in {"replay-v3", "report-v3", "prepare-v3", "history-v3"}:
        from .v3_workflows import replay_v3, report_replay_v3, prepare_v3, archive_history_v3
        if operation == "history-v3":
            return archive_history_v3(args.historical_root,args.historical_run_id,args.root,args.run_id,args.out)
        if operation == "replay-v3":
            return replay_v3(args.historical_root, args.historical_run_id, args.out,
                mapping_config=args.mapping_config,
                progress=lambda value: print(json.dumps({"kind":"V3_OFFLINE_REPLAY_PROGRESS", **value})))
        if operation == "report-v3":
            return report_replay_v3(args.root, args.run_id, args.out,mapping_config=args.mapping_config,history_manifest=args.history_manifest)
        return prepare_v3(args.historical_root, args.historical_run_id, args.out, mapping_config=args.mapping_config,pending_source_pack=args.pending_source_pack,runtime_root=args.runtime_root)
    if operation == "plan":
        pending = {**DEFAULT_CONFIG, "mode": "HIGH_SPEED_V2", "run_id": args.run_id,
            "snapshot_id": args.run_id, "stage": args.stage, "origin": "OFFLINE_FIXTURE" if args.stage == "T0" else "LIVE_CAPTURE",
            "sources": [], "sources_reviewed": False, "review_status": "PENDING_SOURCE_CAPACITY_AND_HUMAN_APPROVAL"}
        if args.out:
            immutable_json(args.out, pending)
        return pending
    if operation == "discover":
        from .sources import discover_sources, source_coverage
        if not args.html or not args.page_url or not args.source_type or not args.observed_at:
            raise ValueError("DISCOVER_REQUIRES_SAVED_HTML_URL_TYPE_ORIGINAL_TIME")
        html = Path(args.html).read_text(encoding="utf-8")
        rows = discover_sources(html, args.source_type, args.page_url, evidence_path=str(Path(args.html).resolve()),
            observed_at=args.observed_at, origin=args.origin)
        result = {"sources": rows, "coverage": source_coverage(rows), "live_requests": 0}
        if args.out:
            immutable_json(args.out, result)
        return result
    if operation == "benchmark":
        from .benchmark import run_benchmark
        specs = load_json(args.pages)
        return run_benchmark(specs.get("pages", []) if isinstance(specs, dict) else specs, args.out,
            synthetic_unique=args.synthetic_unique, synthetic_observations=args.synthetic_observations)
    from .store import RunStore, ReadOnlyRunStore
    from .path_checks import validate_store_root, validate_run_storage_root
    validate_store_root(args.root)
    preloaded_plan = load_json(args.plan) if operation in {"run", "resume"} else None
    if preloaded_plan or args.run_id:
        validate_run_storage_root(args.root, preloaded_plan["run_id"] if preloaded_plan else args.run_id)
    if preloaded_plan:
        if preloaded_plan.get('stage') == 'T0' and args.live:
            raise ValueError('T0_MUST_REMAIN_OFFLINE')
        if preloaded_plan.get('stage') != 'T0':
            raise ValueError('LEGACY_HIGHSPEED_LIVE_RETIRED: use the reviewed PAGE snapshot entry')
    read_operations = {'status', 'qa', 'report', 'gate-export', 'snapshot-review-status'}
    if operation == 'snapshot-review-correct' and not getattr(args, 'apply', False):
        raise ValueError('EXPLICIT_METADATA_APPLY_REQUIRED')
    if operation in {'report', 'gate-export', 'snapshot-review-status', 'snapshot-review-correct'}:
        source = Path(args.root).resolve()
        output = Path(args.out).resolve()
        if output == source or output.is_relative_to(source) or source.is_relative_to(output):
            raise ValueError('OUTPUT_MUST_BE_OUTSIDE_SOURCE')
    store = (ReadOnlyRunStore if operation in read_operations else RunStore)(args.root)
    try:
        if operation in {"run", "resume"}:
            from .runner import HighSpeedRunner
            plan = preloaded_plan
            if args.run_id and args.run_id != plan["run_id"]:
                raise ValueError("RUN_ID_PLAN_MISMATCH")
            if plan["stage"] == "T0":
                if args.live:
                    raise ValueError("T0_MUST_REMAIN_OFFLINE")
                if operation == "run":
                    store.begin_run(plan, started_at=entry_time)
                result = HighSpeedRunner(store, progress=lambda event: print(json.dumps(event, ensure_ascii=False))).run(plan, resume=True)
            else:
                result = run_live(args, store, plan, load_json(args.authorization) if args.authorization else None,
                    entry_time=entry_time)
            return {"run": result["run"], "summary": store.summary(plan["run_id"])}
        if operation == "status":
            return {"run": store.get_run(args.run_id), "summary": store.summary(args.run_id), "access_gate": store.get_gate()}
        if operation == "snapshot-review-status":
            from .snapshot_review import preview_snapshot_review
            report = preview_snapshot_review(store, args.run_id)
            immutable_json(Path(args.out), report)
            return report
        if operation == "snapshot-review-correct":
            from .snapshot_review import refresh_snapshot_review
            return refresh_snapshot_review(store, args.run_id, args.out)
        if operation == "qa":
            from .quality import audit_run
            return audit_run(store, args.run_id)
        if operation == "report":
            from .reports import export_report
            return export_report(store, args.run_id, args.out)
        if operation == "gate-export":
            return export_gate(store, args.run_id, args.out, authorization_path=args.authorization)
        if operation == "renormalize":
            from .reprocess import renormalize_run
            return renormalize_run(store, args.run_id, args.out)
        if operation == "review-identity":
            return store.apply_review(args.run_id, load_json(args.review))
        if operation == "gate-recover":
            return store.recover_gate(load_json(args.authorization))
        raise ValueError("UNKNOWN_HIGH_SPEED_OPERATION")
    finally:
        store.close()

def command(args):
    try:
        result = dispatch(args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise SystemExit("HIGH_SPEED_ERROR: "+str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))

def register(subparsers):
    parser = subparsers.add_parser("highspeed", help="历史快照离线回放与审查；旧LIVE入口已退役")
    operations = parser.add_subparsers(dest="operation", required=True)
    op = operations.add_parser("baseline-v3", help="Historical CSV baseline; no browser")
    op.add_argument("--csv", required=True); op.add_argument("--candidate-jsonl"); op.add_argument("--out", required=True)
    op.set_defaults(func=command)
    for name in ("replay-v3", "prepare-v3"):
        op = operations.add_parser(name, help="Read-only old evidence; no LIVE requests")
        op.add_argument("--historical-root", required=True); op.add_argument("--historical-run-id", required=True)
        op.add_argument("--mapping-config", required=True); op.add_argument("--out", required=True)
        if name == "prepare-v3":
            op.add_argument("--pending-source-pack");op.add_argument("--runtime-root")
        op.set_defaults(func=command)
    op = operations.add_parser("report-v3", help="Full historical V3 DOM/receipt QA and contract export")
    op.add_argument("--root", required=True); op.add_argument("--run-id", required=True); op.add_argument("--out", required=True)
    op.add_argument("--mapping-config"); op.add_argument("--history-manifest")
    op.set_defaults(func=command)
    op = operations.add_parser("history-v3", help="Read-only all historical index and actual capture binding audit")
    op.add_argument("--historical-root",required=True);op.add_argument("--historical-run-id",required=True)
    op.add_argument("--root",required=True);op.add_argument("--run-id",required=True);op.add_argument("--out",required=True)
    op.set_defaults(func=command)
    for name in ("run", "resume", "status", "qa", "report", "gate-export", "renormalize", "review-identity", "gate-recover", "snapshot-review-status", "snapshot-review-correct"):
        op = operations.add_parser(name)
        op.add_argument("--root", required=True)
        op.add_argument("--run-id", default="")
        if name in {"run", "resume"}:
            op.add_argument("--plan", required=True)
            op.add_argument("--authorization")
            op.add_argument("--live", action="store_true")
            op.add_argument("--headful", action="store_true")
            op.add_argument("--profile-dir", help="One fixed normal browser profile for this run and its recovery; no rotation")
        if name in {"report", "gate-export", "renormalize", "snapshot-review-status", "snapshot-review-correct"}:
            op.add_argument("--out", required=True)
        if name == 'snapshot-review-correct':
            op.add_argument('--apply', action='store_true', help='Explicit stopped-run metadata correction; status is preview only')
        if name in {"gate-export", "gate-recover"}:
            op.add_argument("--authorization", required=name == "gate-recover")
        if name == "review-identity":
            op.add_argument("--review", required=True)
        op.set_defaults(func=command)
    op = operations.add_parser("plan")
    op.add_argument("--run-id", required=True); op.add_argument("--stage", choices=STAGES, default="T0")
    op.add_argument("--out"); op.set_defaults(func=command)
    op = operations.add_parser("discover")
    op.add_argument("--html", required=True); op.add_argument("--page-url", required=True)
    op.add_argument("--source-type", choices=list(SOURCE_ROOTS), required=True)
    op.add_argument("--observed-at", required=True)
    op.add_argument("--origin", choices=("OFFLINE_FIXTURE", "HISTORICAL_REPLAY"), default="HISTORICAL_REPLAY")
    op.add_argument("--out"); op.set_defaults(func=command)
    op = operations.add_parser("benchmark")
    op.add_argument("--pages", required=True); op.add_argument("--out", required=True)
    op.add_argument("--synthetic-unique", type=int, default=30000)
    op.add_argument("--synthetic-observations", type=int, default=100000)
    op.set_defaults(func=command)
