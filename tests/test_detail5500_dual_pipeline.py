import asyncio
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from amazon_es_bestseller.collection import detail_dual_runtime5500 as mod
from amazon_es_bestseller.multisource.storage import atomic_json, immutable_json, read_sealed, sealed


async def pipeline(tmp_path, monkeypatch, failure=None):
    tasks = [dict(task_id=str(i), candidate_id=str(i), request_url=f'https://www.amazon.es/dp/B00000000{i}') for i in range(8)]
    plan = dict(run_id='test', tasks=tasks, shared_gate_path=str(tmp_path/'shared.json'))
    commits, pending, starts = [], [], []
    root = tmp_path/'run'
    for gp in (root/'access_gate.json', tmp_path/'shared.json'):
        immutable_json(gp, sealed(dict(state='NORMAL')))
    def save(task, result):
        if failure == 'persistence': raise OSError('INJECTED_DISK_FAILURE')
        commits.append(task['task_id'])
        atomic_json(root/'tasks'/task['task_id']/'checkpoint.json', sealed(result))
    bridge = NS(root=root, plan=plan, validate_authorization=lambda *a:None,
                gates_normal=lambda:None, save_result=save)
    now = datetime.now(timezone.utc)
    auth = dict(request_start_interval_seconds=3, starts_at=(now-timedelta(hours=1)).isoformat(),
                request_deadline=(now+timedelta(hours=2)).isoformat(), deadline=(now+timedelta(hours=3)).isoformat(),
                max_requests=6050, max_page_requests=6050)
    monkeypatch.setattr(mod, 'validate_parallel_mode', lambda *a:None)
    clock = [now.timestamp()]
    async def sleep(seconds): clock[0] += seconds; await asyncio.sleep(0)
    monkeypatch.setattr(mod, 'asyncio', NS(Event=asyncio.Event, Semaphore=asyncio.Semaphore, Lock=asyncio.Lock,
        create_task=asyncio.create_task, wait=asyncio.wait, FIRST_COMPLETED=asyncio.FIRST_COMPLETED,
        sleep=sleep, to_thread=asyncio.to_thread, wrap_future=asyncio.wrap_future,
        wait_for=asyncio.wait_for, TimeoutError=asyncio.TimeoutError, gather=asyncio.gather))
    monkeypatch.setattr(mod.time, 'time', lambda:clock[0])
    monkeypatch.setattr(mod, 'utc', lambda:datetime.fromtimestamp(clock[0],timezone.utc).isoformat())
    c = mod.ParallelCollector(bridge, auth)
    c.scheduler.precise_default_timer=False;c.render_clock=lambda:clock[0];c.scheduler.clock=lambda:clock[0]; c.scheduler.sleep=sleep
    def complete():
        for task, future in pending:
            if not future.done():
                future.set_result(dict(status='IDENTITY_REVIEW' if failure=='identity' and task['task_id']=='0' else 'SUCCESS'))
    class Pool:
        def submit(self, fn, task, *args):
            f=Future(); pending.append((task,f))
            if len(starts)>=3: complete()
            return f
    c.executors=[Pool(),Pool()]
    class Page:
        def __init__(self,lane): self.lane=lane; self.main_frame=object(); self.url='about:blank'
        async def goto(self,url,**kw):
            self.url=url
            req=NS(resource_type='document',method='GET',frame=self.main_frame,url=url,
                   redirected_from=None,is_navigation_request=lambda:True)
            async def proceed():
                starts.append((c.tokens[self.lane]['task']['task_id'], clock[0]))
                if len(starts)>=3: complete()
            async def abort(): raise AssertionError('unexpected route abort')
            await c.route(NS(request=req,continue_=proceed,abort=abort))
            response=NS(status=200,request=req,url=url); c.observe(self.lane,response)
            return response
        async def content(self): return '<html><title>Offline product fixture</title><div id="ppd"></div></html>'
    c.pages=[Page(0),Page(1)]
    try:
        await asyncio.wait_for(c.scheduler.run(tasks),timeout=5)
    except Exception:
        if failure is None: raise
        assert c.scheduler.stop.is_set()
        if failure=='persistence': assert c.persistence_failure is not None
    else:
        assert failure is None
    assert c.scheduler.peak_active<=2
    assert c.peak_uncommitted<=3
    assert len(starts)>=3  # third capture begins before the first parser completes
    assert all(b[1]-a[1]>=3.6-1e-6 for a,b in zip(starts,starts[1:]))
    rows=[read_sealed(p) for p in (root/'receipts').glob('*.json')]
    assert rows and all(r['state']!='RESERVED' and r.get('html_path') for r in rows)
    if failure:
        assert len(rows)<=3
    else:
        assert commits==[str(i) for i in range(8)]
        assert not c.parse_jobs and not c.commit_order and not c.all_tokens
        assert all(read_sealed(p)['status']=='SUCCESS' for p in (root/'tasks').glob('*/checkpoint.json'))


def test_browser_lane_reused_before_parser_finishes_with_bounded_FIFO(tmp_path,monkeypatch):
    asyncio.run(pipeline(tmp_path,monkeypatch))


@pytest.mark.parametrize('failure',['identity','persistence'])
def test_delayed_failure_stops_admission_and_drains_without_deadlock(tmp_path,monkeypatch,failure):
    asyncio.run(pipeline(tmp_path,monkeypatch,failure))


def test_both_full_validations_finish_inside_receipt_pacing_window(tmp_path, monkeypatch):
    async def scenario():
        now=datetime.now(timezone.utc); clock=[now.timestamp()]; calls=[]
        root=tmp_path/'run';task=dict(task_id='next',candidate_id='next',request_url='https://www.amazon.es/dp/B000000001')
        immutable_json(root/'receipts/old.json',sealed(dict(state='NORMAL',kind='PAGE',started_at=now.isoformat())))
        def validate(auth):calls.append(clock[0]);clock[0]+=.8
        bridge=NS(root=root,plan={'run_id':'test'},validate_authorization=validate)
        auth=dict(request_start_interval_seconds=3,max_requests=6050,max_page_requests=6050)
        monkeypatch.setattr(mod,'validate_parallel_mode',lambda *a:None)
        monkeypatch.setattr(mod,'uncertain_setup_page_holds',lambda *a:0)
        c=mod.ParallelCollector(bridge,auth);c.check_qa_clock=lambda:None
        async def sleep(seconds):clock[0]+=seconds;await asyncio.sleep(0)
        monkeypatch.setattr(mod,'asyncio',NS(Event=asyncio.Event,Semaphore=asyncio.Semaphore,Lock=asyncio.Lock,
            sleep=sleep,to_thread=asyncio.to_thread))
        monkeypatch.setattr(mod.time,'time',lambda:clock[0])
        monkeypatch.setattr(mod,'utc',lambda:datetime.fromtimestamp(clock[0],timezone.utc).isoformat())
        token=await c.reserve_admitted(task,0)
        assert len(calls)==2  # no validation removed or cached
        assert clock[0]-now.timestamp()==pytest.approx(3.0)
        assert token['receipt']['state']=='RESERVED' and not token['sent']
    asyncio.run(scenario())


def test_durable_FIFO_writer_does_not_block_browser_event_loop(tmp_path):
    import inspect, threading
    async def scenario():
        progressed=threading.Event(); observations=[]
        def save(task,result):observations.append(progressed.wait(.5))
        c=mod.ParallelCollector(NS(root=tmp_path,plan={},save_result=save),{'request_start_interval_seconds':3})
        task={'task_id':'one'}
        c.commit_order=['one'];c.results={'one':(task,{'status':'SUCCESS'})}
        c.all_tokens={'one':{'committed':asyncio.Event()}}
        async def browser_event():await asyncio.sleep(.01);progressed.set()
        event=asyncio.create_task(browser_event())
        result=c.commit_ready()
        if inspect.isawaitable(result):await result
        await event
        assert observations==[True]  # browser callback ran while durable save was waiting
        assert not c.commit_order and not c.results and not c.all_tokens
    asyncio.run(scenario())


def test_warmup_requires_two_distinct_ready_parser_processes(tmp_path):
    class Pool:
        def __init__(self,pid):self.pid=pid
        def submit(self,fn):
            assert fn is mod.parser_worker_ready
            f=Future();f.set_result(self.pid);return f
    async def scenario():
        c=mod.ParallelCollector(NS(root=tmp_path,plan={}),{'request_start_interval_seconds':3})
        c.executors=[Pool(11),Pool(22)]
        await c.warm_parsers()
        assert c.ready_parser_pids==[11,22]
        c.executors=[Pool(11),Pool(11)]
        with pytest.raises(RuntimeError,match='INDEPENDENT_READY'):
            await c.warm_parsers()
        assert not c.all_tokens and not c.commit_order  # warmup reserves no task
    asyncio.run(scenario())


def test_concurrent_commit_callers_have_one_FIFO_writer(tmp_path):
    import threading,time
    saved=[];active=[0];peak=[0];lock=threading.Lock()
    def save(task,result):
        with lock:active[0]+=1;peak[0]=max(peak[0],active[0])
        time.sleep(.01);saved.append(task['task_id'])
        with lock:active[0]-=1
    async def scenario():
        c=mod.ParallelCollector(NS(root=tmp_path,plan={},save_result=save),{'request_start_interval_seconds':3})
        c.commit_order=['a','b']
        for task_id in c.commit_order:
            c.results[task_id]=({'task_id':task_id},{'status':'SUCCESS'})
            c.all_tokens[task_id]={'committed':asyncio.Event()}
        await asyncio.gather(c.commit_ready(),c.commit_ready())
        assert saved==['a','b'] and peak==[1]
        assert not c.all_tokens and not c.results and not c.commit_order
    asyncio.run(scenario())


def test_render_buffer_keeps_six_samples_and_three_seconds_without_adding_IPC_delays(tmp_path,monkeypatch):
    async def scenario():
        now=[0.0];samples=[]
        async def sleep(seconds):now[0]+=seconds;await asyncio.sleep(0)
        monkeypatch.setattr(mod,'asyncio',NS(Event=asyncio.Event,Semaphore=asyncio.Semaphore,
            Lock=asyncio.Lock,sleep=sleep))
        c=mod.ParallelCollector(NS(root=tmp_path,plan={}),{'request_start_interval_seconds':3})
        c.render_clock=lambda:now[0]
        class Page:
            async def content(self):samples.append(now[0]);now[0]+=.2;return '<div id="ppd">offline</div>'
        await c.render_product_buffer(Page(),200,'<div id="ppd">offline</div>')
        assert len(samples)==6 and samples[-1]>=3
        assert now[0]==pytest.approx(3.2)
    asyncio.run(scenario())
