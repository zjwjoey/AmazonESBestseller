"""Completeness and readiness gates for identity snapshots."""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from .urls import is_valid_asin, normalize_asin


def evaluate_authority(*, ranking_complete: bool, slots_complete: bool,
                       identity_ready: bool, identity_complete: bool,
                       access_normal: bool = True, no_conflicts: bool = True,
                       no_rank_gap: bool = True,
                       no_duplicate_rank_slot: bool = True) -> dict[str, Any]:
    """Apply one final promotion gate to ranking and identity evidence."""
    gates = {
        "ranking_complete": bool(ranking_complete),
        "ranking_slots_complete": bool(slots_complete),
        "identity_ready": bool(identity_ready),
        "identity_complete": bool(identity_complete),
        "access_normal": bool(access_normal),
        "no_conflicts": bool(no_conflicts),
        "no_rank_gap": bool(no_rank_gap),
        "no_duplicate_rank_slot": bool(no_duplicate_rank_slot),
    }
    failed = [name for name, passed in gates.items() if not passed]
    return {"authority_status": "AUTHORITATIVE" if not failed else "BLOCKED",
            "authoritative": not failed, "authority_gates": gates,
            "authority_block_reasons": failed}


def audit_identity_records(records: Iterable[dict[str, Any]], raw_candidates: Iterable[dict[str, Any]] = (),
                           *, server_rendered_count: int | None = None,
                           client_recs_count: int = 0, acp_identity_count: int = 0,
                           expected_count: int | None = None,
                           expected_count_source: str = "UNKNOWN",
                           expected_slot_count: int | None = None,
                           evidence_files: Iterable[str] = (),
                           supplemental_parse_statuses: Iterable[dict[str, Any]] = (),
                           ranking_audit: dict[str, Any] | None = None,
                           access_normal: bool = True) -> dict[str, Any]:
    records = [dict(row) for row in records]
    raw_candidates = [dict(row) for row in raw_candidates]
    valid_asins = {normalize_asin(row.get("asin")) for row in records
                   if is_valid_asin(row.get("asin"))}
    raw_valid = [normalize_asin(row.get("asin")) for row in raw_candidates
                 if is_valid_asin(row.get("asin"))]
    duplicate_count = max(0, len(raw_valid) - len(set(raw_valid)))
    duplicate_slots = Counter(
        (row.get("page_instance_id") or row.get("page_number"),
         row.get("rank"), normalize_asin(row.get("asin")),
         row.get("representation_type") or "UNKNOWN")
        for row in raw_candidates
        if is_valid_asin(row.get("asin")) and row.get("rank") is not None
    )
    ranking_slots: dict[tuple, set[str]] = {}
    for row in raw_candidates:
        asin = normalize_asin(row.get("asin"))
        rank = row.get("rank")
        if not is_valid_asin(asin) or rank is None:
            continue
        page = row.get("page_instance_id") or (
            row.get("page_number"), row.get("source_url") or "")
        try:
            rank = int(rank)
        except (TypeError, ValueError):
            continue
        ranking_slots.setdefault((page, rank), set()).add(asin)
    slot_conflict_count = sum(1 for asins in ranking_slots.values() if len(asins) > 1)
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
    product_identity_ready = (unique_count > 0 and product_urls == unique_count
                               and conflicts == 0 and invalid_count == 0)
    if expected_count is not None:
        product_identity_complete = bool(product_identity_ready
                                         and unique_count == int(expected_count))
    else:
        # For multiple ranking contexts there is no valid global expected
        # product count: one ASIN may occupy slots in several contexts. The
        # reviewed per-context slot total is the completeness evidence, while
        # ``unique_count`` remains the product identity count.
        product_identity_complete = bool(product_identity_ready
                                         and expected_slot_count is not None
                                         and len(ranking_slots) == int(expected_slot_count))
    ranking_slot_complete = bool(ranking_slots) and slot_conflict_count == 0 \
        and (expected_slot_count is None or len(ranking_slots) == int(expected_slot_count))
    # Keep the historical ``identity_ready`` meaning for callers that only
    # have product evidence.  The final promotion gate below requires the
    # separate ranking-slot gate as well.
    ready = bool(product_identity_ready)
    complete = product_identity_complete
    if not product_identity_ready or slot_conflict_count:
        status = "IDENTITY_BLOCKED"
    elif complete:
        status = "IDENTITY_COMPLETE"
    elif expected_count is None:
        status = "IDENTITY_COMPLETENESS_UNKNOWN"
    else:
        status = "IDENTITY_PARTIAL"
    parse_statuses = [dict(item) for item in supplemental_parse_statuses]
    ranking_audit = dict(ranking_audit or {})
    authority = evaluate_authority(
        ranking_complete=bool(ranking_audit.get("page_complete",
                                               ranking_audit.get("ranking_complete", False))),
        slots_complete=ranking_slot_complete,
        identity_ready=ready, identity_complete=complete,
        access_normal=access_normal and str(ranking_audit.get("access_state", "NORMAL")).upper()
        in {"NORMAL", "SUCCESS", "AUTHORITATIVE", "COMPLETE"},
        no_conflicts=conflicts == 0 and slot_conflict_count == 0,
        no_rank_gap=not bool(ranking_audit.get("rank_gap_count")),
        no_duplicate_rank_slot=not bool(ranking_audit.get("rank_duplicate_count"))
    )
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
        "ranking_slot_conflict_count": int(slot_conflict_count),
        "ranking_slot_count": len(ranking_slots),
        "invalid_asin_count": invalid_count,
        "product_url_count": product_urls,
        "url_derived_count": int(derived),
        "url_raw_confirmed_count": int(raw_confirmed),
        "identity_conflict_count": int(conflicts),
        "expected_count": expected_count,
        "expected_slot_count": expected_slot_count,
        "expected_count_source": expected_count_source if expected_count is not None else "UNKNOWN",
        "product_identity_ready": bool(product_identity_ready),
        "product_identity_complete": bool(product_identity_complete),
        "ranking_slot_complete": bool(ranking_slot_complete),
        "identity_ready": bool(ready),
        "identity_complete": bool(complete),
        "status": status,
        "evidence_files": list(evidence_files),
        "supplemental_parse_status": parse_statuses,
        **authority,
    }
