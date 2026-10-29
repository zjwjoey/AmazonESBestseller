"""Validate a parallel-execution amendment without changing the original plan.

Isolated development only; this is not connected to the production CLI.
"""
from pathlib import Path
import hashlib
from .budgets import aware_time
from ..multisource.storage import digest,read_sealed


def validate_parallel_amendment(plan, original_auth, amendment, *, now, gate, code_manifest):
    """Fail closed on plan, clock, budget, source or worker-scope drift."""
    if gate.get('state')!='NORMAL':raise ValueError('GLOBAL_ACCESS_GATE_BLOCKED')
    from .v4_authorization import validate_v4_authorization
    validate_v4_authorization(plan,original_auth,gate,now=now,store_root=original_auth['store_root'])
    if not isinstance(amendment,dict) or amendment.get('kind')!='PAGE_PARALLEL_AMENDMENT_V1':
        raise ValueError('PARALLEL_AMENDMENT_REQUIRED')
    if (amendment.get('approved') is not True or not amendment.get('approved_by') or
        not amendment.get('approval_id') or not amendment.get('authorization_text')):
        raise ValueError('EXPLICIT_USER_AMENDMENT_REQUIRED')
    if (amendment.get('run_id')!=plan['run_id'] or amendment.get('original_plan_sha256')!=digest(plan) or
        amendment.get('original_authorization_sha256')!=digest(original_auth)):
        raise ValueError('ORIGINAL_PLAN_AND_AUTHORIZATION_REQUIRED')
    clock_ref=original_auth.get('task_clock_reference') or {};path=Path(clock_ref.get('path','__missing__'))
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=clock_ref.get('sha256'):
        raise ValueError('ORIGINAL_CLOCK_REFERENCE_REQUIRED')
    clock=read_sealed(path)
    if (amendment.get('original_clock_reference')!=clock_ref or amendment.get('original_deadline')!=clock.get('deadline') or
        clock.get('run_id')!=plan['run_id'] or clock.get('plan_sha256')!=digest(plan)):
        raise ValueError('ORIGINAL_CLOCK_MUST_NOT_RESET')
    current=aware_time(now);deadline=aware_time(clock['deadline'])
    if not aware_time(amendment['reviewed_at'])<=current<deadline:
        raise ValueError('PARALLEL_AMENDMENT_TIME_INVALID')
    for k in ('max_requests_per_run','max_requests_per_day','max_pages_per_run','max_raw_observations_per_run',
              'history_observation_limit','request_pacing_seconds','category_cooldown_seconds','source_budgets','target_policy'):
        if digest(amendment.get(k))!=digest(plan.get(k)):
            raise ValueError('PARALLEL_CANNOT_CHANGE_BUDGET_OR_TARGET:'+k)
    if (amendment.get('workers')!=3 or amendment.get('parallel_scope')!='THREE_FIXED_WINDOWS_SAME_CONTEXT' or
        amendment.get('access_stop_scope')!='all' or amendment.get('profile_rotation') is not False or
        amendment.get('cookie_rotation') is not False or amendment.get('account_rotation') is not False or
        amendment.get('ip_rotation') is not False):
        raise ValueError('PARALLEL_FIXED_CONTEXT_GLOBAL_STOP_REQUIRED')
    if amendment.get('code_manifest_sha256')!=digest(code_manifest):
        raise ValueError('PARALLEL_CODE_MANIFEST_MISMATCH')
    return amendment
