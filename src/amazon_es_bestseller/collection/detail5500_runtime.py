"""Single Windows-independent worker; T1/T2 audit before full queue expansion."""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path

from ..access.browser import BrowserSession
from ..multisource.storage import atomic_json, digest, file_lock, immutable_json, read_sealed, sealed
from .detail_bridge5500 import DetailBridge5500, SUCCESS, parse_candidate_detail, safe_url, utc
from .quick_selection import file_sha
from .quick5500_cli import export


def audit_stage(bridge, stage, count):
    """Reopen HTML, metadata/receipt and parser result independently of writer."""
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
            meta = json.loads((path.parent / "response_metadata.json").read_text(encoding="utf8"))
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
    times = sorted(datetime.fromisoformat(r["started_at"]) for r in requests)
    intervals = [(b-a).total_seconds() for a, b in zip(times, times[1:])]
    # Requests of a stage can be interleaved with cache hits, but never repeated.
    if len({r["task_id"] for r in requests}) != len(requests) or intervals and min(intervals) < 3:
        raise ValueError("DUPLICATE_OR_PACING_VIOLATION")
    bridge.gates_normal()
    proof = {"stage": stage, "passed": True, "completed_at": utc(), "run_id": bridge.plan["run_id"],
        "plan_sha256": digest(bridge.plan), "success_count": count, "actual_live_tasks": len(requests),
        "cache_tasks": count-len(requests), "minimum_request_interval_seconds": min(intervals, default=None),
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
    # No bootstrap navigation, no change of profile/identity or delivery region.
    with file_lock(root / "controller.lock"), BrowserSession(headless=True, profile_dir=profile) as session:
        session.document_responses = []
        def observe(response):
            request = response.request
            if request.resource_type == "document" and request.frame == session.page.main_frame:
                session.document_responses.append({"url": response.url, "status_code": response.status,
                                                   "observed_at": utc()})
        session.page.on("response", observe)
        allowed_initial = {t["request_url"] for t in plan["tasks"]}
        def boundary(route):
            req = route.request
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
        session.page.route("**/*", boundary)
        try:
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
