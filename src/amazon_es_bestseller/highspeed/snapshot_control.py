"""Plan-bound cooperative pause; never clears access gates or renews budgets."""
from pathlib import Path
import uuid

from ..multisource.storage import atomic_json, digest
from .budgets import utc_text
from .cli import load_json


def request_pause(root, plan):
    value = {'run_id': plan['run_id'], 'plan_sha256': digest(plan), 'token': uuid.uuid4().hex,
             'requested_at': utc_text(), 'action': 'PAUSE_AT_PAGE_BOUNDARY'}
    atomic_json(Path(root) / 'snapshot_pause.json', value)
    return {**value, 'collection_started': False}


def pause_pending(root, plan):
    path = Path(root) / 'snapshot_pause.json'
    if not path.exists():
        return False
    value = load_json(path)
    if value.get('run_id') != plan['run_id'] or value.get('plan_sha256') != digest(plan):
        raise ValueError('SNAPSHOT_PAUSE_PLAN_MISMATCH')
    release = Path(root) / 'snapshot_pause_release.json'
    return not release.exists() or load_json(release).get('pause_sha256') != digest(value)


def release_pause(root, plan):
    # Explicit resume only acknowledges this exact pause; retain both records.
    path = Path(root) / 'snapshot_pause.json'
    if not path.exists():
        return
    pause_pending(root, plan)
    atomic_json(Path(root) / 'snapshot_pause_release.json', {
        'run_id': plan['run_id'], 'plan_sha256': digest(plan),
        'pause_sha256': digest(load_json(path)), 'released_at': utc_text()})


def stop_category(reason):
    reason = reason or ''
    if reason == 'USER_PAUSED': return 'USER_PAUSED'
    if 'TARGET_REACHED' in reason: return 'QUANTITY_REACHED_QA_REQUIRED'
    if any(s in reason for s in ('CHALLENGE', 'CAPTCHA', 'ROBOT', 'ACCESS', '403', '429')):
        return 'ACCESS_BLOCKED_HUMAN_HANDLING_REQUIRED'
    if 'DELIVERY' in reason: return 'DELIVERY_NOT_VERIFIED'
    if 'AMBIGUOUS' in reason or 'RESERVED' in reason: return 'AMBIGUOUS_REQUEST_NO_AUTO_RETRY'
    if any(s in reason for s in ('BUDGET', 'CAP', 'DEADLINE', 'DURATION', 'LIMIT', 'RESERVE')):
        return 'BUDGET_OR_TIME_LIMIT'
    if any(s in reason for s in ('NETWORK', 'TIMEOUT', 'Timeout')): return 'NETWORK_REVIEW_REQUIRED'
    return 'SCOPE_EXHAUSTED_OR_EVIDENCE_REVIEW_REQUIRED'
