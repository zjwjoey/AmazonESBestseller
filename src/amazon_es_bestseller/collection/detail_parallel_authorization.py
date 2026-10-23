"""Fail-closed, run-bound opt-in for the new V2 five-lane entry point."""
from __future__ import annotations

from datetime import datetime,timezone
import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit

from ..access.detector import AccessStopError
from ..identity import asin_from_url
from ..multisource.storage import digest,read_sealed,atomic_json,sealed


def checked_document(reference):
    path=Path(reference['path'])
    if digest(path.read_bytes())!=reference['sha256']:raise ValueError('AUTHORIZATION_REFERENCE_CHANGED')
    value=json.loads(path.read_text(encoding='utf8'))
    return read_sealed(path) if 'payload_sha256' in value else value


def instant(value):
    parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if parsed.tzinfo is None:raise ValueError('TIMEZONE_AWARE_ORIGINAL_CLOCK_REQUIRED')
    return parsed


def validate_approval(approval,plan,root,profile,code_sha,*,now=None):
    now=now or datetime.now(timezone.utc)
    if (approval.get('approved') is not True or not approval.get('approved_by')
            or not approval.get('explicit_user_instruction')
            or approval.get('scope')!='FIVE_LANES_GROUPED_3_PLUS_2'
            or approval.get('parser_contract')!=2 or approval.get('lanes')!=5
            or approval.get('processing_groups')!=[3,2]
            or approval.get('max_uncommitted_tasks')!=5
            or approval.get('plan_sha256')!=digest(plan)
            or approval.get('code_sha')!=code_sha
            or approval.get('run_id')!=plan.get('run_id')
            or Path(approval['runtime_root']).resolve()!=Path(root).resolve()
            or Path(approval['browser_profile']).resolve()!=Path(profile).resolve()):
        raise ValueError('EXPLICIT_CURRENT_FIVE_LANE_APPROVAL_REQUIRED')
    base=checked_document(approval['base_authorization_reference'])
    if (Path(base['runtime_root']).resolve()!=Path(root).resolve()
            or base.get('plan_sha256')!=digest(plan)):
        raise ValueError('ORIGINAL_ROOT_AND_FROZEN_PLAN_REQUIRED')
    if Path(base['browser_profile']).resolve()!=Path(profile).resolve():
        raise ValueError('ORIGINAL_BROWSER_PROFILE_REQUIRED')
    for key in ('run_id','starts_at','deadline','request_deadline','max_requests',
                'max_page_requests','max_other_requests','max_attempts_per_task','required_postal_code'):
        if approval.get(key)!=base.get(key):raise ValueError('ORIGINAL_CONTROL_CHANGED:'+key)
    if base.get('run_id')!=plan['run_id'] or base.get('max_attempts_per_task')!=1:
        raise ValueError('SAME_RUN_SINGLE_ATTEMPT_REQUIRED')
    if not re.fullmatch(r'\d{5}',str(base.get('required_postal_code') or '')):
        raise ValueError('ORIGINAL_DELIVERY_POSTAL_EVIDENCE_REQUIRED')
    for key in ('max_requests','max_page_requests','max_other_requests'):
        if type(base.get(key)) is not int or base[key]<(0 if key=='max_other_requests' else 1):
            raise ValueError('INVALID_ORIGINAL_BUDGET')
    interval=approval.get('request_start_interval_seconds')
    if (not isinstance(interval,(int,float)) or isinstance(interval,bool)
            or not math.isfinite(interval) or interval<3.6
            or interval<base.get('request_start_interval_seconds',3.6)):
        raise ValueError('GLOBAL_PACING_CANNOT_BE_REDUCED')
    if not instant(base['starts_at'])<=now<min(instant(base['deadline']),instant(base['request_deadline'])):
        raise ValueError('ORIGINAL_REQUEST_WINDOW_CLOSED')
    ledger=checked_document(approval['cumulative_budget_reference'])
    if ledger.get('run_id')!=plan['run_id'] or ledger.get('base_authorization_sha256')!=approval['base_authorization_reference']['sha256']:
        raise ValueError('ORIGINAL_BUDGET_LEDGER_REQUIRED')
    if type(ledger.get('paid_requests')) is not int or type(ledger.get('budget_holds')) is not int:
        raise ValueError('CUMULATIVE_PAID_AND_HOLDS_REQUIRED')
    maximum=min(base['max_requests'],base['max_page_requests'])
    if (min(ledger['paid_requests'],ledger['budget_holds'])<0
            or ledger['paid_requests']+ledger['budget_holds']>maximum):
        raise ValueError('INVALID_CUMULATIVE_LEDGER')
    original_gates=[Path(path).resolve() for path in base.get('access_gate_paths',[])]
    current_gates=[Path(path).resolve() for path in approval.get('access_gate_paths',[])]
    if len(set(original_gates))!=2 or set(current_gates)!=set(original_gates) or len(current_gates)!=2:
        raise ValueError('TWO_ORIGINAL_SHARED_GATES_REQUIRED')
    ids=[task['task_id'] for task in plan['tasks']]
    asins=[task['asin'] for task in plan['tasks']]
    if len(ids)!=len(set(ids)) or len(asins)!=len(set(asins)):
        raise ValueError('DUPLICATE_FROZEN_TASK_OR_PRODUCT')
    for task in plan['tasks']:
        parsed=urlsplit(task['request_url'])
        if (parsed.scheme!='https' or parsed.hostname not in ('amazon.es','www.amazon.es')
                or parsed.username or parsed.password or asin_from_url(task['request_url'])!=task['asin']):
            raise ValueError('EXACT_AMAZON_ES_PRODUCT_SCOPE_REQUIRED')
    return {'max_requests':min(base['max_requests'],base['max_page_requests']),
            'prior_requests':ledger['paid_requests'],'budget_holds':ledger['budget_holds']}


class SharedDetailGates:
    def __init__(self,paths):self.paths=[Path(path) for path in paths]

    def require_normal(self):
        for path in self.paths:
            state=read_sealed(path).get('state')
            if state!='NORMAL':raise AccessStopError('SHARED_GATE_'+str(state))

    def stop(self,state):
        if state=='NORMAL':raise ValueError('AUTOMATIC_ACCESS_RECOVERY_FORBIDDEN')
        for path in self.paths:
            previous=read_sealed(path)
            if previous.get('state')!='NORMAL':continue
            atomic_json(path,sealed({'state':state,'previous_gate':previous,
                        'origin':'V2_PARALLEL_DETAIL_CAPTURE',
                        'at':datetime.now(timezone.utc).isoformat()}))
