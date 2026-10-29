"""Original 50k snapshot hooks with saved, locally intercepted page fixtures.

Navigation anchors are removed in this fixture test: new navigation cannot be
authorized by OFFLINE_FIXTURE receipts. Existing live navigation is separately
verified by the real-checkpoint importer. No fixture is production evidence.
"""
import asyncio,json,time,pathlib
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from amazon_es_bestseller.highspeed.store import RunStore,ReadOnlyRunStore
from amazon_es_bestseller.highspeed.page_snapshot import CurrentPageSnapshotRunner
from amazon_es_bestseller.highspeed.parallel_page_ledger import ParallelPageLedger
from amazon_es_bestseller.highspeed.parallel_dispatch_gate import AsyncDispatchGate
from amazon_es_bestseller.highspeed.parallel_coordinator import ParallelSnapshotCoordinator
from amazon_es_bestseller.highspeed.three_metrics import measure_three


def test_original_snapshot_metric_and_after_page_hooks_in_three_windows(tmp_path):
    original=pathlib.Path(r'F:\ai文件\CodexStorage\work\AmazonESBestseller\snapshot50k-readiness-20261029\future-runtime\snapshot50000_c238eaefb58f')
    plan=json.loads((original/'plan.approved.json').read_text(encoding='utf8'))
    auth=json.loads((original/'authorization.approved.json').read_text(encoding='utf8'))
    old=ReadOnlyRunStore(original/'store')
    try:
        product_urls={r['page_url'] for r in old.iter_observations(plan['run_id'])}
        selected=[p for p in old.pages(plan['run_id']) if p['page_url'] in product_urls][:3]
        root_urls={p['page_url'] for p in selected}
        receipts=[r for r in old.iter_responses(plan['run_id']) if r['request_url'] in root_urls]
    finally:old.close()
    assert len(receipts)==3
    fixtures={}
    for receipt in receipts:
        dom=BeautifulSoup(pathlib.Path(receipt['html_path']).read_bytes(),'lxml')
        for link in dom.select('a[href]'):
            if '/dp/' not in link.get('href','') and '/gp/product/' not in link.get('href',''):
                del link['href']
        fixtures[receipt['request_url']]=(200,str(dom))
    store=RunStore(tmp_path/'store')
    try:
        store.begin_run(plan);runner=CurrentPageSnapshotRunner(store)
        runner._record_approval(plan,auth);runner.mark_run_running(plan)
        runner.page_work(plan)
        work=[(p['source'],p['page_url']) for p in selected]
        runner._work=work
        queue=ParallelPageLedger(tmp_path/'queue.sqlite',run_id='TEST_ONLY',original_plan_sha256='TEST_ONLY',
            deadline=time.time()+600,page_budget=3)
        gate=AsyncDispatchGate(deadline=time.time()+600,persist_next_start=queue.persist_actual_dispatch_deadline)
        coordinator=ParallelSnapshotCoordinator(runner,plan,queue,gate,admit=lambda w,i:True,
            stop_reason=lambda:None,fixture_mode=True)
        coordinator.add_work(work)
        async def exercise():
            async with async_playwright() as p:
                context=await p.chromium.launch_persistent_context(str(tmp_path/'profile'),headless=True,service_workers='block')
                try:
                    await context.route('**/*',lambda route:route.abort())
                    return await coordinator.run(context,offline_pages=fixtures)
                finally:await context.close()
        result=asyncio.run(exercise())
        assert not result['failures'],result
        assert len(store.requests(plan['run_id']))==len(store.pages(plan['run_id']))==3
        metrics=measure_three(list(store.iter_observations(plan['run_id'])),plan['run_id'],target_policy=plan['target_policy'])
        assert store.get_run(plan['run_id'])['snapshot_metrics']==metrics
        assert len(list(store.iter_observations(plan['run_id'])))>0
        assert metrics['A_raw_observations']==0 # offline fixtures never count as live progress
        assert all(r[3]=='COMMITTED' for r in queue.snapshot()['pages'][:3])
    finally:store.close()
