"""Independent V4 approvals; no survey or legacy stage can authorize LIVE."""
from __future__ import annotations

import hashlib
import math
import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .budgets import LIMIT_FIELDS, aware_time
from .v3_links import NORMALIZER_VERSION
from .v4_sources import SEEDS, RANKED, amazon_url
from ..multisource.storage import digest

_PREREQUISITES=ContextVar('v4_execution_prerequisites',default=None)


@contextmanager
def prerequisite_review_session():
    """One execution only. Initial DOM review, then all dependency byte hashes.

    A changed dependency triggers the full review again. Public stage validation
    and final export always perform central DOM review independently.
    """
    token=_PREREQUISITES.set({})
    try:yield
    finally:_PREREQUISITES.reset(token)


def _verify_prerequisite(reference,stage):
    from .v4_quality import verify_stage_reference
    cache=_PREREQUISITES.get()
    if cache is None or not reference:return verify_stage_reference(reference,stage)
    key=(stage,reference['path'],reference['sha256'])
    old=cache.get(key)
    if (old and all(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==sha for p,sha in old['files'])
            and all(sorted(p.name for p in d.iterdir() if p.is_file())==names for d,names in old['directories'])):return old['value']
    value=verify_stage_reference(reference,stage)
    paths={Path(reference['path'])}
    def collect(x):
        if isinstance(x,dict):
            for k,v in x.items():
                # Live control state is checked independently before requests;
                # changing it cannot invalidate immutable prior-stage DOM.
                # Do not recursively collect its unrelated historical receipts.
                if k=='shared_access_gate_path':continue
                if isinstance(v,str) and (k=='path' or k.endswith('_path')) and Path(v).is_file():paths.add(Path(v))
                elif isinstance(v,(dict,list)):collect(v)
        elif isinstance(x,list):
            for v in x:collect(v)
    collect(value)
    # Journal history participates in central QA. Freeze every adjacent journal
    # and all its nested receipt/native/HTML references, not just latest frames.
    for page in value.get('pages',[]):
        if page.get('journal_path'):
            p=Path(page['journal_path'])
            for j in p.parent.glob(p.name.split('_',1)[0]+'_*.json'):
                paths.add(j);collect(json.loads(j.read_text(encoding='utf8')))
    visited=set()
    while paths-visited:
        p=(paths-visited).pop();visited.add(p)
        if p.suffix=='.json':
            collect(json.loads(p.read_text(encoding='utf8')))
            if p.parent.name=='responses':
                paths.update(p.parent.parent.joinpath('requests').glob('*.reserved.json'))
    dirs={p.parent for p in paths}
    cache[key]={'value':value,'files':[(p,hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths],
        'directories':[(d,sorted(p.name for p in d.iterdir() if p.is_file())) for d in dirs]}
    return value


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
    three_source = plan.get('collection_contract') == 'V4_THREE_SOURCE_5001'
    if three_source:
        from .three_contract import validate_three_plan
        validate_three_plan(plan)
    # Execution-local DOM verification cache. Every subsequent request still
    # hashes each unique navigation dependency; a changed byte repeats the full
    # proof validation. Offline/final QA receives no cache and reparses normally.
    cache=_PREREQUISITES.get()
    scope_key=('SOURCE_SCOPE',digest(sources),plan.get('collection_contract'))
    cached=cache.get(scope_key) if cache is not None else None
    if cached is not None and all(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==sha for p,sha in cached):
        return plan
    seen = set()
    for s in sources:
        if s.get('source_id') in seen or not s.get('source_id') or s.get('parser_profile') != 'V4':
            raise ValueError('V4_UNIQUE_SOURCE_PROFILE_REQUIRED')
        seen.add(s['source_id'])
        if three_source:
            from .three_contract import verify_source
            verify_source(s)
            continue
        seed = next((x for x in SEEDS if x['url'] == s.get('actual_source_url')), None)
        if not seed or any(seed[k] != s.get(k) for k in ('source_type', 'source_subtype')):
            # Future capacity extensions require the DOM proof validator.
            from .v4_navigation import verify_discovered_source
            verify_discovered_source(s)
        from .v4_navigation import verify_pagination_chain
        verify_pagination_chain(s)
        if not amazon_url(s['actual_source_url']):
            raise ValueError('V4_EXACT_EVIDENCED_SOURCE_PAGES_REQUIRED')
    if cache is not None:
        paths=set()
        def collect(value):
            if isinstance(value,dict):
                for k,v in value.items():
                    if k in {'evidence_path','html_path','path'} and isinstance(v,str) and Path(v).is_file():paths.add(Path(v))
                    elif k=='historical_capture_reference' and isinstance(v,dict):
                        from ..multisource.storage import read_sealed
                        receipt_path=Path(v['path']);receipt=read_sealed(receipt_path)
                        paths.add(receipt_path)
                        paths.add(receipt_path.parent.parent/'requests'/(receipt['request_id']+'.reserved.json'))
                        collect(receipt)
                    elif isinstance(v,(dict,list)):collect(v)
            elif isinstance(value,list):
                for v in value:collect(v)
        collect(sources)
        cache[scope_key]=[(p,hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths]
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
    if plan.get('collection_contract')=='V4_THREE_SOURCE_5001' and current<aware_time(plan['campaign_created_at']):
        raise ValueError('THREE_REQUEST_BEFORE_CAMPAIGN_CREATION')
    if not a.get('approval_id') or not a.get('approved_by'):
        raise ValueError('V4_NAMED_APPROVAL_REQUIRED')
    if (plan.get('collection_contract')=='V4_THREE_SOURCE_5001'
            and aware_time(a['reviewed_at'])<aware_time(plan['campaign_created_at'])):
        raise ValueError('THREE_APPROVAL_BEFORE_CAMPAIGN_CREATION')
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
    if plan.get('collection_contract') == 'V4_THREE_SOURCE_5001':
        for key in ('collection_contract','source_targets','source_budgets','campaign_id','campaign_created_at','task_reference','target_policy'):
            if digest(a.get(key)) != digest(plan.get(key)):
                raise ValueError('THREE_EXACT_CAMPAIGN_APPROVAL_REQUIRED:'+key)
    checked_evidence={}
    for s in plan['sources']:
        scope = a['sources'][s['source_id']]
        for key in ('source_type', 'source_subtype', 'page_urls', 'parser_profile'):
            if scope.get(key) != s.get(key):
                raise ValueError('V4_EXACT_SOURCE_APPROVAL_REQUIRED:' + key)
        ref = scope.get('evidence') or {}
        path=Path(ref.get('path','__missing__'))
        if path not in checked_evidence and path.is_file():
            checked_evidence[path]=hashlib.sha256(path.read_bytes()).hexdigest()
        if not ref.get('reviewed') or checked_evidence.get(path) != ref.get('sha256'):
            raise ValueError('V4_SOURCE_EVIDENCE_REQUIRED')
    for stage in {'T1': (), 'T2': ('T0', 'T1'), 'T3': ('T0', 'T1', 'T2')}[plan['stage']]:
        _verify_prerequisite(a.get('prerequisites', {}).get(stage), stage)
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
    if not (descriptor.get('metadata') or {}).get('carousel'):
        return validate_v4_native_grid(url,descriptor,source,authorization)
    # Reuse V3's entire native byte/owner/parent/source validator. The explicit
    # compatibility view changes no run IDs, actual bytes or receipt provenance.
    from .authorization import _validate_v3_native_carousel
    legacy_scope = {**scope, 'parser_profile': 'V3'}
    legacy_auth = {**authorization, 'snapshot_contract_version': 3,
                   'sources': {**authorization['sources'], source['source_id']: legacy_scope}}
    return _validate_v3_native_carousel(url, descriptor, legacy_scope, legacy_auth, source_id=source['source_id'])


def saved_grid_binding(descriptor,source):
    # Ownership is identical across literal batches and historical journal
    # frames. Reuse only inside a review session, rehashing the parent bytes.
    # Payload/native-contract validation still runs separately on every call.
    from copy import deepcopy
    cache=_PREREQUISITES.get()
    if cache is None:return _saved_grid_binding(descriptor,source)
    fields={k:descriptor.get(k) for k in ('source_id','run_id','request_contract_parent_page_url',
        'evidence_path','metadata','url','headers')}
    key=('V4_GRID_PARENT_DOM',digest([source,fields]))
    path=Path(descriptor['evidence_path']);sha=hashlib.sha256(path.read_bytes()).hexdigest()
    old=cache.get(key)
    if old and old['sha256']==sha:return deepcopy(old['value'])
    value=_saved_grid_binding(descriptor,source)
    cache[key]={'sha256':sha,'value':deepcopy(value)}
    return value


def _saved_grid_binding(descriptor,source):
    """Rebuild grid ownership from the actual saved page, never endpoint guesses."""
    from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
    from .v4_adapters import parse_v4_page
    from .sources import same_source_category
    d=descriptor;parent=d.get('request_contract_parent_page_url')
    if parent not in source['page_urls'] or d.get('source_id')!=source['source_id']:
        raise ValueError('V4_GRID_EXACT_SOURCE_REQUIRED')
    body=Path(d['evidence_path']).read_bytes()
    parsed=parse_v4_page(body.decode('utf8'),source=source,page_url=parent,run_id=d['run_id'],
        observed_at='2000-01-01T00:00:00Z',evidence_path=d['evidence_path'],origin='OFFLINE_FIXTURE')
    grids=[p for p in parsed['acp_pending'] if not p.get('metadata',{}).get('carousel')]
    contexts={r['rank_context'] for r in parsed['observations']}
    def category_view(value):
        parts=urlsplit(value)
        # The saved grid's heading links include the explicit display encoding.
        # Preserve every other unknown query byte in category comparison.
        query=[(k,v) for k,v in parse_qsl(parts.query,keep_blank_values=True) if (k,v)!=('ie','UTF8')]
        return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),parts.fragment))
    if (len(grids)!=1 or grids[0]['metadata']!=d.get('metadata') or not grids[0].get('url')
            or urlsplit(grids[0]['url']).path!=urlsplit(d['url']).path
            or parsed['page']['status']!='VERIFIED_WITH_PRODUCTS'
            or len(contexts)!=1 or not all(same_source_category(category_view(c),category_view(parent)) for c in contexts)
            or (d.get('headers') or {}).get('x-amz-acp-params')!=grids[0]['metadata']['params']):
        raise ValueError('V4_GRID_SAVED_DOM_OWNERSHIP_REQUIRED')
    return {'source_id':source['source_id'],'page_evidence_sha256':hashlib.sha256(body).hexdigest(),
        'ranking_context_url':next(iter(contexts))}


def validate_v4_native_grid(url,descriptor,source,authorization):
    from .authorization import validate_native_acp
    scope=authorization.get('sources',{}).get(source['source_id'],{})
    if (authorization.get('snapshot_contract_version')!=4 or scope.get('parser_profile')!='V4'
            or scope.get('allow_native_acp') is not True or scope.get('source_type')!=source['source_type']
            or scope.get('page_urls')!=source['page_urls'] or source['source_type'] not in RANKED
            or descriptor.get('run_id')!=authorization.get('run_id')
            or descriptor.get('ranking_context_binding')!=saved_grid_binding(descriptor,source)):
        raise ValueError('V4_GRID_SOURCE_OR_BINDING_MISMATCH')
    # Existing exact captured request / opaque rec-object contract checks are
    # retained. No new endpoint, width, rec ID, or request shape is invented.
    # Checkpoints canonically sort object keys, while request payload strings
    # remain immutable. Rebuild rec objects using the original DOM JSON text's
    # key order, rather than the sorted metadata projection. Full saved-DOM
    # equality above still binds both representations and every opaque value.
    metadata=descriptor['metadata']
    entries=json.loads(metadata['client_recs_raw'])
    if entries!=metadata['entries']:
        raise ValueError('V4_GRID_LITERAL_RECS_MISMATCH')
    proof={**descriptor,'metadata':{**metadata,'entries':entries}}
    return validate_native_acp(url,proof,scope,authorization)
