"""ASIN validation and deterministic Amazon.es product URL rules."""
from __future__ import annotations

import re
from urllib.parse import urljoin

ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
PRODUCT_URL_ASIN_RE = re.compile(
    r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})(?:[/?#]|$)", re.I
)
CANONICAL_HOST = "www.amazon.es"


def normalize_asin(value: object) -> str:
    return str(value or "").strip().upper()


def is_valid_asin(value: object) -> bool:
    return bool(ASIN_RE.fullmatch(normalize_asin(value)))


def asin_from_product_url(value: object) -> str:
    match = PRODUCT_URL_ASIN_RE.search(str(value or ""))
    return normalize_asin(match.group(1)) if match else ""


def canonical_product_url(asin: object) -> str:
    value = normalize_asin(asin)
    return f"https://{CANONICAL_HOST}/dp/{value}" if is_valid_asin(value) else ""


def absolute_raw_url(raw_href: object, source_url: object = "") -> str:
    """Resolve a raw href for diagnostics without changing the raw evidence."""
    raw = str(raw_href or "")
    if not raw:
        return ""
    base = str(source_url or f"https://{CANONICAL_HOST}")
    return urljoin(base, raw)
