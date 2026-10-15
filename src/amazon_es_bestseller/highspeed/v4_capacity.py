"""Receipt-based T2 sample capacity. Never infer global supply or confirmed SKUs."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from ..multisource.storage import read_sealed, digest
from .budgets import aware_time, madrid_day
from .v4_quality import audit_v4, build_candidates_v4

FAMILIES={'BEST_SELLERS','NEW_RELEASES','CUSTOMER_FAVORITES','DEALS','AMAZON_HAUL'}


def audit_capacity(pages, observations, sources, execution):
    """Re-read every reservation/receipt and DOM, including failed attempts.

    A valid diagnostic is distinct from sufficient measured supply for T3.
    Unknown completeness is legal; missing measurement or unsafe access is not.
    """
    errors=[]; receipts=[]; reservations=[]
    plan=execution['plan'];run_id=plan['run_id']
    quality=audit_v4(pages,observations,sources,require_live=True)
    if not quality['central_full_dom_review_passed']:errors.append('CENTRAL_DOM_QA_FAILED')
    if plan.get('stage')!='T2' or plan.get('sources')!=sources:errors.append('EXACT_T2_PLAN_REQUIRED')
    try:
        from .v4_authorization import validate_plan_v4, authorize_v4_request,validate_v4_authorization
        validate_plan_v4(plan)
        root=Path(execution['run_directory'])
        if read_sealed(root/'plan.json')!=plan:raise ValueError('IMMUTABLE_PLAN_MISMATCH')
        start=read_sealed(root/'started.json')
        if start['plan_hash']!=digest(plan) or start['started_at']!=execution['started_at']:
            raise ValueError('IMMUTABLE_START_MISMATCH')
        reservation_paths=sorted((root/'requests').glob('*.reserved.json'))
        receipt_paths=sorted((root/'responses').glob('*.json'))
        for p in reservation_paths:reservations.append(read_sealed(p))
        for p in receipt_paths:
            r=read_sealed(p)
            if r['run_id']!=run_id or r['origin']!='LIVE_CAPTURE':raise ValueError('FOREIGN_RECEIPT')
            if hashlib.sha256(Path(r['html_path']).read_bytes()).hexdigest()!=r['sha256']:
                raise ValueError('RECEIPT_BODY_SHA_MISMATCH')
            receipts.append(r)
        if {r['request_id'] for r in receipts}!={r['request_id'] for r in reservations}:
            raise ValueError('UNFINISHED_OR_MISSING_RECEIPTS')
        source_map={s['source_id']:s for s in sources}
        approvals=[]
        for ref in execution['authorization_references']:
            approval_path=Path(ref['path'])
            if hashlib.sha256(approval_path.read_bytes()).hexdigest()!=ref['sha256']:
                raise ValueError('EXECUTION_APPROVAL_SHA_MISMATCH')
            auth=json.loads(approval_path.read_text(encoding='utf8'))
            validate_v4_authorization(plan,auth,{'state':'NORMAL'},now=auth['reviewed_at'],store_root=root.parent.parent)
            approvals.append(auth)
        if not approvals:raise ValueError('EXECUTION_APPROVAL_REQUIRED')
        by_id={r['request_id']:r for r in receipts}
        for q in reservations:
            r=by_id[q['request_id']]
            if (q['run_id']!=run_id or q['plan_sha256']!=digest(plan) or q['origin']!='LIVE_CAPTURE'
                    or q['source_id']!=r['source_id'] or q['kind']!=r['kind'] or q['url']!=r['request_url']
                    or q['observed_at']!=r['observed_at']):raise ValueError('RESERVATION_BINDING_MISMATCH')
            matches=[a for a in approvals if aware_time(a['reviewed_at'])<=aware_time(q['observed_at'])<aware_time(a['expires_at'])]
            if not matches:raise ValueError('REQUEST_OUTSIDE_APPROVED_TIME')
            allowed=False
            for auth in matches:
                try:
                    for url in (q['url'],r.get('final_url')):
                        authorize_v4_request(url,source_map[q['source_id']],auth,kind=q['kind'],descriptor=r.get('native_acp_descriptor'))
                    allowed=True;break
                except ValueError:continue
            if not allowed:raise ValueError('REQUEST_NOT_IN_APPROVED_SCOPE')
            if r.get('status_code')!=200 or r.get('access_state')!='NORMAL':errors.append('NON_NORMAL_RECEIPT:'+q['request_id'])
        if not receipts:errors.append('NO_LIVE_RECEIPTS')
    except (OSError,ValueError,KeyError,TypeError) as exc:errors.append(str(exc))
    times=sorted(aware_time(q['observed_at']) for q in reservations)
    started=aware_time(execution['started_at']);finished=aware_time(execution['finished_at'])
    elapsed=(finished-started).total_seconds()
    intervals=[(b-a).total_seconds() for a,b in zip(times,times[1:])]
    if elapsed<=0 or elapsed>plan['max_duration_hours']*3600:errors.append('DURATION_OUTSIDE_PLAN')
    if times and (times[0]<started or times[-1]>finished):errors.append('REQUEST_TIME_OUTSIDE_EXECUTION')
    if intervals and min(intervals)<plan['request_pacing_seconds']:errors.append('PACING_VIOLATION')
    page_count=sum(q['kind']=='PAGE' for q in reservations)
    daily=Counter(madrid_day(q['observed_at']) for q in reservations)
    if len(reservations)>plan['max_requests_per_run'] or page_count>plan['max_pages_per_run']:
        errors.append('REQUEST_OR_PAGE_LIMIT_EXCEEDED')
    if any(n>plan['max_requests_per_day'] for n in daily.values()):errors.append('DAY_LIMIT_EXCEEDED')
    if len(observations)>min(plan['max_raw_observations_per_run'],plan['history_observation_limit']):
        errors.append('OBSERVATION_LIMIT_EXCEEDED')
    candidates=build_candidates_v4(observations)
    groups={c['target_review_group'] for c in candidates if c['eligible_for_target']}
    targets={c['normalized_product_url']:c['target_review_group'] for c in candidates if c['eligible_for_target']}
    metrics=[]; measured=set()
    for kind in sorted(FAMILIES):
        rows=[r for r in observations if r['source_type']==kind and r['origin']=='LIVE_CAPTURE']
        urls={r['normalized_product_url'] for r in rows if r.get('is_valid')}
        other={r['normalized_product_url'] for r in observations if r['source_type']!=kind and r.get('is_valid')}
        rs=[r for r in receipts if r.get('source_type')==kind]
        if urls and rs:measured.add(kind)
        metrics.append({'source_type':kind,'raw_observations':len(rows),'normalized_unique_links':len(urls),
            'cross_source_overlap_links':len(urls&other),'source_exclusive_sample_links':len(urls-other),
            'duplicate_observation_ratio':1-len(urls)/len(rows) if rows else None,
            'rating_valid':sum(r.get('rating_status')=='VALID' for r in rows),
            'rating_valid_coverage':sum(r.get('rating_status')=='VALID' for r in rows)/len(rows) if rows else None,
            'monthly_bought_valid':sum(r.get('monthly_bought_status')=='VALID' for r in rows),
            'invalid_link_observations':sum(not r.get('is_valid') for r in rows),
            'request_count':len(rs),'PAGE_count':sum(r['kind']=='PAGE' for r in rs),
            'loading_count':sum(r['kind']=='ACP' for r in rs),
            'mean_PAGE_seconds':_mean([r.get('duration_seconds',0) for r in rs if r['kind']=='PAGE']),
            'observed_capacity_lower_bound':len({targets[u] for u in urls if u in targets}),
            'capacity_unit':'CONSERVATIVE_URL_REVIEW_GROUPS_NOT_CONFIRMED_SKUS','global_capacity_status':'UNKNOWN'})
    if measured!=FAMILIES:errors.append('FIVE_FAMILY_MEASUREMENT_INCOMPLETE')
    from .v4_coverage import loading_coverage
    loading=loading_coverage(pages,sources)
    if not loading['all_loading_capabilities_observed']:errors.append('T2_LOADING_MEASUREMENT_INCOMPLETE')
    for kind in ('DEALS','AMAZON_HAUL'):
        if sum(r['kind']=='ACP' and r['source_type']==kind for r in receipts)<2:
            errors.append('TWO_LOADING_RECEIPTS_REQUIRED:'+kind)
    expected={u for s in sources for u in s['page_urls']}
    if not expected.issubset({p['page_url'] for p in pages}):errors.append('APPROVED_PAGE_SCOPE_INCOMPLETE')
    if any(p['status'] not in {'VERIFIED_WITH_PRODUCTS','EXPLICIT_EMPTY'} for p in pages):errors.append('UNVERIFIED_SOURCE_FRAME')
    # Real checkpoints provide marginal growth; retained repeats never become new supply.
    seen=set();samples=[];batches=defaultdict(set)
    for r in observations:
        if r.get('is_valid') and r['normalized_product_url'] in targets:
            batches[aware_time(r['observed_at'])].add(targets[r['normalized_product_url']])
    for t,values in sorted(batches.items()):
        seen.update(values);samples.append((t,len(seen)))
    active_seconds=(samples[-1][0]-samples[0][0]).total_seconds() if len(samples)>1 else 0
    growth=(samples[-1][1]-samples[0][1]) if samples else 0
    rate=growth*3600/active_seconds if active_seconds>0 else None
    return {'schema_version':1,'stage':'T2','run_id':run_id,'diagnostic_passed':not errors,
        'errors':sorted(set(errors)),'central_full_dom_review_passed':quality['central_full_dom_review_passed'],
        'actual_requests':len(reservations),'actual_PAGE':page_count,'minimum_request_interval_seconds':min(intervals) if intervals else None,
        'elapsed_wall_seconds':elapsed,'raw_observations':len(observations),'normalized_unique_links':len(candidates),
        'conservative_review_groups':len(groups),'source_measurements':metrics,
        'sample_goal':plan['target_unique_links'],'sample_goal_shortfall':max(0,plan['target_unique_links']-len(groups)),
        'loading_measurements':loading,
        'sample_window_seconds':active_seconds,'sample_new_review_groups':growth,'sample_review_groups_per_hour':rate,
        'rate_scope':'First-to-last eligible capture window; subtracts the entire initial capture baseline. Not a sustained-rate guarantee.',
        'linear_30000_hours_if_sample_rate_persists':30000/rate if rate else None,
        'global_source_completeness':'UNKNOWN','thirty_thousand_supply_demonstrated':len(groups)>=30000 and not errors,
        'T3_execution_enabled':False,'strict_independent_buyable_SKUs_confirmed':False}


def _mean(values):
    return sum(values)/len(values) if values else None


def execution_for_store(store,run_id):
    run=store.get_run(run_id)
    return {'plan':run['plan'],'run_directory':str(store.root/'runs'/run_id),
            'authorization_references':run['execution_approval_references'],
            'started_at':run['started_at'],'finished_at':run['finished_at']}
