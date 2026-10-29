import asyncio,time
from types import SimpleNamespace
import pytest
from test_highspeed_v4_execution import setup_scope,NOW
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.v4_runner import V4T1Runner
from amazon_es_bestseller.highspeed.parallel_page_ledger import ParallelPageLedger
from amazon_es_bestseller.highspeed.parallel_dispatch_gate import AsyncDispatchGate
from amazon_es_bestseller.highspeed.parallel_coordinator import ParallelSnapshotCoordinator
from amazon_es_bestseller.highspeed.parallel_transport import ThreeWindowTransport
from amazon_es_bestseller.multisource.storage import read_sealed


@pytest.mark.parametrize('fixture_mode',[False,True])
@pytest.mark.parametrize('status',[403,429])
def test_observed_restriction_survives_response_body_failure(tmp_path,status,fixture_mode):
    plan,auth=setup_scope(tmp_path);store=RunStore(auth['store_root'])
    try:
        store.begin_run(plan,started_at=NOW.isoformat());source=plan['sources'][0];url=source['actual_source_url']
        q=ParallelPageLedger(tmp_path/'q.sqlite',run_id='TEST_ONLY',original_plan_sha256='TEST_ONLY',deadline=time.time()+60,page_budget=5)
        q.seed([{'url':url,'family':source['source_type'],'category':'test'}]);assert q.claim(1)==url
        gate=AsyncDispatchGate(deadline=time.time()+60)
        coordinator=ParallelSnapshotCoordinator(V4T1Runner(store),plan,q,gate,admit=lambda w,i:True,stop_reason=lambda:None,fixture_mode=fixture_mode)
        async def fail_body():raise OSError('TEST_ONLY response body unavailable')
        response=SimpleNamespace(status=status,request=SimpleNamespace(resource_type='document'),text=fail_body)
        adapter=ThreeWindowTransport(None,gate,admit=lambda w,i:True,observed_stop=coordinator.stop,
            captured=lambda *args:(_ for _ in ()).throw(AssertionError('body unexpectedly captured')))
        async def goto(*args,**kwargs):
            adapter._response(response)
            return response
        adapter.pages[1]=SimpleNamespace(goto=goto)
        with pytest.raises(OSError):asyncio.run(adapter.capture(1,{'url':url,'reservation':{'request_id':'TEST_ONLY'}}))
        assert store.get_gate()['state']!='NORMAL'
        shared=read_sealed(plan['shared_access_gate_path'])
        assert (shared['state']=='NORMAL')==fixture_mode
        assert q.snapshot()['config'][0]=='ACCESS_STOP'
        assert q.claim(2,now=time.time()+10) is None
        assert list((store.root/'parallel_access_observations').glob('*.json'))
        q.hold_inflight_after_restart()
        assert q.snapshot()['pages'][0][3]=='HELD'
    finally:store.close()
