import asyncio
import copy
import gzip
import json
from concurrent.futures import Future
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from amazon_es_bestseller.collection import detail_dual_runtime5500 as mod
from amazon_es_bestseller.collection.detail_bridge5500 import DetailBridge5500
from amazon_es_bestseller.multisource.storage import immutable_json, read_sealed, sealed, digest
from amazon_es_bestseller.collection.quick_selection import file_sha


def ref(p):return dict(path=str(p),sha256=file_sha(p))


def mode(tmp_path):
    plan=dict(run_id='test',tasks=[],shared_gate_path=str(tmp_path/'shared_gate.json'))
    base=dict(run_id='test',scope='DETAIL_5500_INDEPENDENT',runtime_root=str(tmp_path.resolve()),
        plan_sha256=digest(plan),network_parallelism=1,request_start_interval_seconds=3,
        max_requests=6050,max_page_requests=6050,max_other_requests=8,starts_at='original',
        deadline='original',request_deadline='original',max_attempts_per_task=1,
        access_recovery_reference={'path':'unchanged','sha256':'same'})
    bp=tmp_path/'base.json';immutable_json(bp,base)
    a=dict(approved=True,approved_by='explicit-user-delegation',
        explicit_user_instruction='Develop two truly concurrent lanes targeting 1000 requests/hour; preserve all other controls',
        scope='TWO_IN_FLIGHT_DETAIL_1000_TARGET_SAME_FROZEN_RUN',run_id='test',runtime_root=str(tmp_path.resolve()),
        plan_sha256=digest(plan),base_authorization_sha256=file_sha(bp),code_sha='new-code',
        network_parallelism=2,target_requests_per_hour=1000,target_start_interval_seconds=3.6,max_uncommitted_tasks=3,request_start_interval_seconds=3,no_successful_task_replay=True,
        browser_profile=str(tmp_path/'original-profile'))
    ap=tmp_path/'amendment.json';immutable_json(ap,a)
    auth={**base,'code_sha':'new-code','network_parallelism':2,'local_processing_lanes':2,'target_requests_per_hour':1000,'max_uncommitted_tasks':3,
        'visible_browser_windows':2,'visible_browser_required':True,
        'parallel_base_authorization_reference':ref(bp),'parallel_execution_approval_reference':ref(ap)}
    return plan,auth,a,ap


def test_explicit_mode_changes_only_concurrency(tmp_path):
    plan,auth,a,_=mode(tmp_path)
    assert mod.validate_parallel_mode(tmp_path,plan,auth)==a


@pytest.mark.parametrize('key,value',[
    ('max_requests',6051),('max_page_requests',6051),('max_other_requests',9),
    ('starts_at','reset'),('deadline','extended'),('request_deadline','extended'),
    ('request_start_interval_seconds',2),('max_attempts_per_task',2),
    ('access_recovery_reference',{'path':'other','sha256':'other'}),
    ('network_parallelism',4),('visible_browser_required',False)])
def test_mode_cannot_reset_budget_clock_recovery_or_pacing(tmp_path,key,value):
    plan,auth,_,_=mode(tmp_path);auth[key]=value
    with pytest.raises(ValueError):mod.validate_parallel_mode(tmp_path,plan,auth)


@pytest.mark.parametrize('key,value',[
    ('run_id','other'),('runtime_root','other'),('code_sha','old'),
    ('base_authorization_sha256','other'),('no_successful_task_replay',False),
    ('approved',False),('explicit_user_instruction','')])
def test_mode_binding_and_user_authority_are_required(tmp_path,key,value):
    plan,auth,a,ap=mode(tmp_path);a[key]=value
    ap.write_text(json.dumps(a));auth['parallel_execution_approval_reference']=ref(ap)
    with pytest.raises(ValueError):mod.validate_parallel_mode(tmp_path,plan,auth)


def test_amendment_tampering_fails_closed(tmp_path):
    plan,auth,a,ap=mode(tmp_path);ap.write_text('{}')
    with pytest.raises(ValueError,match='REFERENCE'):mod.validate_parallel_mode(tmp_path,plan,auth)


def test_resume_never_reclaims_success_or_unreviewed_failures(tmp_path,monkeypatch):
    from amazon_es_bestseller.collection import identity_quarantine5500 as q
    tasks=[{'task_id':str(i)} for i in range(4)]
    bridge=NS(root=tmp_path,plan={'tasks':tasks},outcomes={
        '0':{'status':'SUCCESS'},'1':{'status':'IDENTITY_REVIEW'}},verify_saved_success=lambda *a:None)
    monkeypatch.setattr(q,'reviewed_identity_quarantines',lambda *a:{'1':{}})
    assert mod.pending_tasks(bridge,{})==tasks[2:]
    bridge.outcomes['2']={'status':'ACCESS_STOP'}
    with pytest.raises(ValueError,match='UNRESOLVED'):mod.pending_tasks(bridge,{})


class Ready:
    def submit(self,fn,*args):
        f=Future()
        try:f.set_result(fn(*args))
        except Exception as e:f.set_exception(e)
        return f


@pytest.mark.parametrize('failure',[None,'challenge','identity','403'])
def test_real_fixture_two_capture_overlap_fifo_all_evidence_drained(tmp_path,monkeypatch,failure):
    from test_quick_selection_5500 import auth_for
    f=Path(__file__).parent/'fixtures/detail_bridge5500'
    provenance=json.loads((f/'actual_media_child_parent_canonical.provenance.json').read_text())
    body=gzip.decompress((f/'actual_media_child_parent_canonical.html.gz').read_bytes()).decode('utf8')
    tasks=[]
    for i in range(2):
        t=copy.deepcopy(provenance['task']);t.update(task_id=str(i),candidate_id=str(i));tasks.append(t)
    bridge=DetailBridge5500(tmp_path/'run',dict(run_id='test',tasks=tasks,shared_gate_path=str(tmp_path/'shared_gate.json')))
    auth=auth_for(bridge,tmp_path)
    auth['deadline']=(datetime.now(timezone.utc)+timedelta(hours=3)).isoformat()
    immutable_json(bridge.root/'access_gate.json',sealed({'state':'NORMAL'}))
    monkeypatch.setattr(mod,'validate_parallel_mode',lambda *a:None)
    async def scenario():
        clock=[datetime.now(timezone.utc).timestamp()]
        async def sleep(s):clock[0]+=s;await asyncio.sleep(0)
        monkeypatch.setattr(mod,'asyncio',NS(Event=asyncio.Event,Lock=asyncio.Lock,sleep=sleep,
            to_thread=asyncio.to_thread,wrap_future=asyncio.wrap_future,
            wait_for=asyncio.wait_for,TimeoutError=asyncio.TimeoutError,
            Semaphore=asyncio.Semaphore,create_task=asyncio.create_task,wait=asyncio.wait,
            FIRST_COMPLETED=asyncio.FIRST_COMPLETED,gather=asyncio.gather))
        monkeypatch.setattr(mod.time,'time',lambda:clock[0])
        monkeypatch.setattr(mod,'utc',lambda:datetime.fromtimestamp(clock[0],timezone.utc).isoformat())
        c=mod.ParallelCollector(bridge,auth);c.executors=[Ready() for _ in range(2)]
        c.scheduler.precise_default_timer=False;c.render_clock=lambda:clock[0];c.scheduler.clock=lambda:clock[0];c.scheduler.sleep=sleep
        active=[];released=asyncio.Event();commits=[]
        original=bridge.save_result
        def save(task,result):commits.append(task['task_id']);original(task,result)
        bridge.save_result=save
        class Page:
            def __init__(self,lane):self.lane=lane;self.url=tasks[lane]['request_url'];self.main_frame=object()
            async def goto(self,url,**kw):
                token=c.tokens[self.lane]
                assert read_sealed(token['rp'])['state']=='RESERVED'
                # Exact production route determines when the request starts.
                req=NS(resource_type='document',method='GET',frame=self.main_frame,url=url,
                    redirected_from=None,is_navigation_request=lambda:True)
                async def proceed():
                    active.append(self.lane)
                    if len(active)==2:released.set()
                async def abort():raise RuntimeError('UNEXPECTED_ABORT')
                await c.route(NS(request=req,continue_=proceed,abort=abort))
                await released.wait()
                status=403 if failure=='403' and self.lane==1 else 200
                response=NS(status=status,request=req,url=url)
                c.observe(self.lane,response)
                if self.lane==0:await asyncio.sleep(0)
                return response
            async def content(self):
                if failure=='challenge' and self.lane==1:return '<form action="/errors_page/validateCaptcha"></form>'
                if failure=='identity' and self.lane==1:return body.replace('B0HG8VF2PP','B000000001')
                return body
        c.pages=[Page(i) for i in range(2)]
        if failure:
            with pytest.raises(Exception):await asyncio.wait_for(c.scheduler.run(tasks),timeout=30)
        else:await asyncio.wait_for(c.scheduler.run(tasks),timeout=30)
        assert c.scheduler.peak_active==2 and len(active)==2 and commits==['0','1']
        rows=[read_sealed(p) for p in (bridge.root/'receipts').glob('*.json')]
        assert len(rows)==2 and all(r['state']!='RESERVED' for r in rows)
        starts=sorted(datetime.fromisoformat(r['document_started_at']) for r in rows)
        assert all((b-a).total_seconds()>=3.6-1e-6 for a,b in zip(starts,starts[1:]))
        assert all(Path(r['html_path']).exists() and file_sha(r['html_path'])==r['html_sha256'] for r in rows)
        if failure in {'challenge','403'}:
            assert read_sealed(bridge.plan['shared_gate_path'])['state']!='NORMAL'
            assert c.scheduler.access_failure
        elif failure=='identity':
            assert bridge.outcomes['1']['status']=='IDENTITY_REVIEW'
            assert read_sealed(bridge.plan['shared_gate_path'])['state']=='NORMAL'
        else:
            assert bridge.progress()['success']==2
            # Full fields are from the unchanged parser, not a reduced projection.
            actual_meta=json.loads(Path(bridge.outcomes['0']['response_metadata_path']).read_text())
            expected=mod.parse_captured_detail(tasks[0],body,actual_meta,False)['record']
            assert bridge.outcomes['0']['record']==expected
    asyncio.run(scenario())


def test_routes_block_unsolicited_window_navigation_and_submission(tmp_path):
    bridge=NS(root=tmp_path,plan={})
    c=mod.ParallelCollector(bridge,{'request_start_interval_seconds':3})
    async def scenario():
        aborted=[]
        async def abort():aborted.append(True)
        async def forbidden():raise AssertionError('unexpected allowed navigation')
        req=NS(resource_type='document',method='POST',url='https://www.amazon.es/errors_page/validateCaptcha',
            frame=object(),redirected_from=None,is_navigation_request=lambda:True)
        await c.route(NS(request=req,abort=abort,continue_=forbidden))
        c.scheduler.stop.set();req.resource_type='script'
        await c.route(NS(request=req,abort=abort,continue_=forbidden))
        assert len(aborted)==2
    asyncio.run(scenario())


def test_full_QA_uses_real_T2_measurement_and_stops_before_reservation(tmp_path):
    immutable_json(tmp_path/'T2_acceptance.json',sealed(dict(passed=True,success_count=100,
        independent_audit_elapsed_seconds=253.203)))
    qa=mod.full_qa_reserve(tmp_path,5500)
    assert qa==pytest.approx(17611.398)
    bridge=NS(root=tmp_path,plan={})
    collector=mod.ParallelCollector(bridge,dict(request_start_interval_seconds=3,
        deadline=(datetime.now(timezone.utc)+timedelta(hours=4)).isoformat()),qa_seconds=qa)
    with pytest.raises(ValueError,match='FULL_QA_RESERVE_REACHED'):
        asyncio.run(collector.reserve({'task_id':'must_not_be_reserved'},0))
    assert not list((tmp_path/'receipts').glob('*'))


def test_QA_measurement_missing_or_lowered_reserve_rejects(tmp_path):
    immutable_json(tmp_path/'T2_acceptance.json',sealed(dict(passed=False,success_count=100)))
    with pytest.raises(ValueError,match='MEASUREMENT'):mod.full_qa_reserve(tmp_path,5500)
    with pytest.raises(ValueError,match='CANNOT_BE_LOWERED'):
        mod.ParallelCollector(NS(root=tmp_path,plan={}),{'request_start_interval_seconds':3},qa_seconds=3599)
