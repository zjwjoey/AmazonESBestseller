import asyncio
import json
from types import SimpleNamespace

import pytest

from amazon_es_bestseller.highspeed.parallel_faults import FaultReporter, acknowledge_wait, wait_for_operator
from amazon_es_bestseller.highspeed.parallel_transport import ThreeWindowTransport
from amazon_es_bestseller.highspeed.parallel_dispatch_gate import AsyncDispatchGate
from amazon_es_bestseller.highspeed.parallel_page_ledger import ParallelPageLedger


def test_error_is_durable_before_popup_and_popup_failure_does_not_hide_it(tmp_path):
    def popup(path):
        error = json.loads(path.read_text('utf-8'))
        assert error['exception_type'] == 'TimeoutError'
        assert 'original timeout' in error['traceback']
        raise OSError('desktop unavailable')
    reporter = FaultReporter(tmp_path, 'TEST_ONLY', 'plan-hash', notify=popup)
    try:
        raise TimeoutError('original timeout')
    except TimeoutError as error:
        report = reporter.record(worker=1, url='https://snapshot-test.invalid/1',
                                 request_id='req-1', stage='capture', error=error, scope='worker')
    assert report['persisted'] is True
    assert report['notification_error'] == 'desktop unavailable'
    assert 'original timeout' in json.loads(__import__('pathlib').Path(report['path']).read_text('utf-8'))['traceback']


def test_notice_acknowledgement_never_releases_wait_or_resumes_collection(tmp_path):
    async def exercise():
        reporter = FaultReporter(tmp_path, 'TEST_ONLY', 'hash', notify=lambda p: None)
        task = asyncio.create_task(wait_for_operator(reporter, 'SHARED_WRITE_FAILURE', interval=.01))
        await asyncio.sleep(.03)
        acknowledge_wait(tmp_path, 'TEST_ONLY', exit_wait=False)
        await asyncio.sleep(.03)
        assert not task.done()
        acknowledge_wait(tmp_path, 'TEST_ONLY', exit_wait=True)
        result = await asyncio.wait_for(task, 1)
        assert result == {'action': 'EXIT_WAIT', 'live_resumed': False}
    asyncio.run(exercise())


def test_disabled_worker_and_ambiguous_claim_survive_restart(tmp_path):
    import time
    args = dict(run_id='TEST_ONLY', original_plan_sha256='hash', deadline=time.time()+90,
                page_budget=8, min_interval=3)
    queue = ParallelPageLedger(tmp_path/'queue.sqlite', **args)
    queue.seed([{'url': f'https://snapshot-test.invalid/{i}', 'category': 'test', 'family': 'test'} for i in range(3)])
    first = queue.claim(1)
    queue.disable_worker(1, 'CAPTURE_FAILURE')
    reopened = ParallelPageLedger(tmp_path/'queue.sqlite', **args)
    assert reopened.claim(1, now=time.time()+10) is None
    second = reopened.claim(2, now=time.time()+10)
    assert second and second != first
    assert reopened.snapshot()['config'][0] is None
    assert reopened.snapshot()['pages'][0][3] == 'HELD'
    assert reopened.snapshot()['claims'][0][3] == 'HELD_AMBIGUOUS'


def test_window_timeout_does_not_stop_other_window_or_lose_original_error():
    async def exercise():
        import time
        errors = []
        stops = []
        gate = AsyncDispatchGate(deadline=time.time()+90)
        adapter = ThreeWindowTransport(None, gate, admit=lambda w, i: True,
                                      observed_stop=stops.append, captured=lambda w, i, r: r)
        adapter.configure_faults(on_fault=lambda *args: errors.append(args),
                                 on_access=lambda *args: None, access_stop_scope='worker')
        async def fail(*a, **kw):
            raise TimeoutError('TEST_ONLY stalled page')
        async def good(*a, **kw):
            return SimpleNamespace(status=200, url='https://snapshot-test.invalid/2', text=body)
        async def body():
            return '<html>normal</html>'
        adapter.pages = {1: SimpleNamespace(goto=fail), 2: SimpleNamespace(goto=good, content=body)}
        with pytest.raises(TimeoutError, match='stalled page'):
            await adapter.capture(1, {'url': 'https://snapshot-test.invalid/1', 'reservation': {'request_id': '1'}})
        result = await adapter.capture(2, {'url': 'https://snapshot-test.invalid/2', 'reservation': {'request_id': '2'}})
        assert result['status_code'] == 200
        assert adapter.disabled == {1} and not stops
        assert errors[0][0] == 1 and errors[0][3] == 'capture'
    asyncio.run(exercise())


def test_scheduler_keeps_healthy_workers_and_holds_failed_request(tmp_path, monkeypatch):
    import time
    from pathlib import Path
    from test_highspeed_v4_execution import setup_scope, NOW
    from amazon_es_bestseller.highspeed.store import RunStore
    from amazon_es_bestseller.highspeed.v4_runner import V4T1Runner
    from amazon_es_bestseller.highspeed import parallel_coordinator as module
    plan, auth = setup_scope(tmp_path)
    store = RunStore(auth['store_root'])
    try:
        store.begin_run(plan, started_at=NOW.isoformat())
        work = [({**s, 'collection_category_key': 'TEST_ONLY'}, s['actual_source_url']) for s in plan['sources'][:3]]
        class Transport:
            def __init__(self, context, gate, *, captured, **kwargs):
                self.captured = captured
            async def open(self): return self
            def configure_faults(self, **kwargs): self.fault = kwargs['on_fault']
            async def capture(self, worker, item):
                if worker == 1:
                    try: raise TimeoutError('TEST_ONLY slow worker')
                    except TimeoutError as error:
                        self.fault(worker, item, error, 'capture')
                        raise
                body = Path(auth['sources'][item['source']['source_id']]['evidence']['path']).read_text('utf-8')
                return self.captured(worker, item, {'status_code': 200, 'url': item['url'], 'text': body})
        monkeypatch.setattr(module, 'ThreeWindowTransport', Transport)
        queue = ParallelPageLedger(tmp_path/'q.sqlite', run_id='TEST_ONLY', original_plan_sha256='hash',
                                   deadline=time.time()+120, page_budget=10)
        c = module.ParallelSnapshotCoordinator(V4T1Runner(store), plan, queue,
            AsyncDispatchGate(deadline=time.time()+120), admit=lambda w, i: True, stop_reason=lambda: None,
            fixture_mode=True, access_stop_scope='worker')
        c.add_work(work)
        result = asyncio.run(c.run(None))
        assert len(store.pages(plan['run_id'])) == 2
        assert len(store.requests(plan['run_id'])) == 3
        assert [r[3] for r in queue.snapshot()['pages']].count('HELD') == 1
        assert len(result['failures']) == 1 and result['failures'][0]['scope'] == 'worker'
        assert 'slow worker' in result['failures'][0]['traceback']
        assert queue.disabled_workers() == {1: 'CAPTURE_FAILURE'}
    finally: store.close()


def test_live_owner_keeps_context_open_until_explicit_exit_wait(tmp_path, monkeypatch):
    import time
    from amazon_es_bestseller.highspeed import parallel_cli as module
    import playwright.async_api
    async def exercise():
        closed = []
        statuses = []
        exit_wait = asyncio.Event()
        async def close(): closed.append(True)
        context = SimpleNamespace(close=close)
        async def launch(*args, **kwargs): return context
        class Playwright:
            async def __aenter__(self): return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
            async def __aexit__(self, *args): pass
        class Coordinator:
            def __init__(self, *args, **kwargs): pass
            def add_work(self, work): pass
            async def run(self, context):
                return {'stop_reason': 'SHARED_COMMIT_FAILURE', 'failures': [{'message': 'TEST_ONLY'}]}
        async def wait(reporter, reason):
            assert not closed and reason == 'SHARED_COMMIT_FAILURE'
            await exit_wait.wait()
            return {'action': 'EXIT_WAIT', 'live_resumed': False}
        monkeypatch.setattr(playwright.async_api, 'async_playwright', Playwright)
        monkeypatch.setattr(module, 'ParallelSnapshotCoordinator', Coordinator)
        monkeypatch.setattr(module, 'ParallelAdmission', lambda *a, **k: None)
        monkeypatch.setattr(module, 'wait_for_operator', wait)
        store = SimpleNamespace(root=tmp_path, update_run=lambda *a, **kw: statuses.append(kw))
        runner = SimpleNamespace(store=store, engine=SimpleNamespace(clock=lambda: None),
            _record_approval=lambda *a: None, mark_run_running=lambda *a: None, page_work=lambda p: [])
        queue = SimpleNamespace(identity=(None,None,time.time()+60), pacing_deadline=lambda: 0,
            persist_actual_dispatch_deadline=lambda v: None, snapshot=lambda: {'pages': []})
        task = asyncio.create_task(module.execute(runner, {'run_id': 'TEST_ONLY', 'request_pacing_seconds': 3},
            {}, queue, tmp_path/'TEST_ONLY_profile', headless=True,
            reporter=FaultReporter(tmp_path,'TEST_ONLY','hash',notify=lambda p: None)))
        await asyncio.sleep(.03)
        assert not task.done() and not closed
        assert statuses[-1]['status'] == 'PAUSED_ERROR'
        exit_wait.set()
        result = await task
        assert result['operator_exit']['live_resumed'] is False and closed == [True]
    asyncio.run(exercise())


def test_native_popup_is_warning_and_has_no_resume_action(tmp_path, monkeypatch):
    import ctypes
    from pathlib import Path
    from amazon_es_bestseller.highspeed.parallel_faults import _popup
    calls = []
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(user32=SimpleNamespace(
        MessageBoxW=lambda *args: calls.append(args))), raising=False)
    report = FaultReporter(tmp_path, 'TEST_ONLY', 'hash', notify=lambda p: None).record(
        worker=1, url='https://snapshot-test.invalid/1', request_id='test', stage='capture',
        error=TimeoutError('TEST_ONLY'), scope='worker')
    _popup(Path(report['path']))
    assert '关闭本提醒不会恢复采集' in calls[0][1]
    assert calls[0][2] == 'Amazon 快照异常提醒'
    assert calls[0][3] & 0x40000 and calls[0][3] & 0x30


def test_primary_disk_failure_preserves_error_in_separate_fallback(tmp_path):
    blocked = tmp_path/'blocked'
    blocked.write_text('TEST_ONLY non-directory', encoding='utf-8')
    reporter = FaultReporter(blocked, 'TEST_ONLY', 'hash', fallback_root=tmp_path/'fallback', notify=lambda p: None)
    report = reporter.record(worker=None, url=None, request_id=None, stage='commit',
                             error=OSError('TEST_ONLY original disk failure'), scope='all')
    assert report['persisted'] and 'fallback' in report['path']
    assert report['primary_persistence_error']


def test_old_wait_control_cannot_release_new_wait(tmp_path):
    async def exercise():
        reporter = FaultReporter(tmp_path, 'TEST_ONLY', 'hash', notify=lambda p: None)
        first = asyncio.create_task(wait_for_operator(reporter, 'TEST_ONLY', interval=.01))
        await asyncio.sleep(.02)
        acknowledge_wait(tmp_path, 'TEST_ONLY', exit_wait=True)
        await first
        second = asyncio.create_task(wait_for_operator(reporter, 'TEST_ONLY', interval=.01))
        await asyncio.sleep(.04)
        assert not second.done()
        acknowledge_wait(tmp_path, 'TEST_ONLY', exit_wait=True)
        await second
    asyncio.run(exercise())


def test_worker_scope_requires_new_explicit_contract_without_editing_old_clock(tmp_path,monkeypatch):
    import hashlib
    from datetime import timedelta
    from test_highspeed_v4_execution import NOW
    from amazon_es_bestseller.multisource.storage import digest, sealed
    from amazon_es_bestseller.highspeed.parallel_amendment import validate_parallel_amendment
    from amazon_es_bestseller.highspeed import v4_authorization
    monkeypatch.setattr(v4_authorization,'validate_v4_authorization',lambda *a,**kw:None)
    plan={'run_id':'TEST_ONLY'}
    clock={'run_id':'TEST_ONLY','plan_sha256':digest(plan),'deadline':(NOW+timedelta(hours=24)).isoformat()}
    path=tmp_path/'clock.json';path.write_text(json.dumps(sealed(clock)),encoding='utf-8')
    before=path.read_bytes();ref={'path':str(path),'sha256':hashlib.sha256(before).hexdigest()}
    auth={'task_clock_reference':ref,'store_root':str(tmp_path)};manifest={'files':{}}
    amendment={'kind':'PAGE_PARALLEL_AMENDMENT_V1','approved':True,'approved_by':'USER',
        'approval_id':'TEST_ONLY','authorization_text':'TEST_ONLY approved worker scope',
        'run_id':'TEST_ONLY','original_plan_sha256':digest(plan),'original_authorization_sha256':digest(auth),
        'original_clock_reference':ref,'original_deadline':clock['deadline'],'reviewed_at':NOW.isoformat(),
        'workers':3,'parallel_scope':'THREE_FIXED_WINDOWS_SAME_CONTEXT','access_stop_scope':'all',
        'profile_rotation':False,'cookie_rotation':False,'account_rotation':False,'ip_rotation':False,
        'code_manifest_sha256':digest(manifest)}
    kwargs=dict(now=NOW,gate={'state':'NORMAL'},code_manifest=manifest)
    validate_parallel_amendment(plan,auth,amendment,**kwargs)
    worker={**amendment,'access_stop_scope':'worker'}
    with pytest.raises(ValueError,match='PAUSE_ALERT_CONTRACT'):
        validate_parallel_amendment(plan,auth,worker,**kwargs)
    validate_parallel_amendment(plan,auth,{**worker,'fault_handling_contract':'PAUSE_ALERT_V1'},**kwargs)
    assert amendment['access_stop_scope']=='all' and path.read_bytes()==before


def test_body_error_after_worker_access_stop_keeps_both_evidence_records(tmp_path):
    import time
    from test_highspeed_v4_execution import setup_scope,NOW
    from amazon_es_bestseller.highspeed.store import RunStore
    from amazon_es_bestseller.highspeed.v4_runner import V4T1Runner
    from amazon_es_bestseller.highspeed.parallel_coordinator import ParallelSnapshotCoordinator
    plan,auth=setup_scope(tmp_path);store=RunStore(auth['store_root'])
    try:
        store.begin_run(plan,started_at=NOW.isoformat())
        queue=ParallelPageLedger(tmp_path/'q.sqlite',run_id='TEST_ONLY',original_plan_sha256='hash',
                                 deadline=time.time()+60,page_budget=5)
        url=plan['sources'][0]['actual_source_url']
        queue.seed([{'url':url,'category':'TEST_ONLY','family':'BEST_SELLERS'}]);queue.claim(1)
        c=ParallelSnapshotCoordinator(V4T1Runner(store),plan,queue,AsyncDispatchGate(deadline=time.time()+60),
            admit=lambda w,i:True,stop_reason=lambda:None,fixture_mode=True,access_stop_scope='worker')
        item={'url':url,'reservation':{'request_id':'TEST_ONLY'}}
        c.access_stop(1,'BLOCKED',item)
        try:raise OSError('TEST_ONLY response body lost')
        except OSError as error:c.fault(1,item,error,'capture')
        assert c.stopped is None and len(c.failures)==2
        assert c.failures[-1]['stage']=='capture_after_access'
        assert 'response body lost' in c.failures[-1]['traceback']
        assert queue.snapshot()['pages'][0][3]=='HELD'
    finally:store.close()
