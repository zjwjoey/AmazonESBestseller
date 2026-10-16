"""One approved, fully receipted normal delivery UI operation. No API client."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from ..access.detector import detect_access_status
from ..access.location import inspect_delivery_location
from ..multisource.storage import (atomic_json, digest, immutable_bytes, immutable_json,
                                  read_sealed, sealed, file_lock)
from .quick_selection import file_sha


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_amendment(root, plan, auth):
    root = Path(root)
    ref = auth["delivery_setup_reference"]
    if file_sha(ref["path"]) != ref["sha256"]:
        raise ValueError("DELIVERY_AMENDMENT_SHA_MISMATCH")
    a = json.loads(Path(ref["path"]).read_text(encoding="utf8"))
    for key in ("original_authorization_reference", "original_failed_receipt_reference"):
        r = a[key]
        if file_sha(r["path"]) != r["sha256"]:
            raise ValueError("DELIVERY_ORIGINAL_EVIDENCE_CHANGED")
    original = json.loads(Path(a["original_authorization_reference"]["path"]).read_text(encoding="utf8"))
    failed = read_sealed(a["original_failed_receipt_reference"]["path"])
    t = next(t for t in plan["tasks"] if t["task_id"] == a["task_id"])
    if (a.get("approved") is not True or not a.get("approved_by") or not a.get("approval_id")
            or a.get("scope") != "ONE_NORMAL_DELIVERY_LOCATION_INITIALIZATION_AND_FIRST_FAILED_TASK_RECHECK"
            or a["run_id"] != plan["run_id"] or auth["run_id"] != plan["run_id"]
            or a["plan_sha256"] != digest(plan) or auth["plan_sha256"] != digest(plan)
            or a["candidate_id"] != t["candidate_id"] or a["initial_product_URL"] != t["request_url"]
            or failed.get("task_id") != t["task_id"] or failed.get("request_url") != t["request_url"]
            or failed.get("state") != "NORMAL" or failed.get("status_code") != 200
            or a.get("shipping_postal_code") != "28001"
            or a.get("max_setup_attempts") != 1 or a.get("max_setup_requests") != 8
            or a.get("new_other_request_subcap") != 8 or auth.get("max_other_requests") != 8
            or a.get("first_unsuccessful_task_max_attempts") != 2 or auth.get("max_attempts_per_task") != 2):
        raise ValueError("EXACT_DELIVERY_AMENDMENT_REQUIRED")
    for k, ak in (("max_requests", "total_request_cap_unchanged"),
                  ("max_page_requests", "PAGE_cap_unchanged"),
                  ("request_deadline", "request_deadline_unchanged"), ("deadline", "final_deadline_unchanged")):
        if auth[k] != original[k] or a[ak] != original[k]:
            raise ValueError("DELIVERY_AMENDMENT_CANNOT_EXPAND_BUDGET_OR_TIME")
    if auth["starts_at"] != original["starts_at"] or any(a.get(k) is not True for k in
            ("normal_UI_only", "no_gate_clear_or_access_bypass", "no_other_starting_product_or_ranking_URL", "no_replay_successful_tasks")):
        raise ValueError("DELIVERY_AMENDMENT_SAFETY_REQUIRED")
    return a


def allowed_shipping_request(url, method, body, postcode):
    p = urlsplit(url)
    if (p.scheme != "https" or p.netloc != "www.amazon.es" or p.fragment
            or method not in {"GET", "POST"}):
        return False
    # These paths are only admitted when emitted by the ordinary location UI.
    prefixes = ("/portal-migration/hz/glow/", "/gp/glow/")
    if not p.path.startswith(prefixes):
        return False
    suffix = p.path.rsplit("/", 1)[-1]
    if method == "GET":
        return suffix in {"get-rendered-address-selections", "get-address-selections.html"}
    if suffix not in {"address-change", "address-change.html"}:
        return False
    values = parse_qs(body or "")
    return values.get("zipCode") == [postcode] and values.get("locationType", ["LOCATION_INPUT"]) == ["LOCATION_INPUT"]


class SetupLedger:
    def __init__(self, root, plan, auth, amendment, check):
        self.root, self.plan, self.auth, self.amendment, self.check = Path(root), plan, auth, amendment, check

    def reserve(self, url, method, kind, body=""):
        self.check()
        validate_amendment(self.root, self.plan, self.auth)
        rows = [read_sealed(p) for p in (self.root / "receipts").glob("*.json")]
        if any(r.get("state") == "RESERVED" for r in rows):
            raise ValueError("DELIVERY_SETUP_IN_FLIGHT")
        if sum(r.get("request_role") == "DELIVERY_SETUP" for r in rows) >= 8:
            raise ValueError("DELIVERY_SETUP_BUDGET_EXHAUSTED")
        if (len(rows) >= self.auth["max_requests"] or
                sum(r.get("kind", "PAGE") == kind for r in rows) >= self.auth["max_page_requests" if kind == "PAGE" else "max_other_requests"]):
            raise ValueError("DELIVERY_TOTAL_BUDGET_EXHAUSTED")
        if kind == "PAGE":
            valid = method == "GET" and url == self.amendment["initial_product_URL"]
        else:
            valid = kind == "OTHER" and allowed_shipping_request(url, method, body, "28001")
        if not valid:
            raise ValueError("DELIVERY_SETUP_REQUEST_OUT_OF_SCOPE")
        times = [datetime.fromisoformat(r["started_at"]).timestamp() for r in rows if r.get("started_at")]
        if times:
            time.sleep(max(0, max(times) + self.auth.get("request_start_interval_seconds", 3) - time.time()))
        self.check()
        import hashlib
        r = dict(run_id=self.plan["run_id"], task_id=self.amendment["task_id"], request_role="DELIVERY_SETUP",
                 kind=kind, request_url=url, method=method, request_body_sha256=hashlib.sha256(body.encode()).hexdigest(),
                 started_at=now(), state="RESERVED", plan_sha256=digest(self.plan), authorization_sha256=digest(self.auth))
        p = self.root / "receipts" / ("delivery_setup_%02d.json" % (sum(x.get("request_role") == "DELIVERY_SETUP" for x in rows)+1))
        immutable_json(p, sealed(r))
        return p

    def finish(self, path, status, body, final_url, error=None):
        r = read_sealed(path)
        if r["state"] != "RESERVED":
            raise ValueError("DELIVERY_RECEIPT_ALREADY_FINAL")
        import hashlib
        data = body.encode("utf8")
        hp = self.root / "delivery_setup_evidence" / (hashlib.sha256(data).hexdigest()+".txt")
        immutable_bytes(hp, data)
        state = detect_access_status(status, body).value
        if error and status not in {403, 429}:
            state = "UNKNOWN"
        r.update(state=state, status_code=status, finished_at=now(), final_url=final_url,
                 body_path=str(hp), body_sha256=file_sha(hp), error=error)
        atomic_json(path, sealed(r), backup=True)
        if state != "NORMAL":
            gate = dict(state=state, run_id=self.plan["run_id"], receipt_path=str(path), observed_at=now())
            for gp in (self.root / "access_gate.json", Path(self.plan["shared_gate_path"])):
                previous = read_sealed(gp) if gp.exists() else None
                atomic_json(gp, sealed({**gate, "previous_gate": previous}), backup=True)
        return r


def verify_setup(root, plan, auth):
    a = validate_amendment(root, plan, auth)
    proof = read_sealed(Path(root) / "delivery_setup.complete.json")
    if (proof.get("passed") is not True or proof.get("plan_sha256") != digest(plan)
            or proof.get("amendment_sha256") != auth["delivery_setup_reference"]["sha256"]
            or proof.get("postal_code") != "28001" or file_sha(proof["html_path"]) != proof["html_sha256"]):
        raise ValueError("DELIVERY_SETUP_PROOF_INVALID")
    location = inspect_delivery_location(Path(proof["html_path"]).read_text(encoding="utf8"))
    if (location.is_spain is not True or location.postal_code != "28001" or
            detect_access_status(200, Path(proof["html_path"]).read_text(encoding="utf8")).value != "NORMAL"):
        raise ValueError("DELIVERY_SETUP_DESTINATION_NOT_VERIFIED")
    for p, sha in proof["receipts"].items():
        r = read_sealed(p)
        if file_sha(p) != sha or r.get("state") != "NORMAL" or r.get("request_role") != "DELIVERY_SETUP":
            raise ValueError("DELIVERY_SETUP_RECEIPT_INVALID")
    if not proof["receipts"]:
        raise ValueError("DELIVERY_SETUP_RECEIPTS_REQUIRED")
    return a


def perform_setup(session, bridge, auth):
    """Temporarily admit only UI-emitted shipping requests; retain every receipt."""
    root, plan = bridge.root, bridge.plan
    a = validate_amendment(root, plan, auth)
    if (root / "delivery_setup.complete.json").exists():
        return verify_setup(root, plan, auth)
    immutable_json(root / "delivery_setup.attempt.json", sealed(dict(started_at=now(),
        amendment_sha256=auth["delivery_setup_reference"]["sha256"], max_attempts=1)))
    ledger = SetupLedger(root, plan, auth, a, lambda: bridge.validate_authorization(auth))
    pending, failure = {}, []
    def boundary(route):
        req = route.request
        try:
            if failure or session.access_blocked:
                return route.abort()
            is_page = req.is_navigation_request() and req.frame == session.page.main_frame
            if is_page or req.resource_type in {"xhr", "fetch"}:
                if not is_page and not allowed_shipping_request(req.url, req.method, req.post_data or "", "28001"):
                    return route.abort()
                rp = ledger.reserve(req.url, req.method, "PAGE" if is_page else "OTHER", req.post_data or "")
                pending[req] = rp
                return route.continue_()
            if req.resource_type in {"image", "media", "font"} or req.is_navigation_request():
                return route.abort()
            return route.continue_()
        except Exception as exc:
            failure.append(str(exc)); return route.abort()
    def finish(req):
        if req not in pending:
            return
        rp = pending.pop(req)
        try:
            response = req.response()
            body = response.text() if response else ""
            r = ledger.finish(rp, response.status if response else None, body,
                              response.url if response else req.url, None if response else str(req.failure))
            if r["state"] != "NORMAL":
                failure.append("DELIVERY_SETUP_ACCESS_STOP:"+r["state"])
        except Exception as exc:
            if read_sealed(rp).get("state") == "RESERVED":
                ledger.finish(rp, None, "", req.url, str(exc))
            failure.append(str(exc))
    session.page.on("requestfinished", finish)
    session.page.on("requestfailed", finish)
    session.page.unroute("**/*")
    session.page.route("**/*", boundary)
    try:
        with file_lock(Path(plan["shared_gate_path"]).parent / "highspeed_live.lock"):
            status = session.goto(a["initial_product_URL"])
            session.page.wait_for_timeout(100)
            if failure or pending:
                raise ValueError("DELIVERY_SETUP_TRANSPORT_FAILED:"+str(failure))
            location = session.ensure_spain_delivery("28001", navigate=False, current_status_code=status)
            session.page.wait_for_timeout(200)
            html = session.page.content()
            location = inspect_delivery_location(html)
            if detect_access_status(status, html).value != "NORMAL":
                raise ValueError("DELIVERY_SETUP_FINAL_ACCESS_NOT_NORMAL")
            if failure or pending or location.is_spain is not True or location.postal_code != "28001":
                raise ValueError("DELIVERY_SETUP_NOT_VERIFIED:"+str(failure))
            bridge.gates_normal()
            hp = root / "delivery_setup_evidence" / "verified_destination.html"
            immutable_bytes(hp, html.encode("utf8"))
            receipts = {str(p): file_sha(p) for p in (root / "receipts").glob("delivery_setup_*.json")}
            immutable_json(root / "delivery_setup.complete.json", sealed(dict(passed=True, completed_at=now(),
                plan_sha256=digest(plan), amendment_sha256=auth["delivery_setup_reference"]["sha256"],
                postal_code=location.postal_code, delivery_text=location.text, html_path=str(hp),
                html_sha256=file_sha(hp), receipts=receipts)))
            return verify_setup(root, plan, auth)
    except Exception as exc:
        # Save the real popover/DOM even when an ordinary UI selector changes.
        # A failed attempt remains consumed; this is evidence, not a retry.
        try:
            snapshot = session.page.content()
            hp = root / "delivery_setup_evidence" / "failed_UI_snapshot.html"
            immutable_bytes(hp, snapshot.encode("utf8"))
            evidence = dict(html_path=str(hp), html_sha256=file_sha(hp), final_url=session.page.url)
        except Exception as capture_error:
            evidence = dict(capture_error=str(capture_error))
        immutable_json(root / "delivery_setup.failure.json", sealed(dict(failed_at=now(),
            error_type=type(exc).__name__, error=str(exc), route_errors=failure,
            pending_receipts=[str(p) for p in pending.values()], evidence=evidence)))
        raise
    finally:
        session.page.remove_listener("requestfinished", finish)
        session.page.remove_listener("requestfailed", finish)
        session.page.unroute("**/*", boundary)
