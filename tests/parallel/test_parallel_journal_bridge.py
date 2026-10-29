import asyncio
import pytest
from amazon_es_bestseller.highspeed.parallel_page_ledger import ParallelPageLedger
from amazon_es_bestseller.highspeed.parallel_journal_bridge import OfflineJournalBridge


class FakeStore:
    def __init__(self):self.calls=[];self.fail_reserve=False;self.fail_save=False;self.on_save=None;self.requests=[];self.responses=[]
    def reserve_request(self,run,request):
        self.requests.append(request)
        self.calls.append(('reserve',request['url']))
        if self.fail_reserve:raise OSError('TEST_ONLY journal failure')
        return {'request_id':'TEST_ONLY_'+request['url']}
    def save_response(self,run,reservation,response):
        self.responses.append(response)
        self.calls.append(('save',response['request_url']))
        if self.on_save:self.on_save()
        if self.fail_save:raise OSError('TEST_ONLY receipt failure')
        return {'sha256':'TEST_ONLY_HASH','access_state':response['access_state']}


def setup(tmp_path):
    q=ParallelPageLedger(tmp_path/'queue.sqlite',run_id='TEST_ONLY',original_plan_sha256='original',deadline=1000,page_budget=5)
    q.seed([{'url':x,'category':'one','family':'BEST_SELLERS'} for x in ('a','b','c')])
    store=FakeStore();plan={'run_id':'TEST_ONLY','origin':'OFFLINE_FIXTURE','live_enabled':False}
    sources={x:{'source_id':'id_'+x,'source_type':'BEST_SELLERS'} for x in ('a','b','c')}
    return q,store,OfflineJournalBridge(q,store,plan,sources)


def test_restricted_response_closes_gate_before_evidence_write(tmp_path):
    q,store,bridge=setup(tmp_path)
    item=bridge.reserve(1,now=10,observed_at='2026-01-01T00:00:00Z')
    store.on_save=lambda: (_ for _ in ()).throw(AssertionError('gate remained open')) if q.claim(3,now=20) else None
    saved=bridge.save_response(item,{'status_code':403,'text':'TEST_ONLY access denied'})
    assert saved['access_state']!='NORMAL' and q.snapshot()['config'][0]=='ACCESS_STOP'
    assert q.snapshot()['pages'][0][3]=='HELD'
    assert store.calls==[('reserve','a'),('save','a')]


@pytest.mark.parametrize('stage',['reserve','response'])
def test_journal_failure_holds_claim_and_stops_further_dispatch(tmp_path,stage):
    q,store,bridge=setup(tmp_path)
    if stage=='reserve':store.fail_reserve=True
    if stage=='reserve':
        with pytest.raises(OSError):bridge.reserve(1,now=10,observed_at='2026-01-01T00:00:00Z')
    else:
        item=bridge.reserve(1,now=10,observed_at='2026-01-01T00:00:00Z');store.fail_save=True
        with pytest.raises(OSError):bridge.save_response(item,{'status_code':200,'text':'TEST_ONLY'})
    assert q.snapshot()['config'][0]=='JOURNAL_FAILURE'
    assert q.claim(2,now=20) is None
    q.hold_inflight_after_restart();assert q.snapshot()['pages'][0][3]=='HELD'


def test_prototype_rejects_live_plan(tmp_path):
    q,store,_=setup(tmp_path)
    with pytest.raises(ValueError,match='TEST_ONLY'):
        OfflineJournalBridge(q,store,{'run_id':'real','origin':'LIVE_CAPTURE','live_enabled':True},{})


def test_descendant_page_key_matches_serial_v4_journal(tmp_path):
    import hashlib
    q,store,bridge=setup(tmp_path)
    bridge.sources['a']['actual_source_url']='different-root'
    item=bridge.reserve(1,now=10,observed_at='2026-01-01T00:00:00Z')
    expected='id_a_'+hashlib.sha256(b'a').hexdigest()[:12]
    bridge.save_response(item,{'status_code':200,'text':'TEST_ONLY'})
    assert item['page_key']==store.requests[0]['page_key']==store.responses[0]['page_key']==expected


def test_restriction_closes_dispatch_before_queue_or_receipt_write(tmp_path):
    q,store,bridge=setup(tmp_path)
    item=bridge.reserve(1,now=10,observed_at='2026-01-01T00:00:00Z')
    stopped=[]
    bridge.on_stop=lambda:stopped.append(True)
    old_stop=q.stop
    def checked_stop(reason):
        assert stopped, 'dispatch remained open before queue write'
        old_stop(reason)
    q.stop=checked_stop
    store.on_save=lambda:stopped or (_ for _ in ()).throw(AssertionError('dispatch remained open'))
    bridge.save_response(item,{'status_code':403,'text':'TEST_ONLY access denied'})
    assert stopped and q.snapshot()['config'][0]=='ACCESS_STOP'


def test_restriction_blocks_already_reserved_dispatches(tmp_path):
    from amazon_es_bestseller.highspeed.parallel_dispatch_gate import AsyncDispatchGate,DispatchStopped
    async def exercise():
        q,store,bridge=setup(tmp_path)
        items=[bridge.reserve(worker,now=10+3*(worker-1),observed_at='2026-01-01T00:00:00Z')
               for worker in (1,2,3)]
        gate=AsyncDispatchGate(deadline=1000,clock=lambda:20)
        bridge.on_stop=gate.stop
        sent=[]
        async def request(item):
            async def send():
                sent.append(item['url'])
                bridge.save_response(item,{'status_code':403,'text':'TEST_ONLY access denied'})
            try:
                await gate.dispatch(verify_reservation=lambda:True,send=send)
            except DispatchStopped:
                pass
        await asyncio.gather(*(request(item) for item in items))
        assert len(sent)==1
        assert len(store.calls)==4 # three durable reservations, one response
        assert q.snapshot()['config'][0]=='ACCESS_STOP'
        q.hold_inflight_after_restart()
        assert all(row[3]=='HELD' for row in q.snapshot()['pages'])
        assert q.claim(1,now=40) is None
    asyncio.run(exercise())
