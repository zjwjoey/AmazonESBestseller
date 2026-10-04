"""Deterministic resolution of raw identity candidates."""
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Iterable

from .urls import canonical_product_url, is_valid_asin, normalize_asin


def _slot_key(candidate: dict[str, Any]) -> tuple[str, str, str, int] | None:
    """Return a stable ranking slot key for cross-evidence comparison."""
    page = str(candidate.get("page_instance_id") or "")
    if not page:
        page = "page:%s|url:%s" % (
            candidate.get("page_number") if candidate.get("page_number") is not None else "",
            candidate.get("source_url") or "",
        )
    rank = candidate.get("rank")
    if rank is not None:
        try:
            return (page, "rank", "", int(rank))
        except (TypeError, ValueError):
            pass
    return None


def _candidate_asin(candidate: dict[str, Any]) -> str:
    for value in (candidate.get("card_asin"), candidate.get("href_asin"),
                  candidate.get("asin")):
        normalized = normalize_asin(value)
        if is_valid_asin(normalized):
            return normalized
    return ""


def resolve_candidates(candidates: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return deduplicated identity records and all raw candidate evidence."""
    records: OrderedDict[str, dict[str, Any]] = OrderedDict()
    raw_candidates = [dict(candidate) for candidate in candidates]
    slot_asins: dict[tuple[str, str, str, int], set[str]] = {}
    for candidate in raw_candidates:
        slot = _slot_key(candidate)
        asin = _candidate_asin(candidate)
        if slot is not None and asin:
            slot_asins.setdefault(slot, set()).add(asin)
    for candidate in raw_candidates:
        card_asin = normalize_asin(candidate.get("card_asin"))
        href_asin = normalize_asin(candidate.get("href_asin"))
        if not card_asin and candidate.get("asin_source", "").endswith("_ASIN"):
            card_asin = normalize_asin(candidate.get("asin"))
        valid_card = is_valid_asin(card_asin)
        valid_href = is_valid_asin(href_asin)
        slot = _slot_key(candidate)
        slot_conflict_asins = sorted(slot_asins.get(slot, set())) if slot else []
        cross_source_conflict = len(slot_conflict_asins) > 1
        if valid_card and valid_href and card_asin != href_asin:
            asin = card_asin
            status = "IDENTITY_CONFLICT"
            product_url = None
            product_url_source = None
        else:
            asin = card_asin if valid_card else href_asin
            if not asin:
                asin = normalize_asin(candidate.get("asin"))
            if not is_valid_asin(asin):
                candidate["identity_status"] = "INVALID_ASIN"
                continue
            if candidate.get("raw_href") and valid_href:
                status = "CONFIRMED"
                product_url = canonical_product_url(asin)
                product_url_source = "RAW_HREF_CONFIRMED"
            else:
                status = ("CLIENT_RECS_ONLY" if candidate.get("evidence_source") == "CLIENT_RECS"
                          else "ACP_CONFIRMED" if candidate.get("evidence_source") == "ACP"
                          else "ASIN_CONFIRMED_URL_DERIVED")
                product_url = canonical_product_url(asin)
                product_url_source = (
                    "ACP_HREF" if valid_href and candidate.get("evidence_source") == "ACP"
                    else "CLIENT_RECS_CANONICAL" if candidate.get("evidence_source") == "CLIENT_RECS"
                    else "CANONICAL_FROM_ASIN"
                )
        if cross_source_conflict:
            # Different trusted evidence for the same ranking slot must not
            # become two executable identities. Preserve both records, but
            # block the slot and expose the competing ASINs.
            status = "IDENTITY_CONFLICT"
            product_url = None
            product_url_source = None
        record = {
            "asin": asin,
            "product_url": product_url,
            "product_url_raw": candidate.get("raw_href"),
            "product_url_source": product_url_source,
            "asin_source": candidate.get("asin_source") or "UNKNOWN",
            "identity_status": status,
            "rank": candidate.get("rank"),
            "rank_raw": candidate.get("rank_raw"),
            "page_number": candidate.get("page_number"),
            "card_index": candidate.get("card_index"),
            "source_url": candidate.get("source_url") or "",
            "evidence_type": candidate.get("evidence_source") or "",
            "evidence_file": candidate.get("evidence_file"),
            "identity_evidence": [{
                "card_asin": card_asin or None,
                "href_asin": href_asin or None,
                "raw_href": candidate.get("raw_href"),
                "asin_source": candidate.get("asin_source"),
                "evidence_source": candidate.get("evidence_source"),
                "page_instance_id": candidate.get("page_instance_id"),
                "representation_type": candidate.get("representation_type"),
            }],
            "ranking_contexts": [{
                "rank": candidate.get("rank"),
                "rank_raw": candidate.get("rank_raw"),
                "page_number": candidate.get("page_number"),
                "source_url": candidate.get("source_url") or "",
                "evidence_file": candidate.get("evidence_file"),
                "page_instance_id": candidate.get("page_instance_id"),
                "representation_type": candidate.get("representation_type"),
            }],
        }
        if valid_card and valid_href and card_asin != href_asin:
            record["conflict_asins"] = {"card_asin": card_asin, "href_asin": href_asin}
        if cross_source_conflict:
            record["conflict_asins"] = {
                "ranking_slot": list(slot_conflict_asins),
                "page_instance_id": slot[0] if slot else None,
                "rank": candidate.get("rank"),
            }
        existing = records.get(asin)
        if existing is None:
            records[asin] = record
        else:
            existing["identity_evidence"].extend(record["identity_evidence"])
            existing["ranking_contexts"].extend(record["ranking_contexts"])
            if existing.get("identity_status") != "IDENTITY_CONFLICT" and status == "IDENTITY_CONFLICT":
                # Promote the conflict status without discarding the lower
                # priority evidence already retained for this ASIN.
                for key in ("product_url", "product_url_raw", "product_url_source",
                            "asin_source", "identity_status", "conflict_asins",
                            "rank", "rank_raw", "page_number", "card_index",
                            "source_url", "evidence_type", "evidence_file"):
                    if key in record:
                        existing[key] = record[key]
    return list(records.values()), raw_candidates
