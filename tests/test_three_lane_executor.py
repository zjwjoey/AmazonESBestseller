from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, get_ident
from datetime import timedelta
from types import SimpleNamespace
import pytest
from amazon_es_bestseller.highspeed.three_lane import OwnerThreadBroker, RequestCoordinator, lane_sources
from amazon_es_bestseller.highspeed.store import RunStore
from test_three_source_runner import execution_scope
from test_highspeed_v4_execution import NOW


def test_family_reservation_is_atomic_across_three_connections(tmp_path,monkeypatch):
    p,a=execution_scope(tmp_path,monkeypatch)
    store=RunStore(a['store_root']);store.begin_run(p,started_at=NOW.isoformat());store.close()
    barrier=Barrier(3);source=p['sources'][0]
    def reserve(_):
        s=RunStore(a['store_root'])
        try:
            barrier.wait(timeout=10)
            try:
                s.reserve_request(p['run_id'],{'kind':'PAGE','source_id':source['source_id'],
                    'source_type':source['source_type'],'url':source['actual_source_url'],
                    'observed_at':NOW.isoformat(),'limits':p})
                return 'reserved'
            except ValueError as e:return str(e)
        finally:s.close()
    with ThreadPoolExecutor(3) as pool:results=list(pool.map(reserve,range(3)))
    assert results.count('reserved')==1,results
    assert sum('THREE_SOURCE_' in r for r in results)==2


def test_owner_broker_isolates_transports_and_exception_identity():
    owner=get_ident();calls=[];error=ValueError('real native error')
    class Transport:
        def __init__(self,i):self.i=i
        def prepare_ajax_descriptor(self,d,**kw):
            assert get_ident()==owner
            calls.append((self.i,d['parent']))
            if self.i==1:raise error
            return {**d,'lane':self.i}
    b=OwnerThreadBroker([Transport(i) for i in range(3)])
    def invoke(i):
        try:return b.proxy(i).prepare_ajax_descriptor({'parent':str(i)})
        except ValueError as e:return e
    with ThreadPoolExecutor(3) as pool:
        fs=[pool.submit(invoke,i) for i in range(3)]
        while not all(f.done() for f in fs):b.pump()
        values=[f.result() for f in fs]
    assert values[1] is error
    assert values[0]=={'parent':'0','lane':0}
    assert values[2]=={'parent':'2','lane':2}
    assert set(calls)=={(0,'0'),(1,'1'),(2,'2')}
    b.close()
    with pytest.raises(ValueError):b.proxy(0).fetch_page('x')


def test_lane_sources_keeps_all_pages_on_one_lane():
    sources=[{'source_id':str(i),'page_urls':[str(i)+'a',str(i)+'b']} for i in range(11)]
    lanes=lane_sources({'sources':sources})
    assert sum(map(len,lanes))==11
    assert {s['source_id'] for lane in lanes for s in lane}=={str(i) for i in range(11)}
    for lane in lanes:
        assert [int(s['source_id']) for s in lane]==sorted(int(s['source_id']) for s in lane)
    with pytest.raises(ValueError):lane_sources({'sources':[sources[0],sources[0]]})


def test_stop_prevents_queued_request_without_calling_engine(monkeypatch):
    c=RequestCoordinator();c.stop('RATE_LIMITED')
    with pytest.raises(ValueError,match='RATE_LIMITED'):
        c.request(None)
from amazon_es_bestseller.highspeed.three_lane import run_three_lanes
from amazon_es_bestseller.multisource.storage import digest
from test_highspeed_v4_execution import FakeTransport


def test_three_lane_full_saved_dom_receipt_audit_and_no_complete_replay(tmp_path,monkeypatch):
    p,a=execution_scope(tmp_path,monkeypatch);current=[NOW]
    side={'approved':True,'mode':'THREE_LOCAL_LANES_ONE_REQUEST_DISPATCHER','lanes':3,
        'run_id':p['run_id'],'plan_sha256':digest(p),'authorization_sha256':digest(a),
        'store_root':a['store_root'],'preserve_cumulative_budgets':True,
        'global_request_pacing_seconds':3,'approval_id':'TEST_ONLY','reviewed_by':'offline test',
        'reviewed_at':NOW.isoformat(),'expires_at':a['expires_at']}
    s=RunStore(a['store_root']);s.begin_run(p,started_at=NOW.isoformat())
    owner=get_ident();starts=[]
    class Transport(FakeTransport):
        def fetch_page(self,url):
            assert get_ident()==owner
            starts.append(current[0]);v=super().fetch_page(url)
            current[0]+=timedelta(seconds=1)
            return v
        def cancel_dynamic(self):pass
        def abort_held_native_routes(self):pass
    def sleep(n):current[0]+=timedelta(seconds=n)
    ts=[Transport(p,a) for _ in range(3)]
    try:
        result=run_three_lanes(s,p,a,ts,side,preheld_lock=True,clock=lambda:current[0],sleep=sleep)
        assert len(starts)==3,result
        assert all((b-a).total_seconds()>=3 for a,b in zip(starts,starts[1:]))
        assert result['quality']['central_full_dom_review_passed'],result['quality']
        assert result['receipt_audit']['diagnostic_passed'],result['receipt_audit']['errors']
        assert s.budget_counters(p['run_id'])['requests']==3
        assert len({r['source_id'] for r in s.requests(p['run_id'])})==3
        again=run_three_lanes(s,p,a,ts,side,preheld_lock=True,clock=lambda:current[0],sleep=sleep)
        assert len(starts)==3
        assert again['metrics']==result['metrics']
    finally:s.close()
@pytest.mark.parametrize('code',[403,429,503,'NETWORK'])
def test_any_lane_failure_stops_all_new_requests_without_retry(tmp_path,monkeypatch,code):
    p,a=execution_scope(tmp_path,monkeypatch);current=[NOW];calls=[]
    side={'approved':True,'mode':'THREE_LOCAL_LANES_ONE_REQUEST_DISPATCHER','lanes':3,
        'run_id':p['run_id'],'plan_sha256':digest(p),'authorization_sha256':digest(a),
        'store_root':a['store_root'],'preserve_cumulative_budgets':True,
        'global_request_pacing_seconds':3,'approval_id':'TEST_ONLY','reviewed_by':'offline test',
        'reviewed_at':NOW.isoformat(),'expires_at':a['expires_at']}
    class Transport(FakeTransport):
        def fetch_page(self,url):
            calls.append(url)
            if code=='NETWORK':raise OSError('TEST_ONLY connection closed')
            return super().fetch_page(url)
        def cancel_dynamic(self):pass
        def abort_held_native_routes(self):pass
    s=RunStore(a['store_root']);s.begin_run(p,started_at=NOW.isoformat())
    try:
        result=run_three_lanes(s,p,a,[Transport(p,a,code=code) for _ in range(3)],side,
            preheld_lock=True,clock=lambda:current[0],sleep=lambda n:None)
        assert len(calls)==1
        assert s.budget_counters(p['run_id'])['requests']==1
        assert s.get_gate()['state']!='NORMAL'
        assert result['quality']['central_full_dom_review_passed'] is False
        assert result['receipt_audit']['review_status']=='DEFERRED_UNTIL_TASK_BOUNDARY'
        assert result['run']['quality_gate_passed'] is False
        assert all(r['status']!='RESERVED' for r in s.requests(p['run_id']))
    finally:s.close()


def test_sidecar_expired_during_pacing_stops_before_reservation(tmp_path,monkeypatch):
    from amazon_es_bestseller.highspeed.three_lane import validate_lane_approval
    from amazon_es_bestseller.highspeed.runner import HighSpeedRunner
    p,a=execution_scope(tmp_path,monkeypatch);current=[NOW]
    side={'approved':True,'mode':'THREE_LOCAL_LANES_ONE_REQUEST_DISPATCHER','lanes':3,
        'run_id':p['run_id'],'plan_sha256':digest(p),'authorization_sha256':digest(a),
        'store_root':a['store_root'],'preserve_cumulative_budgets':True,
        'global_request_pacing_seconds':3,'approval_id':'TEST_ONLY','reviewed_by':'offline test',
        'reviewed_at':NOW.isoformat(),'expires_at':(NOW+timedelta(seconds=1)).isoformat()}
    s=RunStore(a['store_root']);s.begin_run(p,started_at=NOW.isoformat());t=FakeTransport(p,a)
    def sleep(n):current[0]+=timedelta(seconds=n)
    e=HighSpeedRunner(s,clock=lambda:current[0],sleep=sleep)
    e._admission_guard=lambda:validate_lane_approval(p,a,side,s.root,current[0])
    try:
        with pytest.raises(ValueError,match='APPROVAL_REQUIRED'):
            e._request(p,a,t,p['sources'][0],p['sources'][0]['actual_source_url'],'PAGE',NOW,NOW)
        assert t.calls==[]
        assert s.budget_counters(p['run_id'])['requests']==0
    finally:s.close()
def test_browser_factory_uses_one_context_distinct_pages_and_blocks_implicit_fetch(monkeypatch,tmp_path):
    from amazon_es_bestseller.highspeed import three_lane_browser as module
    pages=[];transports=[]
    class Page:
        url='about:blank'
        def __init__(self):self.routes=[];self.closed=False
        def route(self,pattern,callback):self.routes.append(callback)
        def close(self):self.closed=True
    class Context:
        def new_page(self):p=Page();pages.append(p);return p
    class Transport:
        def __init__(self,session,**kwargs):
            self.session=session;self.capture=None;transports.append(self)
        def install_bounded_acp_capture(self,**kwargs):self.capture=kwargs
        def cancel_dynamic(self):pass
        def abort_held_native_routes(self):pass
    monkeypatch.setattr(module,'V4LoadingTransport',Transport)
    p={'run_id':'test','sources':[{'source_id':str(i),'source_type':'BEST_SELLERS',
        'page_urls':['https://www.amazon.es/approved/'+str(i)]} for i in range(3)]}
    a={'sources':{str(i):{'allow_held_native_carousel':True} for i in range(3)}}
    original=SimpleNamespace(context=Context(),page=Page(),last_document_response=None)
    with module.lane_browser_transports(original,SimpleNamespace(root=tmp_path),p,a) as ts:
        assert len(ts)==3
        assert len({id(t.session.page) for t in ts})==3
        assert all(t.session.context is original.context for t in ts)
        class Route:
            def __init__(self,url,kind):
                self.request=SimpleNamespace(url=url,method='GET',resource_type=kind,post_data=None)
                self.aborted=False;self.continued=False
            def abort(self):self.aborted=True
            def continue_(self):self.continued=True
        for i,page in enumerate(pages):
            foreign=Route('https://www.amazon.es/approved/'+str((i+1)%3),'document')
            page.routes[0](foreign)
            assert foreign.aborted and not foreign.continued
            unknown=Route('https://www.amazon.es/unobserved-products','fetch')
            page.routes[0](unknown)
            assert unknown.aborted and not unknown.continued
    assert all(p.closed for p in pages)
