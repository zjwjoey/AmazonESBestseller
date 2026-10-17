import asyncio
import pytest


def test_three_requests_really_overlap_with_global_spacing_and_unique_claims():
    from amazon_es_bestseller.collection.detail_parallel5500 import ThreeRequestScheduler
    async def scenario():
        clock=[0.0];starts=[];active=set();release=asyncio.Event()
        async def sleep(seconds):clock[0]+=seconds;await asyncio.sleep(0)
        async def reserve(task,lane):return {'task':task,'lane':lane}
        async def capture(token,sent):
            active.add(token['task']);starts.append(clock[0]);sent()
            if len(active)==3:release.set()
            await release.wait();await asyncio.sleep(0)
            active.remove(token['task'])
        scheduler=ThreeRequestScheduler(reserve,capture,interval=3,clock=lambda:clock[0],sleep=sleep)
        await scheduler.run(['a','b','c'])
        assert scheduler.peak_active==3 and starts==[0,3,6]
        assert scheduler.claimed==['a','b','c']
    asyncio.run(scenario())


def test_restriction_stops_new_starts_but_drains_already_reserved_work():
    from amazon_es_bestseller.collection.detail_parallel5500 import ThreeRequestScheduler
    async def scenario():
        saved=[];reserved=[];clock=[0.0]
        async def sleep(s):clock[0]+=s;await asyncio.sleep(0)
        async def reserve(task,lane):reserved.append(task);return task
        async def capture(task,sent):
            sent();await asyncio.sleep(0)
            if task=='a':scheduler.fail(RuntimeError('CHALLENGE'),access=True)
            saved.append(task)
        scheduler=ThreeRequestScheduler(reserve,capture,interval=3,clock=lambda:clock[0],sleep=sleep)
        with pytest.raises(RuntimeError,match='CHALLENGE'):await scheduler.run(list('abcdef'))
        assert set(saved)==set(reserved) and len(reserved)<6
    asyncio.run(scenario())


def test_single_shared_budget_is_checked_under_dispatch_lock():
    from amazon_es_bestseller.collection.detail_parallel5500 import ThreeRequestScheduler
    async def scenario():
        paid=[];clock=[0.0]
        async def sleep(s):clock[0]+=s;await asyncio.sleep(0)
        async def reserve(task,lane):
            if len(paid)>=1:raise ValueError('BUDGET_EXHAUSTED')
            await asyncio.sleep(0);paid.append(task);return task
        async def capture(task,sent):sent();await asyncio.sleep(0)
        scheduler=ThreeRequestScheduler(reserve,capture,interval=3,clock=lambda:clock[0],sleep=sleep)
        with pytest.raises(ValueError,match='BUDGET'):await scheduler.run(list('abc'))
        assert paid==['a']
    asyncio.run(scenario())


def test_duplicate_tasks_and_lower_global_pacing_rejected():
    from amazon_es_bestseller.collection.detail_parallel5500 import ThreeRequestScheduler
    async def noop(*args):pass
    with pytest.raises(ValueError):ThreeRequestScheduler(noop,noop,interval=1)
    async def scenario():
        scheduler=ThreeRequestScheduler(noop,noop)
        with pytest.raises(ValueError,match='DUPLICATE'):await scheduler.run(['a','a'])
    asyncio.run(scenario())
