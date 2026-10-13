"""V2 defaults are independent of P0 and historical observation quotas."""
from ..multisource.contracts import SOURCE_ROOTS, RESEARCH_CATEGORIES, timestamp

STAGES = ("T0", "T1", "T2", "T3")
ORIGINS = ("OFFLINE_FIXTURE", "HISTORICAL_REPLAY", "LIVE_CAPTURE")
DEDUP_STATUSES = ("EXACT_DUPLICATE", "NORMALIZED_DUPLICATE", "VERIFIED_SAME_DESTINATION",
    "UNIQUE_LINK_CANDIDATE", "POSSIBLE_DUPLICATE", "IDENTITY_UNCONFIRMED", "INVALID_PRODUCT_LINK")
DEFAULT_CONFIG = {
    "schema_version": 2, "target_unique_links": 30000, "max_duration_hours": 10,
    "category_cooldown_seconds": 0, "request_pacing_seconds": 3, "parallelism": 1,
    "max_requests_per_run": 8000, "max_pages_per_run": 2000,
    "max_requests_per_day": 8000, "max_raw_observations_per_run": 150000,
    "history_observation_limit": 1000000, "global_access_gate": True,
    "detail_collection": False, "translation": False, "image_download": False,
    "action_filtering": False, "live_enabled": False,
}


def new_plan(run_id, *, stage="T0", origin="OFFLINE_FIXTURE", **overrides):
    if stage not in STAGES or origin not in ORIGINS:
        raise ValueError("INVALID_HIGH_SPEED_SCOPE")
    return {**DEFAULT_CONFIG, "run_id": run_id, "snapshot_id": run_id,
        "stage": stage, "origin": origin, "sources": [], "pages": [], **overrides}
