"""Offline restart and monitoring scenarios; no live transport."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amazon_es_bestseller.highspeed.page_snapshot import CurrentPageSnapshotRunner, CONTRACT
from amazon_es_bestseller.highspeed.global_target import MAIN_PRIORITY_POLICY
from amazon_es_bestseller.multisource.storage import digest
from test_highspeed_v4_execution import NOW


def test_already_complete_restart_stops_before_source_selection(monkeypatch):
    import amazon_es_bestseller.highspeed.page_snapshot as module
    calls=[]
    monkeypatch.setattr(module, 'measure_three', lambda *a, **k: calls.append('count') or {'target_met': True})
    runner=CurrentPageSnapshotRunner.__new__(CurrentPageSnapshotRunner)
    runner._snapshot_metrics=None
    runner.store=SimpleNamespace(iter_observations=lambda _: [])
    plan={'run_id':'TEST_ONLY','target_policy':deepcopy(MAIN_PRIORITY_POLICY)}
    # V4 checks this BEFORE should_collect_source or any request.
    assert runner.target_stop_reason(plan)=='SNAPSHOT_20000_TARGET_REACHED'
    assert runner.target_stop_reason(plan)=='SNAPSHOT_20000_TARGET_REACHED'
    assert calls==['count']


def test_restart_invalidates_previous_pass_in_database_and_progress(tmp_path):
    state={'collection_contract':CONTRACT,'status':'STOPPED',
           'snapshot_review':{'status':'PASSED','passed':True},
           'snapshot_acceptance':'ACCEPTED_SNAPSHOT_CANDIDATES_QA_PASSED',
           'quality_gate_passed':True}
    class Store:
        root=tmp_path
        def get_run(self,_):return deepcopy(state)
        def iter_observations(self,_):return iter(())
        def pages(self,_):return []
        def update_run(self,_,**fields):state.update(fields);return deepcopy(state)
    (tmp_path/'snapshot_progress.json').write_text(json.dumps({'run_id':'TEST_ONLY',**state}),encoding='utf8')
    runner=CurrentPageSnapshotRunner.__new__(CurrentPageSnapshotRunner)
    runner.store=Store();runner._snapshot_metrics=None
    runner.mark_run_running({'run_id':'TEST_ONLY','target_policy':deepcopy(MAIN_PRIORITY_POLICY)})
    progress=json.loads((tmp_path/'snapshot_progress.json').read_text('utf8'))
    for value in (state,progress):
        assert value['snapshot_review']['status']=='PENDING'
        assert value['snapshot_review']['passed'] is False
        assert value['quality_gate_passed'] is False
        assert value['snapshot_acceptance']=='NOT_ACCEPTED_SNAPSHOT_QA_PENDING'
        assert value['production_acceptance']=='NOT_EVALUATED'
    assert state['status']=='RUNNING'


def test_page_only_monitor_uses_reviewed_target_clock_and_normal_completion(tmp_path,monkeypatch):
    from test_current_page_snapshot import make_probe
    from amazon_es_bestseller.highspeed import task_monitor as module
    store,plan,_=make_probe(tmp_path)
    try:
        plan=deepcopy(plan)
        plan.update(collection_contract=CONTRACT,target_policy=deepcopy(MAIN_PRIORITY_POLICY),
            source_budgets={k:{'native':0} for k in MAIN_PRIORITY_POLICY['source_minima']},
            qa_reserve_policy={'seconds_per_raw':.01,'safety_factor':2,'fixed_seconds':900,'minimum_seconds':3600})
        # Simulate the monitor clock using only the temporary fixture index.
        requests=store.db.execute('select request_id,data_json from requests order by observed_at').fetchall()
        for i,(rid,data) in enumerate(requests):
            row=json.loads(data);row['observed_at']=(NOW+timedelta(minutes=i*5)).isoformat()
            store.db.execute('update requests set data_json=? where request_id=?',(json.dumps(row),rid))
        store.update_run(plan['run_id'],status='RUNNING')
        for path in (store.root/'access_gate.json',Path(plan['shared_access_gate_path'])):
            path.write_text(json.dumps({'state':'NORMAL'}),encoding='utf8')
        monkeypatch.setattr(module,'process_evidence',lambda _: {'verified':True,'status':'TEST_ONLY'})
        start=NOW-timedelta(minutes=1)
        clock={'run_id':plan['run_id'],'plan_sha256':digest(plan),'started_at':start.isoformat(),
               'deadline':(start+timedelta(hours=24)).isoformat()}
        result=module.snapshot(store.root,plan,clock,now=NOW+timedelta(minutes=11))
        assert result['native']==0
        assert result['initial_ten_minute_observation_passed'] is True
        assert result['quantity_goals']['GLOBAL']['expected_by_now']==pytest.approx(20000*720/(23*3600))
        assert result['linear_remaining_capture_seconds']==pytest.approx(
            (20000-result['global_groups'])*3600/result['window_new_groups_per_hour'])
        store.update_run(plan['run_id'],status='STOPPED')
        monkeypatch.setattr(module,'process_evidence',lambda _: {'verified':False,'status':'PROCESS_EXITED'})
        completed=module.snapshot(store.root,plan,clock,now=NOW+timedelta(hours=2))
        assert 'COLLECTOR_PROCESS_NOT_VERIFIED' not in completed['alerts']
        assert 'NO_REQUEST_PROGRESS_FOR_5_MINUTES_CHECK_PHASE' not in completed['alerts']
    finally:store.close()
