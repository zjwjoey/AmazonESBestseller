import json
import pytest

from amazon_es_bestseller.highspeed.performance import RollingProgress, estimate_capacity, summarize_metrics, historical_request_intervals
from amazon_es_bestseller.highspeed.reports import export_rows_report, daily_history, source_coverage


def test_eta_uses_unique_yield_and_zero_is_unknown():
    progress = RollingProgress(target=30000, window_seconds=60)
    assert progress.record(60, 0)["eta_remaining_seconds"] is None
    assert progress.record(120, 100)["eta_remaining_seconds"] == 17940
    assert progress.record(180, 100)["eta_remaining_seconds"] is None
    with pytest.raises(ValueError):
        progress.record(170, 101)


def test_sampling_epoch_keeps_task_elapsed_but_excludes_existing_pool_and_pause():
    progress = RollingProgress(target=30000)
    progress.record(8580, 22369)
    progress.start_epoch(10800, 22369)
    baseline = progress.record(10800, 22369)
    assert baseline["rolling_unique_per_hour"] == 0
    assert baseline["eta_remaining_seconds"] is None
    result = progress.record(10910, 22549)
    assert result["elapsed_seconds"] == 10910
    assert result["window_observed_seconds"] == 110
    assert result["rolling_unique_per_hour"] == pytest.approx(180 * 3600 / 110)
    assert result["sampling_epoch_started_elapsed_seconds"] == 10800
    assert result["sampling_epoch_initial_unique_links"] == 22369
    no_yield = progress.record(11210, 22549)
    assert no_yield["rolling_unique_per_hour"] == 0
    assert no_yield["eta_remaining_seconds"] is None


def test_wall_includes_waits_and_qa_not_sum_of_http():
    result = summarize_metrics([{"stage": "navigation", "duration_seconds": 1}], raw_count=100,
        valid_link_count=90, unique_count=50, started_at="2026-10-13T00:00:00Z",
        finished_at="2026-10-13T01:00:00Z")
    assert result["total_wall_seconds"] == 3600
    assert result["unique_per_hour"] == 50
    assert result["stages"]["navigation"]["p95_seconds"] == 1
    assert not result["ten_hour_target_passed"]


def test_capacity_never_assumes_unseen_supply():
    result = estimate_capacity({"raw_observations": 100, "unique_link_candidates": 50,
        "product_pages": 2, "request_count": 4, "total_wall_seconds": 60})
    assert result["estimated_required_raw_observations"] == 60000
    assert result["estimated_required_pages"] == 1200
    assert result["estimated_required_requests"] == 2400
    assert result["source_capacity_sufficient"] is None
    assert result["actual_t3_completed"] is False


def test_empty_sample_does_not_predict_zero_time():
    result = estimate_capacity({})
    assert result["estimated_total_seconds"] is None
    assert result["estimated_required_pages"] is None


def test_report_preserves_all_observations_crosslists_and_identity(tmp_path):
    evidence = tmp_path / "raw.html"
    evidence.write_text("raw", encoding="utf-8")
    rows = [{"observation_id": str(i), "candidate_id": "same", "source_type": source,
             "research_category": category, "dedup_status": dedup,
             "url_identity_status": "IDENTITY_UNCONFIRMED", "evidence_path": str(evidence)}
            for i, (source, category, dedup) in enumerate([
                ("BEST_SELLERS", "厨房用品", "UNIQUE_LINK_CANDIDATE"),
                ("MOST_GIFTED", "居家生活", "NORMALIZED_DUPLICATE")])]
    first = export_rows_report(observations=rows, candidates=[{"candidate_id": "same"}], output_root=tmp_path)
    second = export_rows_report(observations=rows, candidates=[{"candidate_id": "same"}], output_root=tmp_path)
    assert first != second
    assert len((first / "observations.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    summary = json.loads((first / "summary.json").read_text(encoding="utf-8"))
    assert summary["independent_purchasable_skus_confirmed"] == 0
    assert summary["cross_list_reused_candidates"] == 1
    assert summary["cross_category_reused_candidates"] == 1
    assert len(json.loads((first / "snapshot_manifest.json").read_text())["raw_evidence"]) == 1
    assert evidence.read_text() == "raw"


def test_source_matrix_all75_unknown_without_proof():
    matrix = source_coverage([])
    assert len(matrix) == 75
    assert all(row["status"] == "UNVERIFIED" for row in matrix)


def test_daily_history_not_repeated_target_slots():
    rows = daily_history([{"run_id": "a", "local_date": "2026-10-12", "normalized_product_urls": ["one", "two"]},
                          {"run_id": "b", "local_date": "2026-10-13", "normalized_product_urls": ["two", "three"]}])
    assert rows[1]["historical_first_seen"] == 1
    assert rows[1]["cross_day_reused"] == 1


def test_history_before_window_is_still_historical():
    rows = daily_history([{"run_id": "a", "local_date": "2026-10-10", "normalized_product_urls": ["one"]},
                          {"run_id": "b", "local_date": "2026-10-12", "normalized_product_urls": ["one"]},
                          {"run_id": "c", "local_date": "2026-10-12", "normalized_product_urls": ["one", "two"]}], window_days=1)
    assert len(rows) == 2
    assert rows[0]["historical_first_seen"] == 0
    assert rows[1]["cross_day_reused"] == 1
    assert rows[1]["same_day_reused"] == 1


def test_history_derivation_and_fixture_do_not_count_as_real_new_day():
    rows = daily_history([
        {"run_id": "fixture", "local_date": "2026-10-12", "origin": "OFFLINE_FIXTURE", "normalized_product_urls": ["one"]},
        {"run_id": "actual", "local_date": "2026-10-13", "origin": "LIVE_CAPTURE", "normalized_product_urls": ["one"]},
        {"run_id": "reparse", "local_date": "2026-10-14", "origin": "LIVE_CAPTURE", "is_derivation": True,
         "normalized_product_urls": ["one"]}], window_days=1)
    assert [r["run_id"] for r in rows] == ["fixture", "actual"]
    assert rows[1]["historical_first_seen"] == 1
    assert rows[1]["cross_day_reused"] == 0


def test_old_p0_timestamps_do_not_measure_request_duration():
    result = historical_request_intervals([{"observed_at": "2026-10-13T00:00:00Z"},
                                            {"observed_at": "2026-10-13T01:00:00Z"}])
    assert result["interval_p50_seconds"] == 3600
    assert result["exact_request_duration_seconds"] is None
    assert not result["can_certify_ten_hour_live_throughput"]


def test_identity_status_label_without_proof_cannot_count_sku(tmp_path):
    report = export_rows_report(observations=[{"candidate_id": "a"}], candidates=[{"candidate_id": "a",
           "url_identity_status": "VERIFIED_INDEPENDENT_PURCHASABLE"}], output_root=tmp_path)
    result = json.loads((report / "summary.json").read_text())
    assert result["independent_purchasable_skus_confirmed"] == 0


def test_unproven_query_variants_keep_evidence_without_extra_target_slots(tmp_path):
    candidates = [{"candidate_id": f"c{i}", "normalized_product_url": f"https://www.amazon.es/product/item?unknown={i}",
                   "possible_duplicate_key": "https://www.amazon.es/product/item", "dedup_status": "POSSIBLE_DUPLICATE"}
                  for i in range(30)]
    observations = [{"candidate_id": c["candidate_id"]} for c in candidates]
    report = export_rows_report(observations=observations, candidates=candidates, output_root=tmp_path)
    result = json.loads((report / "summary.json").read_text())
    assert result["unique_link_candidates"] == 1
    assert result["normalized_unique_url_count"] == 30
    assert result["independent_purchasable_skus_confirmed"] == 0
    assert len((report / "unique_links.jsonl").read_text().splitlines()) == 30
    assert len((report / "eligible_target_links.jsonl").read_text().splitlines()) == 1


def test_budget_rejected_cards_are_exported_but_never_counted_into_target(tmp_path_factory):
    from test_highspeed_store import setup, page
    from amazon_es_bestseller.highspeed.reports import export_report
    from pathlib import Path
    tmp_path = tmp_path_factory.mktemp("p")
    store, plan, source = setup(tmp_path, max_raw_observations_per_run=1)
    with pytest.raises(ValueError, match="RAW_OBSERVATION_RUN_BUDGET"):
        page(store, plan, source, ["/product/one", "/product/two"])
    result = export_report(store, "run", tmp_path / "reports")
    directory = Path(result["report_directory"])
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["indexed_raw_observations"] == 0
    assert summary["raw_observations"] == 2
    assert summary["unindexed_preserved_raw_observations"] == 2
    assert summary["unique_link_candidates"] == 0
    assert len((directory / "unindexed_preserved_observations.jsonl").read_text().splitlines()) == 2
    assert len((directory / "observations.jsonl").read_text().splitlines()) == 2
    manifest = json.loads((directory / "snapshot_manifest.json").read_text(encoding="utf-8"))
    assert manifest["unindexed_raw_card_count"] == 2
    assert manifest["pending_unindexed_page_journals"]
    assert manifest["pending_unindexed_page_journals"][0]["journal_sha256"]
    assert summary["performance"]["finished_at"] is None


def test_report_counts_valid_dom_bound_independent_review_projections(tmp_path):
    from test_highspeed_reviews import example
    from amazon_es_bestseller.highspeed.reviews import validate_destination_review
    review, candidates, _ = example(tmp_path, decision="VERIFIED_INDEPENDENT_PURCHASABLE",
        finals=["https://www.amazon.es/product/final?choice=blue", "https://www.amazon.es/product/final?choice=red"])
    projection = validate_destination_review(review, candidates)
    candidates = [{**row, **projection[row["candidate_id"]], "possible_duplicate_key": "https://www.amazon.es/product/group"}
                  for row in candidates]
    observations = [{"candidate_id": row["candidate_id"]} for row in candidates]
    directory = export_rows_report(observations=observations, candidates=candidates, output_root=tmp_path)
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["independent_purchasable_skus_confirmed"] == 2
    assert summary["unique_link_candidates"] == 2
