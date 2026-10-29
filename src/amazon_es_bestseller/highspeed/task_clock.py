"""Immutable task-wide clock and evidence-based final-QA request guard."""
from pathlib import Path
from datetime import timedelta
import hashlib,math,json
from ..multisource.storage import immutable_json,read_sealed,sealed,digest
from .budgets import aware_time,utc_text


def _task_hours(plan):
    policy=plan.get('target_policy') or {}
    from .global_target import SNAPSHOT_PROFILES,CUSTOM_PROFILE
    if policy.get('profile') in {p['profile'] for p in SNAPSHOT_PROFILES.values()} | {CUSTOM_PROFILE}:
        from .global_target import is_global_policy
        is_global_policy(policy)
        return policy['total_task_hours']
    return 10


def start_task_clock(path,plan,commit,*,now=None):
    path=Path(path);now=aware_time(now)
    hours=_task_hours(plan)
    if path.exists():
        value=read_sealed(path)
        if value['run_id']!=plan['run_id'] or value['plan_sha256']!=digest(plan):
            raise ValueError('TASK_CLOCK_BINDING_MISMATCH')
    else:
        value={'schema_version':1,'run_id':plan['run_id'],'plan_sha256':digest(plan),
            'initial_git_commit':commit,'started_at':utc_text(now),
            'deadline':utc_text(now+timedelta(hours=hours)),'total_hours':hours}
        immutable_json(path,sealed(value))
    return {'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def validate_task_clock(plan,authorization,*,now=None):
    hours=_task_hours(plan)
    ref=authorization.get('task_clock_reference') or {};path=Path(ref.get('path','__missing__'))
    if (not path.is_file() or str(path.resolve())!=plan.get('task_clock_path')
            or hashlib.sha256(path.read_bytes()).hexdigest()!=ref.get('sha256')):
        raise ValueError('EXACT_IMMUTABLE_TASK_CLOCK_REQUIRED')
    value=read_sealed(path)
    start=aware_time(value['started_at']);end=aware_time(value['deadline'])
    if (value['run_id']!=plan['run_id'] or value['plan_sha256']!=digest(plan)
            or value.get('total_hours')!=hours or (end-start).total_seconds()!=hours*3600):
        raise ValueError('TASK_CLOCK_BINDING_MISMATCH')
    extension=time_extension(plan,authorization,now=now)
    if extension:
        value={**value,'original_deadline':value['deadline'],'deadline':extension['deadline'],'request_deadline':extension['request_deadline'],'time_extension_reference':authorization['time_extension_reference']}
        end=aware_time(extension['deadline'])
    if not start<=aware_time(now)<end:raise ValueError('TASK_TEN_HOUR_DEADLINE_OR_START_VIOLATION')
    return value


def time_extension(plan,authorization,*,now=None):
    """Explicit time-only amendment. Original plan/clock/budgets stay immutable."""
    ref=authorization.get('time_extension_reference')
    if not ref:return None
    path=Path(ref['path'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=ref['sha256']:raise ValueError('TIME_EXTENSION_SHA_MISMATCH')
    e=json.loads(path.read_text('utf8'));current=aware_time(now)
    if (not plan.get('target_policy') or e.get('kind')!='EXPLICIT_SAME_RUN_TIME_EXTENSION'
            or e.get('approved') is not True or not e.get('approved_by') or not e.get('approval_id')
            or e.get('run_id')!=plan['run_id'] or e.get('plan_sha256')!=digest(plan)
            or e.get('store_root')!=authorization.get('store_root')
            or e.get('original_clock_reference')!=authorization.get('task_clock_reference')
            or e.get('preserve_cumulative_budgets') is not True or e.get('preserve_started_at') is not True):
        raise ValueError('EXACT_EXPLICIT_TIME_EXTENSION_REQUIRED')
    begin=aware_time(e['reviewed_at']);request_end=aware_time(e['request_deadline']);end=aware_time(e['deadline'])
    if (not begin<request_end<end or not 0<(end-begin).total_seconds()<=43200
            or not begin<=current<end or authorization.get('expires_at')!=e['deadline']):
        raise ValueError('TIME_EXTENSION_DEADLINE_VIOLATION')
    return e


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
