"""Resume timing must include real repair/pause time without touching live runs."""
import json
import pytest
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.contracts import new_plan
from amazon_es_bestseller.highspeed.reports import export_report

START = "2026-10-13T15:52:22Z"
STOP = "2026-10-13T15:55:49Z"
END = "2026-10-14T03:52:22Z"


def fixture_store(tmp_path):
    store = RunStore(tmp_path / "store")
    store.begin_run(new_plan("resume_timing", target_unique_links=2), started_at=START)
    return store


def test_resume_clears_previous_end_and_preserves_original_start(tmp_path):
    store = fixture_store(tmp_path)
    store.update_run("resume_timing", status="STOPPED", finished_at=STOP, completed_at=STOP)
    resumed = store.update_run("resume_timing", status="RUNNING")
    assert resumed["ended_at"] is None
    assert resumed.get("finished_at") is None
    assert resumed.get("completed_at") is None
    assert resumed["started_at"] == "2026-10-13T15:52:22+00:00"
    finished = store.update_run("resume_timing", status="FINISHED", finished_at=END, completed_at=END)
    assert finished["ended_at"] == END
    store.close()


def test_new_finish_replaces_stale_stopped_end_without_resume_update(tmp_path):
    store = fixture_store(tmp_path)
    store.update_run("resume_timing", status="STOPPED", finished_at=STOP)
    finished = store.update_run("resume_timing", status="FINISHED", finished_at=END)
    assert finished["ended_at"] == END
    store.close()


def test_explicit_end_including_none_is_respected(tmp_path):
    store = fixture_store(tmp_path)
    assert store.update_run("resume_timing", status="FINISHED", ended_at=STOP, finished_at=END)["ended_at"] == STOP
    assert store.update_run("resume_timing", status="FINISHED", ended_at=None, finished_at=END)["ended_at"] is None
    store.close()


def test_old_worker_stale_end_report_uses_actual_latest_finish_and_full_pause(tmp_path):
    store = fixture_store(tmp_path)
    from amazon_es_bestseller.highspeed.adapters import parse_page
    html = '<h1>Los más vendidos</h1><div id="zg" data-expected-count="2">' + ''.join(
        f'<div class="ranking-card"><span class="zg-bdg-text">#{i+1}</span><a href="/product/fixture-{i}">Fixture</a></div>'
        for i in range(2)) + '</div>'
    saved = store.save_html(html)
    source = {"source_id": "root", "source_type": "BEST_SELLERS", "actual_source_url": "https://www.amazon.es/gp/bestsellers"}
    parsed = parse_page(html, source=source, run_id="resume_timing", page_url=source["actual_source_url"],
                        page_number=1, observed_at=START, evidence_path=saved["evidence_path"])
    store.commit_page("resume_timing", "page", parsed["page"], parsed["observations"])
    store.update_run("resume_timing", status="FINISHED", ended_at=STOP, finished_at=END, completed_at=END)
    result = export_report(store, "resume_timing", tmp_path / "reports")
    summary = json.loads(open(result["summary_path"], encoding="utf-8").read())
    assert summary["performance"]["total_wall_seconds"] == 43200
    assert summary["performance"]["finished_at"] == END
    assert summary["performance"]["unique_per_hour"] == pytest.approx(2 / 12)
    assert summary["performance"]["target_reached"]
    assert not summary["performance"]["ten_hour_target_passed"]
    assert store.get_run("resume_timing")["ended_at"] == STOP
    store.close()


def test_running_legacy_metadata_never_reports_stopped_time_as_completion(tmp_path):
    store = fixture_store(tmp_path)
    store.update_run("resume_timing", status="RUNNING", ended_at=STOP, finished_at=STOP, completed_at=STOP)
    result = export_report(store, "resume_timing", tmp_path / "reports")
    summary = json.loads(open(result["summary_path"], encoding="utf-8").read())
    assert summary["performance"]["finished_at"] is None
    assert summary["performance"]["total_wall_seconds"] is None
    store.close()


def test_user_paused_report_uses_pause_sample_not_stale_stop_or_completion(tmp_path):
    store = fixture_store(tmp_path)
    store.update_run("resume_timing", status="USER_PAUSED", ended_at=STOP,
        finished_at=STOP, completed_at=STOP, user_paused_at=END)
    result = export_report(store, "resume_timing", tmp_path / "reports")
    metric = json.loads(open(result["summary_path"], encoding="utf-8").read())["performance"]
    assert metric["total_wall_seconds"] == 43200
    assert metric["finished_at"] is None
    assert metric["sample_observed_at"] == END
    assert metric["run_completed"] is False
    assert metric["timing_scope"] == "FIRST_TASK_START_THROUGH_USER_PAUSE_NOT_COMPLETION"
    assert metric["eta_remaining_seconds"] is None
    assert not metric["ten_hour_target_passed"]
    assert store.get_run("resume_timing")["ended_at"] == STOP
    store.close()


@pytest.mark.parametrize("pause", [None, "bad", "2026-10-14T03:52:22", "2026-10-13T15:50:00Z"])
def test_invalid_or_missing_pause_does_not_fall_back_to_stale_end(tmp_path, pause):
    store = fixture_store(tmp_path)
    store.update_run("resume_timing", status="USER_PAUSED", ended_at=STOP,
        finished_at=STOP, user_paused_at=pause)
    result = export_report(store, "resume_timing", tmp_path / "reports")
    metric = json.loads(open(result["summary_path"], encoding="utf-8").read())["performance"]
    assert metric["total_wall_seconds"] is None
    assert metric["finished_at"] is None
    assert metric["sample_observed_at"] is None
    store.close()


@pytest.mark.parametrize("bad_finish", ["not-a-time", "2026-10-14T03:52:22", "2026-10-13T15:50:00Z"])
def test_invalid_finish_is_ignored_in_favor_of_valid_completion(tmp_path, bad_finish):
    store = fixture_store(tmp_path)
    store.update_run("resume_timing", status="FINISHED", ended_at=STOP,
                     finished_at=bad_finish, completed_at=END)
    result = export_report(store, "resume_timing", tmp_path / "reports")
    summary = json.loads(open(result["summary_path"], encoding="utf-8").read())
    assert summary["performance"]["total_wall_seconds"] == 43200
    assert summary["performance"]["finished_at"] == END
    store.close()
