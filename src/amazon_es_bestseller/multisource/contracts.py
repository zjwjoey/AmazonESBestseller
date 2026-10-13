"""Public dictionary contracts shared by the isolated snapshot modules."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import uuid
from zoneinfo import ZoneInfo

from . import SCHEMA_VERSION, MODULE_VERSION


class SourceType(str, Enum):
    BEST_SELLERS = "BEST_SELLERS"
    MOVERS_SHAKERS = "MOVERS_SHAKERS"
    NEW_RELEASES = "NEW_RELEASES"
    MOST_WISHED_FOR = "MOST_WISHED_FOR"
    MOST_GIFTED = "MOST_GIFTED"


SOURCE_ROOTS = {
    "BEST_SELLERS": "https://www.amazon.es/gp/bestsellers",
    "MOVERS_SHAKERS": "https://www.amazon.es/gp/movers-and-shakers",
    "NEW_RELEASES": "https://www.amazon.es/gp/new-releases",
    "MOST_WISHED_FOR": "https://www.amazon.es/gp/most-wished-for",
    "MOST_GIFTED": "https://www.amazon.es/gp/most-gifted",
}
RANK_SEMANTICS = {
    "BEST_SELLERS": "BEST_SELLER_LIST_POSITION",
    "MOVERS_SHAKERS": "MOVERS_SHAKERS_LIST_POSITION",
    "NEW_RELEASES": "NEW_RELEASE_LIST_POSITION",
    "MOST_WISHED_FOR": "WISH_LIST_POSITION",
    "MOST_GIFTED": "GIFT_LIST_POSITION",
}
RESEARCH_CATEGORIES = (
    "居家生活", "厨房用品", "家务用品", "办公与文具", "兴趣爱好",
    "DIY与五金", "玩具", "花园", "旅行", "个人护理", "食品饮料",
    "数码多媒体", "时尚", "体育用品", "宠物用品",
)
SOURCE_BUDGETS = dict(zip(SOURCE_ROOTS, (9000, 7500, 6000, 3750, 3750)))
PHASE_TARGETS = {"P0": 1500, "P1": 5000, "P2": 15000, "P3": 30000}
PRODUCTION_SOURCE_QUOTAS = dict(zip(SOURCE_ROOTS, (2750, 1375, 825, 275, 275)))
PRODUCTION_CATEGORY_QUOTAS = dict(zip(RESEARCH_CATEGORIES,
    (550, 650, 500, 300, 250, 500, 500, 350, 250, 400, 200, 300, 150, 250, 350)))
ORIGINS = {"LIVE_CAPTURE", "HISTORICAL_REPLAY", "OFFLINE_FIXTURE"}
SOURCE_STATUSES = {"AVAILABLE", "UNVERIFIED", "UNSUPPORTED", "TEMPORARILY_UNAVAILABLE", "BLOCKED"}
PAGE_STATUSES = {"COMPLETE", "PARTIAL", "UNVERIFIED", "BLOCKED", "FAILED"}


def timestamp(value: str | datetime | None = None) -> dict:
    """Require timezone-aware instants; derive Spain's DST-aware local date."""
    if value is None:
        instant = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        instant = value
    else:
        instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("A timezone-aware observation timestamp is required")
    utc = instant.astimezone(timezone.utc)
    return {"observed_at": utc.isoformat().replace("+00:00", "Z"),
            "local_date": utc.astimezone(ZoneInfo("Europe/Madrid")).date().isoformat(),
            "timezone": "Europe/Madrid"}


def observation_id(snapshot_id: str, source_id: str, page_url: str, slot_position: int) -> str:
    if not snapshot_id or not source_id or not page_url or int(slot_position) < 0:
        raise ValueError("Snapshot, source, page and nonnegative slot are required")
    # Identity uncertainty cannot alter a raw observation's identity.
    key = "|".join((snapshot_id, source_id, page_url, str(slot_position)))
    return "obs_" + uuid.uuid5(uuid.NAMESPACE_URL, key).hex


def make_observation(*, run_id: str, snapshot_id: str, source: dict,
                     page_url: str, page_number: int | None, slot_position: int,
                     observed_at: str, evidence_path: str, **values) -> dict:
    source_type = source["source_type"]
    source_type = source_type.value if isinstance(source_type, SourceType) else str(source_type)
    if source_type not in SOURCE_ROOTS:
        raise ValueError("Unknown source type")
    result = {"schema_version": SCHEMA_VERSION, "run_id": run_id,
        "snapshot_id": snapshot_id,
        "observation_id": observation_id(snapshot_id, source["source_id"], page_url, slot_position),
        "source_id": source["source_id"], "source_type": source_type,
        "source_url": source.get("actual_source_url") or source.get("root_url"),
        "page_url": page_url, "page_number": page_number,
        "source_category": source.get("amazon_category"),
        "research_category": source.get("research_category"),
        "browse_node_id": source.get("browse_node_id"),
        "category_path": source.get("category_path") or [],
        "rank_value": None, "rank_raw": None, "rank_semantics": RANK_SEMANTICS[source_type],
        "slot_position": int(slot_position), "product_url_raw": None,
        "raw_product_url": None, "normalized_product_url": None,
        "requested_product_url": None, "final_product_url": None,
        "observed_asin": None, "asin_observed": None, "asin_from_raw_url": None,
        "asin_from_card": None, "asin_from_final_url": None,
        "canonical_asin_observed": None, "parent_asin_observed": None,
        "product_id": None, "title_raw": None, "price_raw": None, "image_url_raw": None,
        "parser_version": MODULE_VERSION, "evidence_path": evidence_path,
        "access_status": "NORMAL", "identity_status": "UNCONFIRMED",
        "identity_evidence": [], "identity_conflict_reason": [], "review_status": "PENDING"}
    result.update(timestamp(observed_at))
    protected = {"observation_id", "schema_version", "run_id", "snapshot_id", "source_id",
                 "source_type", "page_url", "slot_position", "observed_at", "local_date", "timezone"}
    if protected.intersection(values):
        raise ValueError("Observation provenance cannot be overridden")
    result.update(values)
    return result
