"""Deterministic V4 quick selection. Offline; never changes the input snapshot."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from ..multisource.storage import digest, immutable_json, immutable_bytes, read_sealed
from ..highspeed.v3_links import normalize_product_url_v3
from ..highspeed.response_alias import authorize_v4_response
from ..highspeed.budgets import aware_time

RULE_VERSION = "quick_selection_v0.1"
FAMILIES = {"BEST_SELLERS", "NEW_RELEASES", "CUSTOMER_FAVORITES"}


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def structured(row, key, default=None):
    value = row.get(key)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value if value is not None else default


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def audit_snapshot(directory, campaign):
    from ..highspeed.v4_authorization import prerequisite_review_session
    with prerequisite_review_session():
        return _audit_snapshot(directory, campaign)


def _audit_snapshot(directory, campaign):
    """Certify individual capture chains, quarantining historical failed scope.

    The old global QA failure remains a failure. This is a new, narrower input
    certificate, not an override of QA_FAILED_DO_NOT_USE on the old CSV.
    """
    directory, campaign = Path(directory), Path(campaign)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf8"))
    independent = json.loads((directory / "独立文件验收.json").read_text(encoding="utf8"))
    if (independent["manifest_SHA"] != file_sha(directory / "manifest.json")
            or not independent["central_DOM_passed"]
            or not independent["all_CSV_URL_group_membership_and_latest_paired_fields_match_raw"]):
        raise ValueError("INDEPENDENT_SNAPSHOT_BINDING_FAILED")
    for ref in manifest["files"]:
        if file_sha(directory / ref["name"]) != ref["sha256"]:
            raise ValueError("SNAPSHOT_FILE_SHA_MISMATCH")
    execution = manifest["execution"]
    plan = execution["plan"]
    run = campaign / "store" / "runs" / plan["run_id"]
    if read_sealed(run / "plan.json") != plan:
        raise ValueError("SNAPSHOT_PLAN_MISMATCH")
    sources = {s["source_id"]: s for s in plan["sources"]}
    approvals = []
    for ref in execution["authorization_references"]:
        if file_sha(ref["path"]) != ref["sha256"]:
            raise ValueError("HISTORICAL_AUTH_SHA_MISMATCH")
        approvals.append(json.loads(Path(ref["path"]).read_text(encoding="utf8")))
    receipts, rejected = {}, []
    for path in sorted((run / "responses").glob("*.json")):
        r = read_sealed(path)
        q = read_sealed(run / "requests" / (r["request_id"] + ".reserved.json"))
        reason = None
        try:
            if (r["run_id"] != plan["run_id"] or r["origin"] != "LIVE_CAPTURE"
                    or r["status_code"] != 200 or r["access_state"] != "NORMAL"
                    or q["plan_sha256"] != digest(plan) or q["url"] != r["request_url"]
                    or any(q[k] != r[k] for k in ("run_id", "source_id", "kind", "observed_at"))
                    or file_sha(r["html_path"]) != r["sha256"]):
                raise ValueError("NON_NORMAL_OR_UNBOUND_RECEIPT")
            allowed = False
            for auth in approvals:
                if not aware_time(auth["reviewed_at"]) <= aware_time(q["observed_at"]) < aware_time(auth["expires_at"]):
                    continue
                try:
                    authorize_v4_response(r, sources[r["source_id"]], auth)
                    allowed = True
                    break
                except (ValueError, KeyError):
                    continue
            if not allowed:
                raise ValueError("REQUEST_NOT_IN_APPROVED_SCOPE")
        except (ValueError, OSError, KeyError) as exc:
            reason = str(exc)
            rejected.append({"request_id": r["request_id"], "reason": reason,
                             "receipt_path": str(path), "source_id": r.get("source_id")})
        receipts[str(path.resolve())] = (r, reason)
    # Only observations of fully traceable normal capture frames enter the new
    # certificate. Any failed constituent rejects that frame, not the whole run.
    certified = set()
    frame_rejections = Counter()
    for path in sorted((run / "pages").glob("*.json")):
        capture = read_sealed(path)
        page = capture["page"]
        try:
            ref = page["capture_receipt_reference"]
            if file_sha(ref["path"]) != ref["sha256"]:
                raise ValueError("CAPTURE_RECEIPT_SHA_MISMATCH")
            r, error = receipts[str(Path(ref["path"]).resolve())]
            if error or r["source_id"] != page["source_id"]:
                raise ValueError("FRAME_RECEIPT_REJECTED")
            if (page.get("source_type") not in FAMILIES or not page.get("source_context_valid")
                    or page.get("access_status") != "NORMAL"):
                raise ValueError("FRAME_SOURCE_NOT_VERIFIED")
            for c in page.get("acp_captures", []):
                # Exact receipt reference naming is shared with the V4 parser.
                nr, ne = receipts[str((run / "responses" / (c["request_id"] + ".json")).resolve())]
                if (ne or nr["source_id"] != page["source_id"] or nr["sha256"] != c["sha256"]
                        or nr["request_url"] != c["request_url"] or nr["observed_at"] != c["observed_at"]):
                    raise ValueError("NATIVE_FRAME_REJECTED")
            for row in capture["observations"]:
                if file_sha(row["evidence_path"]) != row["evidence_sha256"]:
                    raise ValueError("OBSERVATION_BODY_SHA_MISMATCH")
            certified.update(r["observation_id"] for r in capture["observations"])
        except (KeyError, ValueError, OSError, TypeError) as exc:
            frame_rejections[str(exc)] += 1
    return {"source_snapshot_id": manifest["run_id"], "manifest_sha256": file_sha(directory / "manifest.json"),
            "original_snapshot_accepted": manifest["accepted"],
            "original_QA_not_overridden": True, "certified_observation_ids": sorted(certified),
            "rejected_receipts": rejected, "rejected_frames": dict(frame_rejections),
            "method": "HASH_BOUND_INDEPENDENT_DOM_REVIEW_PLUS_INDIVIDUAL_NORMAL_AUTHORIZED_CAPTURE_CHAINS"}


def select_candidates(rows, certificate, *, selected_count=5500, reserve_count=1000):
    good_ids = set(certificate["certified_observation_ids"])
    eligible, review, excluded = [], [], []
    context_positions = defaultdict(set)
    for row in rows:
        for m in structured(row, "source_memberships", []):
            n = number(m.get("ranking_position"))
            if n is not None and n > 0:
                context_positions[(m.get("source_type"), m.get("rank_context"))].add(n)
    for source_row in rows:
        row = dict(source_row)
        reasons = []
        memberships = structured(row, "source_memberships", [])
        if (row.get("observation_id") not in good_ids or not memberships
                or any(m.get("observation_id") not in good_ids for m in memberships)):
            reasons.append("SOURCE_CAPTURE_NOT_IN_NEW_CERTIFICATE")
        norm = normalize_product_url_v3(row.get("absolute_product_url") or row.get("raw_product_url", ""))
        if not norm.get("is_valid") or norm.get("normalized_product_url") != row.get("normalized_product_url"):
            reasons.append("INVALID_OR_CONFLICTING_PRODUCT_URL")
        if str(row.get("url_identity_status", "")) in {"IDENTITY_CONFLICT", "INVALID_PRODUCT_LINK"}:
            reasons.append("IDENTITY_CONFLICT")
        if str(row.get("eligible_for_target", "True")).lower() == "false" or str(row.get("source_evidence_eligible", "True")).lower() == "false":
            reasons.append("SOURCE_NOT_ELIGIBLE_FOR_CONSERVATIVE_GROUP")
        # Source route/explicit category evidence only; never title heuristics.
        routes = [urlsplit(m.get("source_url", "")).path.lower() for m in memberships]
        if routes and all(any(f"/{part}/" in route + "/" for part in
                         ("books", "digital-text", "gift-cards", "mobile-apps", "software")) for route in routes):
            reasons.append("EXPLICIT_NON_PHYSICAL_SOURCE")
        value = number(row.get("rating_value"))
        valid_rating = row.get("rating_status") == "VALID" and value is not None and 0 <= value <= 5
        conflicts = structured(row, "observation_conflicts", {}) or {}
        if any("rating" in str(k).lower() and v for k, v in conflicts.items()):
            valid_rating = False
        if valid_rating and value < 3:
            reasons.append("CONFIRMED_RATING_BELOW_3")
        if reasons:
            excluded.append({"candidate_id": row.get("candidate_id"), "reasons": reasons})
            continue
        row["source_memberships"] = memberships
        row["amazon_category_path"] = structured(row, "amazon_category_path", [])
        row["rating_value"] = value if valid_rating else None
        count = number(row.get("rating_count")) if row.get("rating_count_status") == "VALID" else None
        row["rating_count"] = int(count) if count is not None and count >= 0 and count.is_integer() else None
        monthly = number(row.get("monthly_bought_min"))
        row["monthly_bought_min"] = int(monthly) if monthly is not None and monthly >= 0 and monthly.is_integer() else None
        # Ranks compared only as percentiles within the same source context.
        ranks = [(sum(p >= number(m["ranking_position"]) for p in
                      context_positions[(m.get("source_type"), m.get("rank_context"))]) /
                  len(context_positions[(m.get("source_type"), m.get("rank_context"))])) for m in memberships
                 if m.get("ranking_semantics") in {"BESTSELLER_SALES_RANKING", "NEW_RELEASE_SALES_RANKING"}
                 and number(m.get("ranking_position")) is not None and number(m["ranking_position"]) > 0]
        row["selection_score"] = [value if valid_rating else -1, row["rating_count"] or 0,
                                  len({m["source_type"] for m in memberships}),
                                  max(ranks, default=0), number(row.get("monthly_bought_min")) or 0]
        row["selection_reason"] = "VALID_RATING_FIRST;COUNT_CONFIDENCE;CROSS_SOURCE;WITHIN_CONTEXT_RANK;MONTHLY_LOWER_BOUND"
        row["identity_status"] = row.get("url_identity_status") or "IDENTITY_UNCONFIRMED"
        row["asin_if_verified"] = None
        row["source_snapshot_id"] = certificate["source_snapshot_id"]
        (eligible if valid_rating else review).append(row)
    eligible.sort(key=lambda r: (tuple(-x for x in r["selection_score"]), r["normalized_product_url"], r["candidate_id"]))
    review.sort(key=lambda r: (r["normalized_product_url"], r["candidate_id"]))
    unique, seen, duplicates = [], set(), []
    for row in eligible:
        key = row.get("target_review_group") or row.get("target_group_key") or row["normalized_product_url"]
        if key in seen:
            duplicates.append({"candidate_id": row["candidate_id"], "reason": "EXISTING_CONSERVATIVE_GROUP"})
            continue
        seen.add(key)
        row["selection_order"] = len(unique) + 1
        row["selection_id"] = "sel_" + digest([RULE_VERSION, certificate["manifest_sha256"], row["candidate_id"]])[:28]
        unique.append(row)
    report = {"rule_version": RULE_VERSION, "input_count": len(rows), "qualified_unique": len(unique),
              "selected_count": min(len(unique), selected_count), "required_selected": selected_count,
              "shortfall": max(0, selected_count - len(unique)), "review_missing_or_conflicting_rating": len(review),
              "excluded_count": len(excluded), "duplicate_group_count": len(duplicates),
              "exclusion_reasons": dict(Counter(reason for r in excluded for reason in r["reasons"])),
              "rank_policy": "percentile among observed positions within the same source context; tie-breaker only, never global BSR",
              "missing_policy": "missing/conflicting rating remains review; not treated as below 3; not auto-selected",
              "tie_break": "normalized URL then candidate ID; stable Unicode lexical order",
              "source_certificate_sha256": digest(certificate)}
    return unique[:selected_count], unique[selected_count:selected_count + reserve_count], report, review, excluded + duplicates


def write_csv(path, rows, fields):
    import io
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: json.dumps(row.get(k), ensure_ascii=False) if isinstance(row.get(k), (dict, list))
                         else row.get(k) for k in fields})
    immutable_bytes(path, stream.getvalue().encode("utf-8-sig"))


SELECTION_FIELDS = ["selection_id", "candidate_id", "selection_order", "product_title_es", "raw_product_url",
    "absolute_product_url", "normalized_product_url", "asin_if_verified", "source_memberships", "amazon_category_path",
    "rating_value", "rating_count", "monthly_bought_min", "selection_score", "selection_reason", "identity_status",
    "source_snapshot_id", "observation_id", "evidence_path", "evidence_sha256", "target_review_group"]
