"""One explicitly authorized access-recovery capture; never submits a challenge.

The caller retains the shared live locks. This module cannot clear a Gate or
start the queue. A failed/unknown capture is charged and cannot auto-retry.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import time

from ..access.detector import AccessStopError, detect_access_status
from ..access.location import inspect_delivery_location
from ..multisource.storage import atomic_json, digest, immutable_bytes, immutable_json, read_sealed, sealed
from .detail_bridge5500 import parse_candidate_detail, utc
from .detail_budget5500 import uncertain_setup_page_holds
from .quick_selection import file_sha


def validate_capture(task, receipt, html):
    """No HTTP status can be inferred from a normal-looking DOM."""
    if (receipt.get('state') != 'NORMAL' or receipt.get('status_code') != 200
            or receipt.get('request_url') != task['request_url']
            or receipt.get('final_url') != task['request_url']
            or receipt.get('task_id') != task['task_id']
            or receipt.get('run_id') != task['run_id']
            or receipt.get('html_sha256') != hashlib.sha256(html.encode('utf8')).hexdigest()
            or any(r.get('status_code') in {403, 429} for r in receipt.get('document_responses', []))
            or detect_access_status(200, html).value != 'NORMAL'):
        raise ValueError('RECOVERY_ACTUAL_NORMAL_HTTP_AND_EXACT_SCOPE_REQUIRED')
    location = inspect_delivery_location(html)
    if location.is_spain is not True or location.postal_code != '28001':
        raise ValueError('RECOVERY_ACTUAL_28001_REQUIRED')
    meta = dict(status_code=200, requested_url=task['request_url'], final_url=receipt['final_url'],
                access_state='NORMAL', collected_at=receipt['finished_at'])
    result = parse_candidate_detail(task, html, meta)
    if result['status'] != 'SUCCESS':
        raise ValueError('RECOVERY_EXACT_PRODUCT_IDENTITY_REQUIRED')
    return result, meta


def capture_once(session, bridge, auth, task, approval_reference):
    """Reserve before goto, independent of routing/response event delivery.

    Only one exact product navigation is permitted; no CAPTCHA, redirect,
    hidden API or additional product request is sent by this helper.
    """
    from urllib.parse import urlsplit
    rp = bridge.root / 'receipts' / (task['task_id'] + '_controlled_recovery_1.json')
    if rp.exists():
        raise ValueError('RECOVERY_ATTEMPT_ALREADY_CONSUMED')
    # Reuse ALL original authorization/CI/offline/budget-reference checks.
    # Only the final Gate check is expected to fail for this explicitly bound
    # exception; all other failures propagate without a request.
    try:
        bridge.validate_authorization(auth)
    except AccessStopError as exc:
        if not str(exc).startswith('DETAIL_ACCESS_GATE_NOT_NORMAL: '):
            raise
    ap = Path(approval_reference['path'])
    if file_sha(ap) != approval_reference['sha256']:
        raise ValueError('RECOVERY_APPROVAL_CHANGED')
    approval = read_sealed(ap)
    if (approval.get('approved') is not True or approval.get('run_id') != bridge.plan['run_id']
            or approval.get('task_id') != task['task_id']
            or approval.get('request_url') != task['request_url']
            or approval.get('max_attempts') != 1
            or approval.get('plan_sha256') != digest(bridge.plan)
            or approval.get('code_sha') != auth.get('code_sha')
            or approval.get('authority') != 'EXPLICIT_USER_MANUAL_HANDLING_AND_CONTINUATION'
            or not approval.get('user_quotes') or approval.get('runtime_root') != str(bridge.root.resolve())):
        raise ValueError('EXPLICIT_BOUND_RECOVERY_APPROVAL_REQUIRED')
    now = datetime.now(timezone.utc)
    if not datetime.fromisoformat(auth['starts_at']) <= now < datetime.fromisoformat(auth['request_deadline']):
        raise ValueError('RECOVERY_OUTSIDE_ORIGINAL_AUTHORIZATION_TIME')
    if not any(t == task for t in bridge.plan['tasks']):
        raise ValueError('RECOVERY_NOT_FROZEN_TASK')
    old_cp = read_sealed(bridge.root / 'tasks' / task['task_id'] / 'checkpoint.json')
    original = Path(old_cp['receipt_path'])
    if old_cp.get('status') != 'ACCESS_STOP' or read_sealed(original).get('state') != 'CHALLENGE':
        raise ValueError('RECOVERY_NOT_ORIGINAL_CHALLENGE_TASK')
    for path in (bridge.root / 'access_gate.json', Path(bridge.plan['shared_gate_path'])):
        g = read_sealed(path)
        if (file_sha(path) != approval.get('gate_sha256', {}).get(str(path))
                or g.get('state') != 'CHALLENGE' or g.get('receipt_path') != str(original)):
            raise ValueError('RECOVERY_GATE_CHANGED_OR_UNRELATED')
    receipts = [read_sealed(p) for p in (bridge.root / 'receipts').glob('*.json')]
    held = uncertain_setup_page_holds(bridge.root, bridge.plan, auth)
    if (any(r.get('state') == 'RESERVED' for r in receipts)
            or len(receipts) + held >= auth['max_requests']
            or sum(r.get('kind', 'PAGE') == 'PAGE' for r in receipts) + held >= auth['max_page_requests']):
        raise ValueError('RECOVERY_ORIGINAL_CUMULATIVE_BUDGET_OR_INFLIGHT')
    last = max((datetime.fromisoformat(r['started_at']).timestamp() for r in receipts), default=0)
    time.sleep(max(0, last + max(3, auth['request_start_interval_seconds']) - time.time()))
    history = []
    def observe(response):
        if response.request.resource_type == 'document':
            history.append({'url': response.url, 'status_code': response.status})
    initial_allowed = True
    def boundary(route):
        nonlocal initial_allowed
        r = route.request
        if r.is_navigation_request():
            if (initial_allowed and r.method == 'GET' and r.url == task['request_url']
                    and r.frame == session.page.main_frame and r.redirected_from is None):
                initial_allowed = False
                return route.continue_()
            return route.abort()
        # No alternate hidden fetch path or document can escape this boundary.
        if r.resource_type not in {'stylesheet', 'script'} or urlsplit(r.url).scheme != 'https':
            return route.abort()
        return route.continue_()
    session.page.route('**/*', boundary)
    session.page.on('response', observe)
    receipt = dict(state='RESERVED', kind='PAGE', request_role='DETAIL', attempt=2,
        run_id=bridge.plan['run_id'], task_id=task['task_id'], request_url=task['request_url'],
        started_at=utc(), recovery_approval_reference=approval_reference,
        original_challenge_receipt=str(original), human_handled=False,
        user_authorized_verification=True)
    immutable_json(rp, sealed(receipt))
    status = None
    html = ''
    error = None
    try:
        response = session.page.goto(task['request_url'], wait_until='domcontentloaded', timeout=45000)
        initial_allowed = False
        # Direct goto result supplies actual HTTP evidence even if callbacks
        # are not observed. None remains unknown, never fabricated as 200.
        status = response.status if response is not None else None
        html = session.page.content()
        if detect_access_status(status, html).value == 'NORMAL':
            session.wait_for_product_page()
            session.wait_for_price_text()
            html = session.page.content()
    except Exception as exc:
        initial_allowed = False
        error = type(exc).__name__ + ': ' + str(exc)
        try:
            html = session.page.content()
        except Exception:
            pass
    raw = html.encode('utf8')
    hp = bridge.root / 'tasks' / task['task_id'] / 'html' / (hashlib.sha256(raw).hexdigest()+'.html')
    hp.parent.mkdir(parents=True, exist_ok=True)
    if hp.exists() and hp.read_bytes() != raw:
        raise ValueError('RECOVERY_HTML_IMMUTABILITY_CONFLICT')
    if not hp.exists():
        hp.write_bytes(raw)
    state = detect_access_status(status, html).value
    for r in history:
        if r['status_code'] in {403, 429}:
            state = detect_access_status(r['status_code'], html).value
    if error and state == 'NORMAL':
        state = 'UNKNOWN'
    receipt.update(state=state, status_code=status, html_path=str(hp), html_sha256=file_sha(hp),
        final_url=session.page.url, finished_at=utc(), error=error, document_responses=history)
    atomic_json(rp, sealed(receipt), backup=True)
    bridge.progress()
    return rp, receipt, html


def accept_capture(bridge, task, receipt_path, approval_reference, success_baseline):
    """Accept one proved normal capture, preserving both original Gate histories.

    The caller holds the controller/collector/shared locks. Partial failure
    remains fail-closed: the queue needs BOTH Gates NORMAL before any request.
    """
    ap = Path(approval_reference['path'])
    if file_sha(ap) != approval_reference['sha256']:
        raise ValueError('RECOVERY_APPROVAL_CHANGED')
    approval = read_sealed(ap)
    rp = Path(receipt_path)
    r = read_sealed(rp)
    if (rp.parent.resolve() != (bridge.root/'receipts').resolve()
            or r.get('recovery_approval_reference') != approval_reference
            or approval.get('approved') is not True
            or approval.get('authority') != 'EXPLICIT_USER_MANUAL_HANDLING_AND_CONTINUATION'
            or approval.get('runtime_root') != str(bridge.root.resolve())
            or approval.get('task_id') != task['task_id']
            or approval.get('run_id') != bridge.plan['run_id']
            or approval.get('request_url') != task['request_url']
            or approval.get('max_attempts') != 1
            or approval.get('plan_sha256') != digest(bridge.plan)
            or not approval.get('user_quotes') or not any(t == task for t in bridge.plan['tasks'])):
        raise ValueError('RECOVERY_ACCEPTANCE_SCOPE_REQUIRED')
    cp = bridge.root/'tasks'/task['task_id']/'checkpoint.json'
    old = read_sealed(cp)
    if old.get('status') != 'ACCESS_STOP' or old.get('receipt_path') != r.get('original_challenge_receipt'):
        raise ValueError('RECOVERY_ORIGINAL_FAILURE_CHANGED')
    gates = [bridge.root/'access_gate.json', Path(bridge.plan['shared_gate_path'])]
    previous = {}
    for p in gates:
        g = read_sealed(p)
        if (file_sha(p) != approval['gate_sha256'].get(str(p)) or g.get('state') != 'CHALLENGE'
                or g.get('receipt_path') != old['receipt_path']):
            raise ValueError('RECOVERY_GATE_CHANGED_OR_UNRELATED')
        previous[p] = g
    if not success_baseline or task['task_id'] in success_baseline:
        raise ValueError('RECOVERY_SUCCESS_BASELINE_REQUIRED')
    for tid, evidence in success_baseline.items():
        p = bridge.root/'tasks'/tid/'checkpoint.json'
        saved = read_sealed(p)
        if (saved.get('status') != 'SUCCESS' or file_sha(p) != evidence['checkpoint_sha256']
                or file_sha(saved['html_path']) != evidence['html_sha256']):
            raise ValueError('RECOVERY_PRIOR_SUCCESS_CHANGED')
    if any(read_sealed(p).get('state') == 'RESERVED' for p in (bridge.root/'receipts').glob('*.json')):
        raise ValueError('RECOVERY_INFLIGHT_REQUEST')
    if file_sha(r['html_path']) != r['html_sha256']:
        raise ValueError('RECOVERY_HTML_CHANGED')
    html = Path(r['html_path']).read_text(encoding='utf8')
    result, meta = validate_capture(task, r, html)
    # Repeat full parsing independently before changing any task/Gate state.
    if parse_candidate_detail(task, html, dict(meta)) != result:
        raise ValueError('RECOVERY_INDEPENDENT_REPLAY_MISMATCH')
    proof = bridge.root/'access_recovery_1.accepted.json'
    immutable_json(proof, sealed({'run_id':bridge.plan['run_id'],'task_id':task['task_id'],
        'approval_reference':approval_reference,'normal_receipt_path':str(rp),
        'normal_receipt_sha256':file_sha(rp),'original_failure_checkpoint_sha256':file_sha(cp),
        'original_challenge_receipt':old['receipt_path'],'previous_gates':{str(p):v for p,v in previous.items()},
        'prior_success_count':len(success_baseline),'human_handled':False,
        'human_authorized_normal_verification':True,'no_verification_form_submitted_by_agent':True,
        'accepted_at':utc()}))
    immutable_bytes(cp.with_name('checkpoint.challenge.original.json'),cp.read_bytes())
    mp = cp.with_name('response_metadata.recovery_1.json')
    immutable_json(mp,{**meta,'receipt_path':str(rp),'recovery_evidence_path':str(proof)})
    result.update(html_path=r['html_path'],html_sha256=r['html_sha256'],receipt_path=str(rp),
        receipt_sha256=file_sha(rp),response_metadata_path=str(mp),response_metadata_sha256=file_sha(mp))
    bridge.save_result(task,result)
    for p, prior in previous.items():
        atomic_json(p,sealed({'state':'NORMAL','run_id':bridge.plan['run_id'],'previous_gate':prior,
            'recovery_evidence_path':str(proof),'recovery_evidence_sha256':file_sha(proof),
            'human_handled':False,'human_authorized_normal_verification':True,'observed_at':utc()}),backup=True)
    return read_sealed(proof)
