"""Completeness and readiness gates for identity snapshots."""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from .urls import is_valid_asin, normalize_asin


def audit_identity_records(records: Iterable[dict[str, Any]], raw_candidates: Iterable[dict[str, Any]] = (),
                           *, server_rendered_count: int | None = None,
                           client_recs_count: int = 0, acp_identity_count: int = 0,
                           expected_count: int | None = None,
                           expected_count_source: str = "UNKNOWN",
                           evidence_files: Iterable[str] = (),
                           supplemental_parse_statuses: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    records = [dict(row) for row in records]
    raw_candidates = [dict(row) for row in raw_candidates]
    valid_asins = {normalize_asin(row.get("asin")) for row in records
                   if is_valid_asin(row.get("asin"))}
    raw_valid = [normalize_asin(row.get("asin")) for row in raw_candidates
                 if is_valid_asin(row.get("asin"))]
    duplicate_count = max(0, len(raw_valid) - len(set(raw_valid)))
    duplicate_slots = Counter(
        (row.get("page_instance_id") or row.get("page_number"),
         row.get("rank"), row.get("evidence_source"))
        for row in raw_candidates
        if is_valid_asin(row.get("asin")) and row.get("rank") is not None
    )
    conflicts = sum(str(row.get("identity_status") or "").upper() == "IDENTITY_CONFLICT"
                    for row in records)
    product_urls = sum(bool(row.get("product_url")) for row in records)
    derived = sum(str(row.get("product_url_source") or "").startswith("CANONICAL")
                  or row.get("product_url_source") in {"CLIENT_RECS_CANONICAL"}
                  for row in records)
    raw_confirmed = sum(row.get("product_url_source") == "RAW_HREF_CONFIRMED" for row in records)
    invalid_count = sum(
        any(bool(row.get(field)) and not is_valid_asin(row.get(field))
            for field in ("asin", "card_asin", "href_asin"))
        for row in raw_candidates
    )
    unique_count = len(valid_asins)
    ready = (unique_count > 0 and product_urls == unique_count and conflicts == 0
             and invalid_count == 0)
    complete = bool(ready and expected_count is not None
                    and unique_count == int(expected_count))
    if not ready:
        status = "IDENTITY_BLOCKED"
    elif complete:
        status = "IDENTITY_COMPLETE"
    elif expected_count is None:
        status = "IDENTITY_COMPLETENESS_UNKNOWN"
    else:
        status = "IDENTITY_PARTIAL"
    parse_statuses = [dict(item) for item in supplemental_parse_statuses]
    return {
        "server_rendered_count": int(server_rendered_count if server_rendered_count is not None else 0),
        "client_recs_count": int(client_recs_count),
        "acp_identity_count": int(acp_identity_count),
        "raw_candidate_count": len(raw_candidates),
        "valid_asin_count": unique_count,
        "unique_asin_count": unique_count,
        "duplicate_asin_count": duplicate_count,
        "duplicate_identity_evidence_count": duplicate_count,
        "duplicate_ranking_slot_count": sum(max(0, count - 1)
                                             for count in duplicate_slots.values()),
        "invalid_asin_count": invalid_count,
        "product_url_count": product_urls,
        "url_derived_count": int(derived),
        "url_raw_confirmed_count": int(raw_confirmed),
        "identity_conflict_count": int(conflicts),
        "expected_count": expected_count,
        "expected_count_source": expected_count_source if expected_count is not None else "UNKNOWN",
        "identity_ready": bool(ready),
        "identity_complete": bool(complete),
        "status": status,
        "evidence_files": list(evidence_files),
        "supplemental_parse_status": parse_statuses,
    }
