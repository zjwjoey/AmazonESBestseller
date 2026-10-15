"""Immutable task-wide clock and evidence-based final-QA request guard."""
from pathlib import Path
from datetime import timedelta
import hashlib,math
from ..multisource.storage import immutable_json,read_sealed,sealed,digest
from .budgets import aware_time,utc_text


def start_task_clock(path,plan,commit,*,now=None):
    path=Path(path);now=aware_time(now)
    if path.exists():
        value=read_sealed(path)
        if value['run_id']!=plan['run_id'] or value['plan_sha256']!=digest(plan):
            raise ValueError('TASK_CLOCK_BINDING_MISMATCH')
    else:
        value={'schema_version':1,'run_id':plan['run_id'],'plan_sha256':digest(plan),
            'initial_git_commit':commit,'started_at':utc_text(now),
            'deadline':utc_text(now+timedelta(hours=10)),'total_hours':10}
        immutable_json(path,sealed(value))
    return {'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def validate_task_clock(plan,authorization,*,now=None):
    ref=authorization.get('task_clock_reference') or {};path=Path(ref.get('path','__missing__'))
    if (not path.is_file() or str(path.resolve())!=plan.get('task_clock_path')
            or hashlib.sha256(path.read_bytes()).hexdigest()!=ref.get('sha256')):
        raise ValueError('EXACT_IMMUTABLE_TASK_CLOCK_REQUIRED')
    value=read_sealed(path)
    start=aware_time(value['started_at']);end=aware_time(value['deadline'])
    if (value['run_id']!=plan['run_id'] or value['plan_sha256']!=digest(plan)
            or value.get('total_hours')!=10 or (end-start).total_seconds()!=36000):
        raise ValueError('TASK_CLOCK_BINDING_MISMATCH')
    if not start<=aware_time(now)<end:raise ValueError('TASK_TEN_HOUR_DEADLINE_OR_START_VIOLATION')
    return value


def qa_reserve_seconds(plan,raw_count):
    policy=plan.get('qa_reserve_policy') or {}
    values=[policy.get(k) for k in ('seconds_per_raw','safety_factor','fixed_seconds','minimum_seconds')]
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in values):
        raise ValueError('FINITE_MEASURED_QA_RESERVE_REQUIRED')
    rate,safety,fixed,minimum=values
    if rate<=0 or safety<1 or fixed<0 or minimum<3600:raise ValueError('CONSERVATIVE_QA_RESERVE_REQUIRED')
    return max(minimum,rate*raw_count*safety+fixed)


def request_stop_reason(plan,authorization,store,*,now=None):
    if not plan.get('target_policy'):return None
    clock=validate_task_clock(plan,authorization,now=now)
    raw=store.db.execute('SELECT count(*) FROM observations WHERE run_id=?',(plan['run_id'],)).fetchone()[0]
    if (aware_time(clock['deadline'])-aware_time(now)).total_seconds()<=qa_reserve_seconds(plan,raw):
        return 'TASK_FINAL_QA_RESERVE_REACHED'
    return None
