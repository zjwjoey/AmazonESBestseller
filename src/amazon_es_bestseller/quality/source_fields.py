"""Evidence-backed source field audit before Spanish Master promotion."""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from ..models import is_valid_asin, normalize_asin
from .models import QualityStatus, check_result, issue


_JUNK = re.compile(r"(?:<[^>]+>|\b(?:javascript|cookie|captcha|robot check)\b)", re.I)


def _number(value: object) -> Decimal | None:
    text = str(value or "").strip().replace("€", "")
    # Spanish prices use a comma decimal separator and may use dots for
    # thousands. Ratings commonly arrive with a decimal point; never turn 4.2
    # into 42 while normalizing either representation.
    text = text.replace(".", "").replace(",", ".") if "," in text else text
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


def _valid_amazon_url(url: object, asin: str) -> bool:
    parsed = urlparse(str(url or ""))
    return (parsed.scheme == "https" and parsed.hostname in {"amazon.es", "www.amazon.es"}
            and (not asin or asin.casefold() in parsed.path.casefold()))


def audit_source_fields(products: Iterable[Mapping]) -> object:
    """Check identity, rank, URL, price and source-text invariants.

    The audit intentionally reports missing public evidence as reviewable rather
    than inventing it.  Identity, rank/BSR mixing, URL mismatch and invalid
    prices block promotion to Spanish Master.
    """
    issues = []
    rows = [dict(row) for row in products if isinstance(row, Mapping)]
    for row in rows:
        asin = normalize_asin(row.get("asin"))
        if not is_valid_asin(asin):
            issues.append(issue("source_fields", QualityStatus.BLOCK, "P0", "IDENTITY_CONFLICT", asin=asin,
                                message="invalid canonical ASIN"))
            continue
        for name in ("requested_asin", "ranking_asin", "detail_asin", "final_asin", "variation_asin"):
            value = normalize_asin(row.get(name))
            if value and value != asin:
                issues.append(issue("source_fields", QualityStatus.BLOCK, "P0", "IDENTITY_CONFLICT", asin=asin,
                                    message=f"{name} differs from canonical ASIN", evidence={name: value}))
        if row.get("bestseller_rank") in (None, "") and row.get("ranking_contexts"):
            issues.append(issue("source_fields", QualityStatus.REVIEW, "P1", "RANK_SLOT_MISSING", asin=asin,
                                message="ranking context has no bestseller rank"))
        if row.get("detail_bsr") not in (None, "") and row.get("bestseller_rank") == row.get("detail_bsr"):
            issues.append(issue("source_fields", QualityStatus.BLOCK, "P1", "RANK_BSR_MIXED", asin=asin,
                                message="detail BSR must not populate bestseller rank"))
        url = row.get("product_url") or row.get("final_url")
        if url and not _valid_amazon_url(url, asin):
            issues.append(issue("source_fields", QualityStatus.BLOCK, "P0", "SOURCE_FIELD_INVALID", asin=asin,
                                message="product URL is not an https Amazon.es ASIN URL", evidence={"url": url}))
        current = _number(row.get("current_price"))
        original = _number(row.get("original_price"))
        if row.get("current_price") not in (None, "") and (current is None or current <= 0):
            issues.append(issue("source_fields", QualityStatus.BLOCK, "P1", "SOURCE_FIELD_INVALID", asin=asin,
                                message="current price is invalid"))
        if original is not None and current is not None and original <= current:
            issues.append(issue("source_fields", QualityStatus.BLOCK, "P1", "SOURCE_FIELD_INVALID", asin=asin,
                                message="original price must exceed current price"))
        rating = _number(row.get("rating"))
        if row.get("rating") not in (None, "") and (rating is None or not (Decimal("0") <= rating <= Decimal("5"))):
            issues.append(issue("source_fields", QualityStatus.BLOCK, "P1", "SOURCE_FIELD_INVALID", asin=asin,
                                message="rating must be in range 0..5"))
        for field in ("title_es_raw", "specification", "product_details_es", "feature_bullets_es"):
            value = str(row.get(field) or "")
            if value and _JUNK.search(value):
                issues.append(issue("source_fields", QualityStatus.REVIEW, "P1", "MISPLACED", asin=asin,
                                    message=f"{field} contains UI/HTML junk", evidence={"field": field}))
    result = check_result("source_fields", issues, summary={"record_count": len(rows)})
    by_asin: dict[str, list] = defaultdict(list)
    for row in result.issues:
        by_asin[row.asin].append(row)
    result_dict = result.to_dict()
    result_dict["sku_status"] = {
        asin: ("SOURCE_BLOCKED" if any(item.status == "BLOCK" for item in values)
               else "SOURCE_REVIEW_REQUIRED" if values else "SOURCE_READY")
        for asin, values in by_asin.items()
    }
    for row in rows:
        asin = normalize_asin(row.get("asin"))
        if asin and asin not in result_dict["sku_status"]:
            result_dict["sku_status"][asin] = "SOURCE_READY"
    return result_dict


def source_gate(audit: Mapping) -> tuple[bool, str]:
    """A Spanish Master can be built only from source-ready audit results."""
    statuses = set((audit.get("sku_status") or {}).values())
    if "SOURCE_BLOCKED" in statuses:
        return False, "SOURCE_BLOCKED"
    if "SOURCE_REVIEW_REQUIRED" in statuses:
        return False, "SOURCE_REVIEW_REQUIRED"
    return True, "SOURCE_READY"


__all__ = ["audit_source_fields", "source_gate"]
