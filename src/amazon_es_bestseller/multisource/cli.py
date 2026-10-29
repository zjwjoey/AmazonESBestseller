"""Isolated CLI; all offline operations work without a browser profile."""
from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from .contracts import SOURCE_ROOTS, ORIGINS, PHASE_TARGETS, timestamp
from .storage import digest, immutable_bytes, immutable_json, read_sealed


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _plan(path):
    result = read_json(path)
    if result.get("schema_version") != 1:
        raise ValueError("PLAN_SCHEMA_VERSION_REQUIRED")
    for row in result.get("pages", []):
        for field in ("html_path", "capture_metadata_path"):
            if row.get(field):
                row[field] = str((Path(path).resolve().parent / row[field]).resolve())
        row["acp_paths"] = [str((Path(path).resolve().parent / p).resolve()) for p in row.get("acp_paths", [])]
    return result


def _filter_sources(sources, args):
    return [s for s in sources if s.get("enabled", True) and (not args.source_type or s["source_type"] == args.source_type)
        and (not args.research_category or s.get("research_category") == args.research_category)
        and (not args.source_id or s["source_id"] in args.source_id)]


def _history(manager, args):
    selected = args.history_run_id or [path.name for path in (manager.root / "runs").iterdir()
        if (path / "snapshots" / args.version / "manifest.json").exists()]
    return [manager.load(run_id, version=args.version) for run_id in selected]


def _execute(manager, args, spec=None):
    from .authorization import validate_authorization
    data = spec or _plan(args.plan)
    plan = dict(data.get("plan", data))
    sources = _filter_sources(data["sources"], args) if spec is None else data["sources"]
    if args.phase:
        plan["phase"] = args.phase
    origin = spec["origin"] if spec else (args.origin or data.get("origin", "OFFLINE_FIXTURE"))
    if args.operation == "replay" and origin == "LIVE_CAPTURE":
        raise ValueError("REPLAY_CANNOT_CREATE_LIVE_CAPTURE")
    run_id = args.run_id or data.get("run_id")
    if not run_id:
        raise ValueError("RUN_ID_REQUIRED")
    snapshot_id = data.get("snapshot_id")
    if args.run_id and args.run_id != data.get("run_id") and origin != "HISTORICAL_REPLAY":
        snapshot_id = args.run_id
    pages = data.get("pages", data.get("page_inputs"))
    if pages is not None:
        selected_ids = {s["source_id"] for s in sources}
        pages = [p for p in pages if p["source_id"] in selected_ids]
        plan["planned_pages"] = len(pages)
    authorization = read_json(args.authorization) if args.authorization else None
    if origin != "LIVE_CAPTURE":
        return manager.run(plan, sources, run_id=run_id,
            snapshot_id=snapshot_id, origin=origin, pages=pages, resume=args.operation == "resume")
    if getattr(args, "offline", False):
        raise ValueError("GLOBAL_OFFLINE_FLAG_FORBIDS_LIVE_CAPTURE")
    if not args.live:
        raise ValueError("LIVE_SWITCH_REQUIRED")
    # No browser is opened before all scope/budget/previous-gate checks pass.
    validate_authorization(plan, authorization, sources, manager._gate())
    from ..access.browser import BrowserSession
    from ..transport.playwright import PlaywrightTransport

    class BoundedSnapshotSession(BrowserSession):
        def load_lazy_ranking_content(self, *a, **kw):
            # Explicit metered ACP requests replace uncounted lazy requests.
            return None

    with BoundedSnapshotSession(headless=not args.headful) as session:
        allowed_documents = {url for scope in authorization["sources"].values() for url in scope.get("page_urls", [])}
        def document_scope(route):
            request = route.request
            if request.resource_type == "document" and request.url not in allowed_documents:
                route.abort()
            else:
                route.continue_()
        session.page.route("**/*", document_scope)
        transport = PlaywrightTransport(session)
        def save_aborted_native_contract(evidence):
            immutable_json(manager._run_dir(run_id) / "native_acp_contracts" / (uuid.uuid4().hex + ".json"),
                {**evidence, **timestamp(), "run_id": run_id,
                 "note": "Native request was aborted before transmission; not a successful response or a metered network attempt."})
        transport.install_bounded_acp_capture(on_capture=save_aborted_native_contract)
        return manager.run(plan, sources, run_id=run_id,
            snapshot_id=snapshot_id, origin=origin, pages=pages,
            authorization=authorization, transport=transport, resume=args.operation == "resume")


def dispatch(args):
    from .snapshots import SnapshotManager
    from .sources import SourceRegistry, discover_sources
    operation = args.operation
    query = operation in {'status', 'qa', 'compare', 'report', 'exceptions'}
    if query and args.out:
        root, output = Path(args.root).resolve(), Path(args.out).resolve()
        if output == root or output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError('OUTPUT_MUST_BE_OUTSIDE_SOURCE')
    manager = SnapshotManager(args.root, create=not query)
    registry = SourceRegistry(manager.root / "source_registry.json")
    if not query:
        registry.seed_roots()
    if operation == "sources":
        return {"types": SOURCE_ROOTS, "sources": registry.list(), "coverage": registry.coverage()}
    if operation == "discover":
        if not args.html or not args.page_url or not args.source_type or not args.observed_at:
            raise ValueError("DISCOVER_REQUIRES_HTML_URL_TYPE_ORIGINAL_TIMESTAMP")
        raw = Path(args.html).read_bytes()
        evidence = manager.root / "html" / (digest(raw) + ".html")
        immutable_bytes(evidence, raw)
        records = discover_sources(raw.decode("utf-8"), args.source_type, args.page_url,
            research_mapping=read_json(args.mapping) if args.mapping else None,
            evidence_path=str(evidence), observed_at=args.observed_at, origin=args.origin or "HISTORICAL_REPLAY")
        return {"discovered": [registry.upsert(row) for row in records], "coverage": registry.coverage()}
    if operation in {"run", "replay"}:
        if not args.plan:
            raise ValueError("PLAN_REQUIRED")
        if operation == "replay" and args.origin == "LIVE_CAPTURE":
            raise ValueError("REPLAY_CANNOT_CREATE_LIVE_CAPTURE")
        return _execute(manager, args)
    if operation == "status":
        return manager.status(args.run_id)
    if operation == "pause":
        return manager.pause(args.run_id)
    if operation == "resume":
        spec = read_sealed(manager._run_dir(args.run_id) / "run_spec.json")
        # Offline evidence paths come from committed journals or the plan file.
        if args.plan:
            return _execute(manager, args)
        if spec["origin"] != "LIVE_CAPTURE" and not manager.status(args.run_id)["frozen"]:
            raise ValueError("OFFLINE_RESUME_REQUIRES_ORIGINAL_PLAN")
        return _execute(manager, args, spec)
    if operation == "reparse":
        return manager.reparse(args.run_id, version=args.version)
    if operation == "verify":
        bundle = manager.load(args.run_id, version=args.version)
        results = []
        for source in bundle["manifest"]["sources"]:
            if args.source_id and source["source_id"] not in args.source_id:
                continue
            if not any(row["source_id"] == source["source_id"] for row in registry.list()):
                registry.upsert(source)
            for page in bundle["pages"]:
                if page["source_id"] == source["source_id"]:
                    results.append(registry.verify(source["source_id"], {"page": page,
                        "observations": [o for o in bundle["observations"] if o["source_id"] == source["source_id"] and o["page_url"] == page["page_url"]]},
                        origin=bundle["manifest"]["origin"], observed_at=page["observed_at"], evidence_path=page["evidence_path"],
                        capture_receipt_path=page.get("capture_receipt_path")))
        return {"verification": results, "coverage": registry.coverage()}
    if operation == "qa":
        return manager.load(args.run_id, version=args.version)["quality"]
    if operation == "compare":
        from .comparison import SnapshotComparator
        comparator = SnapshotComparator()
        filters = dict(source_type=args.source_type, research_category=args.research_category,
            source_id=args.source_id[0] if args.source_id else None)
        if args.history:
            return comparator.history(_history(manager,args), **filters)
        if args.left_date or args.right_date:
            if not args.left_date or not args.right_date:
                raise ValueError("BOTH_COMPARISON_DATES_REQUIRED")
            return comparator.compare_dates(_history(manager,args),args.left_date,args.right_date,**filters)
        if any((args.left_start,args.left_end,args.right_start,args.right_end)):
            if not all((args.left_start,args.left_end,args.right_start,args.right_end)):
                raise ValueError("BOTH_COMPLETE_COMPARISON_PERIODS_REQUIRED")
            return comparator.compare_periods(_history(manager,args),args.left_start,args.left_end,args.right_start,args.right_end,**filters)
        return comparator.compare(manager.load(args.left, version=args.version), manager.load(args.right, version=args.version), **filters)
    if operation in {"report", "exceptions"}:
        from .reports import SnapshotReportExporter
        bundle = manager.load(args.run_id, version=args.version)
        bundle["manifest"]["source_coverage"] = registry.coverage()
        histories = _history(manager,args)
        day = bundle["manifest"].get("local_date")
        prior_ids = {o["product_id"] for snapshot in histories
            if snapshot["manifest"].get("origin")==bundle["manifest"]["origin"]
            and day and snapshot["manifest"].get("local_date") and snapshot["manifest"]["local_date"]<day
            for o in snapshot["observations"] if o.get("product_id") and o.get("identity_status")=="CONFIRMED"}
        report = SnapshotReportExporter().build(bundle,history=histories,prior_product_ids=prior_ids,
            prior_scope="EARLIER_OBSERVATIONS_IN_SAME_ORIGIN_TEST_STORE",
            test_evidence=read_json(args.test_evidence) if args.test_evidence else None)
        if not args.out:
            raise ValueError("UNIQUE_REPORT_OUTPUT_DIRECTORY_REQUIRED")
        return SnapshotReportExporter().export(report, args.out)
    if operation in {"identity-review", "identity-undo", "identity-attach"}:
        from .identity import ProductIdentityRegistry
        identity = ProductIdentityRegistry(manager.root / "identity")
        if not args.actor or not args.evidence:
            raise ValueError("ACTOR_AND_REVIEW_EVIDENCE_REQUIRED")
        if operation == "identity-undo":
            return identity.undo(args.event_id, actor=args.actor, evidence=args.evidence)
        if operation == "identity-attach":
            if len(args.observation_id) != 1 or not args.reason:
                raise ValueError("ONE_OBSERVATION_AND_ATTACHMENT_REASON_REQUIRED")
            return identity.attach(args.observation_id[0], read_json(args.evidence), actor=args.actor, reason=args.reason)
        return identity.review(args.action, args.observation_id, actor=args.actor, evidence=args.evidence)
    if operation == "budget-adjust":
        adjustment = read_json(args.plan)
        manager.budget.adjust(**adjustment)
        return manager.budget.counters("LIVE_CAPTURE")
    if operation == "daily":
        from .schedule import daily_plan
        data = daily_plan(args.plan, local_date=args.date)
        return _execute(manager, args, data)
    if operation in {"migrate", "rollback"}:
        from .migrations import upgrade_state, rollback_state
        target = Path(args.state).resolve()
        if not target.is_relative_to(manager.root):
            raise ValueError("MIGRATION_OUTSIDE_ISOLATED_STORE")
        return upgrade_state(target) if operation == "migrate" else rollback_state(target)
    raise ValueError("UNKNOWN_OPERATION")


def register(subparsers):
    parser = subparsers.add_parser("multisource", help="隔离多榜单快照、身份审核和离线回放；实时采集默认关闭")
    parser.add_argument("operation", choices=("sources", "discover", "verify", "run", "status", "pause", "resume", "replay", "qa", "compare", "report", "exceptions", "reparse", "identity-review", "identity-undo", "identity-attach", "budget-adjust", "migrate", "rollback", "daily"))
    parser.add_argument("--root", required=True, help="全新或已标记的独立测试目录")
    for name in ("plan", "run-id", "left", "right", "out", "authorization", "html", "page-url", "observed-at", "mapping", "actor", "evidence", "event-id", "state", "date", "reason", "left-date", "right-date", "left-start", "left-end", "right-start", "right-end", "test-evidence"):
        parser.add_argument("--" + name)
    parser.add_argument("--source-type", choices=tuple(SOURCE_ROOTS))
    parser.add_argument("--research-category")
    parser.add_argument("--source-id", action="append", default=[])
    parser.add_argument("--observation-id", action="append", default=[])
    parser.add_argument("--history-run-id", action="append", default=[])
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--action", choices=("same", "different", "family", "pending"))
    parser.add_argument("--origin", choices=sorted(ORIGINS))
    parser.add_argument("--phase", choices=tuple(PHASE_TARGETS))
    parser.add_argument("--version", default="v1")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--headful", action="store_true")

    def command(args):
        try:
            result = dispatch(args)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            parser.exit(2, "!! multisource: " + str(exc) + "\n")
    parser.set_defaults(func=command)
