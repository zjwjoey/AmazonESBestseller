"""Scoped PAGE snapshot status; never a production or legacy stage approval."""
from copy import deepcopy

from .page_snapshot import CONTRACT


def publish_snapshot_review(store, run_id, fields):
    """Keep CLI status and the local progress projection consistent."""
    import json
    from ..multisource.storage import atomic_json
    from .budgets import utc_text
    path = store.root / 'snapshot_progress.json'
    progress = json.loads(path.read_text('utf8')) if path.is_file() else {'run_id': run_id}
    if progress.get('run_id') != run_id:
        raise ValueError('SNAPSHOT_PROGRESS_RUN_MISMATCH')
    progress.update(fields)
    progress['metrics'] = progress.pop('snapshot_metrics')
    progress['review_status_updated_at'] = utc_text()
    run = store.update_run(run_id, **fields)
    progress['status'] = run['status']
    if run['status']=='STOPPED':
        progress['phase'] = ('PAUSED' if run.get('stop_reason')=='USER_PAUSED' else
            'REVIEW_COMPLETE' if fields['snapshot_review']['status'] in {'PASSED','FAILED'} else 'STOPPED_REVIEW_PENDING')
    atomic_json(path, progress)
    return run


def snapshot_review_fields(run, metrics, quality, page_count):
    if run.get('collection_contract') != CONTRACT:
        raise ValueError('CURRENT_PAGE_SNAPSHOT_REQUIRED')
    final = run.get('status') == 'STOPPED'
    covered = (quality.get('reviewed_observations') == metrics['A_raw_observations']
               and quality.get('reviewed_capture_frames', 0) >= page_count > 0
               and quality.get('live_origin_required') is True)
    reviewed = final and covered and quality.get('review_status') not in {
        'DEFERRED_UNTIL_TASK_BOUNDARY', 'PENDING_COMBINED_CAPACITY_REVIEW'}
    passed = bool(reviewed and quality.get('central_full_dom_review_passed') is True
                  and quality.get('errors') == [])
    status = 'PASSED' if passed else 'FAILED' if reviewed else 'PENDING'
    accepted = passed and metrics['target_met'] is True
    acceptance = ('ACCEPTED_SNAPSHOT_CANDIDATES_QA_PASSED' if accepted else
                  'NOT_ACCEPTED_SNAPSHOT_QA_FAILED' if status == 'FAILED' else
                  'NOT_ACCEPTED_SNAPSHOT_QA_PENDING' if status == 'PENDING' else
                  'NOT_ACCEPTED_SNAPSHOT_QUANTITY_SHORTFALL')
    metrics = deepcopy(metrics)
    metrics['quantity_acceptance'] = ('QUANTITY_TARGET_MET' if metrics['target_met']
                                      else 'QUANTITY_TARGET_NOT_MET')
    metrics['acceptance'] = acceptance
    return {
        'snapshot_review': {'scope': CONTRACT, 'status': status, 'passed': passed,
                            'completed_at': run.get('finished_at') if reviewed else None,
                            'reviewed_observations': quality.get('reviewed_observations'),
                            'reviewed_capture_frames': quality.get('reviewed_capture_frames'),
                            'errors': quality.get('errors', [])},
        'snapshot_acceptance': acceptance, 'snapshot_metrics': metrics,
        'quality_gate_scope': CONTRACT, 'quality_gate_passed': bool(accepted),
        'quantity_target_met': metrics['target_met'], 'source_complete': False,
        'legacy_stage_acceptance_claimed': False,
        'SKU_identity_status': 'NOT_VERIFIED', 'production_acceptance': 'NOT_EVALUATED',
    }


def refresh_snapshot_review(store, run_id, output):
    """Correct display metadata from an already completed audit, without I/O transport.

    Keep a before/after report and bind the correction to the final audit event.
    A resumed/changed run requires another full audit instead of trusting old QA.
    """
    from pathlib import Path
    from ..multisource.storage import immutable_json, digest
    from .three_metrics import measure_three

    with store.run_lock(run_id):
        report = preview_snapshot_review(store, run_id)
        report['operation'] = 'SNAPSHOT_REVIEW_STATUS_CORRECTION'
        immutable_json(Path(output), report)
        publish_snapshot_review(store, run_id, report['corrected'])
        store.append_event(run_id, {'kind': 'SNAPSHOT_REVIEW_STATUS_CORRECTED',
                                    'report_path': str(Path(output).resolve()),
                                    'report_sha256': digest(report), 'new_network_requests': 0})
        return report


def preview_snapshot_review(store, run_id):
    """Compute a stopped-run metadata correction without locks or publication."""
    from ..multisource.storage import digest
    from .three_metrics import measure_three

    run = store.get_run(run_id)
    if run.get('collection_contract') != CONTRACT or run['status'] != 'STOPPED':
        raise ValueError('STOPPED_CURRENT_PAGE_SNAPSHOT_REQUIRED')
    events = store.events(run_id)
    evidence_events = [e for e in events if e.get('kind') != 'SNAPSHOT_REVIEW_STATUS_CORRECTED']
    event = evidence_events[-1] if evidence_events else {}
    quality = run.get('qa', {})
    if (event.get('kind') != 'V4_CENTRAL_DOM_REVIEW'
            or event.get('errors') != quality.get('errors') or not run.get('finished_at')):
        raise ValueError('FINAL_CURRENT_RUN_DOM_REVIEW_REQUIRED')
    rows = list(store.iter_observations(run_id))
    pages = store.pages(run_id)
    if (quality.get('reviewed_observations') != len(rows)
            or quality.get('reviewed_capture_frames', 0) < len(pages)):
        raise ValueError('FINAL_REVIEW_COVERAGE_MISMATCH')
    metrics = measure_three(rows, run_id, target_policy=run['plan']['target_policy'])
    fields = snapshot_review_fields(run, metrics, quality, len(pages))
    return {'run_id': run_id, 'operation': 'SNAPSHOT_REVIEW_STATUS_PREVIEW',
            'new_network_requests': 0, 'raw_evidence_modified': False,
            'audit_event': event, 'audit_event_sha256': digest(event), 'original_qa': quality,
            'previous': {key: run.get(key) for key in fields}, 'corrected': fields}
