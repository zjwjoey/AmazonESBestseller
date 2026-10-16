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
