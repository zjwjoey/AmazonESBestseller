import asyncio,time
from pathlib import Path
from test_highspeed_v4_execution import setup_scope,NOW
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.v4_runner import V4T1Runner
from amazon_es_bestseller.highspeed.parallel_page_ledger import ParallelPageLedger
from amazon_es_bestseller.highspeed.parallel_dispatch_gate import AsyncDispatchGate
from amazon_es_bestseller.highspeed.parallel_coordinator import ParallelSnapshotCoordinator
from amazon_es_bestseller.highspeed import parallel_coordinator


def test_scheduler_commits_actual_fixture_pages_once(tmp_path,monkeypatch):
    plan,auth=setup_scope(tmp_path);store=RunStore(auth['store_root'])
    try:
        store.begin_run(plan,started_at=NOW.isoformat());sources=plan['sources'][:3]
        for s in sources:s['collection_category_key']='TEST_ONLY'
        # Mutated sources are local copies referenced by the fixture plan only.
        # The immutable store plan remains the original exact plan.
        sources=store.get_run(plan['run_id'])['plan']['sources'][:3]
        work=[]
        for source in sources:
            source={**source,'collection_category_key':'TEST_ONLY'}
            work.append((source,source['actual_source_url']))
        calls=[]
        class FixtureTransport:
            def __init__(self,context,gate,*,admit,observed_stop,captured,offline_pages):self.captured=captured
            async def open(self):return self
            async def capture(self,w,item):
                calls.append(item['url']);await asyncio.sleep(.01)
                body=Path(auth['sources'][item['source']['source_id']]['evidence']['path']).read_text(encoding='utf8')
                return self.captured(w,item,{'status_code':200,'url':item['url'],'text':body})
        monkeypatch.setattr(parallel_coordinator,'ThreeWindowTransport',FixtureTransport)
        runner=V4T1Runner(store);plan=store.get_run(plan['run_id'])['plan']
        q=ParallelPageLedger(tmp_path/'q.sqlite',run_id='TEST_ONLY',original_plan_sha256='TEST_ONLY',deadline=time.time()+60,page_budget=5)
        gate=AsyncDispatchGate(deadline=time.time()+60)
        c=ParallelSnapshotCoordinator(runner,plan,q,gate,admit=lambda w,i:True,stop_reason=lambda:None,fixture_mode=True)
        c.add_work(work);asyncio.run(c.run(None))
        assert len(calls)==len(set(calls))==3
        assert len(store.pages(plan['run_id']))==3
        assert all(row[3]=='COMMITTED' for row in q.snapshot()['pages'])
        asyncio.run(c.run(None));assert len(calls)==3
    finally:store.close()
