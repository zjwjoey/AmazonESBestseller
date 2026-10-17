import asyncio
import pytest

from amazon_es_bestseller.collection.detail_dual5500 import TwoRequestScheduler


def test_hour_target_global_pacing_and_exactly_two_inflight():
    async def scenario():
        now = [0.0]
        starts = []
        release = asyncio.Event()
        active = set()
        async def sleep(seconds):
            now[0] += seconds
            await asyncio.sleep(0)
        async def reserve(task, lane):
            return task, lane
        async def capture(token, sent):
            active.add(token[0])
            starts.append(now[0])
            sent()
            if len(active) == 2:
                release.set()
            await release.wait()
            active.remove(token[0])
        s = TwoRequestScheduler(reserve, capture, clock=lambda: now[0], sleep=sleep)
        await s.run(['a', 'b'])
        assert starts == [0, 3.6]
        assert s.peak_active == 2
        assert s.claimed == ['a', 'b']
    asyncio.run(scenario())


@pytest.mark.parametrize('interval', [0, 3, 3.599, float('nan'), float('inf'), True])
def test_reject_faster_or_invalid_pacing(interval):
    with pytest.raises(ValueError):
        TwoRequestScheduler(None, None, interval=interval)


def test_hour_target_is_not_a_catch_up_burst():
    async def scenario():
        now = [0.0]
        starts = []
        async def sleep(seconds):
            now[0] += seconds
            await asyncio.sleep(0)
        async def reserve(task, lane):
            if task == 'b':
                now[0] += 100
            return task
        async def capture(task, sent):
            starts.append(now[0]); sent()
        s = TwoRequestScheduler(reserve, capture, clock=lambda: now[0], sleep=sleep)
        await s.run(list('abcd'))
        assert all(b-a >= 3.6-1e-9 for a,b in zip(starts, starts[1:]))
        assert starts[-1] >= 107.2-1e-9
    asyncio.run(scenario())


def test_shared_budget_failure_and_restriction_drain_reserved_work():
    async def scenario(access):
        now = [0.0]; paid = []; saved = []
        async def sleep(seconds):
            now[0] += seconds
            await asyncio.sleep(0)
        async def reserve(task, lane):
            if len(paid) >= 2:
                raise ValueError('BUDGET_EXHAUSTED')
            paid.append(task)
            return task
        async def capture(task, sent):
            sent()
            await asyncio.sleep(0)
            if access:
                s.fail(RuntimeError('CHALLENGE'), access=True)
            saved.append(task)
        s = TwoRequestScheduler(reserve, capture, clock=lambda: now[0], sleep=sleep)
        with pytest.raises((ValueError, RuntimeError)):
            await s.run(list('abcdef'))
        assert set(saved) == set(paid)
        assert len(paid) <= 2
        assert s.active == 0
        assert s.access_failure == access
    asyncio.run(scenario(False))
    asyncio.run(scenario(True))


def test_duplicate_tasks_rejected_before_reservation():
    async def forbidden(*a):
        raise AssertionError('must not claim')
    with pytest.raises(ValueError, match='DUPLICATE'):
        asyncio.run(TwoRequestScheduler(forbidden, forbidden).run(['a', 'a']))


def test_thousand_dispatches_have_no_short_gaps_virtual_clock():
    async def scenario():
        now = [0.0]; starts = []
        async def sleep(seconds):
            now[0] += seconds
            await asyncio.sleep(0)
        async def reserve(task, lane): return task
        async def capture(task, sent): starts.append(now[0]); sent()
        s = TwoRequestScheduler(reserve, capture, clock=lambda: now[0], sleep=sleep)
        await s.run(range(1000))
        assert len(starts) == 1000
        assert starts[-1] == pytest.approx(999 * 3.6)
        assert all(b-a >= 3.6-1e-9 for a,b in zip(starts, starts[1:]))
        assert s.peak_active <= 2
    asyncio.run(scenario())


def test_reservation_validation_is_inside_the_pacing_window_not_added_after_it():
    async def scenario():
        now = [0.0]; starts = []
        async def sleep(seconds):
            now[0] += seconds
            await asyncio.sleep(0)
        async def reserve(task, lane):
            now[0] += 1.5  # unchanged authorization/budget validation cost
            return task
        async def capture(task, sent): starts.append(now[0]); sent()
        s = TwoRequestScheduler(reserve, capture, clock=lambda: now[0], sleep=sleep)
        await s.run(range(10))
        assert starts == pytest.approx([1.5 + i*3.6 for i in range(10)])
    asyncio.run(scenario())


def test_stop_after_reservation_before_start_preserves_unsent_token():
    async def scenario():
        now = [0.0]; reserved = []; saved = []; sent_tasks = []
        async def sleep(seconds):
            now[0] += seconds
            s.fail(RuntimeError('CHALLENGE'), access=True)
            await asyncio.sleep(0)
        async def reserve(task, lane): reserved.append(task); return task
        async def capture(task, sent):
            if not s.stop.is_set():
                sent_tasks.append(task); sent()
            saved.append(task)
        s = TwoRequestScheduler(reserve, capture, clock=lambda: now[0], sleep=sleep)
        with pytest.raises(RuntimeError, match='CHALLENGE'):
            await s.run(list('abcd'))
        assert sent_tasks == ['a']
        assert set(reserved) == set(saved) == {'a', 'b'}
        assert s.active == 0 and s.access_failure
    asyncio.run(scenario())


def test_early_timer_wakeup_does_not_start_before_global_deadline():
    async def scenario():
        now=[0.0];starts=[]
        async def sleep(seconds):
            now[0]+=seconds*.75  # Windows timers may wake before the requested delay
            await asyncio.sleep(0)
        async def reserve(task,lane):return task
        async def capture(task,sent):starts.append(now[0]);sent()
        s=TwoRequestScheduler(reserve,capture,clock=lambda:now[0],sleep=sleep)
        await s.run(range(5))
        assert all(b-a>=3.6-1e-12 for a,b in zip(starts,starts[1:]))
    asyncio.run(scenario())


def test_default_precision_wait_rechecks_deadline_during_cooperative_yields():
    async def scenario():
        now=[0.0];starts=[];yield_count=[0]
        async def reserve(task,lane):return task
        async def capture(task,sent):starts.append(now[0]);sent()
        s=TwoRequestScheduler(reserve,capture)
        async def sleep(seconds):
            if seconds==0:yield_count[0]+=1;now[0]+=.001
            else:now[0]+=seconds*.99
            await asyncio.sleep(0)
        s.clock=lambda:now[0];s.sleep=sleep
        await s.run(range(4))
        assert yield_count[0]>0
        assert all(b-a>=3.6-1e-12 for a,b in zip(starts,starts[1:]))
    asyncio.run(scenario())
