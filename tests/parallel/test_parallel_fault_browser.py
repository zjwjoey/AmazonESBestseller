"""Fault injection through three local browsers; all documents are saved fixtures."""
import asyncio
from pathlib import Path
import time

import pytest
from playwright.async_api import async_playwright
from test_highspeed_v4_execution import setup_scope, NOW
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.v4_runner import V4T1Runner
from amazon_es_bestseller.highspeed.parallel_coordinator import ParallelSnapshotCoordinator
from amazon_es_bestseller.highspeed.parallel_dispatch_gate import AsyncDispatchGate
from amazon_es_bestseller.highspeed.parallel_page_ledger import ParallelPageLedger
from amazon_es_bestseller.highspeed.parallel_transport import ThreeWindowTransport
from amazon_es_bestseller.multisource.storage import read_sealed


@pytest.mark.parametrize('failure', ['timeout', '403', 'journal'])
def test_fault_isolation_and_shared_writer_stop_in_real_windows(tmp_path, monkeypatch, failure):
    plan, auth = setup_scope(tmp_path)
    store = RunStore(auth['store_root'])
    try:
        store.begin_run(plan, started_at=NOW.isoformat())
        work = [({**s, 'collection_category_key': 'TEST_ONLY'},s['actual_source_url']) for s in plan['sources'][:3]]
        fixtures = {url:(200,Path(auth['sources'][s['source_id']]['evidence']['path']).read_text('utf-8')) for s,url in work}
        if failure == '403':fixtures[work[0][1]]=(403,'TEST_ONLY forbidden')
        if failure == 'timeout':
            original = ThreeWindowTransport.open
            async def open_with_timeout(self):
                result = await original(self)
                async def fail_goto(*args, **kwargs):raise TimeoutError('TEST_ONLY browser timeout')
                self.pages[1].goto = fail_goto
                return result
            monkeypatch.setattr(ThreeWindowTransport,'open',open_with_timeout)
        if failure == 'journal':
            def fail_write(*args,**kwargs):raise PermissionError('TEST_ONLY shared response writer unavailable')
            monkeypatch.setattr(store,'save_response',fail_write)
        queue = ParallelPageLedger(tmp_path/'queue.sqlite',run_id='TEST_ONLY',original_plan_sha256='hash',
                                   deadline=time.time()+180,page_budget=10)
        c = ParallelSnapshotCoordinator(V4T1Runner(store),plan,queue,AsyncDispatchGate(deadline=time.time()+180),
            admit=lambda w,i:True,stop_reason=lambda:None,fixture_mode=True,access_stop_scope='worker')
        c.add_work(work)
        async def run():
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(str(tmp_path/'TEST_ONLY_profile'),headless=True,service_workers='block')
                try:
                    await context.route('**/*',lambda route:route.abort())
                    return await c.run(context,offline_pages=fixtures)
                finally:await context.close()
        result=asyncio.run(run())
        assert result['failures']
        if failure=='journal':
            assert c.stopped and len(store.requests(plan['run_id']))==1
            assert any('shared response writer unavailable' in r['traceback'] for r in result['failures'])
            assert queue.snapshot()['config'][0]=='JOURNAL_FAILURE'
        else:
            assert len(store.pages(plan['run_id']))==2
            assert len(store.requests(plan['run_id']))==3
            assert 1 in queue.disabled_workers()
            assert queue.snapshot()['pages'][0][3]=='HELD'
            assert store.get_gate()['state']=='NORMAL'
            assert read_sealed(plan['shared_access_gate_path'])['state']=='NORMAL'
            if failure=='403':
                assert any(r['status_code']==403 for r in store.iter_responses(plan['run_id']))
            else:assert 'browser timeout' in result['failures'][0]['traceback']
        assert all(r['origin']=='OFFLINE_FIXTURE' for r in store.iter_responses(plan['run_id']))
    finally:store.close()
