"""Measured throughput and explicitly conditional capacity estimates (no network)."""
from __future__ import annotations

from collections import deque
from datetime import datetime
import math


def percentile(values, probability):
    if not 0 <= probability <= 1:
        raise ValueError("Percentile probability outside 0..1")
    values = sorted(float(v) for v in values if v is not None)
    if not values:
        return None
    index = (len(values) - 1) * probability
    low = math.floor(index)
    return values[low] + (values[math.ceil(index)] - values[low]) * (index - low)


class RollingProgress:
    """Use monotonic elapsed task time, including waits; never count duplicate yield."""
    def __init__(self, target=30000, window_seconds=300):
        self.target = target
        self.window_seconds = window_seconds
        self.samples = deque([(0.0, 0)])
        self.epoch_started_elapsed_seconds = 0.0
        self.epoch_initial_unique_links = 0

    def start_epoch(self, elapsed_seconds, unique_count):
        """Start a rate sample after resume; retain the caller's original task clock."""
        elapsed_seconds = float(elapsed_seconds)
        if not math.isfinite(elapsed_seconds) or elapsed_seconds < 0 or type(unique_count) is not int or unique_count < 0:
            raise ValueError("Invalid sampling epoch baseline")
        self.samples.clear()
        self.samples.append((elapsed_seconds, unique_count))
        self.epoch_started_elapsed_seconds = elapsed_seconds
        self.epoch_initial_unique_links = unique_count

    def record(self, elapsed_seconds, unique_count):
        elapsed_seconds = float(elapsed_seconds)
        if not math.isfinite(elapsed_seconds) or elapsed_seconds < self.samples[-1][0] or unique_count < self.samples[-1][1]:
            raise ValueError("Progress must be monotonic")
        self.samples.append((elapsed_seconds, int(unique_count)))
        while len(self.samples) > 2 and self.samples[1][0] <= elapsed_seconds - self.window_seconds:
            self.samples.popleft()
        duration = elapsed_seconds - self.samples[0][0]
        rate = (unique_count - self.samples[0][1]) / duration if duration else 0
        remaining = max(0, self.target - unique_count)
        return {"elapsed_seconds": elapsed_seconds, "unique_links": unique_count,
                "rolling_unique_per_hour": rate * 3600,
                "eta_remaining_seconds": remaining / rate if rate else (0 if not remaining else None),
                "eta_is_estimate": True, "window_observed_seconds": duration,
                "sampling_epoch_started_elapsed_seconds": self.epoch_started_elapsed_seconds,
                "sampling_epoch_initial_unique_links": self.epoch_initial_unique_links}


def summarize_metrics(events, *, raw_count, valid_link_count, unique_count,
                      started_at, finished_at=None, target=30000):
    """Events contain measured duration_seconds and stage, not inferred request gaps."""
    if not 0 <= unique_count <= valid_link_count <= raw_count:
        raise ValueError("INCONSISTENT_OBSERVATION_AND_CANDIDATE_COUNTS")
    start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    finish = datetime.fromisoformat(finished_at.replace("Z", "+00:00")) if finished_at else None
    if start.tzinfo is None or (finish and finish.tzinfo is None):
        raise ValueError("Aware timestamps required")
    wall = (finish - start).total_seconds() if finish else None
    if wall is not None and wall < 0:
        raise ValueError("Finish precedes start")
    groups = {}
    for event in events:
        duration = event.get("duration_seconds")
        if duration is not None:
            if not math.isfinite(duration) or duration < 0:
                raise ValueError("Negative timing")
            groups.setdefault(event["stage"], []).append(duration)
    stages = {name: {"count": len(v), "total_seconds": sum(v),
                      "mean_seconds": sum(v) / len(v), "p50_seconds": percentile(v, .5),
                      "p95_seconds": percentile(v, .95)} for name, v in groups.items()}
    requests = [e for e in events if str(e.get("stage", "")).lower() in {"navigation", "acp", "page_request", "acp_request"}
                or e.get("request_kind") in {"PAGE", "ACP"}]
    failures = sum(e.get("success") is False or e.get("access_status", e.get("access_state", "NORMAL")) != "NORMAL" for e in requests)
    grouped_timings = {}
    for field in ("source_type", "research_category"):
        grouped = {}
        for event in events:
            label = event.get(field)
            duration = event.get("duration_seconds")
            if label is not None and duration is not None:
                grouped[label] = grouped.get(label, 0) + duration
        grouped_timings[field] = grouped
    return {"target_unique_links": target, "raw_observations": raw_count,
            "valid_link_observations": valid_link_count, "unique_link_candidates": unique_count,
            "normalization_success_rate": valid_link_count / raw_count if raw_count else None,
            "duplicate_rate_among_valid_links": 1 - unique_count / valid_link_count if valid_link_count else None,
            "total_wall_seconds": wall, "finished_at": finished_at, "started_at": started_at,
            "timing_scope": "FIRST_TASK_START_TO_FINAL_PERSIST_AND_BASIC_QA",
            "unique_per_hour": unique_count * 3600 / wall if wall and wall > 0 else None,
            "unique_per_minute": unique_count * 60 / wall if wall and wall > 0 else None,
            "request_count": len(requests), "request_failure_rate": failures / len(requests) if requests else None,
            "acp_request_count": sum(e.get("request_kind") == "ACP" or str(e.get("stage", "")).lower() in {"acp", "acp_request"} for e in requests),
            "stages": stages, "target_reached": unique_count >= target,
            "measured_stage_seconds_by_scope": grouped_timings,
            "ten_hour_target_passed": bool(finish and unique_count >= target and wall <= 36000)}


def estimate_capacity(sample, *, target=30000, max_hours=10):
    """Extrapolation does not verify unseen source supply or independent SKU identity."""
    raw = sample.get("raw_observations", 0)
    unique = sample.get("unique_link_candidates", 0)
    pages = sample.get("product_pages", 0)
    requests = sample.get("request_count", 0)
    sources = sample.get("source_count", 0)
    wall = sample.get("total_wall_seconds")
    if raw < 0 or unique < 0 or unique > raw or pages < 0 or requests < 0:
        raise ValueError("INCONSISTENT_CAPACITY_SAMPLE_COUNTS")
    yield_rate = unique / raw if raw else None
    per_page = unique / pages if pages else None
    per_request = unique / requests if requests else None
    per_source = unique / sources if sources else None
    seconds = target * wall / unique if unique and wall and wall > 0 else None
    known_capacity = sample.get("verified_source_capacity")
    origin = sample.get("benchmark_origin") or sample.get("origin", "UNKNOWN")
    return {"target_unique_links": target, "sample_raw_observations": raw,
            "sample_unique_link_candidates": unique, "observed_unique_yield": yield_rate,
            "estimated_required_raw_observations": math.ceil(target / yield_rate) if yield_rate else None,
            "estimated_required_pages": math.ceil(target / per_page) if per_page else None,
            "estimated_required_requests": math.ceil(target / per_request) if per_request else None,
            "estimated_required_sources": math.ceil(target / per_source) if per_source else None,
            "estimated_total_seconds": seconds,
            "estimation_basis_origin": origin,
            "ten_hour_live_feasible": (seconds <= max_hours * 3600 and known_capacity >= target)
                if origin == "LIVE_CAPTURE" and seconds is not None and known_capacity is not None else None,
            "ten_hour_throughput_projection": None if seconds is None else seconds <= max_hours * 3600,
            "verified_source_capacity": known_capacity,
            "capacity_gap": max(0, target - known_capacity) if known_capacity is not None else None,
            "source_capacity_sufficient": None if known_capacity is None else known_capacity >= target,
            "actual_t3_completed": False, "estimate_only": True,
            "identity_scope": "VALID_UNIQUE_LINK_CANDIDATES_NOT_PROVEN_INDEPENDENT_PURCHASABLE_SKUS",
            "limitations": ["Observed duplicate yield may change as sources expand.",
                           "Unknown source coverage cannot prove 30000-link capacity.",
                           "Offline wall time does not measure live browser throughput."]}


def historical_request_intervals(receipts):
    """Old P0 receipts stamp request starts, not HTTP completion durations."""
    dates = sorted(datetime.fromisoformat(r["observed_at"].replace("Z", "+00:00"))
                   for r in receipts if r.get("observed_at"))
    gaps = [(later - earlier).total_seconds() for earlier, later in zip(dates, dates[1:])]
    return {"attempted_requests": len(receipts), "interval_count": len(gaps),
            "interval_p50_seconds": percentile(gaps, .5), "interval_p95_seconds": percentile(gaps, .95),
            "exact_request_duration_seconds": None,
            "measurement_kind": "REQUEST_START_INTERVALS_NOT_HTTP_DURATIONS",
            "includes_pacing_wait_and_possible_human_pause": True,
            "can_certify_ten_hour_live_throughput": False}
