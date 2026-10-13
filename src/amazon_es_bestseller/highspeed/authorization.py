"""Explicit stage-scoped human approval, checked before every V2 request."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlsplit
from ..access.detector import detect_access_status
from ..multisource.storage import read_sealed
from .budgets import LIMIT_FIELDS, aware_time, validate_plan
from .links import normalize_product_url, candidate_id

STOP_CONDITIONS = {"CHALLENGE", "BLOCKED", "RATE_LIMITED", "UNKNOWN", "NETWORK_ERROR"}

def amazon_url(url):
    try:
        item = urlsplit(str(url))
        return item.scheme == "https" and item.hostname == "www.amazon.es" and not item.username and not item.password and item.port in {None, 443}
    except ValueError:
        return False

def _verified_reference(reference, error):
    if not isinstance(reference, dict) or not reference.get("path") or not reference.get("sha256"):
        raise ValueError(error + "_REQUIRED")
    path = Path(reference["path"])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise ValueError(error + "_HASH_MISMATCH")
    return path, raw

def _verified_json(reference, error):
    path, raw = _verified_reference(reference, error)
    record = json.loads(raw)
    return read_sealed(path) if "payload" in record else record

def _verified_rows(reference, error):
    _, raw = _verified_reference(reference, error)
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]

def reviewed_target_count(candidates, recomputed, run_id):
    """Rebuild review projection from immutable proofs, never exported labels."""
    from .reviews import validate_destination_review
    lookup = {c["candidate_id"]: c for c in recomputed}
    reviews, journals = {}, {}
    for candidate in candidates:
        path, sha = candidate.get("review_journal_path"), candidate.get("review_journal_sha256")
        if not path and not sha:
            if str(candidate.get("url_identity_status", "")).startswith("VERIFIED_"):
                raise ValueError("PRIOR_STAGE_REVIEW_JOURNAL_REQUIRED")
            continue
        if path in journals:
            if journals[path] != sha:
                raise ValueError("PRIOR_STAGE_REVIEW_JOURNAL_CONFLICT")
            continue
        journal = _verified_json({"path": path, "sha256": sha}, "PRIOR_STAGE_REVIEW_JOURNAL")
        if journal.get("run_id") != run_id:
            raise ValueError("PRIOR_STAGE_REVIEW_RUN_CONTEXT_MISMATCH")
        projection = validate_destination_review(journal["review"], lookup)
        if projection != journal.get("projection") or set(reviews) & set(projection):
            raise ValueError("PRIOR_STAGE_REVIEW_PROJECTION_CONFLICT")
        reviews.update(projection)
        journals[path] = sha
    parent = {c["possible_duplicate_key"] or c["normalized_product_url"]: c["possible_duplicate_key"] or c["normalized_product_url"] for c in recomputed}
    by_url = {c["normalized_product_url"]: c for c in recomputed}
    selected_by_destination = {}
    for proof in reviews.values():
        destination = proof["final_product_url"]
        selected = json.dumps(proof["review_evidence"].get("selection_evidence"), sort_keys=True, ensure_ascii=False)
        if destination in selected_by_destination and selected_by_destination[destination] != selected:
            raise ValueError("PRIOR_STAGE_REVIEW_SELECTED_IDENTITY_CONFLICT")
        selected_by_destination[destination] = selected
    def find(key):
        parent.setdefault(key, key)
        if parent[key] != key:
            parent[key] = find(parent[key])
        return parent[key]
    for candidate in recomputed:
        proof = reviews.get(candidate["candidate_id"])
        if proof and proof["url_identity_status"] == "VERIFIED_SAME_DESTINATION":
            base = candidate["possible_duplicate_key"] or candidate["normalized_product_url"]
            key = "verified_destination:"+proof["verified_destination_key"]
            parent[find(base)] = find(key)
            direct = by_url.get(proof["final_product_url"])
            if direct:
                parent[find(direct["possible_duplicate_key"] or direct["normalized_product_url"])] = find(key)
        elif proof and proof["url_identity_status"] == "VERIFIED_INDEPENDENT_PURCHASABLE":
            direct = by_url.get(proof["final_product_url"])
            if direct:
                parent[find(direct["possible_duplicate_key"] or direct["normalized_product_url"])] = find(candidate["possible_duplicate_key"] or candidate["normalized_product_url"])
    independent = {}
    for candidate in recomputed:
        proof = reviews.get(candidate["candidate_id"])
        if proof and proof["url_identity_status"] == "VERIFIED_INDEPENDENT_PURCHASABLE":
            base = find(candidate["possible_duplicate_key"] or candidate["normalized_product_url"])
            independent.setdefault(base, []).append(candidate["candidate_id"])
    groups = set()
    for candidate in recomputed:
        cid = candidate["candidate_id"]
        base = find(candidate["possible_duplicate_key"] or candidate["normalized_product_url"])
        proof = reviews.get(cid)
        key = cid if proof and proof["url_identity_status"] == "VERIFIED_INDEPENDENT_PURCHASABLE" else min(independent[base]) if base in independent else base
        groups.add(key)
    return len(groups)

def _artifact_link_pool(record, expected_stage):
    manifest = _verified_json(record.get("manifest"), "PRIOR_STAGE_MANIFEST")
    quality = _verified_json(record.get("quality_report"), "PRIOR_STAGE_QUALITY_REPORT")
    observations = _verified_rows(record.get("observations"), "PRIOR_STAGE_OBSERVATIONS")
    candidates = _verified_rows(record.get("candidates"), "PRIOR_STAGE_CANDIDATES")
    plan = manifest.get("plan", {})
    if (manifest.get("run_id") != record.get("run_id") or manifest.get("stage", plan.get("stage")) != expected_stage
            or manifest.get("origin") != record["origin"] or plan.get("run_id") != record.get("run_id")):
        raise ValueError("PRIOR_STAGE_MANIFEST_CONTEXT_MISMATCH")
    validate_plan(plan)
    if quality.get("gate_passed") is not True or quality.get("run_id") != record["run_id"]:
        raise ValueError("PRIOR_STAGE_QUALITY_CONTEXT_MISMATCH")
    source_map = {s["source_id"]: s for s in plan["sources"]}
    actual_urls, source_types = set(), set()
    for observation in observations:
        if observation.get("run_id") != record["run_id"] or observation.get("origin") != record["origin"]:
            raise ValueError("PRIOR_STAGE_OBSERVATION_CONTEXT_MISMATCH")
        source = source_map.get(observation.get("source_id"))
        if not source or observation.get("source_type") != source["source_type"]:
            raise ValueError("PRIOR_STAGE_OBSERVATION_SOURCE_MISMATCH")
        link = normalize_product_url(observation.get("raw_product_url"), base_url=observation.get("page_url", source["page_urls"][0]))
        if link["is_valid"]:
            actual_urls.add(link["normalized_product_url"])
            source_types.add(source["source_type"])
    recorded_urls, recomputed_candidates = set(), []
    for candidate in candidates:
        url = candidate.get("normalized_product_url")
        if not url or candidate.get("candidate_id") != candidate_id(url) or url in recorded_urls:
            raise ValueError("PRIOR_STAGE_CANDIDATE_ID_OR_DUPLICATE_INVALID")
        recorded_urls.add(url)
        link = normalize_product_url(url)
        if not link["is_valid"] or link["normalized_product_url"] != url:
            raise ValueError("PRIOR_STAGE_NONCANONICAL_CANDIDATE_URL")
        recomputed_candidates.append({"candidate_id": candidate_id(url), "normalized_product_url": url,
            "possible_duplicate_key": link["possible_duplicate_key"]})
    if actual_urls != recorded_urls:
        raise ValueError("PRIOR_STAGE_CANDIDATE_OBSERVATION_POOL_MISMATCH")
    # Conservative target accounting groups unverified variant parameters.
    actual_count = reviewed_target_count(candidates, recomputed_candidates, record["run_id"])
    if actual_count <= 0 or actual_count != record.get("unique_valid_links") or quality.get("unique_valid_links") != actual_count:
        raise ValueError("PRIOR_STAGE_DECLARED_LINK_COUNT_MISMATCH")
    if expected_stage != "T0" and source_types != {"BEST_SELLERS", "MOVERS_SHAKERS", "NEW_RELEASES", "MOST_WISHED_FOR", "MOST_GIFTED"}:
        raise ValueError("PRIOR_STAGE_FIVE_LIST_VALID_CARD_BREADTH_REQUIRED")
    return manifest, observations, actual_urls

def verified_stage_artifact(reference, expected_stage):
    path = Path(reference["path"])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference.get("sha256"):
        raise ValueError("PRIOR_STAGE_HASH_MISMATCH")
    record = json.loads(raw)
    if "payload" in record:
        record = read_sealed(path)
    if record.get("stage") != expected_stage or record.get("passed") is not True:
        raise ValueError("PRIOR_STAGE_NOT_PASSED:" + expected_stage)
    if record.get("origin") not in ({"OFFLINE_FIXTURE", "HISTORICAL_REPLAY"} if expected_stage == "T0" else {"LIVE_CAPTURE"}):
        raise ValueError("PRIOR_STAGE_ORIGIN_MISMATCH")
    artifacts = record.get("artifacts")
    if not artifacts:
        raise ValueError("PRIOR_STAGE_ARTIFACTS_REQUIRED")
    for item in artifacts:
        evidence = Path(item["path"])
        if hashlib.sha256(evidence.read_bytes()).hexdigest() != item.get("sha256"):
            raise ValueError("PRIOR_STAGE_ARTIFACT_HASH_MISMATCH")
    if record.get("quality_gate_passed") is not True or record.get("unique_valid_links", 0) <= 0:
        raise ValueError("PRIOR_STAGE_QUALITY_OR_VALID_LINKS_REQUIRED")
    manifest, observations, actual_urls = _artifact_link_pool(record, expected_stage)
    if expected_stage != "T0":
        receipts = record.get("request_receipts", [])
        if not receipts:
            raise ValueError("PRIOR_STAGE_LIVE_RECEIPTS_REQUIRED")
        source_map = {s["source_id"]: s for s in manifest["plan"]["sources"]}
        stage_approval = _verified_json(record.get("authorization"), "PRIOR_STAGE_AUTHORIZATION")
        if stage_approval.get("approved") is not True or stage_approval.get("stage") != expected_stage or stage_approval.get("run_id") != record["run_id"] or not stage_approval.get("approved_by") or not stage_approval.get("approval_id"):
            raise ValueError("PRIOR_STAGE_APPROVAL_CONTEXT_INVALID")
        if not manifest.get("store_root") or stage_approval.get("store_root") != str(Path(manifest["store_root"]).resolve()):
            raise ValueError("PRIOR_STAGE_APPROVAL_STORE_CONTEXT_INVALID")
        received, parsed_urls, parsed_slots = [], set(), set()
        for item in receipts:
            raw_receipt = Path(item["path"]).read_bytes()
            if hashlib.sha256(raw_receipt).hexdigest() != item.get("sha256"):
                raise ValueError("PRIOR_STAGE_RECEIPT_HASH_MISMATCH")
            receipt = json.loads(raw_receipt)
            if "payload" in receipt:
                receipt = read_sealed(item["path"])
            if receipt.get("origin") != "LIVE_CAPTURE" or receipt.get("access_state") != "NORMAL" or receipt.get("status_code") != 200:
                raise ValueError("PRIOR_STAGE_LIVE_RECEIPT_INVALID")
            aware_time(receipt["observed_at"])
            if not amazon_url(receipt.get("request_url")) or not amazon_url(receipt.get("final_url")):
                raise ValueError("PRIOR_STAGE_LIVE_RECEIPT_URL_INVALID")
            html_path = receipt.get("html_path")
            html_sha = receipt.get("html_sha256") or receipt.get("sha256")
            if not html_path or not html_sha:
                raise ValueError("PRIOR_STAGE_RECEIPT_BODY_REQUIRED")
            body = Path(html_path).read_bytes()
            if hashlib.sha256(body).hexdigest() != html_sha:
                raise ValueError("PRIOR_STAGE_RECEIPT_BODY_HASH_MISMATCH")
            if detect_access_status(receipt["status_code"], body.decode("utf-8", errors="replace")).value != "NORMAL":
                raise ValueError("PRIOR_STAGE_RECEIPT_BODY_RESTRICTED")
            source = source_map.get(receipt.get("source_id"))
            if not source or receipt.get("run_id") != record["run_id"] or receipt.get("source_type") != source["source_type"]:
                raise ValueError("PRIOR_STAGE_RECEIPT_SOURCE_CONTEXT_INVALID")
            scope = stage_approval.get("sources", {}).get(source["source_id"], {})
            if scope.get("source_type") != source["source_type"] or scope.get("research_category") != source.get("research_category"):
                raise ValueError("PRIOR_STAGE_APPROVED_SOURCE_MAPPING_MISMATCH")
            proof = scope.get("evidence", {})
            if proof.get("reviewed") is not True:
                raise ValueError("PRIOR_STAGE_SOURCE_PROOF_NOT_REVIEWED")
            _verified_reference(proof, "PRIOR_STAGE_SOURCE_PROOF")
            observed = aware_time(receipt["observed_at"])
            if not aware_time(stage_approval["reviewed_at"]) <= observed < aware_time(stage_approval["expires_at"]):
                raise ValueError("PRIOR_STAGE_RECEIPT_OUTSIDE_APPROVAL_WINDOW")
            authorize_request(receipt["request_url"], source, stage_approval, kind=receipt.get("kind", "PAGE"),
                descriptor={"method": receipt.get("request_method"), "payload": receipt.get("request_payload")})
            authorize_request(receipt["final_url"], source, stage_approval, kind=receipt.get("kind", "PAGE"),
                descriptor={"method": receipt.get("request_method"), "payload": receipt.get("request_payload")})
            received.append((receipt, body.decode("utf-8", errors="replace"), source))
        from .adapters import parse_page
        for receipt, body, source in received:
            if receipt.get("kind", "PAGE") != "PAGE":
                continue
            acp = [{**r, "body": b, "evidence_path": r["html_path"]} for r,b,s in received
                if r.get("kind") == "ACP" and r.get("page_key") == receipt.get("page_key") and s["source_id"] == source["source_id"]]
            result = parse_page(body, source=source, run_id=record["run_id"], page_url=receipt["request_url"],
                page_number=source["page_urls"].index(receipt["request_url"])+1,
                observed_at=receipt["observed_at"], evidence_path=receipt["html_path"],
                origin="LIVE_CAPTURE", http_status=receipt["status_code"], acp_responses=acp)
            if result["page"].get("status") != "COMPLETE":
                raise ValueError("PRIOR_STAGE_LIVE_PAGE_QA_NOT_COMPLETE")
            for observation in result["observations"]:
                link = normalize_product_url(observation.get("raw_product_url"), base_url=receipt["request_url"])
                if link["is_valid"]:
                    parsed_urls.add(link["normalized_product_url"])
                    parsed_slots.add((source["source_id"], link["normalized_product_url"],
                        observation.get("ranking_position", observation.get("rank_value")), observation.get("rank_context")))
        if parsed_urls != actual_urls:
            raise ValueError("PRIOR_STAGE_LIVE_BODY_CARD_POOL_MISMATCH")
        for observation in observations:
            source = source_map[observation["source_id"]]
            link = normalize_product_url(observation.get("raw_product_url"), base_url=observation.get("page_url", source["page_urls"][0]))
            if link["is_valid"] and (source["source_id"], link["normalized_product_url"],
                    observation.get("ranking_position", observation.get("rank_value")), observation.get("rank_context")) not in parsed_slots:
                raise ValueError("PRIOR_STAGE_LIVE_SOURCE_OR_RANK_BINDING_MISMATCH")
    return record

def validate_authorization(plan, authorization, gate, *, now=None, store_root=None):
    validate_plan(plan)
    if plan["stage"] == "T0":
        if plan.get("live_enabled"):
            raise ValueError("T0_MUST_REMAIN_OFFLINE")
        return None
    if not plan.get("live_enabled") or not authorization or authorization.get("approved") is not True:
        raise ValueError("LIVE_AUTHORIZATION_REQUIRED")
    if authorization.get("stage") != plan["stage"] or authorization.get("run_id") != plan.get("run_id"):
        raise ValueError("STAGE_OR_RUN_AUTHORIZATION_MISMATCH")
    if store_root is not None and authorization.get("store_root") != str(Path(store_root).resolve()):
        raise ValueError("EXACT_AUTHORIZED_STORE_ROOT_REQUIRED")
    if plan.get("shared_access_gate_path") and authorization.get("shared_access_gate_path") != str(Path(plan["shared_access_gate_path"]).resolve()):
        raise ValueError("EXACT_AUTHORIZED_SHARED_GATE_PATH_REQUIRED")
    current = aware_time(now)
    for field in ("approval_id", "approved_by", "reviewed_at", "expires_at"):
        if not authorization.get(field):
            raise ValueError("AUTHORIZATION_MISSING:" + field)
    if aware_time(authorization["reviewed_at"]) > current or aware_time(authorization["expires_at"]) <= current:
        raise ValueError("AUTHORIZATION_EXPIRED_OR_NOT_YET_VALID")
    if gate.get("state") != "NORMAL" or authorization.get("access_gate_state") != "NORMAL":
        raise ValueError("GLOBAL_ACCESS_GATE_BLOCKED_NO_AUTOMATIC_RESET")
    if not STOP_CONDITIONS.issubset(set(authorization.get("stop_conditions", []))):
        raise ValueError("INCOMPLETE_STOP_CONDITIONS")
    for field in (*LIMIT_FIELDS, "target_unique_links", "max_duration_hours"):
        cap = authorization.get(field)
        if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not math.isfinite(cap) or cap < plan[field]:
            raise ValueError("UNREVIEWED_BUDGET:" + field)
    if authorization.get("category_cooldown_seconds") != 0 or authorization.get("parallelism") != 1:
        raise ValueError("V2_SERIAL_ZERO_COOLDOWN_REVIEW_REQUIRED")
    if authorization.get("request_pacing_seconds") != plan["request_pacing_seconds"]:
        raise ValueError("PACING_REVIEW_REQUIRED")
    for source in plan["sources"]:
        scope = authorization.get("sources", {}).get(source["source_id"])
        if not scope or scope.get("source_type") != source["source_type"] or scope.get("research_category") != source.get("research_category"):
            raise ValueError("UNREVIEWED_SOURCE_MAPPING")
        proof = scope.get("evidence", {})
        if proof.get("reviewed") is not True or not proof.get("path") or not proof.get("sha256"):
            raise ValueError("SOURCE_PROOF_REQUIRED")
        if hashlib.sha256(Path(proof["path"]).read_bytes()).hexdigest() != proof["sha256"]:
            raise ValueError("SOURCE_PROOF_HASH_MISMATCH")
        for url in source.get("page_urls", []):
            if not amazon_url(url) or url not in scope.get("page_urls", []):
                raise ValueError("UNREVIEWED_SOURCE_URL")
    stages = {"T1": (), "T2": ("T0", "T1"), "T3": ("T0", "T1", "T2")}[plan["stage"]]
    for stage in stages:
        reference = authorization.get("prerequisites", {}).get(stage)
        if not reference:
            raise ValueError("PRIOR_STAGE_REQUIRED:" + stage)
        verified_stage_artifact(reference, stage)
    return authorization

def authorize_request(url, source, authorization, *, kind="PAGE", descriptor=None):
    if not amazon_url(url):
        raise ValueError("INVALID_AMAZON_REQUEST_URL")
    scope = authorization["sources"][source["source_id"]]
    if kind == "PAGE":
        if url not in scope.get("page_urls", []):
            raise ValueError("UNREVIEWED_PAGE_URL")
    elif kind == "ACP":
        if not scope.get("allow_acp") or url not in scope.get("acp_urls", []):
            raise ValueError("UNREVIEWED_ACP_URL")
        if not descriptor or not descriptor.get("payload") or descriptor.get("method") != "POST":
            raise ValueError("OPAQUE_NATIVE_ACP_CONTRACT_REQUIRED")
    else:
        raise ValueError("UNREVIEWED_REQUEST_KIND")

