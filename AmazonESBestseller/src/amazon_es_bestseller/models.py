from dataclasses import dataclass
from enum import StrEnum


class AccessState(StrEnum):
    NORMAL = "NORMAL"
    BLOCKED = "BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    CHALLENGE = "CHALLENGE"
    NETWORK_ERROR = "NETWORK_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class AccessResult:
    state: AccessState
    reason: str | None = None


@dataclass(frozen=True)
class ProbeEvent:
    requested_url: str
    final_url: str | None
    page_title: str | None
    timestamp: str
    load_duration: float
    navigation_result: str
    access_state: AccessState
    body_length: int
    status: int | None = None
    reason: str | None = None


@dataclass
class RankingRecord:
    index: int | None = None
    snapshot_date: str | None = None
    snapshot_time: str | None = None
    root_category_es: str | None = None
    level2_category_es: str | None = None
    level3_category_es: str | None = None
    category_l1: str | None = None
    category_l2: str | None = None
    category_l3: str | None = None
    leaf_category: str | None = None
    browse_node_id: str | None = None
    rank: int | None = None
    rank_text: str | None = None
    rank_source: str | None = None
    category_rank: int | None = None
    asin: str | None = None
    asin_source: str | None = None
    title: str | None = None
    product_url: str | None = None
    image_url: str | None = None
    price: str | None = None
    currency: str | None = None
    rating: str | None = None
    review_count: str | None = None
    monthly_bought_text: str | None = None
    monthly_bought_value: int | None = None
    monthly_bought_raw: str | None = None
    monthly_bought_min: int | None = None
    brand: str | None = None
    prime: str | None = None
    discount: str | None = None
    original_price: float | None = None
    current_price: float | None = None
    discount_rate: float | None = None
    coupon: str | None = None
    deal: str | None = None
    availability: str | None = None
    sponsored: str | None = None
    badge: str | None = None
    variant_text: str | None = None
    delivery_text: str | None = None
    source_url: str | None = None
    source_category: str | None = None
    ranking_source_url: str | None = None
    collected_at: str | None = None


@dataclass
class ProductSummary:
    asin: str
    parent_asin: str | None = None
    parent_asin_status: str | None = None
    title_es: str | None = None
    brand: str | None = None
    price: str | None = None
    original_price: float | None = None
    current_price: float | None = None
    currency: str | None = None
    discount_rate: float | None = None
    rating: str | None = None
    review_count: str | None = None
    monthly_bought_text: str | None = None
    image_url: str | None = None
    product_url: str | None = None
    image_path: str | None = None
    image_download_status: str | None = None
    image_download_error: str | None = None
    details_json: str | None = None
    details: str | None = None
    specification: str | None = None
    date_first_available: str | None = None
    date_first_available_raw: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    ranking_count: int = 0
    best_rank: int | None = None
