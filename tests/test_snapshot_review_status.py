"""Status regressions only; no collection and no production acceptance."""
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest

from amazon_es_bestseller.highspeed.page_snapshot import CONTRACT
from amazon_es_bestseller.highspeed.snapshot_review import snapshot_review_fields, refresh_snapshot_review


def evidence():
    return ({'collection_contract': CONTRACT, 'status': 'STOPPED'},
            {'target_met': True, 'A_raw_observations': 24000,
             'acceptance': 'QUANTITY_TARGET_MET_QA_STILL_REQUIRED'},
            {'central_full_dom_review_passed': True, 'errors': [],
             'reviewed_observations': 24000, 'reviewed_capture_frames': 935,
             'live_origin_required': True})


def test_completed_page_audit_passes_its_own_gate_without_legacy_or_production_approval():
    run, metrics, quality = evidence()
    original = deepcopy((run, metrics, quality))
    result = snapshot_review_fields(run, metrics, quality, 935)
    assert result['snapshot_review']['status'] == 'PASSED'
    assert result['quality_gate_passed'] is True
    assert result['quality_gate_scope'] == CONTRACT
    assert result['snapshot_metrics']['acceptance'] == 'ACCEPTED_SNAPSHOT_CANDIDATES_QA_PASSED'
    assert result['legacy_stage_acceptance_claimed'] is False
    assert result['source_complete'] is False
    assert result['SKU_identity_status'] == 'NOT_VERIFIED'
    assert result['production_acceptance'] == 'NOT_EVALUATED'
    assert (run, metrics, quality) == original


@pytest.mark.parametrize('change,expected', [
    ({'central_full_dom_review_passed': False, 'errors': [{'code': 'DOM_SHA_MISMATCH'}]}, 'FAILED'),
    ({'errors': [{'code': 'OBSERVATION_DOM_MISMATCH'}]}, 'FAILED'),
    ({'reviewed_observations': 23999}, 'PENDING'),
    ({'reviewed_capture_frames': 934}, 'PENDING'),
    ({'live_origin_required': False}, 'PENDING'),
    ({'review_status': 'DEFERRED_UNTIL_TASK_BOUNDARY'}, 'PENDING'),
])
def test_incomplete_or_failed_audit_never_becomes_a_pass(change, expected):
    run, metrics, quality = evidence()
    quality.update(change)
    result = snapshot_review_fields(run, metrics, quality, 935)
    assert result['snapshot_review']['status'] == expected
    assert result['quality_gate_passed'] is False
    assert result['production_acceptance'] == 'NOT_EVALUATED'


def test_old_pass_while_running_and_quantity_shortfall_are_not_accepted():
    run, metrics, quality = evidence()
    run['status'] = 'RUNNING'
    result = snapshot_review_fields(run, metrics, quality, 935)
    assert result['snapshot_review']['status'] == 'PENDING'
    assert not result['quality_gate_passed']
    run['status'] = 'STOPPED'
    metrics['target_met'] = False
    result = snapshot_review_fields(run, metrics, quality, 935)
    assert result['snapshot_review']['passed'] is True
    assert result['quality_gate_passed'] is False
    assert result['snapshot_acceptance'] == 'NOT_ACCEPTED_SNAPSHOT_QUANTITY_SHORTFALL'


@pytest.mark.parametrize('last_event', [None, 'REQUEST', 'STOP', 'V4_CENTRAL_DOM_REVIEW_DEFERRED'])
def test_correction_rejects_missing_or_stale_review_before_any_write(tmp_path, last_event):
    run, _, quality = evidence()
    run.update(qa=quality, finished_at='2026-10-22T02:10:58Z')
    events = [] if last_event is None else [{'kind': last_event, 'errors': []}]
    store = SimpleNamespace(run_lock=lambda _: nullcontext(), get_run=lambda _: run,
                            events=lambda _: events)
    output = tmp_path / 'must_not_exist.json'
    with pytest.raises(ValueError, match='FINAL_CURRENT_RUN_DOM_REVIEW_REQUIRED'):
        refresh_snapshot_review(store, 'TEST_ONLY', output)
    assert not output.exists()
