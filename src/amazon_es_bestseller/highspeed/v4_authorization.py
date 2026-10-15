"""Independent V4 approvals; no survey or legacy stage can authorize LIVE."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

from .budgets import LIMIT_FIELDS, aware_time
from .v3_links import NORMALIZER_VERSION
from .v4_sources import SEEDS, RANKED, amazon_url
from ..multisource.storage import digest


def validate_plan_v4(plan):
    if plan.get('mode') != 'HIGH_SPEED_V4' or plan.get('snapshot_contract_version') != 4:
        raise ValueError('V4_PLAN_REQUIRED')
    if plan.get('stage') not in {'T0', 'T1', 'T2', 'T3'}:
        raise ValueError('V4_STAGE_REQUIRED')
    if plan.get('normalizer_version') != NORMALIZER_VERSION:
        raise ValueError('V4_REVIEWED_NORMALIZER_REQUIRED')
    for key in (*LIMIT_FIELDS, 'target_unique_links'):
        if type(plan.get(key)) is not int or plan[key] <= 0:
            raise ValueError('FINITE_POSITIVE_BUDGET_REQUIRED:' + key)
    for key in ('max_duration_hours', 'request_pacing_seconds'):
        v = plan.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise ValueError('FINITE_DURATION_OR_PACING_REQUIRED:' + key)
    if plan['max_duration_hours'] > 10 or plan['request_pacing_seconds'] < 3:
        raise ValueError('V4_DURATION_OR_PACING_INVALID')
    for key in ('max_native_requests_per_source','max_dynamic_requests_per_source'):
        if key in plan and (type(plan[key]) is not int or not 1<=plan[key]<=plan['max_requests_per_run']):
            raise ValueError('V4_BOUNDED_LOADING_LIMIT_REQUIRED:'+key)
    if plan.get('parallelism') != 1 or plan.get('category_cooldown_seconds') != 0 or plan.get('global_access_gate') is not True:
        raise ValueError('V4_SERIAL_GATE_AND_ZERO_COOLDOWN_REQUIRED')
    for key in ('detail_collection', 'translation', 'image_download', 'action_filtering'):
        if plan.get(key) is not False:
            raise ValueError('FORBIDDEN_COLLECTION_FEATURE:' + key)
    if plan.get('parent_run_id') or plan.get('seed_reference') or plan.get('continuation_mode'):
        raise ValueError('V4_FRESH_BATCH_REQUIRED')
    if plan['stage'] == 'T0':
        if plan.get('live_enabled') or plan.get('origin') != 'OFFLINE_FIXTURE':
            raise ValueError('V4_T0_OFFLINE_ONLY')
    elif plan.get('origin') != 'LIVE_CAPTURE':
        raise ValueError('V4_LIVE_ORIGIN_REQUIRED')
    sources = plan.get('sources', [])
    if not sources:
        raise ValueError('V4_SOURCES_REQUIRED')
    seen = set()
    for s in sources:
        if s.get('source_id') in seen or not s.get('source_id') or s.get('parser_profile') != 'V4':
            raise ValueError('V4_UNIQUE_SOURCE_PROFILE_REQUIRED')
        seen.add(s['source_id'])
        seed = next((x for x in SEEDS if x['url'] == s.get('actual_source_url')), None)
        if not seed or any(seed[k] != s.get(k) for k in ('source_type', 'source_subtype')):
            # Future capacity extensions require the DOM proof validator.
            from .v4_navigation import verify_discovered_source
            verify_discovered_source(s)
        extra=s.get('pagination_evidence', [])
        from .v4_navigation import verify_pagination_reference
        for reference in extra:
            verify_pagination_reference(reference,s)
        if (s.get('page_urls') != [s['actual_source_url'], *[r['url'] for r in extra]]
                or not amazon_url(s['actual_source_url'])):
            raise ValueError('V4_EXACT_EVIDENCED_SOURCE_PAGES_REQUIRED')
    return plan


def validate_v4_authorization(plan, authorization, gate, *, now=None, store_root=None):
    validate_plan_v4(plan)
    if plan['stage'] == 'T0':
        return None
    from .authorization import STOP_CONDITIONS
    a = authorization or {}
    if not plan.get('live_enabled') or a.get('approved') is not True:
        raise ValueError('LIVE_AUTHORIZATION_REQUIRED')
    if (a.get('snapshot_contract_version') != 4 or a.get('run_id') != plan['run_id']
            or a.get('stage') != plan['stage'] or a.get('plan_sha256') != digest(plan)):
        raise ValueError('V4_EXACT_STAGE_RUN_PLAN_APPROVAL_REQUIRED')
    current = aware_time(now)
    if not a.get('approval_id') or not a.get('approved_by'):
        raise ValueError('V4_NAMED_APPROVAL_REQUIRED')
    if not aware_time(a['reviewed_at']) <= current < aware_time(a['expires_at']):
        raise ValueError('AUTHORIZATION_EXPIRED_OR_NOT_YET_VALID')
    if store_root is None or a.get('store_root') != str(Path(store_root).resolve()):
        raise ValueError('EXACT_AUTHORIZED_STORE_ROOT_REQUIRED')
    if Path(store_root).resolve().parent.name != plan['run_id']:
        raise ValueError('V4_FRESH_RUN_DIRECTORY_REQUIRED')
    shared = plan.get('shared_access_gate_path')
    if not shared or a.get('shared_access_gate_path') != str(Path(shared).resolve()):
        raise ValueError('V4_SHARED_GATE_REQUIRED')
    if gate.get('state') != 'NORMAL' or a.get('access_gate_state') != 'NORMAL':
        raise ValueError('GLOBAL_ACCESS_GATE_BLOCKED_NO_AUTOMATIC_RESET')
    if not STOP_CONDITIONS.issubset(set(a.get('stop_conditions', []))):
        raise ValueError('INCOMPLETE_STOP_CONDITIONS')
    for key in (*LIMIT_FIELDS, 'target_unique_links', 'max_duration_hours', 'request_pacing_seconds',
                'parallelism', 'category_cooldown_seconds'):
        if type(a.get(key)) is not type(plan[key]) or a[key] != plan[key]:
            raise ValueError('V4_EXACT_BUDGET_APPROVAL_REQUIRED:' + key)
    if set(a.get('sources', {})) != {s['source_id'] for s in plan['sources']}:
        raise ValueError('V4_EXACT_SOURCE_SET_REQUIRED')
    for s in plan['sources']:
        scope = a['sources'][s['source_id']]
        for key in ('source_type', 'source_subtype', 'page_urls', 'parser_profile'):
            if scope.get(key) != s.get(key):
                raise ValueError('V4_EXACT_SOURCE_APPROVAL_REQUIRED:' + key)
        ref = scope.get('evidence') or {}
        if not ref.get('reviewed') or hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest() != ref.get('sha256'):
            raise ValueError('V4_SOURCE_EVIDENCE_REQUIRED')
    for stage in {'T1': (), 'T2': ('T0', 'T1'), 'T3': ('T0', 'T1', 'T2')}[plan['stage']]:
        from .v4_quality import verify_stage_reference
        verify_stage_reference(a.get('prerequisites', {}).get(stage), stage)
    return a


def authorize_v4_request(url, source, authorization, *, kind='PAGE', descriptor=None):
    if not amazon_url(url) or authorization.get('snapshot_contract_version') != 4:
        raise ValueError('V4_APPROVED_AMAZON_REQUEST_REQUIRED')
    scope = authorization.get('sources', {}).get(source['source_id'], {})
    if kind == 'PAGE':
        from .v4_navigation import exact_query_token_reordering
        if not any(url==approved or exact_query_token_reordering(url,approved) for approved in scope.get('page_urls', [])):
            raise ValueError('UNREVIEWED_SOURCE_URL')
        return
    if kind=='ACP' and source['source_type'] in {'DEALS','AMAZON_HAUL'}:
        if scope.get('allow_native_dynamic') is not True:
            raise ValueError('V4_NATIVE_LOADING_NOT_APPROVED')
        from .v4_loading import validate_dynamic_descriptor
        return validate_dynamic_descriptor(url,source,scope,authorization,descriptor)
    if kind != 'ACP' or source['source_type'] not in RANKED or not scope.get('allow_native_acp'):
        raise ValueError('V4_NATIVE_LOADING_NOT_APPROVED')
    # Reuse V3's entire native byte/owner/parent/source validator. The explicit
    # compatibility view changes no run IDs, actual bytes or receipt provenance.
    from .authorization import _validate_v3_native_carousel
    legacy_scope = {**scope, 'parser_profile': 'V3'}
    legacy_auth = {**authorization, 'snapshot_contract_version': 3,
                   'sources': {**authorization['sources'], source['source_id']: legacy_scope}}
    return _validate_v3_native_carousel(url, descriptor, legacy_scope, legacy_auth, source_id=source['source_id'])
