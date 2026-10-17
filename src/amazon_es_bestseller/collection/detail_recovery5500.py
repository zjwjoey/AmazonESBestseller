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
from .detail_budget5500 import total_conservative_page_holds as uncertain_setup_page_holds
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


TIMEOUT_RECHECK_KIND = 'USER_AUTHORIZED_DOMCONTENTLOADED_TIMEOUT_RECHECK_V1'
TIMEOUT_RECHECK_AUTHORITY = 'EXPLICIT_USER_PAGE_CHECK_AND_CONDITIONAL_CONTINUATION'


def _explicit_recovery_authority(approval):
    return (approval.get('authority') == 'EXPLICIT_USER_MANUAL_HANDLING_AND_CONTINUATION'
            or (approval.get('authority') == TIMEOUT_RECHECK_AUTHORITY
                and approval.get('original_failure_kind') == TIMEOUT_RECHECK_KIND
                and approval.get('user_authorized_exact_page_recheck') is True
                and approval.get('max_attempts') == 1))


def review_failure_state(approval, original, task):
    """A reported restored ERR_FAILED is not a blanket UNKNOWN exception."""
    expected = approval.get('expected_failure_state', 'CHALLENGE')
    if approval.get('original_failure_kind') == TIMEOUT_RECHECK_KIND:
        responses = original.get('document_responses') or []
        if (expected != 'UNKNOWN' or original.get('state') != 'UNKNOWN'
                or approval.get('authority') != TIMEOUT_RECHECK_AUTHORITY
                or not _explicit_recovery_authority(approval) or not approval.get('user_quotes')
                or original.get('status_code') is not None
                or original.get('task_id') != task['task_id']
                or original.get('run_id') != task['run_id']
                or original.get('request_url') != task['request_url']
                or original.get('final_url') != task['request_url']
                or original.get('document_start_event_observed') is not True
                or not responses or any(r.get('status_code') != 200
                    or r.get('url') != task['request_url'] for r in responses)
                or not str(original.get('error') or '').startswith(
                    'TimeoutError: Page.goto: Timeout 45000ms exceeded.')
                or 'waiting until "domcontentloaded"' not in str(original.get('error'))):
            raise ValueError('RECOVERY_ORIGINAL_TIMEOUT_NOT_EXPLICITLY_SUPPORTED')
        return expected
    if expected == 'CHALLENGE' and original.get('state') == 'CHALLENGE':
        return expected
    if (expected != 'UNKNOWN' or original.get('state') != 'UNKNOWN'
            or approval.get('original_failure_kind') != 'USER_REPORTED_RESTORED_NET_ERR_FAILED_NO_HTTP_V1'
            or approval.get('user_reported_page_restored') is not True
            or not approval.get('user_quotes')
            or original.get('status_code') is not None
            or original.get('document_responses') != []
            or original.get('final_url') != 'chrome-error://chromewebdata/'
            or original.get('task_id') != task['task_id'] or original.get('run_id') != task['run_id']
            or original.get('request_url') != task['request_url']
            or not str(original.get('error') or '').startswith('Error: Page.goto: net::ERR_FAILED at '+task['request_url']+'\n')):
        raise ValueError('RECOVERY_ORIGINAL_FAILURE_NOT_EXPLICITLY_SUPPORTED')
    return expected


def bound_failure_state(approval, original_path, task):
    original = read_sealed(original_path)
    expected = review_failure_state(approval, original, task)
    if expected == 'UNKNOWN' and approval.get('original_failure_receipt_reference') != {
            'path':str(original_path), 'sha256':file_sha(original_path)}:
        raise ValueError('RECOVERY_ORIGINAL_NETWORK_RECEIPT_BINDING_REQUIRED')
    if approval.get('original_failure_kind') == TIMEOUT_RECHECK_KIND:
        hp = Path(original.get('html_path') or '')
        if not hp.is_file() or file_sha(hp) != original.get('html_sha256'):
            raise ValueError('RECOVERY_ORIGINAL_TIMEOUT_HTML_CHANGED')
        html = hp.read_text(encoding='utf8')
        location = inspect_delivery_location(html)
        # This is only a precondition for one new observation. It never changes
        # the original null HTTP status or UNKNOWN receipt into a success.
        # An explicitly bound four-task cohort may contain incomplete original
        # DOMs without a delivery widget. It never treats missing location as
        # verified: each NEW capture must still prove Spain/28001 in full.
        incomplete_cohort_dom = (approval.get('timeout_cohort_reference') is not None
                                 and location.is_spain is None and not location.postal_code
                                 and not location.text)
        if (detect_access_status(None, html).value != 'UNKNOWN'
                or (not incomplete_cohort_dom
                    and (location.is_spain is not True or location.postal_code != '28001'))):
            raise ValueError('RECOVERY_ORIGINAL_TIMEOUT_RESTRICTION_OR_LOCATION')
    return expected



def _timeout_cohort(bridge, approval, task):
    reference = approval.get('timeout_cohort_reference')
    if reference is None:
        return None
    p = Path(reference['path'])
    if file_sha(p) != reference['sha256']:
        raise ValueError('RECOVERY_COHORT_CHANGED')
    cohort = read_sealed(p)
    entries = cohort.get('entries', [])
    if (cohort.get('approved') is not True
            or cohort.get('scope') != 'FOUR_EXACT_TIMEOUT_RECHECKS_ALL_NORMAL_BEFORE_QUEUE'
            or cohort.get('run_id') != bridge.plan['run_id']
            or cohort.get('runtime_root') != str(bridge.root.resolve())
            or cohort.get('plan_sha256') != digest(bridge.plan)
            or cohort.get('code_sha') != approval.get('code_sha')
            or cohort.get('max_total_rechecks') != 4
            or cohort.get('max_attempts_per_task') != 1
            or not cohort.get('user_quotes') or len(entries) != 4
            or len({e['task_id'] for e in entries}) != 4
            or len({e['request_url'] for e in entries}) != 4
            or approval.get('original_failure_kind') != TIMEOUT_RECHECK_KIND
            or approval.get('gate_sha256') != cohort.get('gate_sha256')):
        raise ValueError('RECOVERY_EXACT_FOUR_TIMEOUT_COHORT_REQUIRED')
    for entry in entries:
        if not any(t['task_id'] == entry['task_id'] and t['request_url'] == entry['request_url']
                   for t in bridge.plan['tasks']):
            raise ValueError('RECOVERY_COHORT_NOT_FROZEN_TASKS')
    matches = [e for e in entries if e['task_id'] == task['task_id']
               and e['request_url'] == task['request_url']]
    if len(matches) != 1:
        raise ValueError('RECOVERY_TASK_OUTSIDE_COHORT')
    e = matches[0]
    if approval.get('original_failure_receipt_reference') != e['original_failure_receipt_reference']:
        raise ValueError('RECOVERY_COHORT_ORIGINAL_RECEIPT_MISMATCH')
    cp = bridge.root/'tasks'/task['task_id']/'checkpoint.json'
    if str(cp) != e['original_checkpoint_reference']['path'] or file_sha(cp) != e['original_checkpoint_reference']['sha256']:
        raise ValueError('RECOVERY_COHORT_ORIGINAL_CHECKPOINT_CHANGED')
    if cohort.get('gate_original_receipt') not in [e['original_failure_receipt_reference']['path'] for e in entries]:
        raise ValueError('RECOVERY_COHORT_GATE_NOT_BOUND_TO_FAILURE')
    return cohort


def _recovery_gate_evidence(bridge, approval, task, original, expected):
    cohort = _timeout_cohort(bridge, approval, task)
    receipt = cohort['gate_original_receipt'] if cohort else str(original)
    previous = {}
    for p in (bridge.root/'access_gate.json', Path(bridge.plan['shared_gate_path'])):
        g = read_sealed(p)
        if (file_sha(p) != approval.get('gate_sha256', {}).get(str(p))
                or g.get('state') != expected or g.get('receipt_path') != receipt):
            raise ValueError('RECOVERY_GATE_CHANGED_OR_UNRELATED')
        previous[p] = g
    return previous


def finalize_timeout_cohort(bridge, cohort_reference, approval_references, success_baseline):
    """No admission until every exact once-only capture has durable acceptance."""
    p = Path(cohort_reference['path'])
    if file_sha(p) != cohort_reference['sha256']:
        raise ValueError('RECOVERY_COHORT_CHANGED')
    cohort = read_sealed(p)
    if len(approval_references) != 4 or not success_baseline:
        raise ValueError('RECOVERY_FOUR_ACCEPTANCES_REQUIRED')
    proofs = []
    seen = set()
    for ar in approval_references:
        ap = Path(ar['path'])
        if file_sha(ap) != ar['sha256']:
            raise ValueError('RECOVERY_APPROVAL_CHANGED')
        a = read_sealed(ap)
        if a.get('timeout_cohort_reference') != cohort_reference or not _explicit_recovery_authority(a):
            raise ValueError('RECOVERY_COHORT_APPROVAL_MISMATCH')
        t = next((t for t in bridge.plan['tasks'] if t['task_id'] == a.get('task_id')), None)
        if t is None or t['task_id'] in seen or t['task_id'] in success_baseline:
            raise ValueError('RECOVERY_DUPLICATE_OR_PRIOR_SUCCESS')
        seen.add(t['task_id'])
        cp = bridge.root/'tasks'/t['task_id']/'checkpoint.json'
        historical = cp.with_name('checkpoint.network_unknown.original.json')
        # Validate the entire cohort binding against the preserved original CP.
        e = next((e for e in cohort.get('entries', []) if e['task_id'] == t['task_id']), None)
        if not e or not historical.is_file() or file_sha(historical) != e['original_checkpoint_reference']['sha256']:
            raise ValueError('RECOVERY_COHORT_ORIGINAL_NOT_PRESERVED')
        # _timeout_cohort ordinarily requires the current CP still failed;
        # its acceptance proof records that original hash before save_result.
        proof_path = bridge.root/(a['recovery_evidence_name']+'.accepted.json')
        proof = read_sealed(proof_path)
        if (proof.get('approval_reference') != ar
                or proof.get('original_failure_checkpoint_sha256') != file_sha(historical)
                or proof.get('task_id') != t['task_id'] or proof.get('run_id') != bridge.plan['run_id']):
            raise ValueError('RECOVERY_COHORT_PROOF_MISMATCH')
        rp = Path(proof['normal_receipt_path'])
        if file_sha(rp) != proof['normal_receipt_sha256']:
            raise ValueError('RECOVERY_COHORT_RECEIPT_CHANGED')
        r = read_sealed(rp)
        current = read_sealed(cp)
        if current.get('status') != 'SUCCESS' or current.get('receipt_path') != str(rp) or current.get('receipt_sha256') != file_sha(rp):
            raise ValueError('RECOVERY_COHORT_SUCCESS_NOT_DURABLE')
        if file_sha(r['html_path']) != r['html_sha256'] or current.get('html_sha256') != r['html_sha256']:
            raise ValueError('RECOVERY_COHORT_HTML_CHANGED')
        validate_capture(t, r, Path(r['html_path']).read_text(encoding='utf8'))
        mp = Path(current['response_metadata_path'])
        if file_sha(mp) != current['response_metadata_sha256']:
            raise ValueError('RECOVERY_COHORT_METADATA_CHANGED')
        proofs.append(dict(path=str(proof_path),sha256=file_sha(proof_path)))
    if seen != {e['task_id'] for e in cohort.get('entries', [])}:
        raise ValueError('RECOVERY_COHORT_INCOMPLETE')
    for tid, evidence in success_baseline.items():
        cp = bridge.root/'tasks'/tid/'checkpoint.json'
        c = read_sealed(cp)
        if file_sha(cp) != evidence['checkpoint_sha256'] or file_sha(c['html_path']) != evidence['html_sha256']:
            raise ValueError('RECOVERY_PRIOR_SUCCESS_CHANGED')
    previous = {}
    for gp in (bridge.root/'access_gate.json', Path(bridge.plan['shared_gate_path'])):
        g = read_sealed(gp)
        if (file_sha(gp) != cohort.get('gate_sha256', {}).get(str(gp))
                or g.get('state') != 'UNKNOWN' or g.get('receipt_path') != cohort.get('gate_original_receipt')):
            raise ValueError('RECOVERY_GATE_CHANGED_OR_UNRELATED')
        previous[gp] = g
    if any(read_sealed(rp).get('state') == 'RESERVED' for rp in (bridge.root/'receipts').glob('*.json')):
        raise ValueError('RECOVERY_INFLIGHT_REQUEST')
    final = bridge.root/'access_recovery_four_timeout_cohort.accepted.json'
    immutable_json(final, sealed(dict(run_id=bridge.plan['run_id'],cohort_reference=cohort_reference,
        accepted_recovery_references=proofs,prior_success_count=len(success_baseline),
        all_four_actual_normal=True,human_handled=False,accepted_at=utc(),not_final_acceptance=True)))
    for gp, prior in previous.items():
        atomic_json(gp,sealed(dict(state='NORMAL',run_id=bridge.plan['run_id'],previous_gate=prior,
            recovery_evidence_path=str(final),recovery_evidence_sha256=file_sha(final),
            human_handled=False,human_authorized_normal_verification=True,observed_at=utc())),backup=True)
    return read_sealed(final)


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
            or not _explicit_recovery_authority(approval)
            or not approval.get('user_quotes') or approval.get('runtime_root') != str(bridge.root.resolve())):
        raise ValueError('EXPLICIT_BOUND_RECOVERY_APPROVAL_REQUIRED')
    now = datetime.now(timezone.utc)
    if not datetime.fromisoformat(auth['starts_at']) <= now < datetime.fromisoformat(auth['request_deadline']):
        raise ValueError('RECOVERY_OUTSIDE_ORIGINAL_AUTHORIZATION_TIME')
    if not any(t == task for t in bridge.plan['tasks']):
        raise ValueError('RECOVERY_NOT_FROZEN_TASK')
    old_cp = read_sealed(bridge.root / 'tasks' / task['task_id'] / 'checkpoint.json')
    if old_cp.get('status') != 'ACCESS_STOP' or not old_cp.get('receipt_path'):
        raise ValueError('RECOVERY_NOT_ORIGINAL_CHALLENGE_TASK')
    original = Path(old_cp['receipt_path'])
    expected_failure = bound_failure_state(approval, original, task)
    _recovery_gate_evidence(bridge, approval, task, original, expected_failure)
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


def accept_capture(bridge, task, receipt_path, approval_reference, success_baseline, *,
                   recovery_name='access_recovery_1'):
    """Accept one proved normal capture, preserving both original Gate histories.

    The caller holds the controller/collector/shared locks. Partial failure
    remains fail-closed: the queue needs BOTH Gates NORMAL before any request.
    """
    ap = Path(approval_reference['path'])
    if file_sha(ap) != approval_reference['sha256']:
        raise ValueError('RECOVERY_APPROVAL_CHANGED')
    approval = read_sealed(ap)
    # A second explicitly approved event must never collide with the first
    # immutable proof. This names evidence only; it grants no new attempt.
    import re
    if (not isinstance(recovery_name, str)
            or not re.fullmatch(r'access_recovery_[1-9][0-9]*', recovery_name)
            or approval.get('recovery_evidence_name', 'access_recovery_1') != recovery_name):
        raise ValueError('RECOVERY_EVIDENCE_NAME_NOT_EXPLICITLY_BOUND')
    rp = Path(receipt_path)
    r = read_sealed(rp)
    if (rp.parent.resolve() != (bridge.root/'receipts').resolve()
            or r.get('recovery_approval_reference') != approval_reference
            or approval.get('approved') is not True
            or not _explicit_recovery_authority(approval)
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
    expected_failure = bound_failure_state(approval, Path(old['receipt_path']), task)
    previous = _recovery_gate_evidence(bridge, approval, task, Path(old['receipt_path']), expected_failure)
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
    proof = bridge.root/(recovery_name+'.accepted.json')
    immutable_json(proof, sealed({'run_id':bridge.plan['run_id'],'task_id':task['task_id'],
        'approval_reference':approval_reference,'normal_receipt_path':str(rp),
        'normal_receipt_sha256':file_sha(rp),'original_failure_checkpoint_sha256':file_sha(cp),
        'original_challenge_receipt':old['receipt_path'],'previous_gates':{str(p):v for p,v in previous.items()},
        'prior_success_count':len(success_baseline),'human_handled':False,
        'human_authorized_normal_verification':True,'no_verification_form_submitted_by_agent':True,
        'accepted_at':utc()}))
    history_name = 'checkpoint.challenge.original.json' if expected_failure=='CHALLENGE' else 'checkpoint.network_unknown.original.json'
    immutable_bytes(cp.with_name(history_name),cp.read_bytes())
    mp = cp.with_name('response_metadata.recovery_'+recovery_name.removeprefix('access_recovery_')+'.json')
    immutable_json(mp,{**meta,'receipt_path':str(rp),'recovery_evidence_path':str(proof)})
    result.update(html_path=r['html_path'],html_sha256=r['html_sha256'],receipt_path=str(rp),
        receipt_sha256=file_sha(rp),response_metadata_path=str(mp),response_metadata_sha256=file_sha(mp))
    bridge.save_result(task,result)
    for p, prior in ([] if approval.get('timeout_cohort_reference') else previous.items()):
        atomic_json(p,sealed({'state':'NORMAL','run_id':bridge.plan['run_id'],'previous_gate':prior,
            'recovery_evidence_path':str(proof),'recovery_evidence_sha256':file_sha(proof),
            'human_handled':False,'human_authorized_normal_verification':True,'observed_at':utc()}),backup=True)
    return read_sealed(proof)
