import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from amazon_es_bestseller.collection import detail_item_errors5500 as errors
from amazon_es_bestseller.collection import detail_five_runtime5500 as five
from amazon_es_bestseller.collection.detail_bridge5500 import DetailBridge5500
from amazon_es_bestseller.multisource.storage import digest,immutable_json,read_sealed,sealed,atomic_json
from amazon_es_bestseller.collection.quick_selection import file_sha


def ref(p):return dict(path=str(p),sha256=file_sha(p))


def fixture(tmp_path,status='IDENTITY_REVIEW',reason='ACTIVE_CHILD_ASIN_CHANGED'):
    task=dict(task_id='first',candidate_id='first',selection_id='test',request_url='https://www.amazon.es/dp/B000000001')
    next_task={**task,'task_id':'next','candidate_id':'next'}
    plan=dict(run_id='test',tasks=[task,next_task],shared_gate_path=str(tmp_path/'shared.json'))
    bridge=DetailBridge5500(tmp_path,plan)
    p=tmp_path/'item_error_policy.json'
    immutable_json(p,dict(approved=True,approved_by='agent_under_explicit_user_delegation',
        explicit_user_instruction='有问题留个报错就行，不要退出，继续采集',scope='CONTINUE_RECORDED_ITEM_DATA_ERRORS_NO_RETRY',
        run_id='test',runtime_root=str(tmp_path.resolve()),plan_sha256=digest(plan),code_sha='new',
        allowed_statuses=['IDENTITY_REVIEW','PARSE_FAILED'],no_successful_task_replay=True,
        preserve_failed_status=True,stop_on_access_restriction=True,stop_on_persistence_failure=True))
    auth=dict(code_sha='new',item_error_continuation_reference=ref(p),request_start_interval_seconds=3)
    hp=tmp_path/'tasks/first/html/original.html';hp.parent.mkdir(parents=True)
    hp.write_text('<html><div id="glow-ingress-line2">Madrid 28001</div></html>',encoding='utf8')
    rp=tmp_path/'receipts/first_1.json';immutable_json(rp,sealed(dict(run_id='test',task_id='first',kind='PAGE',request_role='DETAIL',request_url=task['request_url'],state='NORMAL',status_code=200,html_sha256=file_sha(hp),authorization_sha256=digest(auth))))
    mp=tmp_path/'tasks/first/response_metadata.json';immutable_json(mp,dict(access_state='NORMAL',status_code=200,requested_url=task['request_url'],final_url=task['request_url'],receipt_path=str(rp)))
    evidence=dict(html_path=str(hp),html_sha256=file_sha(hp),receipt_path=str(rp),receipt_sha256=file_sha(rp),response_metadata_path=str(mp),response_metadata_sha256=file_sha(mp))
    result=dict(status=status,reason=reason,record=None,**evidence)
    return task,bridge,auth,result


@pytest.mark.parametrize('status,reason',[('IDENTITY_REVIEW','ACTIVE_CHILD_ASIN_CHANGED'),('IDENTITY_REVIEW','ACTIVE_PRODUCT_ASIN_NOT_UNAMBIGUOUS'),('PARSE_FAILED','LOCAL_PARSER_EXCEPTION')])
def test_normal_item_error_is_durable_failure_and_next_task_is_still_pending(tmp_path,status,reason):
    task,b,auth,result=fixture(tmp_path,status,reason)
    policy=errors.read_policy(b.root,b.plan,auth)
    b.save_result(task,result)
    errors.persist_failure(b,auth,task,result)
    assert read_sealed(b.root/'tasks/first/checkpoint.json')['status']==status
    assert errors.pending_tasks(b,auth)==[b.plan['tasks'][1]]
    assert not policy.get('all_requests_successful',False)
    event=read_sealed(b.root/'item_errors/first.json')
    assert event['failed_not_successful'] and event['checkpoint_reference']['sha256']==file_sha(b.root/'tasks/first/checkpoint.json')


@pytest.mark.parametrize('change',['HTML','receipt','checkpoint','policy'])
def test_logged_failure_resume_rejects_tampering(tmp_path,change):
    task,b,auth,result=fixture(tmp_path);b.save_result(task,result);errors.persist_failure(b,auth,task,result)
    p={'HTML':Path(result['html_path']),'receipt':Path(result['receipt_path']),'checkpoint':b.root/'tasks/first/checkpoint.json','policy':Path(auth['item_error_continuation_reference']['path'])}[change]
    p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(ValueError):errors.pending_tasks(b,auth)


@pytest.mark.parametrize('status,reason',[('ACCESS_STOP','CHALLENGE'),('CAPTURE_FAILED','UNKNOWN'),('IDENTITY_REVIEW','SPAIN_DELIVERY_LOCATION_NOT_VERIFIED')])
def test_access_capture_and_delivery_failures_cannot_enter_continue_path(tmp_path,status,reason):
    task,b,auth,result=fixture(tmp_path,status,reason);b.save_result(task,result)
    with pytest.raises(ValueError):errors.persist_failure(b,auth,task,result)


def test_missing_policy_retains_existing_fail_closed_behavior(tmp_path):
    _,b,_,_=fixture(tmp_path)
    c=five.FiveCollector(b,dict(request_start_interval_seconds=3))
    assert c.should_stop_for_result(dict(status='IDENTITY_REVIEW'),dict(status_code=200,access_state='NORMAL'))


def test_five_continues_only_approved_item_errors_and_never_access(tmp_path):
    _,b,auth,result=fixture(tmp_path)
    c=five.FiveCollector(b,auth)
    assert not c.should_stop_for_result(result,dict(status_code=200,access_state='NORMAL'))
    assert c.should_stop_for_result(dict(status='ACCESS_STOP'),dict(status_code=403,access_state='CHALLENGE'))
    assert c.should_stop_for_result(result,dict(status_code=200,access_state='UNKNOWN'))


def test_error_log_disk_failure_stops_admission_before_release(tmp_path,monkeypatch):
    task,b,auth,result=fixture(tmp_path)
    c=five.FiveCollector(b,auth)
    c.commit_order=[task['task_id']];c.results[task['task_id']]=(task,result)
    token=dict(committed=asyncio.Event());c.all_tokens[task['task_id']]=token
    def fail(*a):raise OSError('error log disk failure')
    monkeypatch.setattr(errors,'persist_failure',fail)
    with pytest.raises(OSError):asyncio.run(c.commit_ready())
    assert c.scheduler.stop.is_set() and c.persistence_failure and not token['committed'].is_set()


@pytest.mark.parametrize('first_status',['IDENTITY_REVIEW','PARSE_FAILED'])
def test_actual_five_pipeline_keeps_collecting_after_one_item_failure(tmp_path,monkeypatch,first_status):
    from concurrent.futures import Future
    from datetime import datetime,timedelta,timezone
    from amazon_es_bestseller.collection import detail_dual_runtime5500 as dual
    tasks=[dict(task_id=str(i),candidate_id=str(i),selection_id='test',request_url=f'https://www.amazon.es/dp/B00000000{i}') for i in range(8)]
    plan=dict(run_id='test',tasks=tasks,shared_gate_path=str(tmp_path/'shared.json'))
    b=DetailBridge5500(tmp_path/'run',plan)
    for gp in (b.root/'access_gate.json',tmp_path/'shared.json'):immutable_json(gp,sealed(dict(state='NORMAL')))
    pp=b.root/'policy.json'
    immutable_json(pp,dict(approved=True,approved_by='agent_under_explicit_user_delegation',explicit_user_instruction='Log item errors and continue',scope='CONTINUE_RECORDED_ITEM_DATA_ERRORS_NO_RETRY',run_id='test',runtime_root=str(b.root.resolve()),plan_sha256=digest(plan),code_sha='new',allowed_statuses=['IDENTITY_REVIEW','PARSE_FAILED'],no_successful_task_replay=True,preserve_failed_status=True,stop_on_access_restriction=True,stop_on_persistence_failure=True))
    now=datetime.now(timezone.utc)
    auth=dict(code_sha='new',item_error_continuation_reference=ref(pp),request_start_interval_seconds=3,max_requests=6050,max_page_requests=6050,starts_at=(now-timedelta(hours=1)).isoformat(),request_deadline=(now+timedelta(hours=2)).isoformat(),deadline=(now+timedelta(hours=6)).isoformat())
    monkeypatch.setattr(b,'validate_authorization',lambda *a:None)
    monkeypatch.setattr(five,'validate_parallel_mode',lambda *a:None)
    clock=[now.timestamp()];starts=[]
    async def sleep(seconds):clock[0]+=seconds;await asyncio.sleep(0)
    monkeypatch.setattr(dual.time,'time',lambda:clock[0])
    monkeypatch.setattr(dual,'utc',lambda:datetime.fromtimestamp(clock[0],timezone.utc).isoformat())
    class Pool:
        def submit(self,fn,task,*a):
            future=Future();future.set_result(dict(status=first_status,reason='ACTIVE_CHILD_ASIN_CHANGED' if first_status=='IDENTITY_REVIEW' else 'LOCAL_PARSER_EXCEPTION',record=None) if task['task_id']=='0' else dict(status='SUCCESS'))
            return future
    async def scenario():
        c=five.FiveCollector(b,auth);c.executors=[Pool() for _ in range(5)]
        c.scheduler.precise_default_timer=False;c.scheduler.clock=lambda:clock[0];c.scheduler.sleep=sleep;c.render_clock=lambda:clock[0]
        async def render(page,status,body):return body
        c.render_product_buffer=render
        class Page:
            def __init__(self,lane):self.main_frame=object();self.url='about:blank';self.lane=lane
            async def goto(self,url,**kw):
                self.url=url;req=NS(resource_type='document',method='GET',frame=self.main_frame,url=url,redirected_from=None,is_navigation_request=lambda:True)
                async def proceed():starts.append((c.tokens[self.lane]['task']['task_id'],clock[0]))
                async def abort():raise AssertionError('unexpected abort')
                await c.route(NS(request=req,continue_=proceed,abort=abort))
                response=NS(status=200,request=req,url=url);c.observe(self.lane,response);return response
            async def content(self):return '<html><div id="glow-ingress-line2">Madrid 28001</div></html>'
        c.pages=[Page(i) for i in range(5)]
        await asyncio.wait_for(c.scheduler.run(tasks),5)
        assert not c.scheduler.stop.is_set() and not c.persistence_failure
        assert len(starts)==8 and len(set(t for t,_ in starts))==8
        assert all(second[1]-first[1]>=3.6-1e-6 for first,second in zip(starts,starts[1:]))
        assert c.peak_uncommitted<=5 and not c.commit_order
        assert b.outcomes['0']['status']==first_status and sum(v['status']=='SUCCESS' for v in b.outcomes.values())==7
        assert read_sealed(b.root/'item_errors/0.json')['failed_not_successful']
        monkeypatch.setattr(b,'verify_saved_success',lambda *a:None)
        assert errors.pending_tasks(b,auth)==[]
    asyncio.run(scenario())
