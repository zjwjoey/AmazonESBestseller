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

def _normalizer_for_plan(plan):
    if plan.get('snapshot_contract_version')==3:
        from .v3_links import normalize_product_url_v3
        return normalize_product_url_v3
    return normalize_product_url


def _v3_fresh_observation(observation, stage):
    if stage=='T0':
        return
    if (observation.get('origin')!='LIVE_CAPTURE' or observation.get('is_derivation')
            or any(observation.get(k) for k in ('parent_run_id','parent_observation_id',
                'parent_observation_ids','seed_provenance_path','seed_provenance_sha256'))):
        raise ValueError('V3_STAGE_FRESH_LIVE_OBSERVATION_REQUIRED')


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
    normalizer = _normalizer_for_plan(plan)
    v3 = plan.get("snapshot_contract_version") == 3
    source_map = {s["source_id"]: s for s in plan["sources"]}
    actual_urls, source_types = set(), set()
    for observation in observations:
        if v3:
            _v3_fresh_observation(observation, expected_stage)
        if observation.get("run_id") != record["run_id"] or observation.get("origin") != record["origin"]:
            raise ValueError("PRIOR_STAGE_OBSERVATION_CONTEXT_MISMATCH")
        source = source_map.get(observation.get("source_id"))
        if not source or observation.get("source_type") != source["source_type"]:
            raise ValueError("PRIOR_STAGE_OBSERVATION_SOURCE_MISMATCH")
        link = normalizer(observation.get("raw_product_url"), base_url=observation.get("page_url", source["page_urls"][0]))
        if link["is_valid"]:
            actual_urls.add(link["normalized_product_url"])
            source_types.add(source["source_type"])
    recorded_urls, recomputed_candidates = set(), []
    for candidate in candidates:
        url = candidate.get("normalized_product_url")
        if not url or candidate.get("candidate_id") != candidate_id(url) or url in recorded_urls:
            raise ValueError("PRIOR_STAGE_CANDIDATE_ID_OR_DUPLICATE_INVALID")
        recorded_urls.add(url)
        link = normalizer(url)
        if not link["is_valid"] or link["normalized_product_url"] != url:
            raise ValueError("PRIOR_STAGE_NONCANONICAL_CANDIDATE_URL")
        recomputed_candidates.append({"candidate_id": candidate_id(url), "normalized_product_url": url,
            "possible_duplicate_key": link["possible_duplicate_key"]})
    if actual_urls != recorded_urls:
        raise ValueError("PRIOR_STAGE_CANDIDATE_OBSERVATION_POOL_MISMATCH")
    # Conservative target accounting groups unverified variant parameters.
    if v3:
        # Canonical URL equality is the only automatic V3 target identity.
        # Unknown query and variant URLs never collapse through coarse groups.
        if any(str(c.get("url_identity_status", "")).startswith("VERIFIED_") for c in candidates):
            reviewed_target_count(candidates, recomputed_candidates, record["run_id"])
        actual_count = len(actual_urls)
    else:
        actual_count = reviewed_target_count(candidates, recomputed_candidates, record["run_id"])
    if actual_count <= 0 or actual_count != record.get("unique_valid_links") or quality.get("unique_valid_links") != actual_count:
        raise ValueError("PRIOR_STAGE_DECLARED_LINK_COUNT_MISMATCH")
    if expected_stage != "T0" and source_types != {"BEST_SELLERS", "MOVERS_SHAKERS", "NEW_RELEASES", "MOST_WISHED_FOR", "MOST_GIFTED"}:
        raise ValueError("PRIOR_STAGE_FIVE_LIST_VALID_CARD_BREADTH_REQUIRED")
    return manifest, observations, actual_urls

_V3_CARD_FIELDS=('product_title_raw','product_title_status','product_title_evidence','parser_version',
    'rating_raw','rating_value','rating_status','rating_source','rating_evidence','rating_binding_evidence',
    'rating_observed_at','review_count_raw','review_count','review_count_status','review_count_evidence')


def _v3_binding_key(row):
    return (row.get('source_id'),row.get('raw_product_url'),row.get('normalized_product_url'),
        row.get('representation'),row.get('representation_slot'),row.get('ranking_position'),row.get('rank_raw'),
        row.get('rank_context'),row.get('observed_at'),str(Path(row.get('evidence_path') or '').resolve()),row.get('evidence_sha256'))


def _verify_v3_stage_fields(manifest, observations, candidates, *, parsed_rows=None):
    """Reparse actual cards instead of trusting resealed quality-result labels."""
    from .v3_quality import audit_observations
    from .adapters import parser_version
    sources={s['source_id']:s for s in manifest['plan']['sources']}
    expected={_v3_binding_key(row):row for row in parsed_rows or []}
    live=manifest.get('stage',manifest['plan'].get('stage'))!='T0'
    offline_cards={}
    for row in observations:
        if row.get('parser_version')!=parser_version(sources[row['source_id']]):
            raise ValueError('V3_PRIOR_STAGE_DECLARED_PARSER_MISMATCH')
        if any(field not in row for field in _V3_CARD_FIELDS):
            raise ValueError('V3_PRIOR_STAGE_CARD_FIELDS_REQUIRED')
        if not live:
            # T0 is offline only, but every published field still has to be
            # reconstructed from the exact saved card, not a QA boolean.
            from bs4 import BeautifulSoup
            from .adapters import _parse_cards
            key=(row.get('evidence_path'),row.get('evidence_sha256'),row['source_id'],
                row.get('representation'),row.get('page_url'),row.get('observed_at'),row.get('snapshot_id'))
            if key not in offline_cards:
                body=Path(row['evidence_path']).read_bytes()
                if hashlib.sha256(body).hexdigest()!=row.get('evidence_sha256'):
                    raise ValueError('V3_PRIOR_STAGE_CARD_BODY_HASH_MISMATCH')
                offline_cards[key]=_parse_cards(BeautifulSoup(body.decode('utf8'),'lxml'),
                    source=sources[row['source_id']],run_id=row['run_id'],snapshot_id=row.get('snapshot_id'),
                    page_url=row['page_url'],page_number=row.get('page_number',1),observed_at=row['observed_at'],
                    evidence_path=row['evidence_path'],sha=row['evidence_sha256'],
                    representation=row['representation'],fragment=row['representation']!='SERVER')
            slot=row.get('representation_slot')
            actual=next((r for r in offline_cards[key] if r['representation_slot']==slot),None)
            if actual is None or any(row.get(f)!=actual.get(f) for f in _V3_CARD_FIELDS):
                raise ValueError('V3_PRIOR_STAGE_PARSED_CARD_FIELDS_MISMATCH')
        if live:
            actual=expected.get(_v3_binding_key(row))
            if actual is None:
                raise ValueError('V3_PRIOR_STAGE_CAPTURE_EVIDENCE_OR_TIME_MISMATCH')
            if any(row.get(field)!=actual.get(field) for field in _V3_CARD_FIELDS):
                raise ValueError('V3_PRIOR_STAGE_PARSED_CARD_FIELDS_MISMATCH')
    def receipt(row,source):
        return _v3_binding_key(row) in expected if live else True
    def context(row,card,source):
        # LIVE expected rows were rebuilt from fully approved, byte-bound
        # PAGE+ACP captures, including the actual native owner context.
        return live and _v3_binding_key(row) in expected
    quality=audit_observations(observations,sources,receipt_validator=receipt,
        require_receipts=live,context_validator=context)
    if not quality['ok']:
        raise ValueError('V3_PRIOR_STAGE_DOM_CARD_FIELDS_NOT_VERIFIED:'+','.join(sorted({f['code'] for f in quality['findings']})))
    # The default stage exporter uses first-observation representatives. Any
    # published candidate card fields must point to a verified observation.
    byid={r.get('observation_id'):r for r in observations}
    for candidate in candidates:
        if str(candidate.get('contract_version','')).startswith('candidate_snapshot_v3.'):
            from .candidate_contract import build_candidate_records,CONTRACT_VERSION
            if candidate['contract_version']!=CONTRACT_VERSION:
                raise ValueError('V3_PRIOR_STAGE_CANDIDATE_CONTRACT_VERSION_MISMATCH')
            group=[r for r in observations if r.get('normalized_product_url')==candidate.get('normalized_product_url')]
            recomputed=build_candidate_records(group,batch_id=manifest['run_id'],snapshot_id=manifest['plan'].get('snapshot_id') or manifest['run_id'])
            if len(recomputed)!=1 or any(candidate.get(k)!=v for k,v in recomputed[0].items()):
                raise ValueError('V3_PRIOR_STAGE_CANDIDATE_CARD_FIELDS_MISMATCH')
            continue
        published=[f for f in _V3_CARD_FIELDS if f in candidate]
        if published:
            row=byid.get(candidate.get('observation_id'))
            if row is None or any(candidate.get(f)!=row.get(f) for f in published):
                raise ValueError('V3_PRIOR_STAGE_CANDIDATE_CARD_FIELDS_MISMATCH')
    # Source/category projections are independently rebuilt when present. A
    # matched subset of reviewed policies is sufficient and cannot invent a
    # category for an unknown sibling; prior-human proof is separately checked.
    from .category_mapping import validate_mapping_rules,load_mapping_rules,map_observation_category
    global_rules=None;reference=manifest['plan'].get('category_mapping_config_reference')
    if reference:
        _verified_reference(reference,'V3_PRIOR_STAGE_CATEGORY_RULES')
        global_rules=load_mapping_rules(reference['path'])
    parents={(p.get('source_id'),p.get('page_url')):p for p in manifest.get('pages',[])}
    cache={}
    for row in observations:
        if 'category_mapping_status' not in row and not row.get('research_category'):
            continue
        parent=parents.get((row.get('source_id'),row.get('page_url')))
        if parent is None and row.get('representation')=='SERVER':
            parent={'evidence_path':row.get('evidence_path'),'evidence_sha256':row.get('evidence_sha256')}
        if parent is None:
            raise ValueError('V3_PRIOR_STAGE_CATEGORY_PARENT_EVIDENCE_REQUIRED')
        rules=global_rules if global_rules is not None else validate_mapping_rules([r for r in row.get('category_mapping_evidence',[]) if r.get('scope')])
        projection=map_observation_category(row,sources[row['source_id']],rules,
            parent_html_path=parent['evidence_path'],parent_html_sha256=parent['evidence_sha256'],cache=cache)
        if (projection.get('research_category')!=row.get('research_category')
                or projection.get('category_mapping_status')!=row.get('category_mapping_status')):
            raise ValueError('V3_PRIOR_STAGE_CATEGORY_POLICY_OR_CONTEXT_MISMATCH')
    return quality


def _verify_t1_probe_scope_receipts(plan, receipts):
    """Additional exact PAGE coverage guard, after receipt integrity validation."""
    if not plan.get('probe_all_reviewed_page_urls', False):
        return
    expected = {(s['source_id'],url) for s in plan['sources'] for url in s['page_urls']}
    actual = {(r.get('source_id'),r.get('request_url')) for r in receipts if r.get('kind') == 'PAGE'}
    if not expected.issubset(actual):
        raise ValueError('V3_T1_APPROVED_PAGE_PROBE_SCOPE_INCOMPLETE')


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
    normalizer = _normalizer_for_plan(manifest["plan"])
    v3 = manifest["plan"].get("snapshot_contract_version") == 3
    parsed_v3_rows=[]
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
        if v3 and stage_approval.get("snapshot_contract_version") != 3:
            raise ValueError("V3_PRIOR_STAGE_APPROVAL_PROFILE_REQUIRED")
        if v3 and (type(stage_approval.get('probe_all_reviewed_page_urls',False)) is not bool
                or stage_approval.get('probe_all_reviewed_page_urls',False) != manifest['plan'].get('probe_all_reviewed_page_urls',False)):
            raise ValueError('V3_PRIOR_STAGE_PROBE_SCOPE_APPROVAL_MISMATCH')
        received, parsed_urls, parsed_slots, parsed_bindings = [], set(), set(), set()
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
                descriptor=receipt.get("native_acp_descriptor") or {"method": receipt.get("request_method"), "payload": receipt.get("request_payload")})
            authorize_request(receipt["final_url"], source, stage_approval, kind=receipt.get("kind", "PAGE"),
                descriptor=receipt.get("native_acp_descriptor") or {"method": receipt.get("request_method"), "payload": receipt.get("request_payload")})
            received.append((receipt, body.decode("utf-8", errors="replace"), source))
        _verify_t1_probe_scope_receipts(manifest['plan'],[r for r,body,source in received])
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
            if v3:parsed_v3_rows.extend(result['observations'])
            for observation in result["observations"]:
                link = normalizer(observation.get("raw_product_url"), base_url=receipt["request_url"])
                if link["is_valid"]:
                    parsed_urls.add(link["normalized_product_url"])
                    parsed_slots.add((source["source_id"], link["normalized_product_url"],
                        observation.get("ranking_position", observation.get("rank_value")), observation.get("rank_context")))
                    if v3:
                        parsed_bindings.add((source["source_id"], link["normalized_product_url"],
                            observation.get("ranking_position", observation.get("rank_value")), observation.get("rank_context"),
                            observation.get("raw_product_url"), observation.get("observed_at"),
                            str(Path(observation.get("evidence_path") or '').resolve()), observation.get("evidence_sha256")))
        if parsed_urls != actual_urls:
            raise ValueError("PRIOR_STAGE_LIVE_BODY_CARD_POOL_MISMATCH")
        for observation in observations:
            source = source_map[observation["source_id"]]
            link = normalizer(observation.get("raw_product_url"), base_url=observation.get("page_url", source["page_urls"][0]))
            if link["is_valid"] and (source["source_id"], link["normalized_product_url"],
                    observation.get("ranking_position", observation.get("rank_value")), observation.get("rank_context")) not in parsed_slots:
                raise ValueError("PRIOR_STAGE_LIVE_SOURCE_OR_RANK_BINDING_MISMATCH")
            if v3 and link['is_valid'] and (source['source_id'],link['normalized_product_url'],
                    observation.get('ranking_position',observation.get('rank_value')),observation.get('rank_context'),
                    observation.get('raw_product_url'),observation.get('observed_at'),
                    str(Path(observation.get('evidence_path') or '').resolve()),observation.get('evidence_sha256')) not in parsed_bindings:
                raise ValueError('V3_PRIOR_STAGE_CAPTURE_EVIDENCE_OR_TIME_MISMATCH')
    if v3:
        _verify_v3_stage_fields(manifest,observations,_verified_rows(record.get('candidates'),'PRIOR_STAGE_CANDIDATES'),
            parsed_rows=parsed_v3_rows)
    return record

def validate_authorization(plan, authorization, gate, *, now=None, store_root=None):
    if plan.get('snapshot_contract_version') == 4:
        from .v4_authorization import validate_v4_authorization
        return validate_v4_authorization(plan, authorization, gate, now=now, store_root=store_root)
    validate_plan(plan)
    if plan.get('snapshot_contract_version') == 3 and plan.get('stage') != 'T0':
        approved_probe = authorization.get('probe_all_reviewed_page_urls', False) if authorization else False
        if type(approved_probe) is not bool or approved_probe != plan.get('probe_all_reviewed_page_urls', False):
            raise ValueError('EXACT_PROBE_SCOPE_AUTHORIZATION_REQUIRED')
    if plan.get('snapshot_contract_version')==3 and plan.get('live_enabled'):
        from .v3_links import NORMALIZER_VERSION
        if (not authorization or authorization.get('snapshot_contract_version')!=3
                or authorization.get('normalizer_version')!=NORMALIZER_VERSION):
            raise ValueError('V3_EXPLICIT_PROFILE_AUTHORIZATION_REQUIRED')
        if plan['stage']=='T3' and (plan.get('origin')!='LIVE_CAPTURE'
                or plan.get('new_batch_required') is not True
                or any(plan.get(k) for k in ('parent_run_id','continuation_mode','seed_reference'))):
            raise ValueError('V3_T3_FRESH_BATCH_REQUIRED')
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
    proof_hash_cache = {}
    for source in plan["sources"]:
        scope = authorization.get("sources", {}).get(source["source_id"])
        if not scope or scope.get("source_type") != source["source_type"] or scope.get("research_category") != source.get("research_category"):
            raise ValueError("UNREVIEWED_SOURCE_MAPPING")
        if plan.get('snapshot_contract_version')==3 and scope.get('parser_profile')!='V3':
            raise ValueError('V3_APPROVED_SOURCE_PARSER_PROFILE_REQUIRED')
        proof = scope.get("evidence", {})
        if proof.get("reviewed") is not True or not proof.get("path") or not proof.get("sha256"):
            raise ValueError("SOURCE_PROOF_REQUIRED")
        proof_path = Path(proof["path"]).resolve()
        if proof_path not in proof_hash_cache:
            proof_hash_cache[proof_path] = hashlib.sha256(proof_path.read_bytes()).hexdigest()
        if proof_hash_cache[proof_path] != proof["sha256"]:
            raise ValueError("SOURCE_PROOF_HASH_MISMATCH")
        for url in source.get("page_urls", []):
            if not amazon_url(url) or url not in scope.get("page_urls", []):
                raise ValueError("UNREVIEWED_SOURCE_URL")
    stages = {"T1": (), "T2": ("T0", "T1"), "T3": ("T0", "T1", "T2")}[plan["stage"]]
    for stage in stages:
        reference = authorization.get("prerequisites", {}).get(stage)
        if not reference:
            raise ValueError("PRIOR_STAGE_REQUIRED:" + stage)
        prerequisite=verified_stage_artifact(reference, stage)
        if plan.get('snapshot_contract_version')==3:
            prior_manifest=_verified_json(prerequisite.get('manifest'),'V3_PRIOR_STAGE_MANIFEST')
            if prior_manifest.get('plan',{}).get('snapshot_contract_version')!=3:
                raise ValueError('V3_PRIOR_STAGE_PROFILE_REQUIRED:'+stage)
    return authorization

def authorize_request(url, source, authorization, *, kind="PAGE", descriptor=None):
    if authorization.get('snapshot_contract_version') == 4:
        from .v4_authorization import authorize_v4_request
        return authorize_v4_request(url, source, authorization, kind=kind, descriptor=descriptor)
    if not amazon_url(url):
        raise ValueError("INVALID_AMAZON_REQUEST_URL")
    scope = authorization["sources"][source["source_id"]]
    if kind == "PAGE":
        if url not in scope.get("page_urls", []):
            raise ValueError("UNREVIEWED_PAGE_URL")
    elif kind == "ACP":
        if not descriptor or not descriptor.get("payload") or descriptor.get("method") != "POST":
            raise ValueError("OPAQUE_NATIVE_ACP_CONTRACT_REQUIRED")
        if (descriptor.get("metadata") or {}).get("carousel") is True:
            if (source.get("parser_profile") != "V3" or scope.get("allow_native_acp") is not True
                    or scope.get("source_type") != source.get("source_type")):
                raise ValueError("V3_CAROUSEL_APPROVED_PROFILE_REQUIRED")
            validate_native_acp(url, descriptor, scope, authorization, source_id=source["source_id"])
            return
        if scope.get("allow_acp") and url in scope.get("acp_urls", []):
            return
        if scope.get("allow_native_acp") is not True:
            raise ValueError("UNREVIEWED_ACP_URL")
        validate_native_acp(url, descriptor, scope, authorization)
    else:
        raise ValueError("UNREVIEWED_REQUEST_KIND")

def validate_native_acp(url, descriptor, scope, authorization, *, source_id=None):
    """Dynamic endpoints require the saved aborted browser contract as proof."""
    if (descriptor.get("metadata") or {}).get("carousel") is True:
        return _validate_v3_native_carousel(url, descriptor, scope, authorization, source_id=source_id)
    parent = descriptor.get("request_contract_parent_page_url")
    target, origin = urlsplit(url), urlsplit(str(parent))
    if (parent not in scope.get("page_urls", []) or not amazon_url(parent)
            or target.netloc != origin.netloc or not target.path.startswith("/acp/")
            or not target.path.endswith("/nextPage") or descriptor.get("url") != url):
        raise ValueError("NATIVE_ACP_PARENT_OR_ENDPOINT_NOT_APPROVED")
    if descriptor.get("request_contract_source") not in {"NATIVE_BROWSER_REQUEST_ABORTED", "NATIVE_BROWSER_CONTRACT_DERIVED_BATCH"}:
        raise ValueError("NATIVE_ACP_TRANSPORT_PROOF_REQUIRED")
    payload = descriptor.get("payload")
    if not isinstance(payload, str) or hashlib.sha256(payload.encode("utf-8")).hexdigest() != descriptor.get("request_payload_sha256"):
        raise ValueError("NATIVE_ACP_ACTUAL_PAYLOAD_HASH_MISMATCH")
    _, native_raw = _verified_reference({"path": descriptor.get("native_contract_evidence_path"),
        "sha256": descriptor.get("native_contract_evidence_sha256")}, "NATIVE_ACP_CAPTURE_EVIDENCE")
    native = json.loads(native_raw)
    if (native.get("request_contract_source") != "NATIVE_BROWSER_REQUEST_ABORTED"
            or native.get("run_id") != authorization.get("run_id") or native.get("parent_page_url") != parent
            or native.get("url") != url or native.get("method") != "POST"
            or native.get("resource_type") not in {"xhr", "fetch"} or not native.get("payload")
            or not native.get("headers", {}).get("x-amz-acp-params")):
        raise ValueError("NATIVE_ACP_CAPTURE_CONTEXT_INVALID")
    from ..transport.playwright import PlaywrightTransport
    rebuilt = PlaywrightTransport._native_batch_descriptor(descriptor, native)
    if (rebuilt.get("payload") != payload or rebuilt.get("method") != descriptor["method"]
            or rebuilt.get("url") != url or rebuilt.get("headers") != descriptor.get("headers")):
        raise ValueError("NATIVE_ACP_BODY_NOT_BOUND_TO_BROWSER_CAPTURE")



def _validate_v3_native_carousel(url, descriptor, scope, authorization, *, source_id=None):
    """Authorize only exact aborted native bytes from a saved unique DOM owner.

    This separate V3 branch never derives grid batches or guesses endpoints.
    Mock capture tests are protocol evidence, not current website validation.
    """
    from ..transport.playwright import PlaywrightTransport
    from ..multisource.sources import source_type_from_url
    from .adapters import _cards, _scope
    from bs4 import BeautifulSoup
    parent=descriptor.get('request_contract_parent_page_url')
    if (authorization.get('snapshot_contract_version')!=3 or scope.get('parser_profile')!='V3'
            or scope.get('allow_native_acp') is not True):
        raise ValueError('V3_CAROUSEL_APPROVED_PROFILE_REQUIRED')
    sid=source_id or descriptor.get('source_id')
    if (not sid or descriptor.get('source_id')!=sid or authorization.get('sources',{}).get(sid)!=scope
            or descriptor.get('run_id')!=authorization.get('run_id')):
        raise ValueError('V3_CAROUSEL_RUN_OR_SOURCE_MISMATCH')
    if (parent not in scope.get('page_urls',[]) or not amazon_url(parent)
            or not amazon_url(url) or descriptor.get('url')!=url):
        raise ValueError('V3_CAROUSEL_PARENT_OR_URL_NOT_APPROVED')
    allowed_contracts={'NATIVE_BROWSER_REQUEST_ABORTED'}
    if scope.get('allow_held_native_carousel') is True:
        allowed_contracts.add('NATIVE_BROWSER_REQUEST_HELD')
    if descriptor.get('request_contract_source') not in allowed_contracts or descriptor.get('method')!='POST':
        raise ValueError('V3_CAROUSEL_EXACT_ABORTED_POST_REQUIRED')
    payload=descriptor.get('payload')
    if not isinstance(payload,str) or not payload or hashlib.sha256(payload.encode('utf8')).hexdigest()!=descriptor.get('request_payload_sha256'):
        raise ValueError('V3_CAROUSEL_PAYLOAD_HASH_MISMATCH')
    _,native_raw=_verified_reference({'path':descriptor.get('native_contract_evidence_path'),
        'sha256':descriptor.get('native_contract_evidence_sha256')},'V3_CAROUSEL_CAPTURE_EVIDENCE')
    native=json.loads(native_raw)
    if (native.get('request_contract_source') not in allowed_contracts
            or native.get('request_contract_source')!=descriptor.get('request_contract_source')
            or native.get('request_contract_source')=='NATIVE_BROWSER_REQUEST_HELD' and native.get('request_sent') is not False
            or native.get('run_id')!=authorization.get('run_id') or native.get('parent_page_url')!=parent
            or native.get('resource_type') not in {'xhr','fetch'}
            or native.get('request_payload_sha256')!=descriptor.get('request_payload_sha256')
            or native.get('request_contract_parent_page_url')!=parent):
        raise ValueError('V3_CAROUSEL_CAPTURE_CONTEXT_INVALID')
    for field in ('url','method','payload','headers','resource_type','parent_page_url','run_id','ranking_context_binding','native_carousel_owner'):
        if native.get(field)!=descriptor.get(field):
            raise ValueError('V3_CAROUSEL_BROWSER_BYTES_OR_OWNER_MISMATCH')
    owner=PlaywrightTransport._saved_carousel_owner(descriptor,parent)
    expected={k:owner[k] for k in ('owner_id','owner_path','owner_params','carousel_options_sha256')}
    if (native.get('native_carousel_owner')!=expected
            or native.get('ranking_context_binding')!=owner['ranking_context_binding']
            or not PlaywrightTransport._carousel_request_matches(native,owner)):
        raise ValueError('V3_CAROUSEL_CAPTURE_NOT_BOUND_TO_SAVED_OWNER')
    binding=owner['ranking_context_binding']
    context=binding.get('ranking_context_url')
    if (binding.get('source_id')!=sid or not amazon_url(context)
            or source_type_from_url(context)!=source_type_from_url(parent)
            or source_type_from_url(parent)!=scope.get('source_type')):
        raise ValueError('V3_CAROUSEL_CATEGORY_CONTEXT_INVALID')
    # Recheck every owned card's category context, not just the first card.
    soup=BeautifulSoup(Path(descriptor['evidence_path']).read_bytes(),'lxml')
    nodes=[n for n in soup.select('[data-card-metrics-id^="p13n-zg-list-carousel"] [data-a-carousel-options]')
           if n.get('data-a-carousel-options')==descriptor['metadata']['carousel_options_raw']]
    if len(nodes)!=1:
        raise ValueError('V3_CAROUSEL_OWNER_NOT_UNIQUE')
    owned=[c for c in _cards(soup) if any(p is nodes[0] for p in c.parents)]
    if not owned or {_scope(c,parent) for c in owned}!={context}:
        raise ValueError('V3_CAROUSEL_CARD_CONTEXT_NOT_UNIQUE')
    return None
