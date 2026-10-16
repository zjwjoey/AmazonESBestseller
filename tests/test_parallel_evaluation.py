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
