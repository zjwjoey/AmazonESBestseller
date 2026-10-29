"""Current PAGE reporting: read-only evidence review, never legacy T2 acceptance."""
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
from pathlib import Path
import time
import uuid

from ..multisource.storage import canonical_bytes, immutable_bytes, immutable_json, read_sealed, digest
from .budgets import aware_time
from .page_snapshot import CONTRACT, validate_page_snapshot
from .three_metrics import measure_three, qualify_three
from .v4_quality import audit_v4, build_candidates_v4
from .v4_workflows import csv_bytes
from .snapshot_control import stop_category, pause_pending


def receipt_review(store, run):
    """Reconcile ALL reservations, including interrupted/failed non-page work."""
    errors = []
    plan = run['plan']; run_id = plan['run_id']
    root = store.root / 'runs' / run_id
    proofs = []
    def read(path):
        body = path.read_bytes()
        proofs.append({'path': str(path), 'sha256': hashlib.sha256(body).hexdigest()})
        return read_sealed(path)
    try:
        if read(root / 'plan.json') != plan:
            raise ValueError('IMMUTABLE_PLAN_MISMATCH')
        reservations = [read(p) for p in sorted((root / 'requests').glob('*.reserved.json'))]
        receipts = [read(p) for p in sorted((root / 'responses').glob('*.json'))]
        if (not receipts or {r['request_id'] for r in receipts} != {r['request_id'] for r in reservations}
                or len(receipts) != len(reservations)):
            raise ValueError('UNFINISHED_OR_MISSING_RECEIPTS')
        request_index = {q['request_id']:q for q in store.requests(run_id)}
        if set(request_index) != {q['request_id'] for q in reservations}:
            raise ValueError('RESERVATION_INDEX_MISMATCH')
        for q in reservations:
            indexed_q = dict(request_index[q['request_id']]); status = indexed_q.pop('status')
            original_q = dict(q); original_q.pop('status', None)
            if status != 'NORMAL' or indexed_q != original_q:
                raise ValueError('RESERVATION_INDEX_CONTENT_OR_STATUS_MISMATCH')
        if len(reservations) > min(plan['max_requests_per_run'], plan['max_pages_per_run']):
            raise ValueError('PAGE_OR_REQUEST_BUDGET_EXCEEDED')
        indexed = {r['request_id']: r for r in store.iter_responses(run_id)}
        if set(indexed) != {r['request_id'] for r in receipts}:
            raise ValueError('RECEIPT_INDEX_MISMATCH')
        approvals = []
        from .v4_authorization import validate_v4_authorization
        for ref in run.get('execution_approval_references', []):
            if hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest() != ref['sha256']:
                raise ValueError('EXECUTION_APPROVAL_SHA_MISMATCH')
            approval = json.loads(Path(ref['path']).read_text('utf8'))
            # Historical authorization, independently bound to every receipt below.
            request_times = [q['observed_at'] for q in reservations if
                aware_time(approval['reviewed_at']) <= aware_time(q['observed_at']) < aware_time(approval['expires_at'])]
            if request_times:
                validate_v4_authorization(plan, approval, {'state': 'NORMAL'}, now=min(request_times, key=aware_time), store_root=store.root)
                approvals.append(approval)
            proofs.append(ref)
        if not approvals:
            raise ValueError('EXECUTION_APPROVAL_REQUIRED')
        sources = {s['source_id']: s for s in plan['sources']}
        for page in store.pages(run_id):
            source = page.get('source', {})
            if source.get('snapshot_admission_root'):
                from .page_navigation import validate_admitted_source
                validate_admitted_source(source, sources[source['snapshot_admission_root']], run_id, digest(plan))
                sources[source['source_id']] = source
        reserved = {r['request_id']: r for r in reservations}
        from .response_alias import authorize_v4_response
        for receipt in receipts:
            q = reserved[receipt['request_id']]
            if (q['run_id'] != run_id or receipt['run_id'] != run_id or q['plan_sha256'] != digest(plan)
                    or q['source_id'] != receipt['source_id'] or q['kind'] != 'PAGE' or receipt['kind'] != 'PAGE'
                    or q['url'] != receipt['request_url'] or q['observed_at'] != receipt['observed_at']
                    or receipt.get('origin') != 'LIVE_CAPTURE' or q.get('origin') != 'LIVE_CAPTURE'
                    or receipt.get('status_code') != 200 or receipt.get('access_state') != 'NORMAL'):
                raise ValueError('NORMAL_PAGE_RESERVATION_BINDING_REQUIRED')
            saved = dict(indexed[receipt['request_id']]); saved.pop('evidence_path', None)
            actual = dict(receipt); actual.pop('evidence_path', None)
            if saved != actual:
                raise ValueError('RECEIPT_INDEX_CONTENT_MISMATCH')
            body_path = Path(receipt['html_path']); body = body_path.read_bytes()
            if hashlib.sha256(body).hexdigest() != receipt['sha256']:
                raise ValueError('RECEIPT_BODY_SHA_MISMATCH')
            proofs.append({'path': str(body_path), 'sha256': receipt['sha256']})
            source = sources[receipt['source_id']]
            applicable = [a for a in approvals if aware_time(a['reviewed_at']) <= aware_time(q['observed_at']) < aware_time(a['expires_at'])]
            allowed = False
            for approval in applicable:
                try:
                    from .task_clock import validate_task_clock
                    validate_task_clock(plan, approval, now=q['observed_at'])
                    # Same validation used by the request engine for admitted pages.
                    authorize_v4_response(receipt, source, approval)
                    allowed = True; break
                except (ValueError, KeyError):
                    continue
            if not allowed:
                raise ValueError('REQUEST_NOT_IN_APPROVED_SCOPE')
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        errors.append(str(exc))
    return {'passed': not errors, 'errors': errors, 'evidence': proofs,
            'new_network_requests': 0, 'fabricated_receipts': False}


def selection_summary(candidates, observations):
    categories = defaultdict(set)
    fields = ('product_title_es', 'current_price', 'rating_value', 'rating_count', 'monthly_bought_min', 'image_url')
    for row in observations:
        category = row.get('amazon_category_l1') or row.get('source_category_l1_raw') or 'UNVERIFIED'
        categories[category].add(row.get('possible_duplicate_key') or row.get('normalized_product_url'))
    return {'candidate_count': len(candidates), 'field_present_counts': {
        field: sum(r.get(field) is not None and r.get(field) != '' for r in candidates) for field in fields},
        'category_observed_groups': {k: len(v) for k, v in sorted(categories.items())},
        'category_counts_are_nonadditive': True,
        'identity_observation_statuses': dict(Counter(r.get('identity_observation_status', 'UNVERIFIED') for r in observations)),
        'exclusion_reasons': dict(Counter(reason for r in observations for reason in r.get('snapshot_exclusion_reasons', []))),
        'SKU_identity_status': 'NOT_VERIFIED'}


def inspect_snapshot(store, run_id):
    run = store.get_run(run_id); plan = run['plan']
    validate_page_snapshot(plan)
    rows = list(store.iter_observations(run_id))
    metrics = measure_three(rows, run_id, target_policy=plan['target_policy'])
    return {'run_id': run_id, 'status': run['status'], 'stop_reason': run.get('stop_reason'),
            'snapshot_phase': run.get('snapshot_phase') if run['status'] in {'RUNNING','PAUSED_ERROR'} else stop_category(run.get('stop_reason')),
            'stop_category': stop_category(run.get('stop_reason')), 'metrics': metrics,
            'pause_pending': pause_pending(store.root, plan), 'category_schedule': run.get('category_schedule'),
            'last_request_at': store.latest_request_time(), 'budgets_used': store.budget_counters(run_id),
            'budgets': {k: plan[k] for k in ('max_requests_per_run', 'max_pages_per_run', 'source_budgets')},
            'stored_snapshot_review': run.get('snapshot_review'), 'fresh_audit_performed': False,
            'process_liveness': 'NOT_INSPECTED', 'production_acceptance': 'NOT_EVALUATED',
            'new_network_requests': 0}


def export_page_snapshot(store, run_id, output):
    run = store.get_run(run_id); plan = run['plan']; validate_page_snapshot(plan)
    # Reject aliases into this store/its parents: reports never write to evidence.
    out = Path(output).resolve()
    if out == store.root or out.is_relative_to(store.root):
        raise ValueError('SNAPSHOT_REPORT_OUTSIDE_STORE_REQUIRED')
    started = time.perf_counter()
    pages = store.pages(run_id); rows = list(store.iter_observations(run_id))
    loaded = time.perf_counter()
    from .v4_authorization import prerequisite_review_session
    with prerequisite_review_session():
        quality = audit_v4(pages, rows, plan['sources'], require_live=True)
        receipts = receipt_review(store, run)
    reviewed = time.perf_counter()
    metrics = measure_three(rows, run_id, target_policy=plan['target_policy'])
    _, qualified, invalid, excluded = qualify_three(rows, run_id)
    candidates = build_candidates_v4(qualified)
    from .snapshot_review import snapshot_review_fields
    fields = snapshot_review_fields(run, metrics, quality, len(pages))
    passed = fields['snapshot_review']['passed'] and receipts['passed']
    accepted = bool(passed and metrics['target_met'])
    for row in candidates:
        row['export_QA_status'] = 'CURRENT_PAGE_DOM_RECEIPTS_PASSED' if passed else 'QA_NOT_PASSED_DO_NOT_USE'
    summary = selection_summary(candidates, rows)
    projection = [{k: r.get(k) for k in ('candidate_id', 'product_title_es', 'image_url', 'current_price',
        'rating_value', 'rating_count', 'monthly_bought_raw', 'monthly_bought_min', 'amazon_category_l1',
        'source_category_l1_raw', 'source_type', 'ranking_position', 'normalized_product_url',
        'asin_url_hint', 'identity_observation_status', 'target_review_group', 'observation_count', 'export_QA_status')}
        for r in candidates]
    directory = out / ('page_snapshot_report_' + uuid.uuid4().hex)
    artifacts = {'candidates.csv': csv_bytes(candidates), 'selection.csv': csv_bytes(projection),
        'observations.jsonl': b''.join(canonical_bytes(r) + b'\n' for r in rows),
        'held_observations.jsonl': b''.join(canonical_bytes(r) + b'\n' for r in invalid),
        'quality.json': canonical_bytes({'DOM': quality, 'receipts': receipts}),
        'selection_summary.json': canonical_bytes(summary), 'metrics.json': canonical_bytes(metrics)}
    csv_expected = {'candidates.csv': candidates, 'selection.csv': projection}
    for kind in metrics['sources']:
        own = build_candidates_v4([r for r in qualified if r['source_type'] == kind])
        for candidate in own:
            candidate['export_QA_status'] = 'CURRENT_PAGE_DOM_RECEIPTS_PASSED' if passed else 'QA_NOT_PASSED_DO_NOT_USE'
        csv_expected[kind + '.csv'] = own
        artifacts[kind + '.csv'] = csv_bytes(own)
    directory.mkdir(parents=True, exist_ok=False)
    for name, data in artifacts.items():
        immutable_bytes(directory / name, data)
    # Compare every CSV cell (including all nested provenance), not only row count.
    for name, expected in csv_expected.items():
        saved = list(csv.DictReader(io.StringIO((directory / name).read_text('utf-8-sig'))))
        want = list(csv.DictReader(io.StringIO(csv_bytes(expected).decode('utf-8-sig'))))
        if saved != want:
            raise ValueError('SNAPSHOT_CSV_ROUNDTRIP_MISMATCH')
    if [json.loads(line) for line in (directory / 'observations.jsonl').read_text('utf8').splitlines()] != rows:
        raise ValueError('SNAPSHOT_JSONL_ROUNDTRIP_MISMATCH')
    manifest = {'run_id': run_id, 'contract': CONTRACT, 'target': metrics['global_target'], 'accepted': accepted,
        'quantity_target_met': metrics['target_met'], 'DOM_review_passed': fields['snapshot_review']['passed'],
        'receipt_review_passed': receipts['passed'], 'source_complete': False, 'SKU_identity_status': 'NOT_VERIFIED',
        'production_acceptance': 'NOT_EVALUATED', 'legacy_stage_acceptance_claimed': False,
        'excluded_noncurrent_or_nonlive': excluded, 'CSV_JSONL_independently_verified': True,
        'timings': {'read_seconds': loaded-started, 'audit_seconds': reviewed-loaded,
                    'metrics_and_export_seconds': time.perf_counter()-reviewed},
        'plan_sha256': digest(plan), 'new_network_requests': 0,
        'files': [{'name': n, 'sha256': hashlib.sha256(b).hexdigest()} for n, b in artifacts.items()]}
    immutable_json(directory / 'manifest.json', manifest)
    return {'directory': str(directory), **manifest}
