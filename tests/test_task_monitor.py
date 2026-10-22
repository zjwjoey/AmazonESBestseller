from pathlib import Path
from datetime import timedelta
import json
from test_three_source_runner import execution_scope,Advancing
from test_highspeed_v4_execution import NOW
from test_global_30000_contract import POLICY,bind_clock
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.three_runner import V4ThreeSourceRunner
from amazon_es_bestseller.highspeed.task_monitor import snapshot
from amazon_es_bestseller.multisource.storage import read_sealed
from copy import deepcopy


def test_monitor_reads_real_saved_fixture_progress_without_network_and_excludes_reparse_growth(tmp_path,monkeypatch):
    p,a=execution_scope(tmp_path,monkeypatch)
    p.update(target_policy=deepcopy(POLICY),target_unique_links=30000)
    a.update(target_policy=deepcopy(POLICY),target_unique_links=30000)
    bind_clock(p,a,tmp_path)
    store=RunStore(a['store_root']);clock=[NOW]
    try:
        transport=Advancing(p,a,clock)
        V4ThreeSourceRunner(store,clock=lambda:clock[0],sleep=lambda n:None).run(p,authorization=a,transport=transport)
        calls=len(transport.calls)
        # Explicit TEST_ONLY state files; monitor itself never writes gates.
        for path in [store.root/'access_gate.json',Path(p['shared_access_gate_path'])]:
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps({'state':'NORMAL'}),'utf8')
        result=snapshot(store.root,p,read_sealed(p['task_clock_path']),now=NOW+timedelta(hours=1),window_start=NOW+timedelta(minutes=10))
        assert len(transport.calls)==calls and result['network_actions']==0
        assert result['hour_new_capture_groups']==0 and result['global_groups']>0
        assert result['hour_requests']==0
        assert set(result['quantity_goals'])=={'BEST_SELLERS','NEW_RELEASES','CUSTOMER_FAVORITES','GLOBAL'}
        assert result['task_elapsed_wall_seconds']==3600
        assert result['alerts'] and result['sources']['CUSTOMER_FAVORITES']['E_qualified_review_groups']>0
    finally:store.close()


def test_one_run_monitor_lock_cannot_be_bypassed_by_another_output_directory(tmp_path,monkeypatch):
    import threading,pytest
    from amazon_es_bestseller.highspeed import task_monitor as monitor
    from amazon_es_bestseller.highspeed.task_clock import start_task_clock
    path=tmp_path/'clock.json';plan={'run_id':'TEST_ONLY','task_clock_path':str(path.resolve())}
    start_task_clock(path,plan,'TEST_ONLY',now=NOW)
    pp=tmp_path/'plan.json';pp.write_text(json.dumps(plan),'utf8')
    entered=threading.Event();release=threading.Event();errors=[]
    def fake(*a,**k):
        entered.set();assert release.wait(5)
        return {'observed_at':NOW.isoformat(),'remaining_wall_seconds':0}
    monkeypatch.setattr(monitor,'snapshot',fake)
    args=['--root',str(tmp_path),'--plan',str(pp),'--clock',str(path),'--out',str(tmp_path/'one'),'--once']
    def first():
        try:monitor.main(args)
        except BaseException as e:errors.append(e)
    t=threading.Thread(target=first);t.start();assert entered.wait(5)
    try:
        with pytest.raises(OSError):monitor.main([*args[:-3],'--out',str(tmp_path/'two'),'--once'])
    finally:release.set();t.join(5)
    assert not errors and not t.is_alive()


def test_restart_requires_new_ten_minute_capture_observation_without_resetting_task_clock():
    from amazon_es_bestseller.highspeed.task_monitor import observation_times
    clock={'started_at':NOW.isoformat()}
    events=[{'kind':'STOP','observed_at':(NOW+timedelta(minutes=20)).isoformat()}]
    requests=[{'observed_at':(NOW+timedelta(minutes=1)).isoformat()},
              {'observed_at':(NOW+timedelta(minutes=25)).isoformat()}]
    epoch,times=observation_times(clock,events,requests)
    assert epoch==NOW+timedelta(minutes=20) and times==[NOW+timedelta(minutes=25)]
    assert (NOW+timedelta(minutes=26)-min(times)).total_seconds()==60
    assert clock['started_at']==NOW.isoformat()
    assert observation_times(clock,events,requests[:1])[1]==[]
