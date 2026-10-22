from copy import deepcopy
from test_three_source_metrics import real_rows
from amazon_es_bestseller.highspeed import three_metrics as module
from amazon_es_bestseller.highspeed.global_target import POLICY


def test_global_thirty_thousand_alone_must_not_meet_four_goals(monkeypatch):
    row=real_rows()[0]
    monkeypatch.setattr(module,'build_candidates_v4',lambda rows:([
        {'target_review_group':'TEST_ONLY_'+str(i),'eligible_for_target':True,'normalized_product_url':'TEST_ONLY_'+str(i)}
        for i in range(30000)] if rows else []))
    m=module.measure_three([row],'TEST_ONLY',target_policy=POLICY)
    assert m['F_global_conservative_review_groups']==30000
    assert not m['target_met']
    assert m['quantity_goals']['BEST_SELLERS']['shortfall']==5001
    assert m['quantity_goals']['NEW_RELEASES']['shortfall']==5001


def test_joint_goal_all_sources_and_global_union_must_pass(monkeypatch):
    row=real_rows()[0];rows=[]
    for kind in ['BEST_SELLERS','NEW_RELEASES','CUSTOMER_FAVORITES']:
        r=deepcopy(row);r['source_type']=kind;rows.append(r)
    def candidates(rows):
        if not rows:return []
        kind=rows[0]['source_type']
        return [{'target_review_group':'TEST_ONLY_'+kind+str(i),'eligible_for_target':True,
                 'normalized_product_url':'TEST_ONLY_'+kind+str(i)} for i in range(10000)]
    monkeypatch.setattr(module,'build_candidates_v4',candidates)
    m=module.measure_three(rows,'TEST_ONLY',target_policy=POLICY)
    assert m['target_met']
    assert all(v['met'] for v in m['quantity_goals'].values())


def test_after_global_target_only_deficit_families_continue():
    from amazon_es_bestseller.highspeed.three_runner import V4ThreeSourceRunner
    from types import SimpleNamespace
    runner=V4ThreeSourceRunner(None)
    runner._global_target_met=False
    runner._global_metrics={'F_global_conservative_review_groups':30000,'sources':{
        'BEST_SELLERS':{'target_met':True},'NEW_RELEASES':{'target_met':False},'CUSTOMER_FAVORITES':{'target_met':False}}}
    plan={'target_policy':POLICY}
    assert not runner.should_collect_source(plan,{'source_type':'BEST_SELLERS'})
    assert runner.should_collect_source(plan,{'source_type':'NEW_RELEASES'})
    assert runner.should_collect_source(plan,{'source_type':'CUSTOMER_FAVORITES'})
