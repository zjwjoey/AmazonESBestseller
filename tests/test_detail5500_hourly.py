import json
from datetime import datetime, timezone

from amazon_es_bestseller.collection.detail5500_hourly import persist_hour, build_report
from amazon_es_bestseller.multisource.storage import immutable_json, sealed


def setup(root):
    gate = root / "shared.json"
    immutable_json(gate, sealed(dict(state="NORMAL")))
    immutable_json(root / "detail_plan_5500.json", dict(run_id="run", tasks=[{},{}], shared_gate_path=str(gate)))
    immutable_json(root / "authorization.approved.json", dict(max_requests=6050, request_deadline="fixed", deadline="fixed2"))


def test_zero_progress_report_exists_when_collector_is_dead(tmp_path):
    setup(tmp_path)
    sample = dict(process=None, worker_status=dict(phase="STOPPED_REVIEW_REQUIRED", reason="UI_FAILED"))
    at = datetime(2026,10,16,14,30,tzinfo=timezone.utc)
    p = persist_hour(tmp_path, sample, at)
    r = json.loads(p.read_text(encoding="utf8"))
    assert "本小时无新增" in r["hourly_summary"] and r["success_details"] == 0
    assert r["suspected_stall"] and r["mode"] == "STOPPED_OR_NOT_COLLECTING"
    assert r["ETA_including_full_QA"] is None and p.with_suffix(".txt").exists()
    assert r["lanes"][1]["state"] == "NOT_ENABLED"
    assert persist_hour(tmp_path, sample, at) == p


def test_hourly_growth_counts_only_actual_success_not_reparse_or_cache(tmp_path):
    setup(tmp_path)
    at = datetime(2026,10,16,14,30,tzinfo=timezone.utc)
    immutable_json(tmp_path / "tasks/a/checkpoint.json", sealed(dict(status="SUCCESS", saved_at="2026-10-16T14:00:00+00:00")))
    immutable_json(tmp_path / "tasks/b/checkpoint.json", sealed(dict(status="REUSED_VALID_CACHE", saved_at="2026-10-16T14:10:00+00:00")))
    r=build_report(tmp_path, dict(process={"CommandLine":"test"}, worker_status=dict(phase="FINAL_COLLECTING")), at)
    assert r["success_details"] == 2 and r["cache_reused"] == 1
    assert r["real_new_details_last_hour"] == 1 and r["mode"] == "SERIAL"
    assert r["remaining_request_budget"] == 6050


def test_true_parallel_mode_reports_observed_HTTP_overlap_separately(tmp_path):
    setup(tmp_path)
    for i in range(3):
        immutable_json(tmp_path/f'receipts/{i}.json',sealed(dict(state='NORMAL',started_at=f'2026-10-16T14:00:0{i*3}+00:00',
            document_started_at=f'2026-10-16T14:00:0{i*3}+00:00',
            document_response_at=f'2026-10-16T14:00:{8+i*3:02d}+00:00',authorization_sha256='current')))
    sample=dict(process={'CommandLine':'test'},worker_status=dict(phase='FINAL_COLLECTING',
        local_processing_lanes=3,network_parallelism=3,authorization_sha256='current'))
    report=build_report(tmp_path,sample,datetime(2026,10,16,14,30,tzinfo=timezone.utc))
    assert report['mode']=='THREE_ASYNC_CAPTURE_LANES' and report['observed_document_HTTP_peak_inflight'] is None
    assert report['python_callback_document_event_peak']==3
    assert report['configured_network_parallelism']==3
    sample['worker_status']['authorization_sha256']='not_observed'
    report=build_report(tmp_path,sample)
    assert report['configured_network_parallelism']==3 and report['observed_document_HTTP_peak_inflight'] is None


def test_hourly_only_claims_HTTP_peak_from_complete_browser_timing(tmp_path):
    setup(tmp_path)
    from test_detail5500_browser_network_timing import example
    timing=example()
    immutable_json(tmp_path/'receipts/browser.json',sealed(dict(state='NORMAL',
        started_at='2026-10-16T14:00:00+00:00',authorization_sha256='current',browser_network_timing=timing)))
    sample=dict(process={'CommandLine':'test'},worker_status=dict(phase='FINAL_COLLECTING',
        network_parallelism=3,authorization_sha256='current'))
    report=build_report(tmp_path,sample)
    assert report['observed_document_HTTP_peak_inflight']==1
    assert report['observed_document_HTTP_body_peak_inflight']==1
    immutable_json(tmp_path/'receipts/unknown.json',sealed(dict(state='NORMAL',
        started_at='2026-10-16T14:00:05+00:00',authorization_sha256='current')))
    report=build_report(tmp_path,sample)
    assert report['observed_document_HTTP_peak_inflight'] is None
    assert report['browser_network_timing_evidence']['completed_receipts_without_browser_timing']==1
