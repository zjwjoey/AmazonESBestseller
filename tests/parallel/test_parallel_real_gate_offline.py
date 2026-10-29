"""Three local windows and the real V4 store/gates; no external requests."""
import asyncio,time
from pathlib import Path
import pytest
from playwright.async_api import async_playwright
from test_highspeed_v4_execution import setup_scope,NOW
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.runner import HighSpeedRunner
from amazon_es_bestseller.highspeed.parallel_page_ledger import ParallelPageLedger
from amazon_es_bestseller.highspeed.parallel_journal_bridge import OfflineJournalBridge
from amazon_es_bestseller.highspeed.parallel_dispatch_gate import AsyncDispatchGate,DispatchStopped
from amazon_es_bestseller.multisource.storage import read_sealed


@pytest.mark.parametrize(('status','body'),[(403,'TEST_ONLY forbidden'),(429,'TEST_ONLY throttled'),
                                             (200,'TEST_ONLY Robot Check')])
def test_real_gate_receipt_stops_queued_windows_and_preserves_ambiguous_claims(tmp_path,status,body):
    plan,auth=setup_scope(tmp_path)
    store=RunStore(auth['store_root'])
    try:
        store.begin_run(plan,started_at=NOW.isoformat())
        selected=plan['sources'][:3]; sources={s['actual_source_url']:s for s in selected}
        q=ParallelPageLedger(tmp_path/'TEST_ONLY_queue.sqlite',run_id='TEST_ONLY',
            original_plan_sha256='TEST_ONLY',deadline=1000,page_budget=3)
        q.seed([{'url':u,'category':'TEST_ONLY','family':s['source_type']} for u,s in sources.items()])
        fakeplan={**plan,'origin':'OFFLINE_FIXTURE','live_enabled':False}
        engine=HighSpeedRunner(store,clock=lambda:NOW)
        def persist_stop(state,receipt):
            store.set_gate(state,run_id=plan['run_id'],evidence_path=receipt['evidence_path'])
            engine._persist_shared_stop(plan,state,receipt)
        async def exercise():
            gate=AsyncDispatchGate(deadline=time.time()+50)
            bridge=OfflineJournalBridge(q,store,fakeplan,sources,on_stop=gate.stop,on_access_receipt=persist_stop)
            items=[bridge.reserve(w,now=10+3*(w-1),observed_at=NOW.isoformat()) for w in (1,2,3)]
            urls=[f'https://snapshot-test.invalid/page/{i}' for i in range(3)]
            sent=[]
            async with async_playwright() as playwright:
                context=await playwright.chromium.launch_persistent_context(str(tmp_path/'TEST_ONLY_profile'),
                    headless=True,service_workers='block')
                try:
                    async def handler(route):
                        if route.request.url not in urls:
                            await route.abort();return
                        async def send():
                            sent.append(route.request.url)
                            await route.fulfill(status=status,body=body)
                        try:await gate.dispatch(verify_reservation=lambda:True,send=send)
                        except DispatchStopped:await route.abort()
                    await context.route('**/*',handler)
                    initial=context.pages[0] if context.pages else await context.new_page()
                    cdp=await context.new_cdp_session(initial);pages=[]
                    for _ in range(3):
                        async with context.expect_page(timeout=10000) as opened:
                            await cdp.send('Target.createTarget',{'url':'about:blank','newWindow':True})
                        pages.append(await opened.value)
                    async def run_page(i):
                        try:response=await pages[i].goto(urls[i])
                        except Exception:return
                        if response:
                            if response.status in (403,429):gate.stop()
                            gate.note_completion()
                            bridge.save_response(items[i],{'status_code':response.status,'url':items[i]['url'],
                                'text':await response.text(),'observed_at':NOW.isoformat()})
                    await asyncio.gather(*(run_page(i) for i in range(3)))
                    assert len(sent)==1
                finally:await context.close()
        asyncio.run(exercise())
        assert len(store.requests(plan['run_id']))==3
        receipts=list(store.iter_responses(plan['run_id']))
        assert len(receipts)==1 and receipts[0]['status_code']==status
        assert Path(receipts[0]['html_path']).is_file()
        assert store.get_gate()['state']!='NORMAL'
        assert read_sealed(plan['shared_access_gate_path'])['state']!='NORMAL'
        q.hold_inflight_after_restart()
        assert q.snapshot()['config'][0]=='ACCESS_STOP'
        assert all(row[3]=='HELD' for row in q.snapshot()['pages'])
        assert q.claim(1,now=100) is None
    finally:store.close()
