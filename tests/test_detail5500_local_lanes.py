import gzip,json
from concurrent.futures import Future
from pathlib import Path
import pytest
from amazon_es_bestseller.collection.detail_local_lanes5500 import LocalDetailLanes,parse_captured_detail

class Executor:
    def __init__(self):self.futures=[];self.closed=False
    def submit(self,*args):
        f=Future();self.futures.append(f);return f
    def shutdown(self,wait):assert wait;self.closed=True

def task(i):return {'task_id':str(i)}
def finish(f,status='SUCCESS'):f.set_result({'status':status,'record':None})

def test_three_bound_fifo_and_single_claim():
    e=Executor();saved=[];p=LocalDetailLanes(lambda t,r:saved.append((t['task_id'],r)),executor_factory=lambda:e)
    for i in range(3):p.submit(task(i),'',{}, {'receipt_path':str(i)},False)
    assert len(p.pending)==3
    finish(e.futures[0]);p.check_before_request();assert len(p.pending)==2
    with pytest.raises(ValueError,match='DUPLICATE'):p.submit(task(1),'',{}, {},False)
    finish(e.futures[2]);assert len(saved)==1
    finish(e.futures[1]);p.__exit__(None,None,None)
    assert [t for t,r in saved]==['0','1','2'] and all(r['receipt_path']==t for t,r in saved) and e.closed

def test_captured_task_never_dropped_if_another_parse_just_failed():
    e=Executor();saved=[];p=LocalDetailLanes(lambda t,r:saved.append(r),executor_factory=lambda:e)
    p.submit(task(0),'',{}, {},False);finish(e.futures[0],'IDENTITY_REVIEW')
    # It was captured before the failure became visible to the dispatcher.
    p.submit(task(1),'',{}, {},False);finish(e.futures[1])
    with pytest.raises(ValueError,match='SAFETY'):p.check_before_request()
    assert len(saved)==2 and saved[0]['status']=='IDENTITY_REVIEW' and saved[1]['status']=='SUCCESS'
    p.__exit__(RuntimeError,RuntimeError('original stop'),None);assert e.closed

def test_access_exception_drains_captured_results_preserving_primary_stop():
    e=Executor();saved=[]
    with pytest.raises(RuntimeError,match='ACCESS_STOP'):
        with LocalDetailLanes(lambda t,r:saved.append(r),executor_factory=lambda:e) as p:
            p.submit(task(0),'',{}, {},False);finish(e.futures[0])
            raise RuntimeError('ACCESS_STOP')
    assert len(saved)==1 and e.closed

def test_parser_exception_is_preserved_as_failure_not_success():
    e=Executor();saved=[];p=LocalDetailLanes(lambda t,r:saved.append(r),executor_factory=lambda:e)
    p.submit(task(0),'',{}, {},False);e.futures[0].set_exception(ValueError('broken parser'))
    with pytest.raises(ValueError,match='PARSE_FAILED'):p.__exit__(None,None,None)
    assert saved[0]['status']=='PARSE_FAILED' and 'broken parser' in saved[0]['local_error'] and e.closed

def test_real_saved_page_matches_serial_record_and_has_no_network_objects():
    from amazon_es_bestseller.collection.detail_bridge5500 import parse_candidate_detail
    f=Path(__file__).parent/'fixtures/detail_bridge5500';v=json.loads((f/'actual_media_child_parent_canonical.provenance.json').read_text())
    html=gzip.decompress((f/'actual_media_child_parent_canonical.html.gz').read_bytes()).decode('utf8')
    a=parse_candidate_detail(v['task'],html,v['metadata']);b=parse_captured_detail(v['task'],html,v['metadata'],True)
    assert a['status']==b['status']=='SUCCESS' and a['record']==b['record'] and b['local_parser_pid']>0

@pytest.mark.parametrize('lanes,auth,count',[(2,{},1),(3,{},1),(3,{'local_processing_lanes':3,'network_parallelism':3},1),(3,{'local_processing_lanes':3,'network_parallelism':1},0)])
def test_unauthorized_modes_reject_before_network(tmp_path,monkeypatch,lanes,auth,count):
    from amazon_es_bestseller.collection.detail_bridge5500 import DetailBridge5500
    b=DetailBridge5500(tmp_path,{'tasks':[task(0)]});monkeypatch.setattr(b,'validate_authorization',lambda _:None)
    with pytest.raises(ValueError,match='REVIEWED_FORMAL'):b.run(None,auth,until_completed=count,local_lanes=lanes)

@pytest.mark.parametrize('stop',['budget','403','challenge','identity',None])
def test_real_bridge_single_dispatcher_budget_gate_resume(tmp_path,monkeypatch,stop):
    from copy import deepcopy
    from types import SimpleNamespace as NS
    from datetime import datetime,timezone
    from test_quick_selection_5500 import auth_for
    from amazon_es_bestseller.collection import detail_bridge5500 as mod
    import amazon_es_bestseller.collection.detail_local_lanes5500 as lanes
    from amazon_es_bestseller.multisource.storage import read_sealed
    f=Path(__file__).parent/'fixtures/detail_bridge5500'
    v=json.loads((f/'actual_media_child_parent_canonical.provenance.json').read_text())
    html=gzip.decompress((f/'actual_media_child_parent_canonical.html.gz').read_bytes()).decode('utf8')
    tasks=[]
    for i in range(3):
        t=deepcopy(v['task']);t.update(task_id=str(i),candidate_id=str(i));tasks.append(t)
    b=mod.DetailBridge5500(tmp_path/'run',{'tasks':tasks,'run_id':'test','shared_gate_path':str(tmp_path/'shared_gate.json')})
    auth=auth_for(b,tmp_path);auth.update(local_processing_lanes=3,network_parallelism=1,max_requests=2 if stop=='budget' else 5)
    auth['max_page_requests']=auth['max_requests']
    class Immediate(Executor):
        def submit(self,fn,*args):
            z=Future()
            try:z.set_result(fn(*args))
            except Exception as e:z.set_exception(e)
            return z
    monkeypatch.setattr(lanes,'LocalDetailLanes',lambda commit:LocalDetailLanes(commit,executor_factory=Immediate))
    clock=[datetime.now(timezone.utc).timestamp()];starts=[]
    monkeypatch.setattr(mod.time,'time',lambda:clock[0])
    monkeypatch.setattr(mod.time,'sleep',lambda s:clock.__setitem__(0,clock[0]+s))
    monkeypatch.setattr(mod,'utc',lambda:datetime.fromtimestamp(clock[0],timezone.utc).isoformat())
    page=NS(url=tasks[0]['request_url'],content=lambda:html)
    def goto(url):
        starts.append(clock[0]);page.url=url
        page.content=lambda:html
        if len(starts)==2:
            if stop=='challenge':page.content=lambda:'<form action="/errors_page/validateCaptcha"></form>'
            if stop=='identity':page.url='https://www.amazon.es/dp/B000000001'
            if stop=='403':return 403
        return 200
    session=NS(page=page,goto=goto,wait_for_product_page=lambda:None,wait_for_price_text=lambda:None)
    if stop:
        with pytest.raises(Exception):b.run(session,auth,until_completed=3,local_lanes=3)
        assert len(starts)==2 and b.progress()['paid_requests']==2
        if stop in {'403','challenge'}:assert read_sealed(b.plan['shared_gate_path'])['state']!='NORMAL'
        if stop=='identity':assert read_sealed(b.root/'tasks/1/checkpoint.json')['status']=='IDENTITY_REVIEW'
    else:
        b.run(session,auth,until_completed=3,local_lanes=3)
        assert b.progress()['success']==3
        b.run(session,auth,until_completed=3,local_lanes=3)
        assert len(starts)==3 and b.progress()['paid_requests']==3
    assert all(y-x>=3 for x,y in zip(starts,starts[1:]))

@pytest.mark.parametrize('visible',[True,False])
def test_runtime_visibility_only_changes_browser_presentation(tmp_path,monkeypatch,visible):
    from types import SimpleNamespace as NS
    import amazon_es_bestseller.collection.detail5500_runtime as runtime
    (tmp_path/'detail_plan_5500.json').write_text(json.dumps({'tasks':[]}))
    ap=tmp_path/'authorization.test.approved.json';ap.write_text(json.dumps({'code_sha':'offline'}))
    monkeypatch.setattr(runtime,'DetailBridge5500',lambda *a:NS(validate_authorization=lambda _:None))
    monkeypatch.setattr(runtime.subprocess,'check_output',lambda args,**kw:'offline' if 'rev-parse' in args else '')
    seen=[]
    def browser(**kw):seen.append(kw);raise RuntimeError('OFFLINE_BROWSER_BOUNDARY')
    monkeypatch.setattr(runtime,'BrowserSession',browser)
    with pytest.raises(RuntimeError,match='OFFLINE_BROWSER_BOUNDARY'):runtime.run(tmp_path,ap,'same-original-profile',visible=visible)
    assert seen==[{'headless':not visible,'profile_dir':'same-original-profile','block_service_workers':True}]

def test_three_independent_single_worker_lanes_claim_round_robin(monkeypatch):
    import amazon_es_bestseller.collection.detail_local_lanes5500 as mod
    executors=[]
    class Ready(Executor):
        def submit(self,*a):
            f=Future();finish(f);self.futures.append(f);return f
    def make(**kw):
        assert kw['max_workers']==1
        e=Ready();executors.append(e);return e
    monkeypatch.setattr(mod,'ProcessPoolExecutor',make)
    saved=[]
    with LocalDetailLanes(lambda t,r:saved.append(t['task_id'])) as p:
        for i in range(9):p.check_before_request();p.submit(task(i),'',{}, {},False)
    assert len(executors)==3 and [len(e.futures) for e in executors]==[3,3,3]
    assert len(set(saved))==9 and all(e.closed for e in executors)

@pytest.mark.parametrize('explicit',[False,True])
def test_detail_cli_is_visible_by_default_and_with_explicit_option(monkeypatch,explicit):
    import sys
    import amazon_es_bestseller.collection.detail5500_runtime as runtime
    calls=[]
    monkeypatch.setattr(runtime,'run',lambda *a,**kw:calls.append(kw))
    monkeypatch.setattr(sys,'argv',['detail5500','--root','offline','--authorization','offline','--profile','original']+(['--visible'] if explicit else []))
    runtime.main()
    assert calls==[{'visible':True,'local_lanes':1}]

def test_task_visible_requirement_cannot_be_silently_disabled(tmp_path):
    import amazon_es_bestseller.collection.detail5500_runtime as runtime
    (tmp_path/'detail_plan_5500.json').write_text('{"tasks":[]}')
    ap=tmp_path/'authorization.test.approved.json';ap.write_text('{"visible_browser_required":true}')
    with pytest.raises(ValueError,match='REQUIRES_VISIBLE'):runtime.run(tmp_path,ap,'original',visible=False)
