"""Independent, finite V2 limits; no legacy phase or source quotas."""
from __future__ import annotations
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import math

LIMIT_FIELDS = ("max_requests_per_run", "max_pages_per_run", "max_requests_per_day",
                "max_raw_observations_per_run", "history_observation_limit")

def aware_time(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("AWARE_TIMESTAMP_REQUIRED")
    return result.astimezone(timezone.utc)

def utc_text(value=None):
    return aware_time(value).isoformat().replace("+00:00", "Z")

def madrid_day(value=None):
    return aware_time(value).astimezone(ZoneInfo("Europe/Madrid")).date().isoformat()

def validate_plan(plan):
    if plan.get("mode") != "HIGH_SPEED_V2" or plan.get("stage") not in {"T0", "T1", "T2", "T3"}:
        raise ValueError("HIGH_SPEED_STAGE_REQUIRED")
    for field in (*LIMIT_FIELDS, "target_unique_links"):
        if isinstance(plan.get(field), bool) or not isinstance(plan.get(field), int) or plan[field] <= 0:
            raise ValueError("FINITE_POSITIVE_BUDGET_REQUIRED:" + field)
    for field in ("max_duration_hours", "request_pacing_seconds"):
        value = plan.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("FINITE_DURATION_OR_PACING_REQUIRED:" + field)
    if plan["request_pacing_seconds"] < 2 or plan.get("parallelism", 1) != 1:
        raise ValueError("SERIAL_CONSERVATIVE_ACCESS_REQUIRED")
    if plan.get("category_cooldown_seconds") != 0:
        raise ValueError("V2_CATEGORY_COOLDOWN_MUST_BE_ZERO")
    if plan.get("global_access_gate") is not True:
        raise ValueError("GLOBAL_ACCESS_GATE_REQUIRED")
    for field in ("detail_collection", "translation", "image_download", "action_filtering"):
        if plan.get(field) is not False:
            raise ValueError("FORBIDDEN_COLLECTION_FEATURE:" + field)
    if not plan.get("sources"):
        raise ValueError("SOURCE_PLAN_REQUIRED")
    identifiers, physical_urls = set(), set()
    for source in plan["sources"]:
        if not source.get("source_id") or source["source_id"] in identifiers:
            raise ValueError("UNIQUE_SOURCE_ID_REQUIRED")
        identifiers.add(source["source_id"])
        if source.get("source_type") not in {"BEST_SELLERS", "MOVERS_SHAKERS", "NEW_RELEASES", "MOST_WISHED_FOR", "MOST_GIFTED"}:
            raise ValueError("VALID_SOURCE_TYPE_REQUIRED")
        if not source.get("page_urls"):
            raise ValueError("EXACT_SOURCE_PAGES_REQUIRED")
        from ..multisource.contracts import canonical_page_url
        for url in source["page_urls"]:
            key = canonical_page_url(url)
            if key in physical_urls:
                raise ValueError("DUPLICATE_PHYSICAL_SOURCE_PAGE")
            physical_urls.add(key)
    return plan

def budget_reason(plan, counters, *, now=None, started_at=None, kind="PAGE", raw_count=0):
    now = aware_time(now)
    if started_at and (now - aware_time(started_at)).total_seconds() >= plan["max_duration_hours"] * 3600:
        return "MAX_DURATION_REACHED"
    if counters.get("requests", 0) >= plan["max_requests_per_run"]:
        return "RUN_REQUEST_BUDGET_EXHAUSTED"
    if counters.get("requests_by_day", {}).get(madrid_day(now), 0) >= plan["max_requests_per_day"]:
        return "DAY_REQUEST_BUDGET_EXHAUSTED"
    if kind == "PAGE" and counters.get("page_requests", counters.get("pages", 0)) >= plan["max_pages_per_run"]:
        return "PAGE_BUDGET_EXHAUSTED"
    if counters.get("observations", 0) + raw_count > plan["max_raw_observations_per_run"]:
        return "RAW_OBSERVATION_BUDGET_EXHAUSTED"
    if counters.get("history_observations", 0) + raw_count > plan["history_observation_limit"]:
        return "HISTORY_STORAGE_BUDGET_EXHAUSTED"
    return None
