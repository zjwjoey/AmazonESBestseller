from copy import deepcopy
import pytest
from amazon_es_bestseller.highspeed import three_contract as contract
from amazon_es_bestseller.multisource.storage import digest
from test_highspeed_v4_execution import setup_scope, NOW
import hashlib


def scope(tmp_path):
    plan, auth = setup_scope(tmp_path)
    auth['reviewed_at']='2026-10-14T14:59:30Z'
    task=tmp_path/'TEST_ONLY_task.txt';task.write_text('TEST_ONLY three-source task','utf8')
    plan.update(stage='T3', collection_contract=contract.CONTRACT, target_unique_links=15003,
        campaign_id='three_source_5001_TEST_ONLY',campaign_created_at='2026-10-14T14:59:00Z',
        task_reference={'path':str(task),'sha256':hashlib.sha256(task.read_bytes()).hexdigest()},
        source_targets={k:5001 for k in contract.FAMILIES},
        source_budgets={k:{'requests':20,'PAGE':10,'native':0 if k=='CUSTOMER_FAVORITES' else 10} for k in contract.FAMILIES},
        max_requests_per_run=60,max_requests_per_day=60,max_pages_per_run=30)
    plan['sources'] = [s for s in plan['sources'] if s['source_type'] in contract.FAMILIES]
    return plan,auth


def test_exact_three_families_and_independent_targets(tmp_path):
    plan,_=scope(tmp_path); contract.validate_three_plan(plan)
    for change in ('target','family','seed','total','float','goal'):
        bad=deepcopy(plan)
        if change=='target':bad['source_targets']['NEW_RELEASES']=5000
        elif change=='family':bad['sources'][0]['source_type']='DEALS'
        elif change=='seed':bad['seed_reference']={'old':True}
        elif change=='total':bad['max_requests_per_run']=61
        elif change=='float':bad['source_budgets']['BEST_SELLERS']['requests']=20.0
        else:bad['target_unique_links']=5001
        with pytest.raises(ValueError):contract.validate_three_plan(bad)


def test_family_request_budget_is_cumulative_and_separate(tmp_path):
    plan,_=scope(tmp_path); source=plan['sources'][0]
    rows=[{'source_type':source['source_type'],'kind':'PAGE'} for _ in range(10)]
    assert contract.family_budget_reason(plan,source,rows,'PAGE')=='THREE_SOURCE_PAGE_BUDGET_EXHAUSTED:BEST_SELLERS'
    assert contract.family_budget_reason(plan,source,rows,'ACP') is None
    rows += [{'source_type':source['source_type'],'kind':'ACP'} for _ in range(10)]
    assert contract.family_budget_reason(plan,source,rows,'ACP').startswith('THREE_SOURCE_REQUEST_BUDGET_EXHAUSTED')
    other=next(s for s in plan['sources'] if s['source_type']=='NEW_RELEASES')
    assert contract.family_budget_reason(plan,other,rows,'PAGE') is None


def test_v4_general_cli_still_rejects_unmarked_T3_and_checks_task_contract(tmp_path):
    from amazon_es_bestseller.highspeed.v4_authorization import validate_plan_v4
    plan,_=scope(tmp_path);validate_plan_v4(plan)
    plan['source_targets']['BEST_SELLERS']=1
    with pytest.raises(ValueError):validate_plan_v4(plan)


def test_actual_new_favorites_root_is_not_added_to_old_eight_seed_contract():
    from amazon_es_bestseller.highspeed.v4_sources import source_for_url, SEEDS
    source=source_for_url(contract.FAVORITES_ROOT)
    assert source['source_type']=='CUSTOMER_FAVORITES'
    assert len(SEEDS)==8 and not source['enabled']


def test_campaign_authorization_requires_same_targets_and_family_budgets(tmp_path, monkeypatch):
    from amazon_es_bestseller.highspeed.v4_authorization import validate_v4_authorization
    plan,a=scope(tmp_path)
    a.update(stage='T3',sources={k:v for k,v in a['sources'].items() if k in {s['source_id'] for s in plan['sources']}},
        collection_contract=contract.CONTRACT,source_targets=deepcopy(plan['source_targets']),
        campaign_id=plan['campaign_id'],campaign_created_at=plan['campaign_created_at'],task_reference=plan['task_reference'],
        source_budgets=deepcopy(plan['source_budgets']),plan_sha256=digest(plan))
    for k in ('target_unique_links','max_requests_per_run','max_requests_per_day','max_pages_per_run'):a[k]=plan[k]
    monkeypatch.setattr('amazon_es_bestseller.highspeed.v4_authorization._verify_prerequisite',lambda *args:None)
    validate_v4_authorization(plan,a,{'state':'NORMAL'},now=NOW,store_root=a['store_root'])
    a['source_targets']['NEW_RELEASES']=5000
    with pytest.raises(ValueError,match='THREE_EXACT_CAMPAIGN_APPROVAL_REQUIRED'):
        validate_v4_authorization(plan,a,{'state':'NORMAL'},now=NOW,store_root=a['store_root'])
