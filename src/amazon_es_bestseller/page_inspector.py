import re
from dataclasses import dataclass

from bs4 import BeautifulSoup


ASIN_URL_RE = re.compile(r"/dp/([A-Z0-9]{10})(?:[/?#]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class PageInspection:
    product_card_candidate_count: int
    structured_data_kinds: tuple[str, ...]
    candidate_selector: str


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
    for script in soup.find_all("script"):
        script_type = (script.get("type") or "").lower()
        if script_type == "application/ld+json":
            kinds.append("json_ld")
        elif script_type in {"application/json", "text/json"}:
            kinds.append("embedded_json")
    return PageInspection(
        product_card_candidate_count=len(valid_candidates),
        structured_data_kinds=tuple(dict.fromkeys(kinds)),
        candidate_selector=selector,
    )
