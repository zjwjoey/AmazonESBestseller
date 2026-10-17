from datetime import datetime, timezone

import pytest

from amazon_es_bestseller.collection import detail_dual_runtime5500 as dual
from amazon_es_bestseller.collection import detail_parallel_runtime5500 as original
from amazon_es_bestseller.collection.detail5500_hourly import build_report
from amazon_es_bestseller.multisource.storage import immutable_json
from test_detail5500_dual_runtime import mode, ref
from test_detail5500_hourly import setup


@pytest.mark.parametrize('key,value', [
    ('network_parallelism', 3), ('local_processing_lanes', 3),
    ('visible_browser_windows', 3), ('target_requests_per_hour', 1001),
    ('target_requests_per_hour', None), ('manual_PAGE_budget_hold_references', []),
])
def test_dual_cannot_change_target_lanes_or_inherited_manual_holds(tmp_path, key, value):
    plan, auth, _, _ = mode(tmp_path)
    # Explicit non-empty original hold binding must remain exactly inherited.
    if key == 'manual_PAGE_budget_hold_references':
        bp = tmp_path/'base2.json'
        import json
        base = json.loads((tmp_path/'base.json').read_text())
        base[key] = [{'path': 'original', 'sha256': 'bound'}]
        immutable_json(bp, base)
        auth['parallel_base_authorization_reference'] = ref(bp)
    auth[key] = value
    with pytest.raises(ValueError): dual.validate_parallel_mode(tmp_path, plan, auth)


@pytest.mark.parametrize('key,value', [
    ('scope', 'THREE_IN_FLIGHT_DETAIL_SAME_FROZEN_RUN'),
    ('target_requests_per_hour', 1200), ('target_start_interval_seconds', 3),
    ('network_parallelism', 3),
])
def test_wrong_amendment_cannot_activate_dual(tmp_path, key, value):
    import json
    plan, auth, amendment, ap = mode(tmp_path)
    amendment[key] = value
    ap.write_text(json.dumps(amendment), encoding='utf8')
    auth['parallel_execution_approval_reference'] = ref(ap)
    with pytest.raises(ValueError): dual.validate_parallel_mode(tmp_path, plan, auth)


def test_original_three_lane_entry_rejects_dual_approval(tmp_path):
    plan, auth, _, _ = mode(tmp_path)
    with pytest.raises(ValueError): original.validate_parallel_mode(tmp_path, plan, auth)


def test_hourly_does_not_label_two_lanes_as_three_or_target_as_actual(tmp_path):
    setup(tmp_path)
    sample = dict(process={'CommandLine': 'dual'}, worker_status=dict(
        phase='FINAL_COLLECTING', local_processing_lanes=2, network_parallelism=2,
        target_requests_per_hour=1000, effective_dispatch_interval_seconds=3.6))
    report = build_report(tmp_path, sample, datetime(2026,10,17,tzinfo=timezone.utc))
    assert report['mode'] == 'TWO_ASYNC_CAPTURE_LANES_1000_TARGET'
    assert len(report['lanes']) == 2
    assert report['target_requests_per_hour'] == 1000
    assert report['configured_network_parallelism'] == 2
    assert report['observed_document_HTTP_peak_inflight'] is None
    assert report['real_new_details_last_hour'] == 0
