from copy import deepcopy
import pytest
from test_three_source_contract import scope
from test_three_source_metrics import real_rows
from amazon_es_bestseller.highspeed.three_contract import validate_three_plan
from amazon_es_bestseller.highspeed.three_metrics import measure_three

POLICY={'mode':'GLOBAL_CONSERVATIVE_REVIEW_GROUPS','target':30000,'fresh_live_only':True,'stop_at_page_boundary':True}

def global_plan(tmp_path):
    p,a=scope(tmp_path)
    p.update(target_policy=deepcopy(POLICY),target_unique_links=30000)
    return p,a

def test_exact_global_target_is_explicit_and_old_contract_stays_strict(tmp_path):
    p,_=global_plan(tmp_path);validate_three_plan(p)
    del p['target_policy']
    with pytest.raises(ValueError):validate_three_plan(p)
    p,_=scope(tmp_path);validate_three_plan(p)

@pytest.mark.parametrize('change',[{'target':29999},{'target':30000.0},{'fresh_live_only':False},{'stop_at_page_boundary':False},{'extra':True}])
def test_no_reduced_or_implicit_global_goal(tmp_path,change):
    p,_=global_plan(tmp_path);p['target_policy'].update(change)
    with pytest.raises(ValueError):validate_three_plan(p)

def test_global_metric_does_not_sum_source_groups_or_count_history():
    rows=real_rows(); copies=deepcopy(rows)
    for r in copies:r['source_type']='BEST_SELLERS'
    result=measure_three(rows+copies,'TEST_ONLY',target_policy=POLICY)
    assert result['E_sum_source_review_groups']==2*result['F_global_conservative_review_groups']
    assert result['global_shortfall']==30000-result['F_global_conservative_review_groups']
    assert not result['target_met']
    assert result['acceptance']=='NOT_ACCEPTED — GLOBAL_30000_TARGET_NOT_MET'
    old=deepcopy(rows)
    for r in old:r.update(run_id='OLD',origin='HISTORICAL_REPLAY')
    assert measure_three(rows+copies+old,'TEST_ONLY',target_policy=POLICY)['global_shortfall']==result['global_shortfall']


def test_global_mode_cannot_count_multiple_runs():
    with pytest.raises(ValueError,match='GLOBAL_FRESH_SINGLE_RUN_REQUIRED'):
        measure_three(real_rows(),'TEST_ONLY',allowed_run_ids={'TEST_ONLY','OLD'},target_policy=POLICY)


def test_global_acceptance_uses_union_threshold_and_still_requires_quality(monkeypatch):
    from amazon_es_bestseller.highspeed import three_metrics as module
    from amazon_es_bestseller.highspeed.global_target import acceptance
    row=real_rows()[0]
    # Accounting boundary only; TEST_ONLY candidate projections are not DOM evidence.
    for n in (29999,30000):
        monkeypatch.setattr(module,'build_candidates_v4',lambda rows,n=n:([
            {'target_review_group':'TEST_ONLY_'+str(i),'eligible_for_target':True,'normalized_product_url':'TEST_ONLY_'+str(i)}
            for i in range(n)] if rows else []))
        m=measure_three([row],'TEST_ONLY',target_policy=POLICY)
        assert m['target_met']==(n==30000)
        assert not acceptance(m,False).startswith('ACCEPTED')
        assert acceptance(m,True).startswith('ACCEPTED')==(n==30000)


def test_global_policy_bound_in_authorization(tmp_path,monkeypatch):
    from test_three_source_runner import execution_scope
    from test_highspeed_v4_execution import NOW
    from amazon_es_bestseller.highspeed.v4_authorization import validate_v4_authorization
    from amazon_es_bestseller.multisource.storage import digest
    p,a=execution_scope(tmp_path,monkeypatch)
    p.update(target_policy=deepcopy(POLICY),target_unique_links=30000)
    a.update(plan_sha256=digest(p),target_unique_links=30000)
    with pytest.raises(ValueError,match='THREE_EXACT_CAMPAIGN_APPROVAL_REQUIRED:target_policy'):
        validate_v4_authorization(p,a,{'state':'NORMAL'},now=NOW,store_root=a['store_root'])
    a['target_policy']=deepcopy(POLICY)
    validate_v4_authorization(p,a,{'state':'NORMAL'},now=NOW,store_root=a['store_root'])


def test_global_run_and_export_keep_separate_target_and_source_diagnostics(tmp_path,monkeypatch):
    from test_three_source_runner import execution_scope,Advancing
    from test_highspeed_v4_execution import NOW
    from amazon_es_bestseller.highspeed.store import RunStore
    from amazon_es_bestseller.highspeed.three_runner import V4ThreeSourceRunner
    from amazon_es_bestseller.highspeed.three_report import export_three
    from amazon_es_bestseller.multisource.storage import digest
    p,a=execution_scope(tmp_path,monkeypatch)
    p.update(target_policy=deepcopy(POLICY),target_unique_links=30000)
    a.update(target_policy=deepcopy(POLICY),plan_sha256=digest(p),target_unique_links=30000)
    store=RunStore(a['store_root']);clock=[NOW]
    try:
        t=Advancing(p,a,clock)
        r=V4ThreeSourceRunner(store,clock=lambda:clock[0],sleep=lambda n:None).run(p,authorization=a,transport=t)
        assert len(t.calls)==3
        assert r['acceptance']=='NOT_ACCEPTED — GLOBAL_30000_TARGET_NOT_MET'
        assert r['quality']['central_full_dom_review_passed']
        result=export_three(store,p['run_id'],tmp_path/'export')
        assert result['acceptance']=='NOT_ACCEPTED — GLOBAL_30000_TARGET_NOT_MET'
        assert result['metrics']['global_shortfall']>0
    finally:store.close()


def test_global_stop_at_page_boundary_never_requests_next_source(tmp_path,monkeypatch):
    from test_three_source_runner import execution_scope,Advancing
    from test_highspeed_v4_execution import NOW
    from amazon_es_bestseller.highspeed.store import RunStore
    from amazon_es_bestseller.highspeed.three_runner import V4ThreeSourceRunner
    from amazon_es_bestseller.multisource.storage import digest
    p,a=execution_scope(tmp_path,monkeypatch)
    p.update(target_policy=deepcopy(POLICY),target_unique_links=30000)
    a.update(target_policy=deepcopy(POLICY),plan_sha256=digest(p),target_unique_links=30000)
    store=RunStore(a['store_root']);clock=[NOW]
    try:
        t=Advancing(p,a,clock);runner=V4ThreeSourceRunner(store,clock=lambda:clock[0],sleep=lambda n:None)
        original=runner.after_page
        def at_boundary(*args):
            original(*args)
            runner._global_target_met=True # TEST_ONLY reach boundary after first complete page.
        monkeypatch.setattr(runner,'after_page',at_boundary)
        r=runner.run(p,authorization=a,transport=t)
        assert len(t.calls)==1
        assert r['run']['stop_reason']=='GLOBAL_30000_QUALIFIED_GROUPS_REACHED'
        # Final audit independently counts actual fixture, never trusts stop flag.
        assert not r['metrics']['target_met'] and not r['run']['quality_gate_passed']
    finally:store.close()


def test_preparation_finite_limits_exact_scope_and_no_inherited_approval(tmp_path):
    from amazon_es_bestseller.highspeed.global_prepare import build_global_scope,FAMILY_LIMITS
    p,a=scope(tmp_path);sources=[]
    for kind,n in [('BEST_SELLERS',677),('NEW_RELEASES',200),('CUSTOMER_FAVORITES',20)]:
        for i in range(n):
            u='https://TEST_ONLY.invalid/'+kind+'/'+str(i)
            sources.append({'source_type':kind,'source_id':'TEST_ONLY_'+kind+str(i),'page_urls':[u]})
    for i in range(205):sources[i]['page_urls'].append(sources[i]['page_urls'][0]+'/page2')
    proposal={'kind':'V4_GLOBAL_30000_TEN_HOUR_SCOPE_PROPOSAL',
        'target_global_conservative_review_groups':30000,'sources':sources,
        'exact_page_URLs':[u for src in sources for u in src['page_urls']],
        'suggested_family_limits':deepcopy(FAMILY_LIMITS)}
    result,auth=build_global_scope(p,a,proposal,run_id='TEST_ONLY_GLOBAL',
        created_at=p['campaign_created_at'],task_reference=p['task_reference'])
    assert result['sources']==sources and result['sources'] is not sources
    assert result['max_requests_per_run']==6000 and result['max_pages_per_run']==1200
    assert result['max_duration_hours']==8.65 and result['request_pacing_seconds']==3
    assert auth['approved'] is False and auth['approval_id'] is None
    assert auth['sources']=={} and auth['reviewed_at'] is None and auth['expires_at'] is None
    assert p['target_unique_links']==15003 and a['approved'] is True
    bad=deepcopy(proposal);bad['sources'][0]['page_urls'].append('https://TEST_ONLY.invalid/extra')
    with pytest.raises(ValueError):build_global_scope(p,a,bad,run_id='TEST_ONLY',created_at=p['campaign_created_at'],task_reference=p['task_reference'])
