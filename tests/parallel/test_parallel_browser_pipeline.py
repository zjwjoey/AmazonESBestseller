"""Real local browser + scheduler + original store/parser/DOM audit.

All document bodies are intercepted saved fixtures. No external route is sent.
This is integration evidence, never live capture or business acceptance.
"""
import asyncio,time
from pathlib import Path
from playwright.async_api import async_playwright
from test_highspeed_v4_execution import setup_scope,NOW
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.v4_runner import V4T1Runner
from amazon_es_bestseller.highspeed.parallel_page_ledger import ParallelPageLedger
from amazon_es_bestseller.highspeed.parallel_dispatch_gate import AsyncDispatchGate
from amazon_es_bestseller.highspeed.parallel_coordinator import ParallelSnapshotCoordinator
from amazon_es_bestseller.highspeed.v4_quality import audit_v4


def test_real_three_window_pipeline_original_journals_and_restart(tmp_path):
    plan,auth=setup_scope(tmp_path);store=RunStore(auth['store_root'])
    try:
        store.begin_run(plan,started_at=NOW.isoformat());work=[];fixtures={}
        for source in plan['sources'][:3]:
            source={**source,'collection_category_key':'TEST_ONLY'}
            url=source['actual_source_url'];work.append((source,url))
            fixtures[url]=(200,Path(auth['sources'][source['source_id']]['evidence']['path']).read_text(encoding='utf8'))
        runner=V4T1Runner(store)
        q=ParallelPageLedger(tmp_path/'queue.sqlite',run_id='TEST_ONLY',original_plan_sha256='TEST_ONLY',deadline=time.time()+90,page_budget=5)
        gate=AsyncDispatchGate(deadline=time.time()+90,prior_next_start=q.pacing_deadline(),
            persist_next_start=q.persist_actual_dispatch_deadline)
        c=ParallelSnapshotCoordinator(runner,plan,q,gate,admit=lambda w,i:True,stop_reason=lambda:None,fixture_mode=True)
        c.add_work(work)
        async def run():
            async with async_playwright() as p:
                context=await p.chromium.launch_persistent_context(str(tmp_path/'TEST_ONLY_profile'),headless=True,service_workers='block')
                try:
                    # Defense in depth: context-level deny remains beneath all
                    # page routes; fixture mode always fulfills documents.
                    await context.route('**/*',lambda route:route.abort())
                    return await c.run(context,offline_pages=fixtures)
                finally:await context.close()
        result=asyncio.run(run())
        assert result['stop_reason'] is None
        assert len(store.requests(plan['run_id']))==len(store.pages(plan['run_id']))==3
        assert all(row[3]=='COMMITTED' for row in q.snapshot()['pages'])
        quality=audit_v4(store.pages(plan['run_id']),list(store.iter_observations(plan['run_id'])),
            [s for s,u in work],require_live=False)
        assert quality['central_full_dom_review_passed'],quality['errors']
        assert all(r['origin']=='OFFLINE_FIXTURE' for r in store.iter_responses(plan['run_id']))
        asyncio.run(run())
        assert len(store.requests(plan['run_id']))==3
    finally:store.close()
