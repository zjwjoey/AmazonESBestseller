"""URL-keyed adapter over the existing detail parser, browser and DetailState.

No URL is synthesized from an ASIN. Historical stores are only read. Success
belongs to the frozen candidate task; even equal parent ASINs never merge tasks.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..access.detector import AccessStopError, detect_access_status
from ..access.location import inspect_delivery_location
from ..identity import asin_from_url
from ..multisource.storage import (atomic_json, canonical_bytes, digest, file_lock,
                                  immutable_bytes, immutable_json, read_sealed, sealed)
from ..highspeed.v3_links import normalize_product_url_v3
from .detail import _classify_saved_page, CURRENT_DETAIL_SCHEMA_VERSION, CURRENT_DETAIL_PARSER_VERSION
from .planning import DetailState
from .quick_selection import file_sha

BRIDGE_VERSION = "DetailBridge5500.v0.1"
SUCCESS = {"SUCCESS", "REUSED_VALID_CACHE"}


def utc():
    return datetime.now(timezone.utc).isoformat()


def safe_url(url):
    try:
        p = urlsplit(str(url))
        return p.scheme == "https" and p.hostname == "www.amazon.es" and p.port is None and not p.username and not p.password
    except ValueError:
        return False


def normalize(url):
    return normalize_product_url_v3(url).get("normalized_product_url")


def _owned_child_parent_canonical(soup, active, canonical_asins, requested, final):
    """Prove a selected child while Amazon canonical names its explicit parent.

    URL or a family member alone never proves the selected product. Require
    matching buybox input and owned Twister current/landing/display evidence.
    """
    if (not requested or requested != final or active != {requested}
            or len(canonical_asins) != 1 or requested in canonical_asins
            or len(soup.select('link[rel="canonical"]')) != 1):
        return None
    owned = soup.select("#ppd #twister_feature_div")
    if len(owned) != 1:
        return None
    scripts = [e.get_text() for e in owned[0].select("script")
               if re.search(r"P\.register\(['\"]twister-js-init-dpx-data['\"]\s*,", e.get_text())]
    buybox = {str(e.get("value") or "").strip().upper()
              for e in soup.select("#ppd #buybox input#ASIN[name=ASIN]")}
    if len(scripts) != 1 or buybox != {requested}:
        return None
    script = scripts[0]
    def unique(key):
        values = re.findall(r'"' + key + r'"\s*:\s*"([A-Z0-9]{10})"', script)
        return values[0] if values and len(set(values)) == 1 else None
    parent = unique("parentAsin")
    explicit_parents = {str(e.get("value") or "").strip().upper()
                        for e in soup.select("input#parentASIN, input[name=parentASIN]")}
    from .detail_v2 import _json_after
    display = _json_after(script, '"dimensionValuesDisplayData"')
    if (unique("currentAsin") != requested or unique("landingAsin") != requested
            or canonical_asins != {parent} or not isinstance(display, dict)
            or requested not in display or not isinstance(display[requested], list)
            or (explicit_parents and explicit_parents != {parent})):
        return None
    return {"rule": "OWNED_TWISTER_CURRENT_LANDING_BUYBOX_CHILD_CANONICAL_PARENT_V1",
            "current_asin": requested, "parent_asin": parent,
            "source": "#ppd #twister_feature_div / twister-js-init-dpx-data",
            "buybox_input": "#ppd #buybox input#ASIN[name=ASIN]"}


def parse_candidate_detail(task, html, meta):
    """Reuse V2 field extraction, adding strict active-product URL binding."""
    access = detect_access_status(meta.get("status_code"), html).value
    if access != "NORMAL":
        return {"status": "ACCESS_STOP", "access_state": access}
    final = str(meta.get("final_url") or "")
    if not safe_url(final):
        return {"status": "IDENTITY_REVIEW", "reason": "NON_AMAZON_PRODUCT_DESTINATION"}
    soup = BeautifulSoup(html, "lxml")
    # Recommendations/data-asin are deliberately not active-product evidence.
    active = {str(e.get("value") or "").strip().upper() for e in
              soup.select("input#ASIN, input#productAsin, input[name=ASIN]") if e.get("value")}
    active = {a for a in active if re.fullmatch(r"[A-Z0-9]{10}", a)}
    canonicals = [str(e.get("href") or "") for e in soup.select('link[rel="canonical"]')]
    canonical_asins = {asin_from_url(u) for u in canonicals if safe_url(u) and asin_from_url(u)}
    requested_asin = asin_from_url(task["request_url"])
    final_asin = asin_from_url(final)
    strong = active | canonical_asins
    parent_proof = _owned_child_parent_canonical(soup, active, canonical_asins, requested_asin, final_asin)
    if parent_proof:
        strong = active
    if len(strong) != 1 or (active and canonical_asins and active != canonical_asins):
        if not parent_proof:
            return {"status": "IDENTITY_REVIEW", "reason": "ACTIVE_PRODUCT_ASIN_NOT_UNAMBIGUOUS",
                    "observed_active_asins": sorted(strong)}
    observed = next(iter(strong))
    if (requested_asin and requested_asin != observed) or (final_asin and final_asin != observed):
        return {"status": "IDENTITY_REVIEW", "reason": "ACTIVE_CHILD_ASIN_CHANGED",
                "observed_active_asins": [observed]}
    if not requested_asin and normalize(final) != task["normalized_product_url"]:
        return {"status": "IDENTITY_REVIEW", "reason": "OPAQUE_URL_REDIRECT_UNVERIFIED"}
    classification, state, record = _classify_saved_page(html, observed, meta, parser_version="v2")
    if classification != "VALID_PRODUCT_PAGE" or state.value != "NORMAL" or not record:
        return {"status": "PARSE_FAILED", "reason": classification}
    # V0 narrows legacy page-wide fallbacks to evidence owned by this product.
    # It does not alter old caches or the old parser's historical contract.
    from .detail import _monthly_bought_raw
    from ..normalization.monthly_bought import parse_monthly_bought
    from ..normalization.locale_numbers import parse_rating_es, parse_review_count_es
    owned_monthly = soup.select_one("#social-proofing-faceout")
    record["monthly_bought_raw"] = _monthly_bought_raw(BeautifulSoup(str(owned_monthly or ""), "lxml"))
    record["monthly_bought_min"] = parse_monthly_bought(record["monthly_bought_raw"])
    record["monthly_bought_semantics"] = "VISIBLE_LOWER_BOUND_NOT_EXACT_SALES"
    record["rating_value"] = parse_rating_es(record.get("rating_raw"))
    record["rating_count"] = parse_review_count_es(record.get("review_count_raw"))
    record["reference_price_raw"] = record.pop("original_price_raw", "")
    record["reference_price_semantics"] = "EXPLICIT_STRUCK_REFERENCE_NOT_HISTORICAL_ORIGINAL"
    parent_input = soup.select_one("input#parentASIN, input[name=parentASIN]")
    explicit_parent = str(parent_input.get("value") or "").strip().upper() if parent_input else ""
    variation = record.get("variation_evidence") or {}
    linked_parent = variation.get("parent_asin") if variation.get("current_asin") == observed else ""
    parent = explicit_parent or linked_parent or None
    if parent_proof:
        parent = parent_proof["parent_asin"]
    if parent and not re.fullmatch(r"[A-Z0-9]{10}", parent):
        parent = None
    record["parent_asin"] = parent
    record["parent_asin_evidence"] = ({"kind": "EXPLICIT_PARENT_INPUT" if explicit_parent else "CURRENT_ASIN_VARIATION_STATE",
                                         "current_asin": observed, "parent_asin": parent} if parent else None)
    if variation.get("current_asin") != observed:
        record["variation_evidence"] = None
        record["variation_family_asins"] = []
    # Preserve real input URL rather than the old parser's ASIN-derived URL.
    record.update(candidate_id=task["candidate_id"], selection_id=task["selection_id"],
        raw_product_url=task["raw_product_url"], normalized_product_url=task["normalized_product_url"],
        requested_url=task["request_url"], request_url=task["request_url"], final_url=final,
        product_url=final, asin=observed, resolved_asin=observed, identity_status="EXACT_ACTIVE_PRODUCT_MATCH",
        detail_schema_version=CURRENT_DETAIL_SCHEMA_VERSION, detail_parser_version=CURRENT_DETAIL_PARSER_VERSION,
        bridge_version=BRIDGE_VERSION, collected_at=meta.get("collected_at"), access_state="NORMAL",
        status_code=meta["status_code"], canonical_urls=canonicals,
        source_snapshot_id=task["source_snapshot_id"], source_memberships=task["source_memberships"])
    delivery = inspect_delivery_location(html)
    if parent_proof:
        record["active_child_canonical_parent_evidence"] = parent_proof
    record.update(delivery_location_text=delivery.text, delivery_postal_code=delivery.postal_code,
                  delivery_is_spain=delivery.is_spain)
    return {"status": "SUCCESS", "identity_status": record["identity_status"], "record": record}


def find_valid_cache(task, index):
    """Exact candidate URL and saved normal metadata, reparse with current V2."""
    for ref in index.get(task["normalized_product_url"], []):
        try:
            meta = json.loads(Path(ref["meta_path"]).read_text(encoding="utf8"))
            if (meta.get("status_code") != 200 or meta.get("access_state") != "NORMAL"
                    or normalize(meta.get("requested_url") or "") != task["normalized_product_url"]
                    or meta.get("recovered_from_challenge")):
                continue
            path = Path(ref["html_path"])
            html = path.read_text(encoding="utf8")
            if not inspect_delivery_location(html).is_spain:
                continue
            result = parse_candidate_detail(task, html, meta)
            if result["status"] == "SUCCESS":
                result.update(status="REUSED_VALID_CACHE", html_path=str(path), html_sha256=file_sha(path),
                              cache_metadata_path=ref["meta_path"], cache_metadata_sha256=file_sha(ref["meta_path"]))
                return result
        except (OSError, ValueError, KeyError):
            continue
    return None


def cache_index(roots):
    index = {}
    for root in roots:
        for p in sorted(Path(root).rglob("*.meta.json")):
            if "quarantine" in p.parts:
                continue
            try:
                m = json.loads(p.read_text(encoding="utf8"))
                url = m.get("requested_url")
                html = p.with_name(p.name[:-10] + ".html")
                if url and safe_url(url) and html.is_file():
                    index.setdefault(normalize(url), []).append({"html_path": str(html), "meta_path": str(p)})
            except (OSError, ValueError):
                continue
    return index


def create_plan(selected, *, run_id, selection_sha256, source_certificate_sha256, shared_gate_path):
    tasks = []
    for row in selected:
        request_url = row["absolute_product_url"]
        if not safe_url(request_url) or normalize(request_url) != row["normalized_product_url"]:
            raise ValueError("REAL_SNAPSHOT_REQUEST_URL_REQUIRED")
        tasks.append({k: row.get(k) for k in ("candidate_id", "selection_id", "raw_product_url",
            "normalized_product_url", "source_snapshot_id", "source_memberships", "selection_order")})
        tasks[-1].update(task_id="detail_" + digest([run_id, row["candidate_id"]])[:28],
                         request_url=request_url, task_type="PRODUCT_DETAIL_ES_RAW", status="PENDING",
                         retry_status="NOT_ATTEMPTED", evidence_path=None, run_id=run_id)
    if len({t["candidate_id"] for t in tasks}) != len(tasks):
        raise ValueError("DUPLICATE_CANDIDATE_TASK")
    # Freeze a source-diverse T1 prefix without changing selection ranking.
    prefix, remaining, covered = [], list(tasks), set()
    for family in ("BEST_SELLERS", "NEW_RELEASES", "CUSTOMER_FAVORITES"):
        chosen = next((t for t in remaining if any(m.get("source_type") == family for m in t["source_memberships"])), None)
        if chosen:
            prefix.append(chosen); remaining.remove(chosen)
            covered.update(m.get("source_id") for m in chosen["source_memberships"])
    for t in list(remaining):
        if len(prefix) >= 30:
            break
        ids = {m.get("source_id") for m in t["source_memberships"]} - {None}
        if ids - covered:
            prefix.append(t); remaining.remove(t); covered.update(ids)
    tasks = prefix + remaining
    for order, t in enumerate(tasks, 1):
        t["task_order"] = order
    return {"run_id": run_id, "bridge_version": BRIDGE_VERSION, "selection_sha256": selection_sha256,
            "source_certificate_sha256": source_certificate_sha256, "shared_gate_path": str(shared_gate_path),
            "tasks": tasks, "parallelism": 1, "request_start_interval_seconds": 3,
            "image_downloads": False, "translation": False}


class DetailBridge5500:
    def __init__(self, root, plan):
        self.root, self.plan = Path(root), plan
        self.root.mkdir(parents=True, exist_ok=True)
        immutable_json(self.root / "detail_plan_5500.json", plan)
        self.outcomes = {}
        for t in plan["tasks"]:
            p = self.root / "tasks" / t["task_id"] / "checkpoint.json"
            if p.exists():
                self.outcomes[t["task_id"]] = read_sealed(p)

    def progress(self):
        from .detail_budget5500 import uncertain_setup_page_holds
        held = uncertain_setup_page_holds(self.root, self.plan)
        outcomes = list(self.outcomes.values())
        counts = Counter(r["status"] for r in outcomes)
        success = sum(counts[s] for s in SUCCESS)
        pending = len(self.plan["tasks"]) - len(outcomes)
        receipts = list((self.root / "receipts").glob("*.json"))
        now = utc()
        payload = {"run_id": self.plan["run_id"], "updated_at": now, "total": len(self.plan["tasks"]),
            "success": success, "remaining": len(self.plan["tasks"]) - success,
            "completed_tasks": len(outcomes), "pending": pending, "statuses": dict(counts),
            "paid_requests": len(receipts), "conservative_budget_reservation": held,
            "effective_request_budget_used": len(receipts)+held, "cache_reused": counts["REUSED_VALID_CACHE"],
            "latest_success_at": max((r.get("saved_at", "") for r in outcomes if r["status"] in SUCCESS), default=None)}
        atomic_json(self.root / "detail_progress.json", payload)
        return payload

    def save_result(self, task, result):
        slot = self.root / "tasks" / task["task_id"]
        result = {**result, "task_id": task["task_id"], "candidate_id": task["candidate_id"],
                  "selection_id": task["selection_id"], "run_id": self.plan["run_id"], "saved_at": utc()}
        if result.get("record"):
            state = DetailState(slot / "details_state.json")
            state.update([result["record"]])
            state.save()
            immutable_json(slot / "parsed_detail.json", result["record"])
        atomic_json(slot / "checkpoint.json", sealed(result))
        self.outcomes[task["task_id"]] = result
        self.progress()

    def apply_caches(self, index):
        count = 0
        for task in self.plan["tasks"]:
            path = self.root / "tasks" / task["task_id"] / "checkpoint.json"
            if path.exists():
                continue
            cached = find_valid_cache(task, index)
            if cached:
                self.save_result(task, cached)
                count += 1
        return count

    def gates_normal(self):
        for p in (Path(self.plan["shared_gate_path"]), self.root / "access_gate.json"):
            if p.exists() and read_sealed(p).get("state") != "NORMAL":
                raise AccessStopError("DETAIL_ACCESS_GATE_NOT_NORMAL: " + str(p))
        if not Path(self.plan["shared_gate_path"]).is_file():
            raise AccessStopError("SHARED_ACCESS_GATE_REQUIRED")

    def validate_authorization(self, auth):
        if json.loads((self.root / "detail_plan_5500.json").read_text(encoding="utf8")) != self.plan:
            raise ValueError("IMMUTABLE_DETAIL_PLAN_MISMATCH")
        if (auth.get("approved") is not True or auth.get("scope") != "DETAIL_5500_INDEPENDENT"
                or auth.get("run_id") != self.plan["run_id"] or auth.get("plan_sha256") != digest(self.plan)
                or not auth.get("approved_by") or not auth.get("approval_id")
                or auth.get("runtime_root") != str(self.root.resolve())
                or not auth.get("code_sha") or not auth.get("ci_success_evidence")
                or not auth.get("offline_acceptance_reference")):
            raise ValueError("INDEPENDENT_DETAIL_AUTHORIZATION_REQUIRED")
        at = datetime.now(timezone.utc)
        if not datetime.fromisoformat(auth["starts_at"]) <= at < datetime.fromisoformat(auth.get("request_deadline") or auth["deadline"]):
            raise ValueError("DETAIL_AUTHORIZATION_OUTSIDE_TIME")
        if auth.get("delivery_setup_reference"):
            from .delivery_setup5500 import validate_amendment
            validate_amendment(self.root, self.plan, auth)
        for name in ("delivery_setup_verification_reference", "delivery_setup_continuation_reference",
                     "delivery_setup_repair_reference", "request_budget_reservation_reference", "post_UI_first_detail_recheck_reference",
                     "reviewed_identity_quarantines_reference"):
            if auth.get(name) and file_sha(auth[name]["path"]) != auth[name]["sha256"]:
                raise ValueError("DELIVERY_CONTROL_REFERENCE_CHANGED")
        from .detail_budget5500 import uncertain_setup_page_holds
        uncertain_setup_page_holds(self.root, self.plan, auth)
        if (auth["max_page_requests"] > auth["max_requests"] or
                (auth["max_other_requests"] != 0 and not auth.get("delivery_setup_reference"))
                or auth["request_start_interval_seconds"] < 3 or auth["max_attempts_per_task"] not in (1, 2)):
            raise ValueError("DETAIL_BUDGET_OR_PACING_INVALID")
        ref = auth["offline_acceptance_reference"]
        if file_sha(ref["path"]) != ref["sha256"]:
            raise ValueError("OFFLINE_ACCEPTANCE_SHA_MISMATCH")
        offline = json.loads(Path(ref["path"]).read_text(encoding="utf8"))
        if offline.get("passed") is not True or offline.get("code_sha") != auth["code_sha"]:
            raise ValueError("CURRENT_CODE_OFFLINE_ACCEPTANCE_REQUIRED")
        ci = auth["ci_success_evidence"]
        if file_sha(ci["path"]) != ci["sha256"]:
            raise ValueError("CI_EVIDENCE_SHA_MISMATCH")
        status = json.loads(Path(ci["path"]).read_text(encoding="utf8"))
        if status.get("headSha") != auth["code_sha"] or status.get("conclusion") != "success":
            raise ValueError("CURRENT_CODE_CI_SUCCESS_REQUIRED")
        self.gates_normal()

    def run(self, session, auth, *, until_completed, authorization_filename=None):
        """Caller owns BrowserSession. One shared live lock, one frozen queue.

        T1/T2 boundaries are explicit; separate stage acceptance must be saved
        before increasing the cumulative task limit. No successful task replay.
        """
        self.validate_authorization(auth)
        auth_name = ("authorization.delivery_location_post_UI_recheck.approved.json" if auth.get("post_UI_first_detail_recheck_reference") else
                     "authorization.delivery_location_repair_3.approved.json" if auth.get("delivery_setup_repair_reference") else
                     "authorization.delivery_location_verification_1.approved.json" if auth.get("delivery_setup_verification_reference") else
                     "authorization.delivery_location_2.approved.json" if auth.get("delivery_setup_continuation_reference") else
                     "authorization.delivery_location_1.approved.json" if auth.get("delivery_setup_reference") else "authorization.approved.json")
        if authorization_filename is not None:
            if (Path(authorization_filename).name != authorization_filename
                    or not authorization_filename.startswith("authorization.")
                    or not authorization_filename.endswith(".approved.json")):
                raise ValueError("AUTHORIZATION_REVISION_FILENAME_INVALID")
            auth_name = authorization_filename
        immutable_json(self.root / auth_name, auth)
        if until_completed > 30:
            self.validate_stage("T1", 30, auth=auth)
        if until_completed > 100:
            self.validate_stage("T2", 100, auth=auth)
        from .identity_quarantine5500 import reviewed_identity_quarantines
        quarantines = reviewed_identity_quarantines(self.root, self.plan, auth)
        with file_lock(self.root / "collector.lock"), file_lock(Path(self.plan["shared_gate_path"]).parent / "highspeed_live.lock"):
            reservations = [read_sealed(p) for p in (self.root / "receipts").glob("*.json")]
            scope = self.plan["tasks"] if quarantines and until_completed > 30 else self.plan["tasks"][:until_completed]
            for task in scope:
                if task["task_id"] in quarantines:
                    continue  # Original failed checkpoint remains immutable and does not count as success.
                if quarantines and until_completed > 30 and sum(o["status"] in SUCCESS for o in self.outcomes.values()) >= until_completed:
                    break
                slot = self.root / "tasks" / task["task_id"]
                checkpoint = slot / "checkpoint.json"
                attempt = 1
                if checkpoint.exists():
                    # Failures require explicit retry action; never auto-success.
                    old = read_sealed(checkpoint)
                    if old["status"] in SUCCESS:
                        self.verify_saved_success(task, old)
                        continue
                    from .delivery_setup5500 import verify_setup
                    if auth.get("post_UI_first_detail_recheck_reference") and not (self.root / "delivery_setup.complete.json").exists():
                        from .delivery_post_UI5500 import validate_post_UI_first_detail_recheck
                        a = validate_post_UI_first_detail_recheck(self.root, self.plan, auth)
                    else:
                        a = verify_setup(self.root, self.plan, auth) if auth.get("delivery_setup_reference") else {}
                    if (a.get("task_id") != task["task_id"] or old.get("status") != "IDENTITY_REVIEW"
                            or old.get("reason") != "SPAIN_DELIVERY_LOCATION_NOT_VERIFIED"
                            or old.get("receipt_path") != a["original_failed_receipt_reference"]["path"]
                            or (self.root / "receipts" / (task["task_id"]+"_2.json")).exists()):
                        raise ValueError("UNRESOLVED_TASK_BLOCKS_STAGE: " + task["task_id"])
                    immutable_bytes(slot / "checkpoint.attempt_1.preserved.json", checkpoint.read_bytes())
                    attempt = 2
                self.validate_authorization(auth)
                from .detail_budget5500 import uncertain_setup_page_holds
                held = uncertain_setup_page_holds(self.root, self.plan, auth)
                if (len(reservations)+held >= auth["max_requests"] or
                        sum(r.get("kind", "PAGE") == "PAGE" for r in reservations)+held >= auth["max_page_requests"]):
                    raise ValueError("DETAIL_CUMULATIVE_BUDGET_EXHAUSTED")
                if any(r["state"] == "RESERVED" for r in reservations):
                    raise ValueError("AMBIGUOUS_IN_FLIGHT_REQUEST_REQUIRES_REVIEW")
                if reservations:
                    last = max(datetime.fromisoformat(r["started_at"]).timestamp() for r in reservations)
                    time.sleep(max(0, last + auth["request_start_interval_seconds"] - time.time()))
                self.validate_authorization(auth)
                receipt = {"run_id": self.plan["run_id"], "task_id": task["task_id"], "kind": "PAGE",
                    "request_url": task["request_url"], "started_at": utc(), "state": "RESERVED",
                    "plan_sha256": digest(self.plan), "authorization_sha256": digest(auth)}
                receipt.update(request_role="DETAIL", attempt=attempt)
                rp = self.root / "receipts" / (task["task_id"] + "_" + str(attempt) + ".json")
                immutable_json(rp, sealed(receipt))
                atomic_json(checkpoint, sealed({"status": "RUNNING", "task_id": task["task_id"],
                    "candidate_id": task["candidate_id"], "run_id": self.plan["run_id"], "receipt_path": str(rp)}))
                html, status, error = "", None, None
                if hasattr(session, "document_responses"):
                    session.document_responses.clear()
                try:
                    status = session.goto(task["request_url"])
                    # Inspect restrictions immediately, before ordinary waits.
                    html = session.page.content()
                    if detect_access_status(status, html).value == "NORMAL":
                        session.wait_for_product_page()
                        session.wait_for_price_text()
                        html = session.page.content()
                except Exception as exc:
                    error = type(exc).__name__ + ": " + str(exc)
                    try:
                        html = session.page.content()
                    except Exception:
                        pass
                stamp = utc()
                body = html.encode("utf8")
                hp = slot / "html" / (hashlib_sha(body) + ".html")
                immutable_bytes(hp, body)
                final = str(getattr(session.page, "url", "") or "")
                access = detect_access_status(status, html).value
                history = list(getattr(session, "document_responses", []))
                restricted = next((r for r in history if r["status_code"] in {403, 429}), None)
                if restricted:
                    access = detect_access_status(restricted["status_code"], html).value
                if error:
                    if not restricted:
                        access = "UNKNOWN"  # Preserve uncertainty; do not auto-retry a safety unknown.
                receipt.update(state=access, status_code=status, final_url=final, finished_at=stamp,
                    html_path=str(hp), html_sha256=file_sha(hp), error=error, document_responses=history)
                atomic_json(rp, sealed(receipt), backup=True)
                reservations.append(receipt)
                meta = {"status_code": status, "requested_url": task["request_url"], "final_url": final,
                        "access_state": access, "collected_at": stamp, "receipt_path": str(rp)}
                mp = slot / ("response_metadata.json" if attempt == 1 else "response_metadata.attempt_2.json")
                immutable_json(mp, meta)
                if access != "NORMAL":
                    gate = {"state": access, "run_id": self.plan["run_id"], "receipt_path": str(rp), "observed_at": stamp}
                    atomic_json(self.root / "access_gate.json", sealed(gate), backup=True)
                    old = read_sealed(self.plan["shared_gate_path"])
                    atomic_json(self.plan["shared_gate_path"], sealed({**gate, "previous_gate": old}), backup=True)
                    self.save_result(task, {"status": "ACCESS_STOP", "access_state": access, "receipt_path": str(rp)})
                    raise AccessStopError("DETAIL_ACCESS_STOP " + access)
                result = parse_candidate_detail(task, html, meta)
                result.update(html_path=str(hp), html_sha256=file_sha(hp), receipt_path=str(rp),
                              receipt_sha256=file_sha(rp), response_metadata_path=str(mp),
                              response_metadata_sha256=file_sha(mp))
                location = inspect_delivery_location(html)
                if location.is_spain is not True or (auth.get("delivery_setup_reference") and location.postal_code != "28001"):
                    result = {**result, "status": "IDENTITY_REVIEW", "record": None,
                              "reason": "SPAIN_DELIVERY_LOCATION_NOT_VERIFIED", "delivery_text": location.text}
                self.save_result(task, result)
                if result["status"] != "SUCCESS":
                    raise ValueError("DETAIL_SYSTEMATIC_SAFETY_REVIEW_REQUIRED: " + result["status"])
        return self.progress()

    def verify_saved_success(self, task, result):
        if file_sha(result["html_path"]) != result["html_sha256"]:
            raise ValueError("SUCCESS_EVIDENCE_SHA_MISMATCH")
        if result["status"] == "REUSED_VALID_CACHE":
            if file_sha(result["cache_metadata_path"]) != result["cache_metadata_sha256"]:
                raise ValueError("CACHE_METADATA_SHA_MISMATCH")
        else:
            if file_sha(result["receipt_path"]) != result["receipt_sha256"]:
                raise ValueError("SUCCESS_RECEIPT_SHA_MISMATCH")
        if result["candidate_id"] != task["candidate_id"] or result["run_id"] != self.plan["run_id"]:
            raise ValueError("SUCCESS_TASK_IDENTITY_MISMATCH")

    def validate_stage(self, name, count, auth=None):
        proof = read_sealed(self.root / (name + "_acceptance.json"))
        if proof.get("passed") is not True or proof.get("plan_sha256") != digest(self.plan):
            raise ValueError("PRIOR_STAGE_ACCEPTANCE_REQUIRED")
        from .identity_quarantine5500 import stage_tasks
        tasks, quarantines = stage_tasks(self, name, count, auth)
        if proof.get("reviewed_identity_quarantines_reference"):
            prior_ref = proof["reviewed_identity_quarantines_reference"]
            if prior_ref != (auth or {}).get("reviewed_identity_quarantines_reference"):
                # Later isolated failures outside this already audited cohort
                # must not rewrite its proof or replace any accepted member.
                from .identity_quarantine5500 import reviewed_identity_quarantines
                prior = reviewed_identity_quarantines(self.root, self.plan,
                    {**(auth or {}), "reviewed_identity_quarantines_reference": prior_ref})
                if not prior or any(quarantines.get(k) != v for k, v in prior.items()):
                    raise ValueError("STAGE_IDENTITY_REVIEW_HISTORY_CHANGED")
            if (proof.get("successful_task_ids") != [t["task_id"] for t in tasks]
                    or proof.get("success_count") != count):
                raise ValueError("STAGE_TRUE_SUCCESS_SCOPE_MISMATCH")
        elif quarantines and name == "T2":
            raise ValueError("STAGE_REVIEWED_FAILURE_SCOPE_REQUIRED")
        for task in tasks:
            p = self.root / "tasks" / task["task_id"] / "checkpoint.json"
            outcome = read_sealed(p)
            if outcome["status"] not in SUCCESS:
                raise ValueError("PRIOR_STAGE_NOT_SUCCESSFUL")
            self.verify_saved_success(task, outcome)
            if proof["checkpoint_sha256"].get(task["task_id"]) != file_sha(p):
                raise ValueError("STAGE_CHECKPOINT_SHA_MISMATCH")


def hashlib_sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()
