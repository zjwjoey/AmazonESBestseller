from datetime import timedelta
from copy import deepcopy
from pathlib import Path
import pytest
from test_highspeed_v4_execution import NOW
from amazon_es_bestseller.highspeed.task_clock import start_task_clock,validate_task_clock,qa_reserve_seconds,request_stop_reason
from amazon_es_bestseller.multisource.storage import digest


def test_clock_cannot_reset_on_restart_and_binds_exact_plan(tmp_path):
    path=tmp_path/'clock.json';p={'run_id':'TEST_ONLY','task_clock_path':str(path.resolve())}
    ref=start_task_clock(path,p,'TEST_ONLY_SHA',now=NOW)
    assert start_task_clock(path,p,'TEST_ONLY_NEW_SHA',now=NOW+timedelta(hours=2))==ref
    a={'task_clock_reference':ref}
    assert validate_task_clock(p,a,now=NOW)['deadline'].startswith('2026-10-15T01:')
    with pytest.raises(ValueError,match='DEADLINE'):validate_task_clock(p,a,now=NOW+timedelta(hours=10))
    bad=deepcopy(p);bad['run_id']='OTHER'
    with pytest.raises(ValueError,match='BINDING'):validate_task_clock(bad,a,now=NOW)


def test_qa_reserve_scales_with_actual_raw_volume_and_never_disappears():
    p={'qa_reserve_policy':{'seconds_per_raw':.34,'safety_factor':1.2,'fixed_seconds':900,'minimum_seconds':3600}}
    assert qa_reserve_seconds(p,0)==3600
    assert qa_reserve_seconds(p,30000)==13140
    for value in (-1,float('nan'),True):
        bad=deepcopy(p);bad['qa_reserve_policy']['seconds_per_raw']=value
        with pytest.raises(ValueError):qa_reserve_seconds(bad,1000)


def test_live_request_guard_reserves_qa_without_requesting_network(tmp_path):
    from types import SimpleNamespace
    p={'run_id':'TEST_ONLY','target_policy':{'test':True},'task_clock_path':str((tmp_path/'clock.json').resolve()),
       'qa_reserve_policy':{'seconds_per_raw':.34,'safety_factor':1.2,'fixed_seconds':900,'minimum_seconds':3600}}
    ref=start_task_clock(p['task_clock_path'],p,'TEST_ONLY',now=NOW)
    class DB:
        def execute(self,*a):return SimpleNamespace(fetchone=lambda:(30000,))
    store=SimpleNamespace(db=DB());a={'task_clock_reference':ref}
    assert request_stop_reason(p,a,store,now=NOW) is None
    assert request_stop_reason(p,a,store,now=NOW+timedelta(hours=7))=='TASK_FINAL_QA_RESERVE_REACHED'
