"""Build deterministic, evidence-preserving detail navigation plans.

The ranking snapshot is immutable evidence.  Its product URL answers *which
page Amazon presented*; the candidate ASIN remains the durable identity used
for deduplication, joins and review.  These roles must not be conflated.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urljoin, urlsplit


_AMAZON_ORIGIN = "https://www.amazon.es"
_ALLOWED_HOSTS = {"amazon.es", "www.amazon.es"}
_PRODUCT_PATHS = ("/dp/", "/gp/product/", "/gp/aw/d/", "/product/")


def _asin(value: object) -> str:
    return str(value or "").strip().upper()


def _normalised_product_url(value: object) -> str:
    """Return a safe Amazon.es product URL, or an empty string.

    Relative ranking hrefs are intentionally supported.  ``urljoin`` is used
    only after rejecting protocol-relative / external host values, so a frozen
    ranking record cannot redirect the collector to another origin.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme.lower() != "https" or parsed.hostname not in _ALLOWED_HOSTS:
            return ""
        url = raw
    else:
        if not raw.startswith("/") or raw.startswith("//"):
            return ""
        url = urljoin(_AMAZON_ORIGIN, raw)
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        return ""
    # Amazon commonly retains a human-readable slug before ``/dp/<ASIN>``.
    # The other canonical paths are root-relative only.
    if not ("/dp/" in parsed.path or any(parsed.path.startswith(prefix) for prefix in _PRODUCT_PATHS[1:])):
        return ""
    return url


def _context_sort_key(row: Mapping[str, Any]) -> tuple:
    """Stable ordering for multiple frozen ranking contexts of one ASIN."""
    role = str(row.get("source_role") or "primary").lower()
    try:
        rank = int(row.get("bestseller_rank") or 10**9)
    except (TypeError, ValueError):
        rank = 10**9
    try:
        page = int(row.get("ranking_page_number") or 10**9)
    except (TypeError, ValueError):
        page = 10**9
    return (0 if role == "primary" else 1, rank, page,
            str(row.get("ranking_source_url") or ""),
            str(row.get("ranking_product_url_raw") or ""))


def build_detail_request_plan(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Return the one approved request URL and preserved ranking evidence.

    The raw ranking href is preferred over the normalised URL even when the
    embedded ASIN differs from the candidate ASIN.  That mismatch is evidence
    to audit after navigation, not a reason to discard the ranking page's
    product target before requesting it.
    """
    candidate_asin = _asin(candidate.get("asin") or candidate.get("ranking_asin"))
    contexts = [dict(item) for item in candidate.get("ranking_contexts") or []
                if isinstance(item, Mapping)]
    contexts.append(dict(candidate))
    contexts.sort(key=_context_sort_key)
    chosen: dict[str, Any] = {}
    for item in contexts:
        raw = _normalised_product_url(item.get("ranking_product_url_raw"))
        if raw:
            chosen = item
            request_url, source = raw, "RANKING_RAW"
            break
        normalised = _normalised_product_url(item.get("ranking_product_url_normalized"))
        if normalised:
            chosen = item
            request_url, source = normalised, "RANKING_NORMALIZED"
            break
    else:
        chosen = dict(candidate)
        request_url, source = _AMAZON_ORIGIN + "/dp/" + candidate_asin, "ASIN_FALLBACK"

    return {
        "candidate_asin": candidate_asin,
        "ranking_asin": _asin(chosen.get("ranking_asin") or candidate_asin),
        "preferred_request_url": request_url,
        "planned_request_url": request_url,
        "request_source": source,
        "ranking_product_url_raw": str(chosen.get("ranking_product_url_raw") or ""),
        "ranking_product_url_normalized": str(chosen.get("ranking_product_url_normalized") or ""),
        "ranking_link_asin": _asin(chosen.get("ranking_link_asin")),
        "ranking_link_identity_status": str(chosen.get("ranking_link_identity_status") or ""),
        "ranking_source_url": str(chosen.get("ranking_source_url") or ""),
        "ranking_page_number": chosen.get("ranking_page_number"),
        "bestseller_rank": chosen.get("bestseller_rank"),
        "source_role": str(chosen.get("source_role") or ""),
    }


__all__ = ["build_detail_request_plan"]
