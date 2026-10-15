"""Central full-DOM review and exact-URL aggregation for V4 evidence."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from ..multisource.storage import read_sealed

from .v4_adapters import parse_v4_page
from .v3_links import normalize_product_url_v3


def verify_rendered_derivation(page,root_receipt,source):
    """DOM after local native promise delivery, never an extra HTTP receipt."""
    d=page.get('rendered_derivation') or {}
    if (page.get('source_representation')!='RENDERED_DOM_DERIVATION'
            or d.get('new_PAGE_request') is not False
            or d.get('root_html_sha256')!=root_receipt['sha256']
            or d.get('root_observed_at')!=root_receipt['observed_at']
            or d.get('rendered_observed_at')!=page['observed_at']
            or not d.get('dynamic_receipt_references')):
        raise ValueError('V4_RENDERED_DERIVATION_LINEAGE_REQUIRED')
    from .v4_loading import validate_dynamic_descriptor
    from .budgets import aware_time
    if aware_time(page['observed_at'])<aware_time(root_receipt['observed_at']):
        raise ValueError('V4_RENDERED_CAPTURE_TIME_INVALID')
    delivered={x.get('request_id'):x for x in d.get('local_ui_deliveries',[])}
    seen=set()
    frames=d.get('rendered_frames',[])
    if (len(frames)!=len(d['dynamic_receipt_references'])
            or frames[-1]['sha256']!=page['evidence_sha256']
            or frames[-1]['observed_at']!=page['observed_at']):
        raise ValueError('V4_RENDERED_INTERMEDIATE_CHAIN_REQUIRED')
    parent_sha=root_receipt['sha256']
    for reference,frame in zip(d['dynamic_receipt_references'],frames):
        path=Path(reference['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=reference['sha256']:
            raise ValueError('V4_RENDERED_DYNAMIC_RECEIPT_SHA_MISMATCH')
        r=read_sealed(path);desc=r.get('native_acp_descriptor') or {}
        reserved=read_sealed(path.parent.parent/'requests'/(r['request_id']+'.reserved.json'))
        event=delivered.get(r['request_id'],{})
        if (r['request_id'] in seen or r.get('run_id')!=page['run_id']
                or r.get('source_id')!=page['source_id'] or r.get('status_code')!=200
                or r.get('access_state')!='NORMAL' or r.get('origin')!='LIVE_CAPTURE'
                or r.get('kind')!='ACP' or r.get('request_url')!=desc.get('url')
                or r.get('final_url')!=desc.get('url') or r.get('request_method')!=desc.get('method')
                or r.get('request_payload')!=desc.get('payload')
                or desc.get('parent_html_sha256')!=parent_sha
                or reserved.get('run_id')!=page['run_id'] or reserved.get('url')!=r['request_url']
                or reserved.get('kind')!='ACP' or reserved.get('source_id')!=page['source_id']
                or event.get('receipt_reference')!=reference or event.get('origin')!='LOCAL_UI_REPLAY'
                or event.get('new_network_request') is not False
                or aware_time(r['observed_at'])>aware_time(page['observed_at'])):
            raise ValueError('V4_RENDERED_DYNAMIC_ACTUAL_RECEIPT_REQUIRED')
        if hashlib.sha256(Path(r['html_path']).read_bytes()).hexdigest()!=r['sha256']:
            raise ValueError('V4_RENDERED_DYNAMIC_BODY_SHA_MISMATCH')
        validate_dynamic_descriptor(r['request_url'],source,{'allow_native_dynamic':True},
            {'run_id':page['run_id']},desc)
        if (hashlib.sha256(Path(frame['path']).read_bytes()).hexdigest()!=frame['sha256']
                or aware_time(frame['observed_at'])<aware_time(r['observed_at'])):
            raise ValueError('V4_RENDERED_INTERMEDIATE_FRAME_INVALID')
        parent_sha=frame['sha256']
        seen.add(r['request_id'])
    return True


def build_candidates_v4(observations):
    groups = defaultdict(list)
    for row in observations:
        normalized = normalize_product_url_v3(row.get('raw_product_url'), row.get('source_page_url') or row['page_url'])
        if normalized['is_valid'] and normalized['normalized_product_url'] == row.get('normalized_product_url'):
            groups[normalized['normalized_product_url']].append(row)
    records = []
    for url, rows in sorted(groups.items()):
        ordered = sorted(rows, key=lambda r: (r['observed_at'], r['observation_id']))
        representative = ordered[-1]
        record = deepcopy(representative)
        record.update(source_memberships=[{k: r.get(k) for k in
                       ('observation_id', 'source_id', 'source_type', 'source_subtype', 'source_url',
                        'source_page_url', 'ranking_position', 'ranking_semantics', 'rank_context',
                        'observed_at', 'evidence_path', 'evidence_sha256')} for r in ordered],
                      first_observed_at=ordered[0]['observed_at'], last_observed_at=ordered[-1]['observed_at'],
                      observation_count=len(rows), strict_buyable_confirmed=False,
                      representative_rule='LATEST_OBSERVATION_ALL_FIELDS_PAIRED_NO_IMPUTATION')
        conflicts = {}
        for field in ('rating_value', 'rating_count', 'current_price', 'monthly_bought_min', 'product_title_es'):
            values = {json.dumps(r.get(field), ensure_ascii=False) for r in ordered if r.get(field) is not None}
            if len(values) > 1:
                conflicts[field] = [{k: r.get(k) for k in ('observation_id', 'source_type', field, 'observed_at')} for r in ordered]
        record['observation_conflicts'] = conflicts
        # Unknown parameters/variant bytes remain in distinct candidate rows.
        # A coarse review family conservatively prevents aliases inflating the
        # target while never merging their candidate records.
        record['target_review_group'] = representative.get('possible_duplicate_key') or url
        record['eligible_for_target'] = bool(representative.get('product_title_es') and not representative.get('binding_doubts'))
        record['dedup_status'] = ('POSSIBLE_DUPLICATE' if any(normalize_product_url_v3(r['raw_product_url'])['dedup_status'] == 'POSSIBLE_DUPLICATE' for r in rows)
                                  else 'EXACT_NORMALIZED_URL_GROUP')
        records.append(record)
    return records


def contribution_v4(observations, candidates):
    sets = defaultdict(set); raw = Counter(); valid = Counter(); rating = Counter()
    for r in observations:
        kind = r['source_type']; raw[kind] += 1
        if r.get('normalized_product_url'):
            valid[kind] += 1; sets[kind].add(r['normalized_product_url'])
        if r.get('rating_status') == 'VALID':
            rating[kind] += 1
    result = []
    for kind in ('BEST_SELLERS', 'NEW_RELEASES', 'CUSTOMER_FAVORITES', 'DEALS', 'AMAZON_HAUL'):
        other = set().union(*(v for k, v in sets.items() if k != kind))
        result.append({'source_type': kind, 'raw_observations': raw[kind], 'valid_link_observations': valid[kind],
                       'normalized_unique_links': len(sets[kind]), 'also_in_other_sources': len(sets[kind] & other),
                       'source_only_candidates': len(sets[kind] - other),
                       'rating_coverage': rating[kind] / raw[kind] if raw[kind] else None,
                       'capacity_verified': False, 'independent_buyable_skus_confirmed': False})
    return result


def audit_v4(pages, observations, sources, *, require_live=False):
    """Reparse every receipt-bound representation, compare every parsed field.

    Serialized SQLite enrichment is ignored, source-derived fields are not.
    No hashes-only shortcut: SHA check is followed by a complete DOM parse.
    """
    errors = []; expected = {}; source_map = {s['source_id']: s for s in sources}
    # Reverification adds a new capture; earlier immutable journals remain part
    # of the observation history and must not be compared with the newest DOM.
    capture_pages = {}
    for current in pages:
        histories = []
        if current.get('journal_path'):
            journal = Path(current['journal_path'])
            for path in journal.parent.glob(journal.name.split('_', 1)[0] + '_*.json'):
                value = read_sealed(path)
                if value.get('run_id') == current['run_id'] and value['page'].get('source_id') == current['source_id']:
                    histories.append(value['page'])
        for page in [*histories, current]:
            key = (page['source_id'], page['evidence_sha256'], page['observed_at'])
            previous = capture_pages.get(key)
            if previous is None or len(page.get('acp_captures', [])) >= len(previous.get('acp_captures', [])):
                capture_pages[key] = page
    for page in capture_pages.values():
        try:
            path = Path(page['evidence_path']); body = path.read_bytes()
            if hashlib.sha256(body).hexdigest() != page['evidence_sha256']:
                raise ValueError('DOM_SHA_MISMATCH')
            source = source_map[page['source_id']]
            acp = []
            for ref in page.get('acp_captures', []):
                cp = Path(ref['evidence_path']); cb = cp.read_bytes()
                if hashlib.sha256(cb).hexdigest() != ref['evidence_sha256']:
                    raise ValueError('ACP_DOM_SHA_MISMATCH')
                if require_live:
                    # Early journals retained request_id but used evidence_path
                    # for HTML. Resolve its original sealed receipt in this same
                    # run's immutable response directory, never a newer capture.
                    receipt_path = Path(page['capture_receipt_reference']['path']).parent / (ref['request_id'] + '.json')
                    actual = read_sealed(receipt_path)
                    reserved = read_sealed(receipt_path.parent.parent / 'requests' / (ref['request_id'] + '.reserved.json'))
                    binding = actual.get('ranking_context_binding') or {}
                    if (actual.get('run_id') != page['run_id'] or actual.get('origin') != 'LIVE_CAPTURE'
                            or actual.get('kind') != 'ACP' or actual.get('status_code') != 200
                            or actual.get('access_state') != 'NORMAL' or actual.get('sha256') != ref['evidence_sha256']
                            or actual.get('observed_at') != ref['observed_at']
                            or actual.get('request_url') != ref.get('request_url')
                            or actual.get('source_id') != page['source_id']
                            or binding.get('page_evidence_sha256') != page['evidence_sha256']
                            or reserved.get('run_id') != page['run_id'] or reserved.get('kind') != 'ACP'
                            or reserved.get('url') != actual.get('request_url')):
                        raise ValueError('ACP_ORIGINAL_LIVE_RECEIPT_BINDING_REQUIRED')
                    from .authorization import _validate_v3_native_carousel
                    scope={'parser_profile':'V3','source_type':source['source_type'],
                        'page_urls':source['page_urls'],'allow_native_acp':True,'allow_held_native_carousel':True}
                    auth={'snapshot_contract_version':3,'run_id':page['run_id'],'sources':{page['source_id']:scope}}
                    _validate_v3_native_carousel(actual['request_url'],actual['native_acp_descriptor'],scope,auth,
                        source_id=page['source_id'])
                acp.append({**ref, 'body': cb.decode('utf8')})
            parsed = parse_v4_page(body.decode('utf8'), source=source, page_url=page['page_url'],
                                   run_id=page['run_id'], snapshot_id=page.get('snapshot_id'),
                                   observed_at=page['observed_at'], evidence_path=str(path),
                                   http_status=page.get('http_status'), origin=page['origin'], acp_responses=acp,
                                   parser_profile_version=page.get('parser_version'))
            for row in parsed['observations']:
                expected[row['observation_id']] = row
            if require_live:
                reference = page['capture_receipt_reference']; receipt_path = Path(reference['path'])
                if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != reference.get('sha256'):
                    raise ValueError('LIVE_RECEIPT_REFERENCE_SHA_MISMATCH')
                receipt = read_sealed(receipt_path)
                reservation = read_sealed(receipt_path.parent.parent / 'requests' / (receipt['request_id'] + '.reserved.json'))
                derived=bool(page.get('rendered_derivation'))
                if derived:
                    verify_rendered_derivation(page,receipt,source)
                if (page['origin'] != 'LIVE_CAPTURE' or receipt.get('run_id') != page['run_id']
                        or receipt.get('status_code') != 200 or receipt.get('access_state') != 'NORMAL'
                        or not derived and receipt.get('sha256') != page['evidence_sha256']
                        or receipt.get('request_url') != page['page_url']
                        or not derived and receipt.get('observed_at') != page['observed_at']
                        or reservation.get('run_id') != page['run_id']
                        or reservation.get('url') != page['page_url']
                        or reservation.get('kind') != 'PAGE' or reservation.get('origin') != 'LIVE_CAPTURE'):
                    raise ValueError('CURRENT_RUN_LIVE_RECEIPT_REQUIRED')
                for name in ('document_html_reference', 'rendered_html_reference'):
                    reference = page.get(name) or {}
                    if reference.get('path') and hashlib.sha256(Path(reference['path']).read_bytes()).hexdigest() != reference.get('sha256'):
                        raise ValueError('PAIRED_CAPTURE_REPRESENTATION_SHA_MISMATCH')
            if parsed['page']['status'] != page['status']:
                raise ValueError('PAGE_STATUS_DOM_MISMATCH')
        except (OSError, KeyError, ValueError, TypeError) as exc:
            errors.append({'code': str(exc), 'page_url': page.get('page_url')})
    seen = set()
    for row in observations:
        oid = row.get('observation_id'); seen.add(oid)
        recomputed = expected.get(oid)
        if recomputed is None:
            errors.append({'code': 'NO_RECOMPUTED_DOM_OBSERVATION', 'observation_id': oid})
            continue
        # RunStore annotates repetition after parsing. It does not own the
        # source fields; all those fields still require exact DOM equality.
        differing = [k for k, v in recomputed.items() if k != 'dedup_status' and row.get(k) != v]
        if differing:
            errors.append({'code': 'OBSERVATION_DOM_MISMATCH', 'observation_id': oid, 'fields': differing})
    if set(expected) != seen or len(seen) != len(observations):
        errors.append({'code': 'OBSERVATION_SET_OR_DUPLICATE_MISMATCH'})
    statuses = {}
    for field in ('product_title', 'rating', 'rating_count', 'monthly_bought', 'price', 'category_mapping'):
        statuses[field] = dict(Counter(r.get(field + '_status', 'UNVERIFIED') for r in observations))
    return {'schema_version': 4, 'central_full_dom_review_passed': not errors, 'errors': errors,
            'reviewed_observations': len(observations), 'field_statuses': statuses,
            'reviewed_capture_frames': len(capture_pages),
            'page_statuses': dict(Counter(p['status'] for p in pages)),
            'completeness_status': 'UNKNOWN', 'live_origin_required': require_live,
            'strict_independent_buyable_skus_confirmed': False}


def verify_stage_reference(reference, expected_stage):
    """A prior gate is recomputed from its immutable complete evidence pack."""
    if not reference:
        raise ValueError('PRIOR_STAGE_REQUIRED:' + expected_stage)
    path = Path(reference['path']); body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != reference.get('sha256'):
        raise ValueError('V4_STAGE_REFERENCE_SHA_MISMATCH')
    value = json.loads(body)
    if value.get('stage') != expected_stage or value.get('schema_version') != 4 or value.get('passed') is not True:
        raise ValueError('V4_PRIOR_STAGE_NOT_PASSED')
    reviewed = audit_v4(value['pages'], value['observations'], value['sources'], require_live=expected_stage != 'T0')
    if not reviewed['central_full_dom_review_passed']:
        raise ValueError('V4_PRIOR_STAGE_DOM_REVIEW_FAILED')
    if expected_stage == 'T0':
        from .v4_sources import SEEDS
        if ({p['page_url'] for p in value['pages']} != {s['url'] for s in SEEDS}
                or any(p['status'] != 'VERIFIED_WITH_PRODUCTS' for p in value['pages'])):
            raise ValueError('V4_T0_FULL_EIGHT_ENTRY_FIXTURE_PROOF_REQUIRED')
    # Collector protocols and capacity are independent of correct parsing.
    if expected_stage=='T1':
        from .v4_sources import SEEDS
        from .v4_coverage import loading_coverage
        loading=loading_coverage(value['pages'],value['sources'])
        products={s['url'] for s in SEEDS}.issubset({p['page_url'] for p in value['pages']
                    if p['status']=='VERIFIED_WITH_PRODUCTS'})
        accepted=value.get('accepted_empty_source_urls',[])
        if accepted:
            from .v4_coverage import assess_capacity_readiness
            if value.get('acceptance_policy')!='USER_EXPLICIT_RETAIN_REAL_SOURCE_EMPTY':
                raise ValueError('V4_EMPTY_SOURCE_ACCEPTANCE_POLICY_REQUIRED')
            products=assess_capacity_readiness(value['pages'],value['sources'],loading,
                central_dom_review_passed=True,accepted_empty_urls=accepted)['ready_for_T2_plan']
        if not loading['all_loading_capabilities_observed'] or not products:
            raise ValueError('V4_PRIOR_STAGE_PROTOCOL_PROOF_REQUIRED')
    if expected_stage not in {'T0','T1'}:
        raise ValueError('V4_T2_CAPACITY_RECEIPT_VALIDATOR_PENDING')
    return value
