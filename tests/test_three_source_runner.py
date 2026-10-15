"""TEST_ONLY transport over actual saved DOM; never live website evidence."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import hashlib
import pytest
from test_three_source_contract import scope
from test_highspeed_v4_execution import FakeTransport, NOW
from amazon_es_bestseller.highspeed.three_runner import V4ThreeSourceRunner
from amazon_es_bestseller.highspeed.three_contract import CONTRACT,FAMILIES,source_from_navigation
from amazon_es_bestseller.highspeed.three_navigation import discover_navigation
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.multisource.storage import digest


def execution_scope(tmp_path,monkeypatch):
    p,a=scope(tmp_path)
    p['sources']=[next(s for s in p['sources'] if s['source_type']==k) for k in FAMILIES]
    p.update(source_budgets={k:{'requests':1,'PAGE':1,'native':0} for k in FAMILIES},
        max_requests_per_run=3,max_requests_per_day=3,max_pages_per_run=3)
    a.update(stage='T3',collection_contract=CONTRACT,source_targets=deepcopy(p['source_targets']),
        campaign_id=p['campaign_id'],campaign_created_at=p['campaign_created_at'],task_reference=p['task_reference'],
        source_budgets=deepcopy(p['source_budgets']),sources={s['source_id']:a['sources'][s['source_id']] for s in p['sources']})
    for k in ('max_requests_per_run','max_requests_per_day','max_pages_per_run','target_unique_links'):a[k]=p[k]
    a['plan_sha256']=digest(p)
    monkeypatch.setattr('amazon_es_bestseller.highspeed.v4_authorization._verify_prerequisite',lambda *a:None)
    return p,a


class Advancing(FakeTransport):
    def __init__(self,p,a,clock,**kw):super().__init__(p,a,**kw);self.clock=clock
    def fetch_page(self,url):
        v=super().fetch_page(url);self.clock[0]+=timedelta(seconds=5);return v


def test_same_run_resume_scope_checkpoint_and_real_dom_audit(tmp_path,monkeypatch):
    p,a=execution_scope(tmp_path,monkeypatch);store=RunStore(a['store_root']);clock=[NOW]
    try:
        transport=Advancing(p,a,clock);runner=V4ThreeSourceRunner(store,clock=lambda:clock[0],sleep=lambda n:None)
        result=runner.run(p,authorization=a,transport=transport)
        assert len(transport.calls)==3 and result['quality']['central_full_dom_review_passed']
        assert result['receipt_audit']['diagnostic_passed'],result['receipt_audit']['errors']
        assert result['acceptance']=='NOT_ACCEPTED — THREE_SOURCE_5001_TARGET_NOT_MET'
        assert all(v['E_qualified_review_groups']>0 for v in result['metrics']['sources'].values())
        assert len(list((store.root/'runs'/p['run_id']/'navigation').glob('*.json')))==3
        clock[0]+=timedelta(seconds=5)
        again=runner.run(p,authorization=a,transport=transport,resume=True)
        assert len(transport.calls)==3 and again['run']['started_at']==result['run']['started_at']
        assert again['metrics']==result['metrics']
        counters=store.budget_counters(p['run_id']);summary=store.summary(p['run_id'])
        assert all(counters[k]==summary[k] for k in counters)
    finally:store.close()


@pytest.mark.parametrize('code',[403,429,503])
def test_restriction_or_unknown_stops_all_sources(tmp_path,monkeypatch,code):
    p,a=execution_scope(tmp_path,monkeypatch);store=RunStore(a['store_root']);clock=[NOW]
    try:
        t=Advancing(p,a,clock,code=code)
        r=V4ThreeSourceRunner(store,clock=lambda:clock[0],sleep=lambda n:None).run(p,authorization=a,transport=t)
        assert len(t.calls)==1 and store.get_gate()['state']!='NORMAL'
        assert r['run']['quality_gate_passed'] is False
        with pytest.raises(ValueError,match='GATE_BLOCKED'):
            V4ThreeSourceRunner(store,clock=lambda:clock[0]).run(p,authorization=a,transport=t,resume=True)
        assert len(t.calls)==1
    finally:store.close()


def test_one_family_cap_does_not_spend_others_or_reset_on_resume(tmp_path,monkeypatch):
    p,a=execution_scope(tmp_path,monkeypatch)
    best=p['sources'][0];path=Path(a['sources'][best['source_id']]['evidence']['path'])
    page={'source_id':best['source_id'],'source_type':best['source_type'],'source_subtype':best['source_subtype'],
        'page_url':best['actual_source_url'],'evidence_path':str(path),'evidence_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'observed_at':'2026-10-14T15:00:00Z','status':'VERIFIED_WITH_PRODUCTS','http_status':200,'access_status':'NORMAL'}
    ref=next(r for r in discover_navigation(page,best) if '/kitchen/' in r['url'])
    extra=source_from_navigation(ref,best);p['sources'].insert(1,extra)
    a['sources'][extra['source_id']]={**deepcopy(a['sources'][best['source_id']]),'page_urls':extra['page_urls']}
    a['plan_sha256']=digest(p)
    store=RunStore(a['store_root']);clock=[NOW]
    try:
        t=Advancing(p,a,clock);runner=V4ThreeSourceRunner(store,clock=lambda:clock[0],sleep=lambda n:None)
        r=runner.run(p,authorization=a,transport=t)
        assert len(t.calls)==3 and extra['actual_source_url'] not in t.calls
        assert any(e['kind']=='SOURCE_BUDGET_STOP' for e in store.events(p['run_id']))
        assert {q['source_type'] for q in store.requests(p['run_id'])}==set(FAMILIES)
        again=runner.run(p,authorization=a,transport=t,resume=True)
        assert len(t.calls)==3 and again['run']['started_at']==r['run']['started_at']
        assert again['run']['quality_gate_passed'] is False
    finally:store.close()
