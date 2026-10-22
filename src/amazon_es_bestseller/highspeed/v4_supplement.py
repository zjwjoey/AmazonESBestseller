"""Prepare a fresh, disabled T2 proposal from independently rechecked saved DOM.

No browser, approval, seed or collection is created by this module. The caller's
selection and every exact URL remain reviewable artifacts.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit
import uuid

from ..multisource.storage import digest, immutable_json
from .authorization import STOP_CONDITIONS
from .budgets import aware_time, LIMIT_FIELDS
from .v4_authorization import validate_plan_v4
from .v4_frontier import extract_page_navigation
from .v4_navigation import verify_discovered_source, verify_pagination_chain
from .v4_quality import verify_stage_reference
from .v4_sources import RANKED, source_for_url

BUDGETS = {'max_requests_per_run', 'max_pages_per_run', 'max_duration_hours',
    'max_native_requests_per_source', 'max_dynamic_requests_per_source',
    'max_raw_observations_per_run', 'history_observation_limit', 'target_unique_links'}
FAMILIES = RANKED | {'CUSTOMER_FAVORITES', 'DEALS', 'AMAZON_HAUL'}


def _read(reference):
    body = Path(reference['path']).read_bytes()
    if hashlib.sha256(body).hexdigest() != reference['sha256']:
        raise ValueError('V4_SUPPLEMENT_REFERENCE_SHA_MISMATCH')
    return json.loads(body)


def ranked_review_key(url):
    """Review-only alias grouping. Never generate request URLs from this key.

    Strip only the observed ranking referral path token. Unknown queries and
    fragments remain byte-for-byte significant; product dedup is unaffected.
    """
    p = urlsplit(url)
    path = re.sub(r'/ref=zg_(?:bs|bsnr)_[A-Za-z0-9_-]+$', '', p.path)
    return urlunsplit((p.scheme, p.netloc, path, p.query, p.fragment))


def _navigation(reference, t2_reference, stage):
    manifest = _read(reference)
    files = manifest.get('files', [])
    if (len(files) != 2 or {f['name'] for f in files} !=
            {'navigation_inventory.json', 'pending_exact_urls.txt'}):
        raise ValueError('V4_SUPPLEMENT_NAVIGATION_FILES_MISMATCH')
    directory = Path(reference['path']).parent
    for f in files:
        if hashlib.sha256((directory / f['name']).read_bytes()).hexdigest() != f['sha256']:
            raise ValueError('V4_SUPPLEMENT_NAVIGATION_FILE_SHA_MISMATCH')
    inventory = json.loads((directory / 'navigation_inventory.json').read_text('utf8'))
    for value in (manifest, inventory):
        if (value.get('t2_reference') != t2_reference or
                any(value.get(k) is not False for k in ('approved', 'live_enabled', 'T3_execution_enabled'))):
            raise ValueError('V4_SUPPLEMENT_NAVIGATION_BINDING_MISMATCH')
    if (inventory.get('kind') != 'OFFLINE_NAVIGATION_REVIEW_QUEUE'
            or inventory.get('source_run_id') != stage['run_id']):
        raise ValueError('V4_SUPPLEMENT_NAVIGATION_RUN_MISMATCH')
    entries = inventory['entries']
    if len({e['exact_url'] for e in entries}) != len(entries):
        raise ValueError('V4_SUPPLEMENT_DUPLICATE_NAVIGATION_URL')
    pending = [e['exact_url'] for e in entries if e['status'] in
               {'PENDING_SOURCE_REVIEW', 'PENDING_PAGINATION_REVIEW'}]
    if (directory / 'pending_exact_urls.txt').read_text('utf8').splitlines() != pending:
        raise ValueError('V4_SUPPLEMENT_PENDING_URL_LIST_MISMATCH')
    return {e['exact_url']: e for e in entries}


def prepare_t2_supplement(t2_reference, navigation_reference, selection_reference,
                          output, runtime_root, shared_gate):
    selection = _read(selection_reference)
    if (selection.get('kind') != 'V4_T2_SUPPLEMENT_SELECTION' or selection.get('schema_version') != 1
            or not isinstance(selection.get('reason'), str) or not selection['reason'].strip()
            or set(selection.get('budgets', {})) != BUDGETS):
        raise ValueError('V4_SUPPLEMENT_EXPLICIT_SELECTION_REQUIRED')
    for key in ('existing_source_ids', 'new_source_urls', 'pagination_urls'):
        values = selection.get(key)
        if (not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values)
                or len(set(values)) != len(values)):
            raise ValueError('V4_SUPPLEMENT_UNIQUE_SELECTION_REQUIRED:' + key)
    if selection['budgets']['target_unique_links'] != 2000:
        raise ValueError('V4_SUPPLEMENT_FRESH_2000_SAMPLE_REQUIRED')
    for key, value in selection['budgets'].items():
        if key == 'max_duration_hours':
            valid = (type(value) in (int, float) and math.isfinite(value) and 0 < value <= 10)
        else:
            valid = type(value) is int and value > 0
        if not valid:
            raise ValueError('V4_SUPPLEMENT_FINITE_POSITIVE_BUDGET_REQUIRED:' + key)

    stage = verify_stage_reference(t2_reference, 'T2')
    execution = stage['capacity_execution']
    approvals = [_read(r) for r in execution['authorization_references']]
    if not approvals or any(a.get('prerequisites') != approvals[0].get('prerequisites') for a in approvals):
        raise ValueError('V4_SUPPLEMENT_PREREQUISITE_REFERENCES_REQUIRED')
    prerequisites = approvals[0]['prerequisites']
    verify_stage_reference(prerequisites['T0'], 'T0')
    t1 = verify_stage_reference(prerequisites['T1'], 'T1')
    entries = _navigation(navigation_reference, t2_reference, stage)
    old_sources = {s['source_id']: s for s in stage['sources']}
    latest = {}
    for p in stage['pages']:
        key = (p['source_id'], p['page_url'])
        if key not in latest or aware_time(p['observed_at']) > aware_time(latest[key]['observed_at']):
            latest[key] = p
    observed = {p['page_url'] for p in latest.values()}
    cache = {}

    def checked_row(url, status):
        entry = entries.get(url)
        if not entry or entry['status'] != status or not entry.get('provenance'):
            raise ValueError('V4_SUPPLEMENT_PENDING_EXACT_URL_REQUIRED:' + url)
        verified = []
        for row in entry['provenance']:
            key = (row['parent_source_id'], row['page_url'])
            if key not in latest:
                raise ValueError('V4_SUPPLEMENT_PARENT_PAGE_NOT_IN_T2')
            if key not in cache:
                cache[key], _ = extract_page_navigation(latest[key], old_sources[key[0]], observed_urls=observed)
            # Full provenance equality prevents a rehashed queue from replacing
            # DOM ownership, index, title, status, or parent evidence.
            if row not in cache[key] or row['url'] != url or row['status'] != status:
                raise ValueError('V4_SUPPLEMENT_NAVIGATION_DOM_MISMATCH')
            verified.append(row)
        return verified[0]

    sources, parents, reviews = [], {}, []
    for source_id in selection['existing_source_ids']:
        if source_id not in old_sources:
            raise ValueError('V4_SUPPLEMENT_EXISTING_SOURCE_REQUIRED')
        s = deepcopy(old_sources[source_id])
        for url in s['page_urls']:
            p = latest.get((source_id, url))
            if not p or p['status'] != 'VERIFIED_WITH_PRODUCTS':
                raise ValueError('V4_SUPPLEMENT_EXISTING_PRODUCTS_REQUIRED')
        sources.append(s)
        parents[source_id] = latest[(source_id, s['actual_source_url'])]
        reviews.append({'source_id': source_id, 'url': s['actual_source_url'], 'review': 'PREVIOUS_T2_PRODUCTS',
                        'destination_product_supply': 'PREVIOUSLY_OBSERVED_NOT_NEW_CAPTURE'})
    for url in selection['new_source_urls']:
        row = checked_row(url, 'PENDING_SOURCE_REVIEW')
        subtype = row['parent_source_subtype']
        if row['parent_source_type'] == 'CUSTOMER_FAVORITES' and urlsplit(url).path == '/s':
            subtype = 'CUSTOMER_FAVORITES_FILTERED_SEARCH'
        s = source_for_url(url, navigation_evidence=row, source_type=row['parent_source_type'], source_subtype=subtype)
        s.update(enabled=True, page_urls=[url])
        verify_discovered_source(s)
        sources.append(s)
        parents[s['source_id']] = latest[(row['parent_source_id'], row['page_url'])]
        reviews.append({'source_id': s['source_id'], 'url': url, 'review': 'SAVED_OWNED_NAVIGATION_ONLY',
                        'provenance': row, 'destination_product_supply': 'UNVERIFIED'})
    for url in selection['pagination_urls']:
        row = checked_row(url, 'PENDING_PAGINATION_REVIEW')
        s = next((s for s in sources if s['source_id'] == row['parent_source_id']), None)
        if s is None or s['page_urls'][-1] != row['page_url']:
            raise ValueError('V4_SUPPLEMENT_ORDERED_NEXT_CHAIN_REQUIRED')
        s.setdefault('pagination_evidence', []).append(row)
        s['page_urls'].append(url)
        verify_pagination_chain(s)
        reviews.append({'source_id': s['source_id'], 'url': url, 'review': 'SAVED_NEXT_LINK_ONLY',
                        'provenance': row, 'destination_product_supply': 'UNVERIFIED'})
    if {s['source_type'] for s in sources} != FAMILIES:
        raise ValueError('V4_SUPPLEMENT_ALL_FIVE_FAMILIES_REQUIRED')
    urls = [u for s in sources for u in s['page_urls']]
    ranked_keys = [ranked_review_key(s['actual_source_url']) for s in sources if s['source_type'] in RANKED]
    if len(set(urls)) != len(urls) or len(set(ranked_keys)) != len(ranked_keys):
        raise ValueError('V4_SUPPLEMENT_DUPLICATE_EXACT_OR_RANKING_ALIAS')

    run_id = 'v4_t2_supplement_' + uuid.uuid4().hex[:12]
    plan = deepcopy(execution['plan'])
    plan.update(selection['budgets'])
    plan.update(stage='T2', run_id=run_id, snapshot_id=run_id, live_enabled=False, sources=sources,
        max_requests_per_day=plan['max_requests_per_run'], request_pacing_seconds=3, parallelism=1,
        category_cooldown_seconds=0, shared_access_gate_path=str(Path(shared_gate).resolve()))
    validate_plan_v4(plan)
    envelope = (len(urls) + len(ranked_keys) * plan['max_native_requests_per_source']
                + sum(s['source_type'] in {'DEALS', 'AMAZON_HAUL'} for s in sources) * plan['max_dynamic_requests_per_source'])
    if plan['max_pages_per_run'] < len(urls) or plan['max_requests_per_run'] < envelope:
        raise ValueError('V4_SUPPLEMENT_SCOPE_EXCEEDS_PROPOSED_BUDGET')
    scopes = {}
    for s in sources:
        p = parents[s['source_id']]
        scopes[s['source_id']] = {k: s[k] for k in ('source_type', 'source_subtype', 'page_urls', 'parser_profile')}
        scopes[s['source_id']].update(allow_native_acp=s['source_type'] in RANKED,
            allow_held_native_carousel=s['source_type'] in RANKED,
            allow_native_dynamic=s['source_type'] in {'DEALS', 'AMAZON_HAUL'},
            evidence={'path': p['evidence_path'], 'sha256': p['evidence_sha256'], 'reviewed': True})
    auth = {'approved': False, 'stage': 'T2', 'snapshot_contract_version': 4, 'run_id': run_id,
        'plan_sha256': digest(plan), 'store_root': str((Path(runtime_root) / run_id / 'store').resolve()),
        'shared_access_gate_path': plan['shared_access_gate_path'], 'sources': scopes, 'prerequisites': prerequisites,
        'approved_by': None, 'approval_id': None, 'reviewed_at': None, 'expires_at': None,
        'access_gate_state': 'NORMAL', 'stop_conditions': sorted(STOP_CONDITIONS),
        'note': 'Pending separate exact T2 approval. Fresh sample; no T3 or historical seed.'}
    for k in (*LIMIT_FIELDS, 'target_unique_links', 'max_duration_hours', 'request_pacing_seconds',
              'parallelism', 'category_cooldown_seconds'):
        auth[k] = plan[k]
    result = {'run_id': run_id, 'source_count': len(sources), 'exact_PAGE_count': len(urls),
        'exact_PAGE_URLs': urls, 'nominal_scope_request_ceiling': envelope,
        'historical_candidates_in_new_target': 0, 'pending_plan_sha256': digest(plan),
        'enabled_plan_sha256_preview': digest({**plan, 'live_enabled': True}),
        'budgets': selection['budgets'], 'live_started': False, 'T3_enabled': False,
        'new_network_requests': 0, 'prior_T2_review_groups': stage['capacity_audit']['conservative_review_groups'],
        'prior_T2_shortfall': stage['capacity_audit']['sample_goal_shortfall'],
        'prior_T0_T1_formal_reverified': True, 'prior_T2_DOM_capacity_reverified': True,
        'destination_capacity': 'UNVERIFIED', 'strict_independent_buyable_skus_confirmed': False}
    directory = Path(output) / ('T2补充准备_' + run_id)
    directory.mkdir(parents=True, exist_ok=False)
    immutable_json(directory / 'plan.pending.json', plan)
    immutable_json(directory / 'authorization.pending.json', auth)
    immutable_json(directory / '真实空源保留.json', [p for p in t1['pages'] if p['status'] == 'EXPLICIT_EMPTY'])
    immutable_json(directory / 'source_review.json', {'selection': selection, 'reviews': reviews,
        't2_reference': t2_reference, 'navigation_reference': navigation_reference, 'selection_reference': selection_reference})
    with (directory / 'exact_PAGE_URLs.txt').open('x', encoding='utf8', newline='\n') as fh:
        fh.write('\n'.join(urls) + '\n')
    immutable_json(directory / 'summary.json', result)
    immutable_json(directory / 'manifest.json', {'schema_version': 1, 'kind': 'DISABLED_T2_SUPPLEMENT_PROPOSAL',
        'approved': False, 'live_enabled': False, 'T3_enabled': False,
        'files': [{'name': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(directory.iterdir())]})
    return {'directory': str(directory), **result}
