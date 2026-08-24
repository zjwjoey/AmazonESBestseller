import re
import json
from dataclasses import dataclass

from bs4 import BeautifulSoup


ASIN_URL_RE = re.compile(r"/dp/([A-Z0-9]{10})(?:[/?#]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class PageInspection:
    product_card_candidate_count: int
    structured_data_kinds: tuple[str, ...]
    structured_data_fields: tuple[str, ...]
    candidate_selector: str


@dataclass(frozen=True)
class NavigationInspection:
    pagination_present: bool
    page_two_url_present: bool
    lazy_loading_present: bool


def inspect_html(html: str) -> PageInspection:
    soup = BeautifulSoup(html, "lxml")
    candidates = soup.select('[data-testid*="product-card"], [data-asin]')
    valid_candidates = [
        node for node in candidates if node.find("a", href=ASIN_URL_RE)
    ]
    selector = '[data-testid*="product-card"], [data-asin]' if valid_candidates else "a[href*='/dp/']"
    if not valid_candidates:
        valid_candidates = [
            anchor for anchor in soup.find_all("a", href=ASIN_URL_RE)
        ]

    kinds: list[str] = []
    structured_fields: set[str] = set()
    for script in soup.find_all("script"):
        script_type = (script.get("type") or "").lower()
        if script_type == "application/ld+json":
            kinds.append("json_ld")
        elif script_type in {"application/json", "text/json"}:
            kinds.append("embedded_json")
        if script_type in {"application/ld+json", "application/json", "text/json"}:
            try:
                payload = json.loads(script.string or script.get_text())
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict):
                structured_fields.update(str(key) for key in payload)
    return PageInspection(
        product_card_candidate_count=len(valid_candidates),
        structured_data_kinds=tuple(dict.fromkeys(kinds)),
        structured_data_fields=tuple(sorted(structured_fields)),
        candidate_selector=selector,
    )


def inspect_detail_fields(html: str) -> dict[str, bool]:
    """Report presence of detail-page fields without parsing private endpoints."""
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True).lower()
    selectors = {
        "title": "#productTitle, h1",
        "brand": "#bylineInfo, [id*='brand']",
        "asin": "[data-asin], #detailBullets_feature_div",
        "parent_asin": "[id*='parentasin'], [id*='parent_asin']",
        "price": ".a-price, [id*='price']",
        "original_price": ".a-text-price, [class*='original']",
        "rating": "#平均客户评分, [id*='averageCustomerReviews'], [class*='rating']",
        "review_count": "#acrCustomerReviewText, [id*='review']",
        "monthly_bought": "comprados el mes pasado",
        "availability": "#availability, [id*='availability']",
        "seller": "#sellerProfileTriggerId, [id*='seller']",
        "fulfilled_by": "[id*='fulfiller'], [id*='merchant-info']",
        "best_sellers_rank": "#SalesRank, [id*='salesrank'], best sellers rank",
        "browse_nodes": "[id*='browse'], browse node",
        "bullet_points": "#feature-bullets, [id*='bullet']",
        "main_image": "#landingImage, img[data-old-hires]",
        "variant_information": "[id*='variation'], [id*='variant']",
    }
    result: dict[str, bool] = {}
    for field, marker in selectors.items():
        if marker.startswith("#") or "," in marker or "[" in marker:
            selector_part = marker.split(",")[0]
            found = bool(soup.select(marker))
        else:
            found = marker in text
        result[field] = found
    return result


def inspect_navigation(html: str) -> NavigationInspection:
    soup = BeautifulSoup(html, "lxml")
    page_two_links = soup.select(
        "a[href*='pg=2'], a[href*='page=2'], a[aria-label*='Page 2'], a[aria-label*='Página 2']"
    )
    pagination = bool(page_two_links or soup.select(".a-pagination, nav[aria-label*='pagination']"))
    lazy_loading = bool(
        soup.select("[data-client-recs-list], [data-csa-c-type='widget']")
        or "lazy" in html.lower()
    )
    return NavigationInspection(
        pagination_present=pagination,
        page_two_url_present=bool(page_two_links),
        lazy_loading_present=lazy_loading,
    )
