import asyncio
import pytest

from amazon_es_bestseller.collection.detail_dispatch import DetailDispatchScheduler


def test_five_share_one_clock_queue_and_drain():
    async def case():
        now = [0.0]
        async def sleep(delay):
            now[0] += delay
            await asyncio.sleep(0)
        starts, active, claimed = [], set(), []
        release = asyncio.Event()
        async def reserve(task, lane):
            claimed.append(task['task_id'])
            return task, lane
        async def capture(token, sent):
            task, lane = token
            sent()
            starts.append(now[0]); active.add(lane)
            if len(active) == 5: release.set()
            await release.wait()
            await asyncio.sleep(0)
        scheduler = DetailDispatchScheduler(reserve, capture, lanes=5,
                                           clock=lambda:now[0], sleep=sleep)
        await scheduler.run([{'task_id':str(i)} for i in range(10)])
        assert claimed == list(map(str,range(10)))
        assert active == set(range(5))
        assert scheduler.peak_active == 5
        assert all(b-a >= 3.6-1e-9 for a,b in zip(starts,starts[1:]))
        assert scheduler.active == 0
    asyncio.run(case())


def test_duplicate_claim_rejected_before_reservation():
    async def never(*args):raise AssertionError('must not reserve')
    async def case():
        runner = DetailDispatchScheduler(never, never, lanes=5)
        with pytest.raises(ValueError, match='DUPLICATE'):
            await runner.run([{'task_id':'same'},{'task_id':'same'}])
    asyncio.run(case())


def test_no_start_or_capture_system_failure_stops_all_new_work():
    async def case():
        claimed=[]
        async def reserve(task,lane):claimed.append(task);return task
        async def capture(task,sent):raise OSError('disk failed')
        runner=DetailDispatchScheduler(reserve,capture,lanes=5)
        with pytest.raises(OSError,match='disk failed'):
            await runner.run([{'task_id':str(i)} for i in range(10)])
        assert len(claimed)==1
        assert runner.active==0
    asyncio.run(case())


@pytest.mark.parametrize('interval',[0,3,3.59,float('nan'),float('inf'),True])
def test_global_floor_cannot_be_lowered(interval):
    with pytest.raises(ValueError):DetailDispatchScheduler(None,None,interval=interval)
