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
    if (body or "").lstrip().startswith("{"):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("DUPLICATE_JSON_KEY")
                result[key] = value
            return result
        try:
            values = json.loads(body, object_pairs_hook=unique)
        except (ValueError, TypeError):
            return False
        return (isinstance(values, dict) and values.get("zipCode") == postcode
                and values.get("locationType", "LOCATION_INPUT") == "LOCATION_INPUT")
    values = parse_qs(body or "")
    return values.get("zipCode") == [postcode] and values.get("locationType", ["LOCATION_INPUT"]) == ["LOCATION_INPUT"]


def validate_continuation(root, plan, auth, amendment):
    """No implicit second initialization. A new human approval must bind the
    exact unconfirmed UI failure and keep the original eight-request ceiling.
    Preparing this capability does not create or imply that approval.
    """
    root = Path(root)
    ref = auth.get("delivery_setup_continuation_reference")
    if not ref:
        raise ValueError("NEW_EXPLICIT_INITIALIZATION_APPROVAL_REQUIRED")
    if file_sha(ref["path"]) != ref["sha256"]:
        raise ValueError("CONTINUATION_APPROVAL_SHA_MISMATCH")
    a = json.loads(Path(ref["path"]).read_text(encoding="utf8"))
    if (a.get("approved") is not True or a.get("approved_by") != "user" or not a.get("approval_id")
            or a.get("scope") != "ONE_ADDITIONAL_NORMAL_UI_INITIALIZATION_AFTER_UNCONFIRMED_UI_FAILURE"
            or a.get("run_id") != plan["run_id"] or a.get("plan_sha256") != digest(plan)
            or a.get("task_id") != amendment["task_id"] or a.get("initial_product_URL") != amendment["initial_product_URL"]
            or a.get("shipping_postal_code") != "28001" or a.get("max_total_setup_attempts") != 2
            or a.get("max_total_setup_requests") != 8 or a.get("total_request_cap_unchanged") != auth["max_requests"]
            or a.get("request_deadline_unchanged") != auth["request_deadline"]
            or a.get("final_deadline_unchanged") != auth["deadline"]):
        raise ValueError("NEW_EXPLICIT_INITIALIZATION_APPROVAL_REQUIRED")
    for name in ("original_amendment_reference", "initial_attempt_reference", "initial_failure_reference",
                 "initial_setup_receipt_reference", "initial_failed_UI_snapshot_reference"):
        r = a[name]
        if not Path(r["path"]).resolve().is_relative_to(root.resolve()) or file_sha(r["path"]) != r["sha256"]:
            raise ValueError("CONTINUATION_INITIAL_EVIDENCE_CHANGED")
    if a["original_amendment_reference"] != auth["delivery_setup_reference"]:
        raise ValueError("CONTINUATION_ORIGINAL_APPROVAL_MISMATCH")
    if Path(a["initial_attempt_reference"]["path"]).resolve() != (root / "delivery_setup.attempt.json").resolve():
        raise ValueError("CONTINUATION_ATTEMPT_BINDING_INVALID")
    failure = read_sealed(a["initial_failure_reference"]["path"])
    r = read_sealed(a["initial_setup_receipt_reference"]["path"])
    if (failure.get("error_type") != "DeliveryLocationError" or failure.get("pending_receipts")
            or failure.get("evidence", {}).get("html_sha256") != a["initial_failed_UI_snapshot_reference"]["sha256"]
            or r.get("request_role") != "DELIVERY_SETUP" or r.get("state") != "NORMAL" or r.get("status_code") != 200
            or not _initial_readonly_setup_get(r, plan, amendment)):
        raise ValueError("CONTINUATION_ONLY_BOUND_NORMAL_UI_FAILURE")
    rows = [read_sealed(p) for p in (root / "receipts").glob("*.json")]
    setup = [x for x in rows if x.get("request_role") == "DELIVERY_SETUP"]
    if len(setup) == 2:
        names = ("failed_read_only_verification_reference", "verification_attempt_reference",
                 "verification_receipt_reference", "verification_html_reference")
        if any(not isinstance(a.get(n), dict) for n in names):
            raise ValueError("CONTINUATION_VERIFICATION_EVIDENCE_REQUIRED")
        for n in names:
            evidence = a[n]
            if (not Path(evidence["path"]).resolve().is_relative_to(root.resolve())
                    or file_sha(evidence["path"]) != evidence["sha256"]):
                raise ValueError("CONTINUATION_VERIFICATION_EVIDENCE_CHANGED")
        if (Path(a[names[0]]["path"]).resolve() != (root / "delivery_setup.verification_1.result.json").resolve()
                or Path(a[names[1]]["path"]).resolve() != (root / "delivery_setup.verification_1.attempt.json").resolve()):
            raise ValueError("CONTINUATION_VERIFICATION_BINDING_INVALID")
        failed = read_sealed(a[names[0]]["path"])
        attempt = read_sealed(a[names[1]]["path"])
        verification = read_sealed(a[names[2]]["path"])
        location = inspect_delivery_location(Path(a[names[3]]["path"]).read_text(encoding="utf8"))
        if (failed.get("passed") is not False or failed.get("no_UI_mutation_performed") is not True
                or attempt.get("mutation_allowed") is not False or failed.get("product_parse_status") != "SUCCESS"
                or failed.get("identity_status") != "EXACT_ACTIVE_PRODUCT_MATCH"
                or failed.get("receipt_path") != a[names[2]]["path"]
                or failed.get("receipt_sha256") != a[names[2]]["sha256"]
                or failed.get("html_path") != a[names[3]]["path"] or failed.get("html_sha256") != a[names[3]]["sha256"]
                or verification.get("body_path") != a[names[3]]["path"] or verification.get("body_sha256") != a[names[3]]["sha256"]
                or verification.get("state") != "NORMAL" or verification.get("status_code") != 200
                or verification.get("run_id") != plan["run_id"] or verification.get("task_id") != amendment["task_id"]
                or verification.get("plan_sha256") != digest(plan)
                or verification.get("request_role") != "DELIVERY_SETUP" or verification.get("kind") != "PAGE"
                or verification.get("method") != "GET" or verification.get("request_url") != amendment["initial_product_URL"]
                or verification.get("final_url") != amendment["initial_product_URL"]
                or (location.is_spain is True and location.postal_code == "28001")
                or detect_access_status(200, Path(a[names[3]]["path"]).read_text(encoding="utf8")).value != "NORMAL"
                or r not in setup or verification not in setup):
            raise ValueError("CONTINUATION_ONLY_NORMAL_WRONG_DESTINATION_VERIFICATION")
    elif len(setup) != 1 or setup[0] != r:
        raise ValueError("CONTINUATION_UNREVIEWED_SETUP_HISTORY")
    if (any(x.get("state") == "RESERVED" for x in rows)
            or (root / "delivery_setup.complete.json").exists()
            or (root / "delivery_setup.continuation_2.attempt.json").exists()
            or (root / "receipts" / (amendment["task_id"]+"_2.json")).exists()):
        raise ValueError("CONTINUATION_NOT_REPLAYABLE_AFTER_SUBMISSION_OR_DETAIL_RECHECK")
    return a


def requires_setup_accounting(url, method, resource_type, main_navigation=False):
    """Never let shipping data escape accounting by using a script/beacon type."""
    return (main_navigation or resource_type in {"xhr", "fetch"} or method != "GET"
            or urlsplit(url).path.startswith(("/portal-migration/hz/glow/", "/gp/glow/", "/gp/delivery/")))


def validate_local_payload_repair(root, plan, auth, amendment):
    """One delegated counted attempt 3 after a proved local pre-send error."""
    root = Path(root)
    ref = auth.get("delivery_setup_repair_reference")
    if not ref or file_sha(ref["path"]) != ref["sha256"]:
        raise ValueError("DELEGATED_UI_REPAIR_REVIEW_REQUIRED")
    a = json.loads(Path(ref["path"]).read_text(encoding="utf8"))
    if (a.get("approved") is not True or a.get("approved_by") != "agent_under_explicit_user_delegation"
            or a.get("scope") != "ONE_NORMAL_UI_REPAIR_AFTER_LOCAL_JSON_PRE_SEND_REJECTION"
            or a.get("run_id") != plan["run_id"] or a.get("plan_sha256") != digest(plan)
            or a.get("task_id") != amendment["task_id"] or a.get("initial_product_URL") != amendment["initial_product_URL"]
            or a.get("maximum_total_UI_attempts") != 3 or a.get("maximum_repair_attempts") != 1
            or a.get("max_total_setup_requests") != 8 or a.get("total_request_cap_unchanged") != auth["max_requests"]
            or a.get("request_deadline_unchanged") != auth["request_deadline"] or a.get("final_deadline_unchanged") != auth["deadline"]
            or a.get("only_browser_emitted_requests") is not True):
        raise ValueError("DELEGATED_UI_REPAIR_SCOPE_INVALID")
    for name in ("delegation_reference", "attempt_2_reference", "failure_2_reference", "failed_UI_reference",
                 "blocked_request_reference", "last_modal_receipt_reference", "budget_reservation_reference"):
        r = a[name]
        if not Path(r["path"]).resolve().is_relative_to(root.resolve()) or file_sha(r["path"]) != r["sha256"]:
            raise ValueError("DELEGATED_UI_REPAIR_EVIDENCE_CHANGED")
    delegation = json.loads(Path(a["delegation_reference"]["path"]).read_text(encoding="utf8"))
    attempt = read_sealed(a["attempt_2_reference"]["path"])
    failure = read_sealed(a["failure_2_reference"]["path"])
    denied = json.loads(Path(a["blocked_request_reference"]["path"]).read_text(encoding="utf8"))
    denied_url = urlsplit(denied.get("url", ""))
    last = read_sealed(a["last_modal_receipt_reference"]["path"])
    failed_html = Path(a["failed_UI_reference"]["path"]).read_text(encoding="utf8")
    failed_location = inspect_delivery_location(failed_html)
    if (delegation.get("approved_by") != "user" or delegation.get("approved") is not True
            or delegation.get("human_quote") != "直到任务提取都不要问我问题，我给你权限你觉得"
            or attempt.get("total_setup_attempt") != 2 or failure.get("error_type") != "DeliveryLocationError"
            or failure.get("pending_receipts") or failure.get("route_errors")
            or failure.get("evidence", {}).get("html_sha256") != a["failed_UI_reference"]["sha256"]
            or denied.get("action") != "ABORTED_BEFORE_SEND" or denied.get("method") != "POST"
            or denied.get("postal_code_values") != "28001" or denied.get("resource_type") != "xhr"
            or denied_url.scheme != "https" or denied_url.netloc != "www.amazon.es"
            or denied_url.path not in {"/portal-migration/hz/glow/address-change", "/gp/glow/address-change.html"}
            or detect_access_status(200, failed_html).value != "NORMAL"
            or (failed_location.is_spain is True and failed_location.postal_code == "28001")
            or last.get("state") != "NORMAL" or last.get("status_code") != 200
            or not _initial_readonly_setup_get(last, plan, amendment)):
        raise ValueError("DELEGATED_UI_REPAIR_NOT_PROVEN_LOCAL_PRE_SEND_FAILURE")
    from .detail_budget5500 import uncertain_setup_page_holds
    if uncertain_setup_page_holds(root, plan, auth) != 2 or auth.get("request_budget_reservation_reference") != a["budget_reservation_reference"]:
        raise ValueError("DELEGATED_UI_REPAIR_CONSERVATIVE_ACCOUNTING_REQUIRED")
    rows = [read_sealed(p) for p in (root / "receipts").glob("*.json")]
    setup = [r for r in rows if r.get("request_role") == "DELIVERY_SETUP"]
    if (len(rows) != 4 or len(setup) != 3 or any(r.get("method") != "GET" or r.get("state") != "NORMAL" for r in setup)
            or any(r.get("state") == "RESERVED" for r in rows)
            or (root / "delivery_setup.repair_3.attempt.json").exists()
            or (root / "delivery_setup.complete.json").exists()
            or (root / "receipts" / (amendment["task_id"]+"_2.json")).exists()):
        raise ValueError("DELEGATED_UI_REPAIR_ALREADY_CONSUMED_OR_HISTORY_CHANGED")
    return a


def _initial_readonly_setup_get(original, plan, amendment):
    if original.get("kind") == "PAGE":
        return original.get("method") == "GET" and original.get("request_url") == amendment["initial_product_URL"]
    return (original.get("kind") == "OTHER" and original.get("method") == "GET"
        and original.get("run_id") == plan["run_id"] and original.get("task_id") == amendment["task_id"]
        and original.get("plan_sha256") == digest(plan) and original.get("error") is None
        and original.get("final_url") == original.get("request_url")
        and allowed_shipping_request(original.get("request_url", ""), "GET", "", "28001"))


def validate_read_only_verification(root, plan, auth, amendment):
    """One non-mutating product document check in the original setup budget.
    This does not authorize another initialization or another detail attempt.
    """
    root = Path(root)
    ref = auth.get("delivery_setup_verification_reference")
    if not ref or file_sha(ref["path"]) != ref["sha256"]:
        raise ValueError("READ_ONLY_VERIFICATION_REVIEW_REQUIRED")
    v = json.loads(Path(ref["path"]).read_text(encoding="utf8"))
    if (v.get("scope") != "ONE_READ_ONLY_DOCUMENT_VERIFICATION_WITHIN_ORIGINAL_SETUP"
            or v.get("reviewed") is not True or v.get("reviewer") != "agent_under_existing_user_authorization"
            or v.get("run_id") != plan["run_id"] or v.get("plan_sha256") != digest(plan)
            or v.get("task_id") != amendment["task_id"] or v.get("initial_product_URL") != amendment["initial_product_URL"]
            or v.get("max_verification_pages") != 1 or v.get("mutation_allowed") is not False
            or v.get("repeat_initialization_allowed") is not False
            or v.get("original_max_setup_attempts_preserved") != 1 or v.get("max_total_setup_requests") != 8
            or v.get("original_amendment_reference") != auth["delivery_setup_reference"]):
        raise ValueError("READ_ONLY_VERIFICATION_SCOPE_INVALID")
    for name in ("initial_attempt_reference", "initial_failure_reference", "initial_setup_receipt_reference"):
        r = v[name]
        if not Path(r["path"]).resolve().is_relative_to(root.resolve()) or file_sha(r["path"]) != r["sha256"]:
            raise ValueError("READ_ONLY_VERIFICATION_INITIAL_EVIDENCE_CHANGED")
    book = v["existing_user_delegation_reference"]
    if file_sha(book["path"]) != book["sha256"]:
        raise ValueError("READ_ONLY_VERIFICATION_DELEGATION_CHANGED")
    failure = read_sealed(v["initial_failure_reference"]["path"])
    original = read_sealed(v["initial_setup_receipt_reference"]["path"])
    rows = [read_sealed(p) for p in (root / "receipts").glob("*.json")]
    # The real failed attempt saved an ordinary read-only modal GET, not a
    # product document. Its presence does not authorize another UI operation.
    if (failure.get("error_type") != "DeliveryLocationError" or failure.get("pending_receipts")
            or original.get("state") != "NORMAL" or original.get("status_code") != 200
            or original.get("request_role") != "DELIVERY_SETUP" or not _initial_readonly_setup_get(original, plan, amendment)
            or len([r for r in rows if r.get("request_role") == "DELIVERY_SETUP"]) != 1
            or any(r.get("state") == "RESERVED" for r in rows)
            or (root / "delivery_setup.verification_1.attempt.json").exists()
            or (root / "delivery_setup.complete.json").exists()
            or (root / "receipts" / (amendment["task_id"]+"_2.json")).exists()):
        raise ValueError("READ_ONLY_VERIFICATION_NOT_REPLAYABLE")
    return v


def verify_existing_setup_without_UI(session, bridge, auth):
    """Reload only the original literal product URL once. Never click/fill/POST.
    A late server-side setting may be visible on the fresh document even when
    the old popover confirmation failed. A still-wrong destination stops here.
    """
    root, plan = bridge.root, bridge.plan
    a = validate_amendment(root, plan, auth)
    if (root / "delivery_setup.complete.json").exists():
        return verify_setup(root, plan, auth)
    validate_read_only_verification(root, plan, auth, a)
    bridge.validate_authorization(auth)
    immutable_json(root / "delivery_setup.verification_1.attempt.json", sealed(dict(started_at=now(),
        verification_reference=auth["delivery_setup_verification_reference"], mutation_allowed=False,
        original_initialization_attempt_preserved=True)))
    ledger = SetupLedger(root, plan, auth, a, lambda: bridge.validate_authorization(auth))
    document_started = False
    unexpected_navigation = []
    def boundary(route):
        nonlocal document_started
        req = route.request
        if session.access_blocked:
            return route.abort()
        if req.is_navigation_request():
            if (document_started or req.method != "GET" or req.frame != session.page.main_frame
                    or req.url != a["initial_product_URL"]):
                unexpected_navigation.append(req.url)
                return route.abort()
            document_started = True
            return route.continue_()
        if (requires_setup_accounting(req.url, req.method, req.resource_type)
                or req.resource_type in {"image", "media", "font"}):
            return route.abort()
        return route.continue_()
    session.page.unroute("**/*")
    session.page.route("**/*", boundary)
    rp = None
    try:
        with file_lock(Path(plan["shared_gate_path"]).parent / "highspeed_live.lock"):
            rp = ledger.reserve(a["initial_product_URL"], "GET", "PAGE")
            html, status, error = "", None, None
            session.document_responses.clear()
            try:
                status = session.goto(a["initial_product_URL"])
                html = session.page.content()
                if detect_access_status(status, html).value == "NORMAL":
                    session.wait_for_product_page()
                    html = session.page.content()
            except Exception as exc:
                error = str(exc)
            observed = next((x for x in session.document_responses if x["status_code"] in {403,429}), None)
            if observed:
                status = observed["status_code"]
            if unexpected_navigation:
                error = "UNEXPECTED_VERIFICATION_NAVIGATION"
            receipt = ledger.finish(rp, status, html, session.page.url, error)
            if receipt["state"] != "NORMAL":
                raise ValueError("READ_ONLY_VERIFICATION_ACCESS_STOP:"+receipt["state"])
            from .detail_bridge5500 import parse_candidate_detail
            task = next(t for t in plan["tasks"] if t["task_id"] == a["task_id"])
            replay = parse_candidate_detail(task, html, dict(status_code=status, requested_url=task["request_url"],
                final_url=session.page.url, collected_at=receipt["finished_at"]))
            location = inspect_delivery_location(html)
            result = dict(observed_at=now(), receipt_path=str(rp), receipt_sha256=file_sha(rp),
                html_path=receipt["body_path"], html_sha256=receipt["body_sha256"],
                delivery_text=location.text, postal_code=location.postal_code, is_spain=location.is_spain,
                identity_status=replay.get("identity_status"), product_parse_status=replay["status"],
                passed=location.is_spain is True and location.postal_code == "28001" and replay["status"] == "SUCCESS",
                no_UI_mutation_performed=True, counted_within_original_eight_setup_requests=True)
            immutable_json(root / "delivery_setup.verification_1.result.json", sealed(result))
            if not result["passed"]:
                raise ValueError("READ_ONLY_VERIFICATION_DESTINATION_OR_IDENTITY_NOT_VERIFIED")
            bridge.gates_normal()
            receipts = {str(p):file_sha(p) for p in (root / "receipts").glob("delivery_setup_*.json")}
            immutable_json(root / "delivery_setup.complete.json", sealed(dict(passed=True, completed_at=now(),
                plan_sha256=digest(plan), amendment_sha256=auth["delivery_setup_reference"]["sha256"],
                postal_code=location.postal_code, delivery_text=location.text,
                html_path=result["html_path"], html_sha256=result["html_sha256"], receipts=receipts,
                finalized_by_read_only_verification=auth["delivery_setup_verification_reference"],
                initialization_attempt_count_preserved=1)))
            return verify_setup(root, plan, auth)
    finally:
        session.page.unroute("**/*", boundary)


class SetupLedger:
    def __init__(self, root, plan, auth, amendment, check):
        self.root, self.plan, self.auth, self.amendment, self.check = Path(root), plan, auth, amendment, check

    def reserve(self, url, method, kind, body=""):
        self.check()
        validate_amendment(self.root, self.plan, self.auth)
        rows = [read_sealed(p) for p in (self.root / "receipts").glob("*.json")]
        from .detail_budget5500 import uncertain_setup_page_holds
        held = uncertain_setup_page_holds(self.root, self.plan, self.auth)
        if any(r.get("state") == "RESERVED" for r in rows):
            raise ValueError("DELIVERY_SETUP_IN_FLIGHT")
        if sum(r.get("request_role") == "DELIVERY_SETUP" for r in rows)+held >= 8:
            raise ValueError("DELIVERY_SETUP_BUDGET_EXHAUSTED")
        if (len(rows)+held >= self.auth["max_requests"] or
                sum(r.get("kind", "PAGE") == kind for r in rows)+(held if kind == "PAGE" else 0) >= self.auth["max_page_requests" if kind == "PAGE" else "max_other_requests"]):
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
    for name in ("delivery_setup_repair_reference", "request_budget_reservation_reference", "post_UI_first_detail_recheck_reference"):
        if proof.get(name) and (auth.get(name) != proof[name] or file_sha(proof[name]["path"]) != proof[name]["sha256"]):
            raise ValueError("DELIVERY_SETUP_REPAIR_PROOF_BINDING_INVALID")
    if proof.get("detail_recheck_receipt_reference"):
        ref = proof["detail_recheck_receipt_reference"]
        r = read_sealed(ref["path"])
        if (file_sha(ref["path"]) != ref["sha256"] or r.get("request_role") != "DETAIL" or r.get("attempt") != 2
                or r.get("state") != "NORMAL" or r.get("status_code") != 200
                or r.get("run_id") != plan["run_id"] or r.get("task_id") != a["task_id"]
                or r.get("html_sha256") != proof.get("html_sha256")):
            raise ValueError("DELIVERY_SETUP_ACTUAL_DETAIL2_RECEIPT_INVALID")
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
    suffix = ""
    repair = bool(auth.get("delivery_setup_repair_reference"))
    if repair:
        validate_local_payload_repair(root, plan, auth, a)
        suffix = ".repair_3"
        immutable_json(root / "delivery_setup.repair_3.attempt.json", sealed(dict(started_at=now(),
            repair_reference=auth["delivery_setup_repair_reference"], total_setup_attempt=3,
            approved_by="agent_under_explicit_user_delegation", prior_attempts_preserved=True)))
    elif (root / "delivery_setup.attempt.json").exists():
        validate_continuation(root, plan, auth, a)
        suffix = ".continuation_2"
        immutable_json(root / "delivery_setup.continuation_2.attempt.json", sealed(dict(started_at=now(),
            continuation_sha256=auth["delivery_setup_continuation_reference"]["sha256"], total_setup_attempt=2)))
    else:
        immutable_json(root / "delivery_setup.attempt.json", sealed(dict(started_at=now(),
            amendment_sha256=auth["delivery_setup_reference"]["sha256"], max_attempts=1)))
    ledger = SetupLedger(root, plan, auth, a, lambda: bridge.validate_authorization(auth))
    pending, failure = {}, []
    initial_document_pending = False
    initial_navigation_seen = False
    def boundary(route):
        nonlocal initial_navigation_seen
        req = route.request
        try:
            if failure or session.access_blocked:
                return route.abort()
            is_page = req.is_navigation_request() and req.frame == session.page.main_frame
            if initial_document_pending and is_page:
                if initial_navigation_seen or req.url != a["initial_product_URL"] or req.method != "GET":
                    failure.append("UNEXPECTED_INITIAL_SETUP_NAVIGATION")
                    return route.abort()
                initial_navigation_seen = True
                return route.continue_()
            if requires_setup_accounting(req.url, req.method, req.resource_type, is_page):
                if not is_page and not allowed_shipping_request(req.url, req.method, req.post_data or "", "28001"):
                    # Retain denied UI requests without sending them. Previously
                    # a legitimate UI mismatch was indistinguishable from no
                    # request at all. Never save cookies or validation tokens.
                    import hashlib
                    data = req.post_data or ""
                    try:
                        fields = json.loads(data) if data.lstrip().startswith("{") else parse_qs(data)
                        postal = fields.get("zipCode") if isinstance(fields, dict) else None
                    except ValueError:
                        postal = None
                    evidence = dict(observed_at=now(), url=req.url, method=req.method,
                        resource_type=req.resource_type, action="ABORTED_BEFORE_SEND", reason="OUTSIDE_APPROVED_UI_REQUEST_SCOPE",
                        body_sha256=hashlib.sha256(data.encode()).hexdigest(), postal_code_values=postal)
                    dp = root / "delivery_setup_evidence" / "denied_routes"
                    if len(list(dp.glob("*.json"))) < 100:
                        immutable_json(dp / (digest(evidence)+".json"), evidence)
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
            initial_receipt = ledger.reserve(a["initial_product_URL"], "GET", "PAGE")
            initial_document_pending = True
            status, initial_html, initial_error = None, "", None
            try:
                session.document_responses.clear()
                status = session.goto(a["initial_product_URL"])
                initial_html = session.page.content()
            except Exception as exc:
                initial_error = str(exc)
            observed = next((r for r in session.document_responses if r["status_code"] in {403,429}), None)
            if observed:
                status = observed["status_code"]
            receipt = ledger.finish(initial_receipt, status, initial_html, session.page.url, initial_error)
            initial_document_pending = False
            if receipt["state"] != "NORMAL":
                raise ValueError("INITIAL_SETUP_DOCUMENT_ACCESS_STOP:"+receipt["state"])
            session.page.wait_for_timeout(100)
            if failure or pending:
                raise ValueError("DELIVERY_SETUP_TRANSPORT_FAILED:"+str(failure))
            options = dict(navigate=False, current_status_code=status)
            if repair:
                options["avoid_confirmation_navigation"] = True
            location = session.ensure_spain_delivery("28001", **options)
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
                html_sha256=file_sha(hp), receipts=receipts,
                actual_UI_attempts=3 if repair else (2 if suffix else 1),
                delivery_setup_repair_reference=auth.get("delivery_setup_repair_reference"),
                request_budget_reservation_reference=auth.get("request_budget_reservation_reference"))))
            return verify_setup(root, plan, auth)
    except Exception as exc:
        # Save the real popover/DOM even when an ordinary UI selector changes.
        # A failed attempt remains consumed; this is evidence, not a retry.
        try:
            snapshot = session.page.content()
            hp = root / "delivery_setup_evidence" / ("failed_UI_snapshot"+suffix+".html")
            immutable_bytes(hp, snapshot.encode("utf8"))
            evidence = dict(html_path=str(hp), html_sha256=file_sha(hp), final_url=session.page.url)
        except Exception as capture_error:
            evidence = dict(capture_error=str(capture_error))
        immutable_json(root / ("delivery_setup"+suffix+".failure.json"), sealed(dict(failed_at=now(),
            error_type=type(exc).__name__, error=str(exc), route_errors=failure,
            pending_receipts=[str(p) for p in pending.values()], evidence=evidence)))
        raise
    finally:
        session.page.remove_listener("requestfinished", finish)
        session.page.remove_listener("requestfailed", finish)
        session.page.unroute("**/*", boundary)
