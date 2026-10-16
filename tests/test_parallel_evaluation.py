import pytest
from amazon_es_bestseller.highspeed.parallel_evaluation import replay_trace


def jobs(n=30):
    return [{'request_id':str(i),'source_id':str(i%3),'source_type':'BEST_SELLERS',
        'kind':'PAGE','access_state':'NORMAL','network_seconds':1,'local_seconds':8} for i in range(n)]


def test_serial_trace_reproduces_original_work_and_parallel_keeps_global_pacing():
    rows=jobs();one=replay_trace(rows);three=replay_trace(rows,lanes=3)
    assert one['elapsed_seconds']==270
    assert 90<=three['elapsed_seconds']<270
    assert three['executable'] is False and three['network_actions']==0
    schedule=three['schedule']
    assert all(b['start']-a['start']>=3 for a,b in zip(schedule,schedule[1:]))
    assert all(b['start']>=a['network_end'] for a,b in zip(schedule,schedule[1:]))
    for sid in ('0','1','2'):
        chain=[s for s in schedule if s['source_id']==sid]
        assert len({s['lane'] for s in chain})==1
        assert [s['request_id'] for s in chain]==[j['request_id'] for j in rows if j['source_id']==sid]
        assert all(b['start']>=a['work_end'] for a,b in zip(chain,chain[1:]))


def test_fully_serial_local_work_has_no_parallel_speedup():
    assert replay_trace(jobs(),lanes=3,serial_local_fraction=1)['elapsed_seconds']==270


def test_finite_shared_request_cap_not_multiplied_by_lanes():
    assert replay_trace(jobs(),lanes=3,request_cap=7)['requests']==7
    assert replay_trace(jobs(),lanes=3,request_cap=0)['requests']==0


@pytest.mark.parametrize('change',[
    {'pacing_seconds':2},{'pacing_seconds':float('nan')},{'lanes':4},
    {'serial_local_fraction':-1},{'request_cap':float('inf')},
])
def test_invalid_scenario_fails_closed(change):
    with pytest.raises(ValueError):replay_trace(jobs(),**change)


@pytest.mark.parametrize('change',[
    {'access_state':'NETWORK_ERROR'},{'access_state':'BLOCKED'},
    {'source_type':'DEALS'},{'network_seconds':float('nan')},{'local_seconds':-1},
])
def test_unknown_failed_or_foreign_trace_not_treated_as_healthy(change):
    rows=jobs();rows[0].update(change)
    with pytest.raises(ValueError):replay_trace(rows,lanes=3)


def test_duplicate_trace_requests_and_unapproved_source_rejected():
    rows=jobs();rows[1]['request_id']=rows[0]['request_id']
    with pytest.raises(ValueError):replay_trace(rows)
    with pytest.raises(ValueError):replay_trace(jobs(),source_order=['0','1'])


@pytest.mark.parametrize('coordinated', [False, True])
def test_real_sqlite_family_budget_precheck_needs_shared_admission_lock(tmp_path,coordinated):
    """No browser/fetch: reproduce the real check/reserve race in isolated stores.

    A dispatch lock fixes this one race; it does not make a parallel executor.
    The serial collector already has an exclusive whole-run lock.
    """
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import nullcontext
    from threading import Barrier
    from test_highspeed_v4_execution import setup_scope,NOW
    from amazon_es_bestseller.highspeed.store import RunStore
    from amazon_es_bestseller.highspeed.three_contract import family_budget_for_store
    from amazon_es_bestseller.multisource.storage import file_lock
    plan,approval=setup_scope(tmp_path)
    source=plan['sources'][0]
    plan['source_budgets']={source['source_type']:{'requests':1,'PAGE':1,'native':0}}
    store=RunStore(approval['store_root'])
    store.begin_run(plan,started_at=NOW.isoformat());store.close()
    barrier=Barrier(3)
    def contender(_):
        s=RunStore(approval['store_root'])
        try:
            if coordinated:barrier.wait(timeout=15)
            with file_lock(tmp_path/'admission.lock') if coordinated else nullcontext():
                reason=family_budget_for_store(s,plan,source,'PAGE')
                if not coordinated:barrier.wait(timeout=15)
                if reason:return False
                s.reserve_request(plan['run_id'],{'kind':'PAGE','url':source['actual_source_url'],
                    'source_id':source['source_id'],'source_type':source['source_type'],
                    'observed_at':NOW.isoformat(),'origin':'OFFLINE_TEST_NO_FETCH'})
                return True
        except (PermissionError,BlockingIOError):
            # Existing file_lock is deliberately nonblocking/fail-closed, not
            # a work queue. A real coordinator would also need bounded queuing.
            if coordinated:return False
            raise
        finally:s.close()
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(contender,range(3)))
    s=RunStore(approval['store_root'])
    try:
        assert s.budget_counters(plan['run_id'])['requests']==(1 if coordinated else 3)
        assert sum(results)==(1 if coordinated else 3)
    finally:s.close()
