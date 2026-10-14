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
                if (page['origin'] != 'LIVE_CAPTURE' or receipt.get('run_id') != page['run_id']
                        or receipt.get('status_code') != 200 or receipt.get('access_state') != 'NORMAL'
                        or receipt.get('sha256') != page['evidence_sha256']
                        or receipt.get('request_url') != page['page_url']
                        or receipt.get('observed_at') != page['observed_at']
                        or reservation.get('run_id') != page['run_id']
                        or reservation.get('url') != page['page_url']
                        or reservation.get('kind') != 'PAGE' or reservation.get('origin') != 'LIVE_CAPTURE'):
                    raise ValueError('CURRENT_RUN_LIVE_RECEIPT_REQUIRED')
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
    required = ('native_best_continuation', 'native_new_continuation', 'favorites_pagination',
                'deals_loading', 'haul_loading')
    if expected_stage != 'T0' and not all(value.get('capability_receipts', {}).get(k) for k in required):
        raise ValueError('V4_PRIOR_STAGE_PROTOCOL_PROOF_REQUIRED')
    # Protocol receipts cannot yet be approved by a boolean-only summary.
    if expected_stage != 'T0':
        raise ValueError('V4_LIVE_CAPABILITY_RECEIPT_VALIDATOR_PENDING')
    return value
