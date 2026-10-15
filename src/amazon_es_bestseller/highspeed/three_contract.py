"""Explicit three-source campaign contract; never an inherited LIVE approval."""
from copy import deepcopy
import hashlib
from urllib.parse import urlsplit
from .three_navigation import CONTRACT, FAMILIES, FAVORITES_ROOT, verify_navigation_reference, verify_chain
from .v4_sources import SEEDS, source_for_url
from .budgets import aware_time
from pathlib import Path


def source_from_navigation(reference, parent):
    verify_navigation_reference(reference, parent)
    if reference['relationship'] == 'NEXT_PAGE':
        raise ValueError('THREE_NEXT_MUST_EXTEND_ORIGINAL_SOURCE')
    kind = parent['source_type']; subtype = parent['source_subtype']
    if kind == 'CUSTOMER_FAVORITES':
        subtype = ('CUSTOMER_FAVORITES_FILTERED_SEARCH' if urlsplit(reference['url']).path == '/s' else
                   'CUSTOMER_FAVORITES_CATEGORY_BROWSE' if urlsplit(reference['url']).path in {'/b','/gp/browse.html'}
                   else 'CUSTOMERS_MOST_LOVED_CURATED_ZONE')
    source = source_for_url(reference['url'], navigation_evidence=reference, source_type=kind, source_subtype=subtype)
    source.update(page_urls=[reference['url']], enabled=True, navigation_contract=CONTRACT,
                  navigation_parent=deepcopy(parent))
    return source


def verify_source(source, visited=None):
    visited = set() if visited is None else visited
    url = source['actual_source_url']
    if url in visited or len(visited) > 50:
        raise ValueError('THREE_NAVIGATION_LINEAGE_CYCLE_OR_LIMIT')
    if source['source_id'] != 'v4_' + hashlib.sha256(url.encode()).hexdigest()[:24]:
        raise ValueError('THREE_EXACT_SOURCE_ID_REQUIRED')
    historical=bool(source.get('historical_capture_reference'))
    if historical:
        from .three_archive import historical_context
        expected,_=historical_context(source['historical_capture_reference'],expected_url=url,expected_type=source['source_type'])
        if source['source_subtype']!=expected['source_subtype']:
            raise ValueError('THREE_ARCHIVE_SUBTYPE_MISMATCH')
    seed = next((s for s in SEEDS if s['url']==url and s['source_type'] in FAMILIES), None)
    if url == FAVORITES_ROOT:
        seed = {'source_type':'CUSTOMER_FAVORITES','source_subtype':'CUSTOMERS_MOST_LOVED_CURATED_ZONE'}
    if seed:
        if any(source[k] != seed[k] for k in ('source_type','source_subtype')):
            raise ValueError('THREE_ROOT_TYPE_MISMATCH')
    elif historical:
        pass
    elif source.get('navigation_contract') == CONTRACT:
        parent = source['navigation_parent']
        verify_source(parent, visited | {url})
        ref = source['navigation_evidence']; verify_navigation_reference(ref,parent)
        if ref['url'] != url or ref['relationship']=='NEXT_PAGE' or source['source_type'] != parent['source_type']:
            raise ValueError('THREE_SOURCE_LINEAGE_MISMATCH')
        expected = source_from_navigation(ref,parent)
        if source['source_subtype'] != expected['source_subtype']:
            raise ValueError('THREE_DISCOVERED_SUBTYPE_MISMATCH')
    else:
        # Backward-compatible saved T1/T2 rank navigation remains evidence, not
        # a seed batch. Its direct parent must itself be an explicit root.
        from .v4_navigation import verify_discovered_source
        verify_discovered_source(source)
        if (source.get('navigation_evidence') or {}).get('page_url') not in {s['url'] for s in SEEDS if s['source_type'] in FAMILIES}:
            raise ValueError('THREE_FULL_NAVIGATION_LINEAGE_REQUIRED')
    return verify_chain(source)


def validate_three_plan(plan):
    from .global_target import is_global_policy
    global_mode=is_global_policy(plan.get('target_policy'))
    expected_target=30000 if global_mode else 15003
    if (plan.get('collection_contract') != CONTRACT or plan.get('stage') != 'T3'
            or (type(plan.get('target_unique_links')) is not int or plan.get('target_unique_links') != expected_target)
            or plan.get('source_targets') != {k:5001 for k in FAMILIES}
            or any(type(v) is not int for v in plan['source_targets'].values())
            or not plan['sources'] or not {s['source_type'] for s in plan['sources']}.issubset(FAMILIES)
            or any(plan.get(k) for k in ('seed_reference','parent_run_id','continuation_mode'))):
        raise ValueError('THREE_INDEPENDENT_5001_FRESH_TARGETS_REQUIRED')
    if not isinstance(plan.get('campaign_id'),str) or not plan['campaign_id'].startswith('three_source_5001_'):
        raise ValueError('THREE_CAMPAIGN_ID_REQUIRED')
    aware_time(plan['campaign_created_at'])
    task=plan.get('task_reference') or {}
    if not Path(task.get('path','__missing__')).is_file() or hashlib.sha256(Path(task['path']).read_bytes()).hexdigest()!=task.get('sha256'):
        raise ValueError('THREE_EXACT_TASK_REFERENCE_REQUIRED')
    budgets = plan.get('source_budgets', {})
    if set(budgets) != set(FAMILIES): raise ValueError('THREE_EXACT_FAMILY_BUDGETS_REQUIRED')
    for kind, limits in budgets.items():
        if set(limits) != {'requests','PAGE','native'}:
            raise ValueError('THREE_FINITE_FAMILY_BUDGET_REQUIRED')
        if any(type(v) is not int or v < 0 for v in limits.values()):
            raise ValueError('THREE_FINITE_FAMILY_BUDGET_REQUIRED')
        if any(s['source_type']==kind for s in plan['sources']) and (limits['requests']<1 or limits['PAGE']<1):
            raise ValueError('THREE_ACTIVE_FAMILY_POSITIVE_BUDGET_REQUIRED')
        if limits['requests'] < limits['PAGE'] + limits['native']:
            raise ValueError('THREE_FAMILY_ENVELOPE_EXCEEDS_REQUESTS')
        if kind == 'CUSTOMER_FAVORITES' and limits['native'] != 0:
            raise ValueError('THREE_FAVORITES_ONLY_EVIDENCED_PAGE_NAVIGATION')
    if (sum(b['requests'] for b in budgets.values()) != plan['max_requests_per_run']
            or plan['max_requests_per_day'] != plan['max_requests_per_run']
            or sum(b['PAGE'] for b in budgets.values()) != plan['max_pages_per_run']):
        raise ValueError('THREE_FAMILY_TOTALS_MUST_MATCH_GLOBAL_LIMITS')
    urls = [u for s in plan['sources'] for u in s['page_urls']]
    if len(set(urls)) != len(urls):raise ValueError('THREE_DUPLICATE_EXACT_PAGE_SCOPE')
    return plan


def family_budget_reason(plan, source, reservations, kind):
    family = source['source_type']; limits = plan['source_budgets'][family]
    rows = [r for r in reservations if r['source_type']==family]
    if len(rows) >= limits['requests']:return 'THREE_SOURCE_REQUEST_BUDGET_EXHAUSTED:'+family
    if kind=='PAGE' and sum(r['kind']=='PAGE' for r in rows) >= limits['PAGE']:
        return 'THREE_SOURCE_PAGE_BUDGET_EXHAUSTED:'+family
    if kind=='ACP' and sum(r['kind']=='ACP' for r in rows) >= limits['native']:
        return 'THREE_SOURCE_NATIVE_BUDGET_EXHAUSTED:'+family
    return None


def family_budget_for_store(store,plan,source,kind):
    counts=dict(store.db.execute("SELECT kind,count(*) FROM requests WHERE run_id=? "
        "AND json_extract(data_json,'$.source_type')=? GROUP BY kind",(plan['run_id'],source['source_type'])))
    limits=plan['source_budgets'][source['source_type']];family=source['source_type']
    if sum(counts.values())>=limits['requests']:return 'THREE_SOURCE_REQUEST_BUDGET_EXHAUSTED:'+family
    if kind=='PAGE' and counts.get('PAGE',0)>=limits['PAGE']:return 'THREE_SOURCE_PAGE_BUDGET_EXHAUSTED:'+family
    if kind=='ACP' and counts.get('ACP',0)>=limits['native']:return 'THREE_SOURCE_NATIVE_BUDGET_EXHAUSTED:'+family
    return None
