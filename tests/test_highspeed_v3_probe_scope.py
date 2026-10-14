"""Offline scheduler seams; never current Amazon card/throughput proof."""
import json
import hashlib
from pathlib import Path
from datetime import timedelta
import pytest
from test_highspeed_runner import plan,approval,Store,NOW,make_runner
from amazon_es_bestseller.highspeed.v3_links import NORMALIZER_VERSION
from amazon_es_bestseller.highspeed.budgets import validate_plan
from amazon_es_bestseller.highspeed.authorization import validate_authorization

SCENARIO=json.loads((Path(__file__).parent/'fixtures/highspeed/v3_t1_probe_policy_scenario.json').read_text())


def setup(tmp_path, flag=True):
    p=plan(tmp_path,'T1');p.update(snapshot_contract_version=3,normalizer_version=NORMALIZER_VERSION,
        origin='LIVE_CAPTURE',target_unique_links=500,probe_all_reviewed_page_urls=flag)
    p['sources']=[{'source_id':str(i),'source_type':kind,'research_category':None,
        'parser_profile':'V3','page_urls':[url]} for i,(kind,url) in enumerate(SCENARIO['pages'])]
    a=approval(p,tmp_path);proof=a['sources']['home']['evidence']
    a['sources']={s['source_id']:{**s,'evidence':proof} for s in p['sources']}
    class ReceiptStore(Store):
        def save_response(self,run_id,reservation,response):
            receipt=super().save_response(run_id,reservation,response)
            receipt.update(source_id=reservation['source_id'],source_type=reservation['source_type'],request_url=reservation['url'])
            return receipt
    s=ReceiptStore(tmp_path/'store');clock=[NOW]
    def sleep(seconds):clock[0]+=timedelta(seconds=seconds)
    def parser(html,**kw):
        count=SCENARIO['first_page_observations'] if kw['source']['source_id']=='0' else 1
        return {'page':{'status':'COMPLETE','actual_count':count,'completeness':'COMPLETE'},
            'observations':[{'raw_product_url':f'https://www.amazon.es/dp/scheduler-{kw["source"]["source_id"]}-{i}'} for i in range(count)]}
    class Transport:
        def __init__(self):self.calls=[];self.status=200
        def fetch_page(self,url):
            self.calls.append(url)
            return {'status_code':self.status,'url':url,'text':'Robot Check' if self.status==403 else 'normal'}
    t=Transport()
    runner=make_runner(s,clock=lambda:clock[0],sleep=sleep,parser=parser,gate_reader=lambda:{'state':'NORMAL'})
    return p,a,s,t,runner


def test_t1_all_exact_probes_continue_after_first_page_exceeds_target(tmp_path):
    p,a,s,t,runner=setup(tmp_path)
    result=runner.run(p,authorization=a,transport=t)
    assert t.calls==[url for kind,url in SCENARIO['pages']]
    assert result['run']['probe_scope']['complete'] is True
    assert result['run']['probe_scope']['attempted_page_count']==9
    assert result['run']['stop_reason']=='SOURCE_PLAN_EXHAUSTED'


def test_target_threshold_unchanged_when_probe_flag_false(tmp_path):
    p,a,s,t,runner=setup(tmp_path,False)
    result=runner.run(p,authorization=a,transport=t)
    assert len(t.calls)==1
    assert result['run']['stop_reason']=='TARGET_UNIQUE_LINKS_REACHED'


@pytest.mark.parametrize('field,value',[('probe_all_reviewed_page_urls','true'),('probe_all_reviewed_page_urls',1),('stage','T2'),('snapshot_contract_version',2)])
def test_probe_flag_rejects_nonbool_or_other_stages_and_versions(tmp_path,field,value):
    p,a,s,t,runner=setup(tmp_path);p[field]=value
    with pytest.raises(ValueError,match='PROBE'):
        validate_plan(p)


def test_probe_flag_needs_exact_authorization_review(tmp_path):
    p,a,s,t,runner=setup(tmp_path);a['probe_all_reviewed_page_urls']=False
    with pytest.raises(ValueError,match='PROBE'):
        validate_authorization(p,a,{'state':'NORMAL'},now=NOW,store_root=s.root)


def test_all_probe_policy_does_not_bypass_restrictions_or_budget(tmp_path):
    p,a,s,t,runner=setup(tmp_path);t.status=403
    result=runner.run(p,authorization=a,transport=t)
    assert len(t.calls)==1 and result['run']['probe_scope']['complete'] is False
    assert result['run']['stop_reason']=='CHALLENGE'


def test_all_probe_policy_stops_at_original_request_budget(tmp_path):
    p,a,s,t,runner=setup(tmp_path);p['max_requests_per_run']=1;a['max_requests_per_run']=1
    result=runner.run(p,authorization=a,transport=t)
    assert len(t.calls)==1 and result['run']['probe_scope']['complete'] is False
    assert result['run']['stop_reason']=='RUN_REQUEST_BUDGET_EXHAUSTED'


def test_all_probe_resume_reuses_committed_pages_without_second_requests(tmp_path):
    p,a,s,t,runner=setup(tmp_path)
    runner.run(p,authorization=a,transport=t)
    result=runner.run(p,authorization=a,transport=t,resume=True)
    assert len(t.calls)==9 and len(s.requests)==9
    assert result['run']['probe_scope']['processed_page_count']==9
    assert result['run']['probe_scope']['complete'] is True


def test_all_probe_policy_preserves_parser_unknown_stop(tmp_path):
    p,a,s,t,runner=setup(tmp_path)
    runner.parser=lambda *args,**kw:{'page':{'status':'FAILED','completeness':'UNKNOWN','actual_count':0},'observations':[]}
    result=runner.run(p,authorization=a,transport=t)
    assert len(t.calls)==1
    assert result['run']['stop_reason']=='PAGE_PARSE_OR_ACCESS_FAILED'
    assert result['run']['probe_scope']['attempted_page_count']==1
    assert result['run']['probe_scope']['processed_page_count']==0
    assert not result['run']['probe_scope']['complete']


def test_exact_stage_probe_scope_cannot_be_filled_by_repeats_or_ACP(tmp_path):
    from amazon_es_bestseller.highspeed.authorization import _verify_t1_probe_scope_receipts
    p,a,s,t,runner=setup(tmp_path)
    receipts=[{'source_id':source['source_id'],'request_url':source['page_urls'][0],'kind':'PAGE'} for source in p['sources']]
    _verify_t1_probe_scope_receipts(p,receipts)
    with pytest.raises(ValueError,match='PROBE_SCOPE_INCOMPLETE'):
        _verify_t1_probe_scope_receipts(p,receipts[:-1]+[receipts[0]])
    with pytest.raises(ValueError,match='PROBE_SCOPE_INCOMPLETE'):
        _verify_t1_probe_scope_receipts(p,receipts[:-1]+[{**receipts[-1],'kind':'ACP'}])
    with pytest.raises(ValueError,match='PROBE_SCOPE_INCOMPLETE'):
        _verify_t1_probe_scope_receipts(p,receipts[:-1]+[{k:v for k,v in receipts[-1].items() if k!='kind'}])


@pytest.mark.parametrize('stage,version',[('T0',3),('T2',3),('T3',3),('T1',2)])
def test_false_probe_policy_does_not_change_other_plan_versions_and_stages(tmp_path,stage,version):
    p,a,s,t,runner=setup(tmp_path,False)
    p.update(stage=stage,snapshot_contract_version=version)
    if stage=='T3':p['new_batch_required']=True
    assert validate_plan(p) is p
    p.pop('probe_all_reviewed_page_urls')
    assert validate_plan(p) is p


def test_offline_t0_keeps_normal_target_stop(tmp_path):
    p,a,s,t,runner=setup(tmp_path,False)
    p.update(stage='T0',origin='OFFLINE_FIXTURE',live_enabled=False)
    html=tmp_path/'saved.html'
    for source in p['sources']:source['offline_pages']={source['page_urls'][0]:str(html)}
    result=runner.run(p)
    assert not t.calls and len(s.requests)==1
    assert result['run']['stop_reason']=='TARGET_UNIQUE_LINKS_REACHED'


def test_probe_shared_gate_during_pacing_stops_after_first_page(tmp_path):
    p,a,s,t,runner=setup(tmp_path)
    gate={'state':'NORMAL'};runner.gate_reader=lambda:gate
    old_sleep=runner.sleep
    def sleep(seconds):old_sleep(seconds);gate['state']='RATE_LIMITED'
    runner.sleep=sleep
    result=runner.run(p,authorization=a,transport=t)
    assert len(t.calls)==1
    assert not result['run']['probe_scope']['complete']
    assert result['run']['stop_reason']=='GLOBAL_ACCESS_GATE_BLOCKED_NO_AUTOMATIC_RESET'


def test_probe_summary_uses_exact_source_url_pairs_and_excludes_unrelated_complete_pages():
    from amazon_es_bestseller.highspeed.runner import _probe_scope_summary
    url='https://www.amazon.es/gp/bestsellers'
    # Counter robustness only: validate_plan separately forbids duplicate physical URLs.
    p={'sources':[{'source_id':sid,'page_urls':[url]} for sid in ('first','second')]}
    key=lambda sid:hashlib.sha256((sid+'\0'+url).encode()).hexdigest()
    bundle={'responses':[{'source_id':'first','request_url':url,'kind':'PAGE'},
        {'source_id':'second','request_url':url,'kind':'ACP'},
        {'source_id':'second','request_url':url}],
        'pages':[{'page_key':key('first'),'status':'COMPLETE'},{'page_key':'unrelated','status':'COMPLETE'}]}
    result=_probe_scope_summary(p,bundle,{key('first')},'SOURCE_PLAN_EXHAUSTED')
    assert result['expected_page_count']==2 and result['attempted_page_count']==1
    assert result['processed_page_count']==1 and result['complete_page_count']==1
    assert result['complete'] is False


def test_current_plan_rejects_shared_physical_page_under_two_source_ids(tmp_path):
    p,a,s,t,runner=setup(tmp_path)
    p['sources'][1]['page_urls']=list(p['sources'][0]['page_urls'])
    with pytest.raises(ValueError,match='DUPLICATE_PHYSICAL_SOURCE_PAGE'):
        validate_plan(p)
