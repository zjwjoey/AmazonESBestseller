"""Read-only run-bound local monitor; never starts or retries a collector."""
import argparse,json,sqlite3,time,os,subprocess,shutil
from pathlib import Path
from collections import Counter
from .budgets import aware_time,utc_text
from .three_metrics import measure_three,qualify_three
from .v4_quality import build_candidates_v4
from .task_clock import qa_reserve_seconds
from ..multisource.storage import read_sealed,digest,file_lock,atomic_json


def process_evidence(plan):
    record_path=Path(plan['task_clock_path']).parent/'worker_process.json'
    if not record_path.is_file():return {'verified':False,'status':'NOT_DEPLOYED'}
    record=json.loads(record_path.read_text('utf-8-sig'));pid=record.get('ProcessId')
    if type(pid) is not int or pid<=0:return {'verified':False,'status':'INVALID_PROCESS_RECORD'}
    if os.name!='nt':return {'verified':False,'status':'WINDOWS_PROCESS_INSPECTION_UNAVAILABLE'}
    command=("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
             "Get-CimInstance Win32_Process -Filter 'ProcessId="+str(pid)+"' | "
             "Select-Object ProcessId,@{Name='CreationDate';Expression={$_.CreationDate.ToUniversalTime().ToString('o')}},CommandLine,KernelModeTime,UserModeTime,WorkingSetSize,ReadTransferCount,WriteTransferCount | ConvertTo-Json -Compress")
    result=subprocess.run(['powershell','-NoProfile','-Command',command],capture_output=True,text=True,
        encoding='utf8',errors='replace',timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or not result.stdout.strip():return {'verified':False,'status':'PROCESS_EXITED_OR_UNAVAILABLE'}
    actual=json.loads(result.stdout)
    matching=(actual.get('ProcessId')==pid and actual.get('CommandLine') and plan['run_id'] in actual['CommandLine'] and
              abs((aware_time(actual['CreationDate'])-aware_time(record['CreationDate'])).total_seconds())<1)
    return {'verified':bool(matching),'status':'MATCHED' if matching else 'PID_REUSED_OR_COMMAND_MISMATCH',
            'actual':actual}


def observation_times(clock,events,requests):
    stops=[aware_time(e['observed_at']) for e in events if e.get('kind')=='STOP' and e.get('observed_at')]
    epoch=max(stops) if stops else aware_time(clock['started_at'])
    times=[aware_time(q['observed_at']) for q in requests if aware_time(q['observed_at'])>epoch or not stops]
    return epoch,times


def _run_token(root,run_id):
    path=Path(root)/'index.sqlite3'
    if not path.is_file():return None
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    try:
        row=db.execute('SELECT status FROM runs WHERE run_id=?',(run_id,)).fetchone()
        stop=db.execute("SELECT max(event_id) FROM events WHERE run_id=? AND json_extract(data_json,'$.kind')='STOP'",(run_id,)).fetchone()
        return (row[0] if row else None,stop[0])
    finally:db.close()


def wait_for_next_sample(root,run_id,seconds):
    # Lightweight status/STOP checks notice resume within30s even during the
    # hourly interval; a new capture epoch receives a fresh10-minute observation.
    token=_run_token(root,run_id);end=time.monotonic()+seconds
    while time.monotonic()<end:
        time.sleep(min(30,max(0,end-time.monotonic())))
        if _run_token(root,run_id)!=token:return


def snapshot(root,plan,clock,*,now=None,window_start=None):
    now=aware_time(now);start=aware_time(window_start or clock['started_at'])
    if clock['run_id']!=plan['run_id'] or clock['plan_sha256']!=digest(plan):raise ValueError('MONITOR_EXACT_RUN_PLAN_REQUIRED')
    db=sqlite3.connect((Path(root)/'index.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    try:
        rows=[json.loads(x[0]) for x in db.execute('SELECT data_json FROM observations WHERE run_id=?',(plan['run_id'],))]
        requests=[{**json.loads(r[0]),'status':r[1]} for r in db.execute('SELECT data_json,status FROM requests WHERE run_id=?',(plan['run_id'],))]
        pages=[json.loads(x[0]) for x in db.execute('SELECT data_json FROM pages WHERE run_id=?',(plan['run_id'],))]
        run=db.execute('SELECT metadata_json,status FROM runs WHERE run_id=?',(plan['run_id'],)).fetchone()
        events=[json.loads(r[0]) for r in db.execute("SELECT data_json FROM events WHERE run_id=? AND json_extract(data_json,'$.kind')='STOP'",(plan['run_id'],))]
    finally:db.close()
    metrics=measure_three(rows,plan['run_id'],target_policy=plan['target_policy'])
    _,qualified,_,_=qualify_three(rows,plan['run_id'])
    first={};own={k:{} for k in metrics['sources']}
    for c in build_candidates_v4(qualified):
        if not c['eligible_for_target']:continue
        key=c['target_review_group'];t=aware_time(c['first_observed_at'])
        first[key]=min(first.get(key,t),t)
        for m in c['source_memberships']:
            kind=m['source_type'];t=aware_time(m['observed_at'])
            own[kind][key]=min(own[kind].get(key,t),t)
    new=lambda values:sum(start<t<=now for t in values.values())
    elapsed=max(0,(now-aware_time(clock['started_at'])).total_seconds())
    reserve=qa_reserve_seconds(plan,len(rows));capture_window=max(1,36000-reserve)
    fraction=min(1,elapsed/capture_window)
    goals=metrics['quantity_goals'];progress={}
    for k,g in goals.items():
        expected=g['target']*fraction
        progress[k]={**g,'expected_by_now':expected,'behind_fraction':max(0,1-g['actual']/expected) if expected else 0,
                     'below_plan_20_percent':bool(expected and g['actual']<expected*.8)}
    gate_values=[]
    for path in [Path(root)/'access_gate.json',Path(plan['shared_access_gate_path'])]:
        if not path.is_file():gate_values.append('UNKNOWN');continue
        value=json.loads(path.read_text('utf8'));gate_values.append(value.get('payload',value).get('state','UNKNOWN'))
    request_times=[aware_time(q['observed_at']) for q in requests]
    observation_epoch,active_times=observation_times(clock,events,requests)
    active_new=new(first);seconds=(now-start).total_seconds();rate=active_new*3600/seconds if seconds>0 else None
    by_family={}
    for k,m in metrics['sources'].items():
        qs=[q for q in requests if q['source_type']==k];ps=[p for p in pages if p['source_type']==k]
        by_family[k]={**m,'hour_new_capture_groups':new(own[k]),'visited_sources':len({p['source_id'] for p in ps}),
            'visited_PAGEs':len({p['page_url'] for p in ps}),'requests':len(qs),
            'native_requests':sum(q['kind']=='ACP' for q in qs),
            'remaining_contexts':len({s['source_id'] for s in plan['sources'] if s['source_type']==k}-{p['source_id'] for p in ps}),
            'incomplete_pages':[p['page_url'] for p in ps if p.get('execution_status')!='APPROVED_SOURCE_PAGE_SCOPE_EXECUTED']}
    process=process_evidence(plan)
    alerts=[]
    if not process['verified']:alerts.append('COLLECTOR_PROCESS_NOT_VERIFIED')
    if any(g!='NORMAL' for g in gate_values):alerts.append('ACCESS_GATE_NOT_NORMAL_NO_REQUESTS_ALLOWED')
    if any(p['below_plan_20_percent'] for p in progress.values()):alerts.append('QUANTITY_GOAL_BEHIND_20_PERCENT')
    if request_times and (now-max(request_times)).total_seconds()>300 and not metrics['target_met']:alerts.append('NO_REQUEST_PROGRESS_FOR_5_MINUTES_CHECK_PHASE')
    state={**json.loads(run[0]),'status':run[1]} if run else {}
    return {'run_id':plan['run_id'],'plan_sha256':digest(plan),'observed_at':utc_text(now),'window_start':utc_text(start),
        'run_status':state.get('status'),'stop_reason':state.get('stop_reason'),
        'process_evidence':process,'disk_free_bytes':shutil.disk_usage(Path(root)).free,
        'quantity_goals':progress,'sources':by_family,'global_groups':metrics['F_global_conservative_review_groups'],
        'raw_observations':len(rows),'hour_new_capture_groups':active_new,'hour_requests':sum(start<aware_time(q['observed_at'])<=now for q in requests),
        'requests':len(requests),'PAGE':sum(q['kind']=='PAGE' for q in requests),'native':sum(q['kind']=='ACP' for q in requests),
        'PAGE_budget_remaining':max(0,plan['max_pages_per_run']-sum(q['kind']=='PAGE' for q in requests)),
        'native_budget_remaining':max(0,sum(v['native'] for v in plan['source_budgets'].values())-sum(q['kind']=='ACP' for q in requests)),
        'request_budget_remaining':max(0,plan['max_requests_per_run']-len(requests)),
        'last_request_at':utc_text(max(request_times)) if request_times else None,'gates':gate_values,
        'first_live_request_at':utc_text(min(request_times)) if request_times else None,
        'observation_epoch_after_last_STOP':utc_text(observation_epoch),
        'live_observation_elapsed_seconds':(now-min(active_times)).total_seconds() if active_times else None,
        'initial_ten_minute_observation_passed':bool(active_times and (now-min(active_times)).total_seconds()>=600
            and (now-max(request_times)).total_seconds()<120 and process['verified']
            and state.get('status')=='RUNNING' and all(g=='NORMAL' for g in gate_values)
            and any(t>observation_epoch for t in first.values())
            and len({q['url'] for q in requests if q['kind']=='PAGE' and q['status']=='NORMAL' and aware_time(q['observed_at'])>observation_epoch})>=2
            and any(q['kind']=='ACP' and q['status']=='NORMAL' and aware_time(q['observed_at'])>observation_epoch for q in requests)),
        'task_elapsed_wall_seconds':elapsed,'remaining_wall_seconds':max(0,(aware_time(clock['deadline'])-now).total_seconds()),
        'estimated_QA_seconds':reserve,'window_new_groups_per_hour':rate,
        'linear_remaining_capture_seconds':max(0,30000-len(first))*3600/rate if rate else None,
        'rate_scope':'Fresh capture time only; old/reparsed rows never add hourly growth. Wall clock includes all pauses.',
        'alerts':alerts,'collector_PID_is_not_health_proof':True,'network_actions':0}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('root','plan','clock','out'):p.add_argument('--'+name,required=True)
    p.add_argument('--authorization');p.add_argument('--once',action='store_true');args=p.parse_args(argv)
    plan=json.loads(Path(args.plan).read_text('utf8'));clock=read_sealed(args.clock)
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    # Exactly one monitor for this run even after app/session restart.
    if Path(args.clock).resolve()!=Path(plan['task_clock_path']).resolve():raise ValueError('MONITOR_EXACT_CLOCK_PATH_REQUIRED')
    with file_lock(Path(args.clock).resolve().parent/(plan['run_id']+'.monitor.lock')):
        previous=out/'latest.json';start=json.loads(previous.read_text('utf8'))['observed_at'] if previous.exists() else clock['started_at']
        while True:
            if args.authorization:
                from .task_clock import validate_task_clock
                approval=json.loads(Path(args.authorization).read_text('utf8'))
                clock=validate_task_clock(plan,approval,now=approval['reviewed_at'])
            value=snapshot(args.root,plan,clock,window_start=start)
            atomic_json(previous,value)
            with (out/'snapshots.jsonl').open('a',encoding='utf8') as f:f.write(json.dumps(value,ensure_ascii=False)+'\n')
            if args.once or value['remaining_wall_seconds']<=0:break
            # First10 minutes are observed every30s; then once per hour.
            observed=value['live_observation_elapsed_seconds']
            delay=3600 if value['run_status']=='STOPPED' or any(g!='NORMAL' for g in value['gates']) else (30 if observed is None or observed<600 else 3600)
            start=value['observed_at'];wait_for_next_sample(args.root,plan['run_id'],max(1,min(delay,value['remaining_wall_seconds'])))
    return value

if __name__=='__main__':main()
