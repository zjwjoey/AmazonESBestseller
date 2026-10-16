"""Single Windows-independent worker; T1/T2 audit before full queue expansion."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from ..access.browser import BrowserSession
from ..access.location import inspect_delivery_location
from ..multisource.storage import atomic_json, digest, file_lock, immutable_json, read_sealed, sealed
from .detail_bridge5500 import DetailBridge5500, SUCCESS, parse_candidate_detail, safe_url, utc
from .quick_selection import file_sha
from .quick5500_cli import export


def audit_stage(bridge, stage, count):
    """Reopen HTML, metadata/receipt and parser result independently of writer."""
    audit_started = time.monotonic()
    hashes, coverage, requests, records = {}, Counter(), [], []
    for t in bridge.plan["tasks"][:count]:
        path = bridge.root / "tasks" / t["task_id"] / "checkpoint.json"
        outcome = read_sealed(path)
        if outcome["status"] not in SUCCESS:
            raise ValueError("STAGE_UNSUCCESSFUL_TASK")
        bridge.verify_saved_success(t, outcome)
        html = Path(outcome["html_path"]).read_text(encoding="utf8")
        if outcome["status"] == "REUSED_VALID_CACHE":
            meta = json.loads(Path(outcome["cache_metadata_path"]).read_text(encoding="utf8"))
        else:
            rp = Path(outcome["receipt_path"])
            r = read_sealed(rp)
            if (r["state"] != "NORMAL" or r["status_code"] != 200 or r["request_url"] != t["request_url"]
                    or r["run_id"] != bridge.plan["run_id"] or r["task_id"] != t["task_id"]
                    or r["html_sha256"] != file_sha(outcome["html_path"])):
                raise ValueError("STAGE_RECEIPT_BINDING_FAILED")
            requests.append(r)
            mp = Path(outcome.get("response_metadata_path") or path.parent / "response_metadata.json")
            if outcome.get("response_metadata_sha256") and file_sha(mp) != outcome["response_metadata_sha256"]:
                raise ValueError("STAGE_METADATA_SHA_MISMATCH")
            meta = json.loads(mp.read_text(encoding="utf8"))
        if inspect_delivery_location(html).is_spain is not True:
            raise ValueError("STAGE_SPAIN_DESTINATION_REQUIRED")
        replay = parse_candidate_detail(t, html, meta)
        if replay["status"] != "SUCCESS" or replay["record"] != outcome["record"]:
            raise ValueError("STAGE_INDEPENDENT_DETAIL_REPLAY_MISMATCH")
        record = replay["record"]
        for k in ("title_es_raw", "brand_raw", "current_price_raw", "rating_raw", "review_count_raw",
                  "monthly_bought_raw", "attributes", "feature_bullets_raw", "product_description_raw",
                  "image_url", "detail_category_trail", "parent_asin", "selected_variation_raw"):
            if record.get(k):
                coverage[k] += 1
        if not isinstance(record.get("ordered_detail_evidence"), list):
            raise ValueError("ORDERED_RAW_ATTRIBUTES_REQUIRED")
        records.append(record)
        hashes[t["task_id"]] = file_sha(path)
    all_paid = [read_sealed(p) for p in (bridge.root / "receipts").glob("*.json")]
    if any(r.get("state") == "RESERVED" for r in all_paid):
        raise ValueError("STAGE_IN_FLIGHT_REQUEST")
    times = sorted(datetime.fromisoformat(r["started_at"]) for r in all_paid)
    intervals = [(b-a).total_seconds() for a, b in zip(times, times[1:])]
    # Requests of a stage can be interleaved with cache hits, but never repeated.
    if len({r["task_id"] for r in requests}) != len(requests) or intervals and min(intervals) < 3:
        raise ValueError("DUPLICATE_OR_PACING_VIOLATION")
    if count >= 20 and any(coverage[k] == 0 for k in ("brand_raw", "current_price_raw", "attributes")):
        raise ValueError("SYSTEMATIC_FIELD_COVERAGE_REVIEW_REQUIRED")
    bridge.gates_normal()
    from .detail_budget5500 import uncertain_setup_page_holds
    held = uncertain_setup_page_holds(bridge.root, bridge.plan)
    proof = {"stage": stage, "passed": True, "completed_at": utc(), "run_id": bridge.plan["run_id"],
        "independent_audit_elapsed_seconds": time.monotonic()-audit_started,
        "plan_sha256": digest(bridge.plan), "success_count": count, "actual_live_tasks": len(requests),
        "cache_tasks": count-len(requests), "minimum_request_interval_seconds": min(intervals, default=None),
        "request_interval_scope": "OBSERVED_RECEIPTS_ONLY", "historical_setup_document_receipts_complete": held == 0,
        "historical_uncertain_PAGE_budget_reservation": held, "effective_request_budget_used_upper_bound": len(all_paid)+held,
        "field_coverage_count": dict(coverage), "field_coverage_denominator": count,
        "missing_fields_not_imputed": True, "checkpoint_sha256": hashes,
        "independent_HTML_identity_and_all_parsed_fields_replayed": True,
        "source_memberships": sorted({m["source_type"] for r in records for m in r["source_memberships"]})}
    immutable_json(bridge.root / (stage + "_acceptance.json"), sealed(proof))
    return proof


def run(root, auth_path, profile):
    root = Path(root)
    plan = json.loads((root / "detail_plan_5500.json").read_text(encoding="utf8"))
    auth = json.loads(Path(auth_path).read_text(encoding="utf8"))
    bridge = DetailBridge5500(root, plan)
    bridge.validate_authorization(auth)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if head != auth["code_sha"] or subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
        raise ValueError("APPROVED_CLEAN_CODE_REQUIRED")
    # The original profile stays fixed; only the separately approved normal UI
    # delivery setting is allowed before the frozen product queue resumes.
    with file_lock(root / "controller.lock"), BrowserSession(
            headless=True, profile_dir=profile, block_service_workers=True) as session:
        session.document_responses = []
        session.access_blocked = False
        def observe(response):
            request = response.request
            if request.resource_type == "document" and request.frame == session.page.main_frame:
                session.document_responses.append({"url": response.url, "status_code": response.status,
                                                   "observed_at": utc()})
                if response.status in {403, 429}:
                    session.access_blocked = True
        session.page.on("response", observe)
        allowed_initial = {t["request_url"] for t in plan["tasks"]}
        def boundary(route):
            req = route.request
            if session.access_blocked:
                return route.abort()
            # Ordinary assets only; no image download, no hidden API collection.
            if req.resource_type in {"image", "media", "font", "xhr", "fetch"}:
                return route.abort()
            if req.is_navigation_request():
                if req.frame != session.page.main_frame or not safe_url(req.url):
                    return route.abort()
                initial = req
                while initial.redirected_from:
                    initial = initial.redirected_from
                if initial.url not in allowed_initial:
                    return route.abort()
            return route.continue_()
        try:
            if auth.get("delivery_setup_reference"):
                from .delivery_setup5500 import perform_setup, verify_existing_setup_without_UI
                atomic_json(root / "worker_status.json", {"phase": "APPROVED_DELIVERY_UI_SETUP", "updated_at": utc()})
                if auth.get("post_UI_first_detail_recheck_reference"):
                    from .delivery_post_UI5500 import finalize_setup_from_first_detail
                    session.page.route("**/*", boundary)
                    atomic_json(root / "worker_status.json", {"phase": "FIRST_DETAIL2_POST_UI_ACTUAL_LOCATION_VERIFICATION", "updated_at": utc()})
                    if not (root / "delivery_setup.complete.json").exists():
                        bridge.run(session, auth, until_completed=1)
                        finalize_setup_from_first_detail(bridge, auth)
                    else:
                        from .delivery_setup5500 import verify_setup
                        verify_setup(root, plan, auth)
                    session.page.unroute("**/*", boundary)
                elif auth.get("delivery_setup_verification_reference"):
                    atomic_json(root / "worker_status.json", {"phase": "READ_ONLY_DELIVERY_VERIFICATION_NO_UI", "updated_at": utc()})
                    verify_existing_setup_without_UI(session, bridge, auth)
                else:
                    perform_setup(session, bridge, auth)
            session.page.route("**/*", boundary)
            for name, count in (("T1", 30), ("T2", 100), ("FINAL", len(plan["tasks"]))):
                if (root / (name + "_acceptance.json")).exists():
                    bridge.validate_stage(name, count)
                    continue
                atomic_json(root / "worker_status.json", {"phase": name + "_COLLECTING", "updated_at": utc(), "code_sha": head})
                bridge.run(session, auth, until_completed=count)
                atomic_json(root / "worker_status.json", {"phase": name + "_INDEPENDENT_AUDIT", "updated_at": utc()})
                audit_stage(bridge, name, count)
                export(root)
            immutable_json(root / "detail_run_manifest.json", {"run_id": plan["run_id"], "completed_at": utc(),
                "plan_sha256": digest(plan), "authorization_sha256": file_sha(auth_path), "code_sha": head,
                "final_acceptance_sha256": file_sha(root / "FINAL_acceptance.json"), "progress": bridge.progress()})
            atomic_json(root / "worker_status.json", {"phase": "COMPLETE", "updated_at": utc()})
        except Exception as exc:
            atomic_json(root / "worker_status.json", {"phase": "STOPPED_REVIEW_REQUIRED", "updated_at": utc(),
                "exception_type": type(exc).__name__, "reason": str(exc), "progress": bridge.progress()})
            export(root)
            raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True); p.add_argument("--authorization", required=True)
    p.add_argument("--profile", required=True)
    a = p.parse_args()
    run(a.root, a.authorization, a.profile)


if __name__ == "__main__":
    main()
