"""Opt-in link snapshot contract; no ASIN extraction occurs in this parser."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..monitoring.ranking_identity.adapters import HREF_SELECTORS, select_ranking_cards
from ..normalization.category import category_levels
from .ranking import _browse_node_id, _extract_category_trail


def safe_product_link(value: str) -> str:
    absolute = urljoin("https://www.amazon.es", str(value or ""))
    try:
        parts = urlsplit(absolute)
        port = parts.port
    except ValueError:
        return ""
    if (parts.scheme != "https" or parts.hostname not in {"amazon.es", "www.amazon.es"}
            or parts.username or parts.password or port not in {None, 443}
            or not re.search(r"/(?:dp|gp/product|gp/aw/d|product)/[^/]+", parts.path)):
        return ""
    return absolute


def parse_ranking_links(html: str, source_url: str, page: int, observed_at: str,
                        research_category: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    trail = _extract_category_trail(soup)
    l1, l2, l3, leaf = category_levels(trail)
    rows = []
    for index, card in enumerate(select_ranking_cards(soup, source_url)):
        link = next((card.select_one(selector) for selector in HREF_SELECTORS
                     if card.select_one(selector) is not None), None)
        raw = str(link.get("href") or "") if link else ""
        url = safe_product_link(raw)
        badge = card.select_one(".zg-bdg-text, span.a-badge-text")
        rank_raw = badge.get_text(" ", strip=True) if badge else ""
        match = re.search(r"#\s*(\d+)", rank_raw)
        rank = int(match.group(1)) if match else None
        image = card.select_one("img")
        token = "\n".join((source_url, str(page), str(index), raw, observed_at))
        rows.append({"snapshot_item_id": hashlib.sha256(token.encode("utf-8")).hexdigest(),
                     "ranking_product_url_raw": raw, "product_link": url,
                     "ranking_source_url": source_url, "ranking_page_number": page,
                     "bestseller_rank": rank, "bestseller_rank_raw": rank_raw,
                     "research_category": research_category, "observed_at": observed_at,
                     "category_l1": l1, "category_l2": l2, "category_l3": l3,
                     "leaf_category": leaf, "category_trail": trail,
                     "browse_node_id": _browse_node_id(source_url, soup),
                     "ranking_title_raw": str(image.get("alt") or "") if image else "",
                     "ranking_image_url": str(image.get("src") or "") if image else "",
                     "snapshot_audit": "PASS" if url and rank else "BLOCK"})
    return rows
