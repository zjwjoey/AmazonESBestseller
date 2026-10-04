"""Completeness and readiness gates for identity snapshots."""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from .urls import is_valid_asin, normalize_asin


def audit_identity_records(records: Iterable[dict[str, Any]], raw_candidates: Iterable[dict[str, Any]] = (),
                           *, server_rendered_count: int | None = None,
                           client_recs_count: int = 0, acp_identity_count: int = 0,
                           expected_count: int | None = None,
                           evidence_files: Iterable[str] = ()) -> dict[str, Any]:
    records = [dict(row) for row in records]
    raw_candidates = [dict(row) for row in raw_candidates]
    valid_asins = {normalize_asin(row.get("asin")) for row in records
                   if is_valid_asin(row.get("asin"))}
    raw_valid = [normalize_asin(row.get("asin")) for row in raw_candidates
                 if is_valid_asin(row.get("asin"))]
    duplicate_count = max(0, len(raw_valid) - len(set(raw_valid)))
    conflicts = sum(str(row.get("identity_status") or "").upper() == "IDENTITY_CONFLICT"
                    for row in records)
    product_urls = sum(bool(row.get("product_url")) for row in records)
    derived = sum(str(row.get("product_url_source") or "").startswith("CANONICAL")
                  or row.get("product_url_source") in {"CLIENT_RECS_CANONICAL"}
                  for row in records)
    raw_confirmed = sum(row.get("product_url_source") == "RAW_HREF_CONFIRMED" for row in records)
    invalid_count = sum(bool(row.get("asin")) and not is_valid_asin(row.get("asin"))
                        for row in raw_candidates)
    unique_count = len(valid_asins)
    ready = (unique_count > 0 and product_urls == unique_count and conflicts == 0
             and invalid_count == 0)
    complete = ready and (expected_count is None or unique_count == int(expected_count))
    status = "IDENTITY_BLOCKED" if not ready else "IDENTITY_COMPLETE" if complete else "IDENTITY_PARTIAL"
    return {
        "server_rendered_count": int(server_rendered_count if server_rendered_count is not None else 0),
        "client_recs_count": int(client_recs_count),
        "acp_identity_count": int(acp_identity_count),
        "raw_candidate_count": len(raw_candidates),
        "valid_asin_count": unique_count,
        "unique_asin_count": unique_count,
        "duplicate_asin_count": duplicate_count,
        "invalid_asin_count": invalid_count,
        "product_url_count": product_urls,
        "url_derived_count": int(derived),
        "url_raw_confirmed_count": int(raw_confirmed),
        "identity_conflict_count": int(conflicts),
        "expected_count": expected_count,
        "identity_ready": bool(ready),
        "identity_complete": bool(complete),
        "status": status,
        "evidence_files": list(evidence_files),
    }
