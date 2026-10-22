"""Finite, explicit same-run favorite budget amendment; original plan stays sealed."""
import hashlib,json
from pathlib import Path
from .budgets import aware_time
from ..multisource.storage import digest

KEY='favorite_budget_reference'

def read_amendment(reference,run_id,plan_sha256=None,root_id=None):
    p=Path(reference['path']);body=p.read_bytes()
    if hashlib.sha256(body).hexdigest()!=reference['sha256']:raise ValueError('FAVORITE_BUDGET_SHA_MISMATCH')
    a=json.loads(body);old=a.get('original_source_budgets',{});new=a.get('effective_source_budgets',{})
    cap=a.get('max_favorite_pages_per_source')
    if (a.get('kind')!='FAVORITE_PAGE_BUDGET_AMENDMENT_V1' or a.get('approved') is not True
            or not a.get('approved_by') or not a.get('authorization_text') or a.get('run_id')!=run_id
            or plan_sha256 is not None and a.get('plan_sha256')!=plan_sha256
            or type(cap) is not int or not 161<=cap<=500
            or set(old)!={'BEST_SELLERS','NEW_RELEASES','CUSTOMER_FAVORITES'} or set(new)!=set(old)
            or any(new[k]!=old[k] for k in ('BEST_SELLERS','NEW_RELEASES'))
            or new['CUSTOMER_FAVORITES']!={'requests':cap,'PAGE':cap,'native':0}
            or cap<=old['CUSTOMER_FAVORITES']['PAGE']
            or a.get('preserve_counters_and_task_clock') is not True
            or not a.get('allowed_root_source_ids')
            or root_id is not None and root_id not in a['allowed_root_source_ids']):
        raise ValueError('EXACT_FINITE_FAVORITE_ONLY_BUDGET_AMENDMENT_REQUIRED')
    return a

def validate_amendment(plan,auth,*,now=None):
    reference=auth.get(KEY)
    if not reference:return None
    a=read_amendment(reference,plan['run_id'],digest(plan))
    ref=a['original_authorization_reference'];p=Path(ref['path']);body=p.read_bytes()
    if hashlib.sha256(body).hexdigest()!=ref['sha256']:raise ValueError('ORIGINAL_AUTHORIZATION_SHA_MISMATCH')
    original=json.loads(body)
    if (plan.get('collection_contract')!='CURRENT_THREE_SOURCE_PAGE_SNAPSHOT_V1'
            or {k:v for k,v in auth.items() if k!=KEY}!=original
            or a['original_source_budgets']!=plan['source_budgets']
            or set(a['allowed_root_source_ids'])!={s['source_id'] for s in plan['sources'] if s['source_type']=='CUSTOMER_FAVORITES'}
            or a['store_root']!=auth['store_root'] or a['expires_at']!=auth['expires_at']
            or not aware_time(a['reviewed_at'])<=aware_time(now)<aware_time(a['expires_at'])):
        raise ValueError('SAME_RUN_SCOPE_CLOCK_AND_COUNTERS_MUST_BE_PRESERVED')
    return a

def effective_budgets(plan,auth,*,now=None):
    a=validate_amendment(plan,auth,now=now) if auth and auth.get(KEY) else None
    return a['effective_source_budgets'] if a else plan['source_budgets']

def page_limit(source,run_id,plan_sha256=None):
    if not source.get(KEY):return 160
    if source['source_type']!='CUSTOMER_FAVORITES':raise ValueError('FAVORITE_AMENDMENT_CANNOT_EXPAND_MAIN_SOURCE')
    a=read_amendment(source[KEY],run_id,plan_sha256,source.get('snapshot_admission_root',source['source_id']))
    return a['max_favorite_pages_per_source']
