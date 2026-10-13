"""Conservative, identifier-agnostic product-link normalization. No network."""
from __future__ import annotations

import re
import uuid
from urllib.parse import unquote_plus, urljoin, urlsplit, urlunsplit

NORMALIZER_VERSION = "link_url_v2.1"
_TRACKING = frozenset({"ref", "ref_", "tag", "linkCode", "camp", "creative", "ascsubtag"})
_PRODUCT_PATH = re.compile(r"/(?:dp|gp/product|gp/aw/d|product)/[^/?#]+", re.I)
_REDIRECT_PATH = re.compile(r"/(?:sspa/click|gp/slredirect|gp/redirect|gp/aw/redirect)(?:/|$)", re.I)
_REVIEW_ROUTE = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([^/?#]+)/?$", re.I)


def candidate_id(normalized_product_url: str) -> str:
    if not normalized_product_url:
        raise ValueError("NORMALIZED_PRODUCT_URL_REQUIRED")
    return "link_" + uuid.uuid5(uuid.NAMESPACE_URL, normalized_product_url).hex


def normalize_product_url(raw_url, base_url="https://www.amazon.es") -> dict:
    """Delete only documented tracking tokens; preserve opaque query bytes/order.

    Product path tokens are treated as opaque strings. Redirect destinations are
    never unwrapped or followed; their identity remains particularly uncertain.
    Fragment and unknown parameters remain because their meaning is unproven.
    """
    result = {"raw_product_url": raw_url, "absolute_product_url": None,
              "normalized_product_url": None, "is_valid": False,
              "reason": "PRODUCT_LINK_MISSING", "possible_duplicate_key": None,
              "url_identity_status": "INVALID_PRODUCT_LINK"}
    if not isinstance(raw_url, str) or not raw_url.strip():
        return result
    value = raw_url.strip()
    if value.startswith("#") or re.search(r"[\x00-\x20\x7f\\]", value):
        result["reason"] = "UNSAFE_URL_SYNTAX"
        return result
    try:
        absolute = urljoin(base_url, value)
        parts = urlsplit(absolute)
        result["absolute_product_url"] = absolute
        if (parts.scheme not in {"http", "https"} or parts.hostname not in {"amazon.es", "www.amazon.es"}
                or parts.username or parts.password or parts.port not in {None, 80, 443}):
            result["reason"] = "PRODUCT_URL_NOT_AMAZON_ES"
            return result
        redirect = bool(_REDIRECT_PATH.search(parts.path))
        if not _PRODUCT_PATH.search(parts.path) and not redirect:
            result["reason"] = "NOT_PRODUCT_CARD_LINK"
            return result
        path = re.sub(r"/ref=[^/]*$", "", parts.path)
        query = "&".join(token for token in parts.query.split("&")
                         if unquote_plus(token.split("=", 1)[0]) not in _TRACKING) if parts.query else ""
        normalized = urlunsplit((parts.scheme, "www.amazon.es", path, query, parts.fragment))
        # This coarse grouping is for review ONLY. It must never be a dedup key.
        possible = urlunsplit((parts.scheme, "www.amazon.es", path, "", ""))
        route = _REVIEW_ROUTE.search(path) if not redirect else None
        if route:
            # A literal opaque URL segment across known route aliases supplies
            # a conservative review group, not confirmed product identity.
            # No validation, decoding, casing or URL construction is performed.
            possible = "opaque_route_review_" + uuid.uuid5(uuid.NAMESPACE_URL,
                "www.amazon.es|" + route.group(1)).hex
        result.update(normalized_product_url=normalized, is_valid=True,
                      reason="REDIRECT_DESTINATION_UNVERIFIED" if redirect else None,
                      possible_duplicate_key=possible,
                      url_identity_status="IDENTITY_UNCONFIRMED")
    except (ValueError, TypeError):
        result["reason"] = "MALFORMED_PRODUCT_URL"
    return result
