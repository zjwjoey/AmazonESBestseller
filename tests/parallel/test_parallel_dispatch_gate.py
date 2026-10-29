import asyncio
import pytest
from amazon_es_bestseller.highspeed.parallel_dispatch_gate import AsyncDispatchGate,DispatchStopped
from amazon_es_bestseller.highspeed.parallel_page_ledger import ParallelPageLedger


def test_actual_dispatch_callback_not_earlier_claim_controls_global_interval():
    async def case():
        clock=[10.0]; actual=[]
        async def wait(delay,stop):
            await asyncio.sleep(0)
            if not stop.is_set():clock[0]+=delay
        gate=AsyncDispatchGate(deadline=1000,clock=lambda:clock[0],wait=wait)
        async def send(worker):
            actual.append((worker,clock[0]));clock[0]+=5 if worker==1 else 0
            return worker
        async def worker(n):
            return await gate.dispatch(verify_reservation=lambda:True,send=lambda:send(n))
        assert await asyncio.gather(*(worker(n) for n in (1,2,3)))==[1,2,3]
        assert actual==[(1,10.0),(2,18.0),(3,21.0)]
    asyncio.run(case())


def test_access_stop_during_wait_blocks_new_dispatch():
    async def case():
        clock=[10.0]; sent=[]
        async def wait(delay,stop):
            gate.stop();await asyncio.sleep(0)
        gate=AsyncDispatchGate(deadline=1000,clock=lambda:clock[0],wait=wait)
        async def send():sent.append(1)
        await gate.dispatch(verify_reservation=lambda:True,send=send)
        with pytest.raises(DispatchStopped,match='GLOBAL_STOP'):
            await gate.dispatch(verify_reservation=lambda:True,send=send)
        assert len(sent)==1
    asyncio.run(case())


def test_bad_reservation_and_dispatch_error_stop_all_lanes():
    async def case():
        gate=AsyncDispatchGate(deadline=1000,clock=lambda:10)
        async def send():raise AssertionError('should not be sent')
        with pytest.raises(DispatchStopped,match='INVALID_RESERVATION'):
            await gate.dispatch(verify_reservation=lambda:False,send=send)
        with pytest.raises(DispatchStopped,match='GLOBAL_STOP'):
            await gate.dispatch(verify_reservation=lambda:True,send=send)
        another=AsyncDispatchGate(deadline=1000,clock=lambda:10)
        with pytest.raises(AssertionError):await another.dispatch(verify_reservation=lambda:True,send=send)
        with pytest.raises(DispatchStopped,match='GLOBAL_STOP'):
            await another.dispatch(verify_reservation=lambda:True,send=send)
    asyncio.run(case())


def test_original_deadline_blocks_dispatch():
    async def case():
        gate=AsyncDispatchGate(deadline=10,clock=lambda:10)
        async def send():raise AssertionError('should not be sent')
        with pytest.raises(DispatchStopped,match='ORIGINAL_DEADLINE'):
            await gate.dispatch(verify_reservation=lambda:True,send=send)
    asyncio.run(case())


def test_prior_pacing_and_durable_failure_before_dispatch_are_fail_closed():
    async def case():
        clock=[10.0];persisted=[];sent=[]
        async def wait(delay,stop):clock[0]+=delay
        gate=AsyncDispatchGate(deadline=1000,clock=lambda:clock[0],wait=wait,
                               prior_next_start=13,persist_next_start=persisted.append)
        async def send():sent.append(clock[0])
        await gate.dispatch(verify_reservation=lambda:True,send=send)
        assert sent==[13] and persisted==[16,16]
        def fail(_):raise OSError('TEST_ONLY_DURABLE_WRITE_FAILURE')
        broken=AsyncDispatchGate(deadline=1000,clock=lambda:10,persist_next_start=fail)
        with pytest.raises(OSError):await broken.dispatch(verify_reservation=lambda:True,send=send)
        assert sent==[13]
        with pytest.raises(DispatchStopped,match='GLOBAL_STOP'):
            await broken.dispatch(verify_reservation=lambda:True,send=send)
    asyncio.run(case())


def test_dispatch_pacing_is_persisted_and_cannot_be_reset_after_restart(tmp_path):
    async def case():
        q=ParallelPageLedger(tmp_path/'queue.sqlite',run_id='TEST_ONLY',original_plan_sha256='original',deadline=1000,page_budget=2)
        q.seed([{'url':'a','category':'one','family':'BEST_SELLERS'}]);assert q.claim(1,now=10)=='a'
        clock=[10.0]
        async def wait(delay,stop):clock[0]+=delay
        gate=AsyncDispatchGate(deadline=1000,clock=lambda:clock[0],wait=wait,
                               prior_next_start=q.pacing_deadline(),persist_next_start=q.persist_actual_dispatch_deadline)
        async def send():return 'TEST_ONLY'
        assert await gate.dispatch(verify_reservation=lambda:True,send=send)=='TEST_ONLY'
        assert q.pacing_deadline()==16
        with pytest.raises(ValueError,match='RESET'):
            q.persist_actual_dispatch_deadline(15)
        assert q.pacing_deadline()==16
    asyncio.run(case())
def test_response_completion_extends_a_dispatch_already_waiting():
    async def exercise():
        clock=[10.0]; waits=[]; persisted=[]
        async def wait(delay,stop):
            waits.append(delay)
            if len(waits)==1:
                clock[0]=11.0
                gate.note_completion()
            else:
                clock[0]+=delay
        gate=AsyncDispatchGate(deadline=100,clock=lambda:clock[0],wait=wait,
            prior_next_start=13,persist_next_start=persisted.append)
        sent=[]
        async def send():sent.append(clock[0])
        await gate.dispatch(verify_reservation=lambda:True,send=send)
        assert sent==[14.0] and waits==[3.0,3.0]
        assert persisted[0]==14.0
    asyncio.run(exercise())


def test_response_completion_persistence_failure_stops_gate():
    async def exercise():
        def fail(_):raise OSError('TEST_ONLY disk failure')
        gate=AsyncDispatchGate(deadline=100,clock=lambda:10,persist_next_start=fail)
        with pytest.raises(OSError):gate.note_completion()
        with pytest.raises(DispatchStopped):
            await gate.dispatch(verify_reservation=lambda:True,send=lambda:None)
    asyncio.run(exercise())
