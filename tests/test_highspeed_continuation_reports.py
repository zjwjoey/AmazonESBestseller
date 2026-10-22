"""Inherited cards must not masquerade as successor-phase network throughput."""
import json
from pathlib import Path
import pytest
from amazon_es_bestseller.highspeed.reports import export_report


class SnapshotStore:
    def __init__(self, fresh=False):
        self.run = {"run_id": "successor", "origin": "LIVE_CAPTURE", "status": "FINISHED",
            "started_at": "2026-10-13T20:43:38Z", "task_started_at": "2026-10-13T20:42:38Z",
            "ended_at": "2026-10-13T22:42:38Z", "target_unique_links": 10,
            "is_continuation": True, "parent_run_id": "parent", "imported_historical_observations": 3}
        self.observations = [
            {"candidate_id": "a", "origin": "HISTORICAL_REPLAY", "is_derivation": True},
            {"candidate_id": "b", "origin": "HISTORICAL_REPLAY", "is_derivation": True},
            {"candidate_id": "a", "origin": "HISTORICAL_REPLAY", "is_derivation": True}]
        self.candidates = [{"candidate_id": cid, "normalized_product_url": "https://www.amazon.es/product/" + cid} for cid in "ab"]
        self.page_rows = [{"origin": "HISTORICAL_REPLAY", "is_derivation": True, "raw_card_count": 3} for _ in range(137)]
        self.request_rows = []
        if fresh:
            self.observations += [{"candidate_id": "c", "origin": "LIVE_CAPTURE", "is_derivation": False},
                                  {"candidate_id": "a", "origin": "LIVE_CAPTURE", "is_derivation": False},
                                  {"origin": "LIVE_CAPTURE", "is_derivation": False}]
            self.candidates.append({"candidate_id": "c", "normalized_product_url": "https://www.amazon.es/product/c"})
            self.page_rows.append({"origin": "LIVE_CAPTURE", "is_derivation": False, "raw_card_count": 3,
                                   "observed_at": "2026-10-13T21:42:38Z"})
            self.request_rows = [{"run_id": "successor", "kind": "PAGE", "origin": "LIVE_CAPTURE"},
                                 {"run_id": "successor", "kind": "ACP", "origin": "LIVE_CAPTURE"}]
    def load_run(self, run_id):
        return {"run": self.run, "manifest": {}, "requests": self.request_rows}
    def iter_observations(self, run_id):
        return iter(self.observations)
    def iter_candidates(self, run_id):
        return iter(self.candidates)
    def pages(self, run_id):
        return self.page_rows
    def events(self, run_id):
        return [{"kind": "HISTORICAL_CONTINUATION_SEED", "seed_unique_links": 2, "parent_run_id": "parent"}]
    def get_run(self, run_id):
        assert run_id == "parent"
        return {"started_at": "2026-10-13T15:52:22Z"}


def reports(store, tmp_path):
    result = export_report(store, "successor", tmp_path)
    directory = Path(result["report_directory"])
    return json.loads((directory / "summary.json").read_text(encoding="utf-8")), json.loads((directory / "capacity_model.json").read_text(encoding="utf-8"))


def test_paused_continuation_uses_actual_pause_and_preserves_inherited_count_scope(tmp_path):
    store = SnapshotStore(fresh=True)
    store.run.update(status="USER_PAUSED", ended_at="2026-10-13T20:43:49Z",
                     user_paused_at="2026-10-13T22:42:38Z")
    summary, model = reports(store, tmp_path)
    metric = summary["performance"]
    assert metric["finished_at"] is None
    assert metric["sample_observed_at"] == "2026-10-13T22:42:38Z"
    assert metric["total_wall_seconds"] == metric["phase_wall_seconds"] == 7200
    assert metric["inherited_unique_links"] == 2
    assert metric["phase_new_unique_links"] == 1
    assert metric["unique_per_hour"] == .5
    assert metric["eta_remaining_seconds"] is None
    assert not metric["run_completed"]
    assert not metric["phase_ten_hour_target_passed"]
    assert model["user_paused"]
    assert model["estimated_total_seconds"] is None


def test_pause_before_successor_phase_start_is_unknown_even_after_parent_start(tmp_path):
    store = SnapshotStore(fresh=True)
    store.run.update(status="USER_PAUSED", user_paused_at="2026-10-13T20:41:38Z")
    summary, _ = reports(store, tmp_path)
    metric = summary["performance"]
    assert metric["finished_at"] is None
    assert metric["sample_observed_at"] is None
    assert metric["total_wall_seconds"] is None
    assert metric["phase_wall_seconds"] is None
    assert metric["cumulative_wall_seconds_including_idle_gap"] is None
    assert metric["unique_per_hour"] is None


def test_inherited_only_has_no_positive_phase_speed_or_capacity_projection(tmp_path):
    summary, model = reports(SnapshotStore(), tmp_path)
    metric = summary["performance"]
    assert metric["unique_per_hour"] is None
    assert metric["inherited_unique_links"] == 2
    assert metric["cumulative_unique_links"] == 2
    assert metric["phase_new_unique_links"] == 0
    assert metric["unique_per_hour"] is None
    assert metric["eta_remaining_seconds"] is None
    assert metric["parent_started_at"] == "2026-10-13T15:52:22Z"
    assert metric["phase_wall_seconds"] == 7200
    assert model["sample_unique_link_candidates"] == 0
    assert model["estimated_required_requests"] is None
    assert model["estimated_total_seconds"] is None
    assert model["fresh_live_product_pages"] == 0


def test_fresh_speed_excludes_inheritance_and_historical_pages(tmp_path):
    summary, model = reports(SnapshotStore(fresh=True), tmp_path)
    metric = summary["performance"]
    assert metric["unique_per_hour"] == .5
    assert metric["inherited_unique_links"] == 2
    assert metric["cumulative_unique_links"] == 3
    assert metric["phase_new_unique_links"] == 1
    assert metric["unique_per_hour"] == .5
    assert metric["eta_remaining_seconds"] == 14 * 3600
    assert metric["raw_observations_by_origin"] == {"HISTORICAL_REPLAY": 3, "LIVE_CAPTURE": 3}
    assert metric["derived_raw_observations"] == 3
    assert model["sample_raw_observations"] == 3
    assert model["sample_unique_link_candidates"] == 1
    assert model["fresh_live_request_count"] == 2
    assert model["fresh_live_product_pages"] == 1
    assert model["estimated_required_requests"] == 14
    assert model["source_capacity_sufficient"] is None


def test_non_continuation_preserves_original_report_counting(tmp_path):
    store = SnapshotStore(fresh=True)
    store.run["is_continuation"] = False
    store.events = lambda run_id: []
    summary, model = reports(store, tmp_path)
    metric = summary["performance"]
    assert metric["unique_per_hour"] == pytest.approx(3 * 3600 / 7140)
    assert metric["total_wall_seconds"] == 7140
    assert "inherited_unique_links" not in metric
    assert model["sample_unique_link_candidates"] == 3
    assert model["sample_raw_observations"] == 6


def test_live_looking_derived_cards_do_not_create_new_capture_speed(tmp_path):
    store = SnapshotStore(fresh=True)
    for row in store.observations:
        row["is_derivation"] = True
    summary, model = reports(store, tmp_path)
    assert summary["performance"]["phase_new_unique_links"] == 1
    assert summary["performance"]["phase_live_raw_observations"] == 0
    assert summary["performance"]["unique_per_hour"] is None
    assert summary["performance"]["eta_remaining_seconds"] is None
    assert model["sample_unique_link_candidates"] == 0
    assert model["estimated_total_seconds"] is None


def test_historical_live_page_label_cannot_supply_new_phase_page_count(tmp_path):
    store = SnapshotStore(fresh=True)
    store.page_rows = [{"origin": "LIVE_CAPTURE", "raw_card_count": 30,
                        "observed_at": "2026-10-13T15:52:22Z"} for _ in range(137)]
    _, model = reports(store, tmp_path)
    assert model["fresh_live_product_pages"] == 0
    assert model["estimated_required_pages"] is None


def test_new_request_absent_keeps_network_throughput_unknown(tmp_path):
    store = SnapshotStore(fresh=True)
    store.request_rows = []
    summary, model = reports(store, tmp_path)
    assert summary["performance"]["unique_per_hour"] is None
    assert summary["performance"]["eta_remaining_seconds"] is None
    assert model["fresh_live_request_count"] == 0
    assert model["estimated_required_requests"] is None
    assert model["estimated_total_seconds"] is None


def test_parent_start_missing_stays_unknown(tmp_path):
    store = SnapshotStore(fresh=True)
    def missing(run_id):
        raise ValueError("MISSING")
    store.get_run = missing
    summary, _ = reports(store, tmp_path)
    assert summary["performance"]["parent_started_at"] is None
    assert summary["performance"]["cumulative_wall_seconds_including_idle_gap"] is None
    assert summary["performance"]["unique_per_hour"] == .5
