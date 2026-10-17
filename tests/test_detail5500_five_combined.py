import asyncio
import json

import pytest

from amazon_es_bestseller.collection import detail_five_runtime5500 as five
from amazon_es_bestseller.collection import detail_dual_runtime5500 as dual
from amazon_es_bestseller.collection.detail_five5500 import FiveRequestScheduler
from test_detail5500_dual_runtime import mode, ref


def five_mode(tmp_path):
    plan, auth, amendment, ap = mode(tmp_path)
    amendment.update(scope='FIVE_LANES_GROUPED_3_PLUS_2_SAME_FROZEN_RUN',
                     network_parallelism=5, max_uncommitted_tasks=5,
                     processing_groups=[3, 2])
    ap.write_text(json.dumps(amendment), encoding='utf8')
    auth.update(network_parallelism=5, local_processing_lanes=5,
                visible_browser_windows=5, max_uncommitted_tasks=5,
                processing_groups=[3, 2], parallel_execution_approval_reference=ref(ap))
    return plan, auth


def test_five_requires_own_explicit_amendment(tmp_path):
    plan, auth, _, _ = mode(tmp_path)
    with pytest.raises(ValueError): five.validate_parallel_mode(tmp_path, plan, auth)
    plan, auth = five_mode(tmp_path/'five')
    assert five.validate_parallel_mode(tmp_path/'five', plan, auth)['processing_groups'] == [3, 2]
    with pytest.raises(ValueError): dual.validate_parallel_mode(tmp_path/'five', plan, auth)


@pytest.mark.parametrize('key,value', [
    ('max_requests', 6051), ('max_page_requests', 6051), ('max_other_requests', 9),
    ('starts_at', 'reset'), ('deadline', 'extended'), ('request_deadline', 'extended'),
    ('request_start_interval_seconds', 2), ('network_parallelism', 6),
    ('local_processing_lanes', 4), ('visible_browser_windows', 4),
    ('max_uncommitted_tasks', 6), ('processing_groups', [2, 3]),
    ('access_recovery_reference', {'path': 'new', 'sha256': 'changed'}),
    ('manual_PAGE_budget_hold_references', []),
])
def test_five_preserves_original_controls(tmp_path, key, value):
    plan, auth = five_mode(tmp_path)
    auth[key] = value
    with pytest.raises(ValueError): five.validate_parallel_mode(tmp_path, plan, auth)


def test_five_single_queue_unique_claims_global_pacing_and_overlap():
    async def scenario():
        clock=[0.]; starts=[]; active=set(); reached=asyncio.Event()
        async def sleep(seconds): clock[0]+=seconds; await asyncio.sleep(0)
        async def reserve(task, lane): return (task, lane)
        async def capture(token, sent):
            task, lane=token
            starts.append((task, lane, clock[0]));active.add(lane);sent()
            if len(active)==5:reached.set()
            await reached.wait()
            await asyncio.sleep(0)
        scheduler=FiveRequestScheduler(reserve,capture,clock=lambda:clock[0],sleep=sleep)
        await asyncio.wait_for(scheduler.run(range(10)),2)
        assert len(starts)==10 and len({t for t,_,_ in starts})==10
        assert {lane for _,lane,_ in starts}==set(range(5))
        assert all(b[2]-a[2]>=3.6-1e-9 for a,b in zip(starts,starts[1:]))
        assert scheduler.peak_active==5 and scheduler.active==0
    asyncio.run(scenario())


def test_five_rejects_duplicate_claim_before_any_reservation():
    async def scenario():
        calls=[]
        async def reserve(*args):calls.append(args)
        async def capture(*args):raise AssertionError('no capture')
        s=FiveRequestScheduler(reserve,capture)
        with pytest.raises(ValueError,match='DUPLICATE'):await s.run([1,1])
        assert not calls
    asyncio.run(scenario())


def test_five_stop_drains_all_started_lanes_and_blocks_new_claims():
    async def scenario():
        calls=[];drained=[];clock=[0.]
        async def sleep(seconds):clock[0]+=seconds;await asyncio.sleep(0)
        async def reserve(task,lane):calls.append(task);return task
        async def capture(task,sent):
            sent()
            s.fail(RuntimeError('challenge'),access=True)
        async def drain():drained.append(True)
        s=FiveRequestScheduler(reserve,capture,drain=drain,clock=lambda:clock[0],sleep=sleep)
        with pytest.raises(RuntimeError,match='challenge'):await s.run(range(20))
        assert len(calls)==1 and s.access_failure and s.active==0 and drained==[True]
    asyncio.run(scenario())


def test_hourly_labels_five_without_claiming_actual_wire_overlap(tmp_path):
    from datetime import datetime,timezone
    from test_detail5500_hourly import setup
    from amazon_es_bestseller.collection.detail5500_hourly import build_report
    setup(tmp_path)
    report=build_report(tmp_path, dict(process={'CommandLine':'five'},worker_status=dict(
        phase='FINAL_COLLECTING',local_processing_lanes=5,network_parallelism=5)),datetime(2026,10,17,tzinfo=timezone.utc))
    assert report['mode']=='FIVE_ASYNC_CAPTURE_LANES_GROUPED_3_PLUS_2'
    assert len(report['lanes'])==5
    assert report['observed_document_HTTP_peak_inflight'] is None
