"""Ranking Snapshot V2 enrichment over the existing ranking parser.

The V1 collector remains responsible for browser navigation and saved HTML.
This module audits the server-rendered/ACP shape and never promotes an
incomplete page to authority.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from html import unescape
from typing import Callable, Mapping

from bs4 import BeautifulSoup

from ..access.detector import detect_access_status
from ..collection.ranking import parse_bestsellers_page
from ..monitoring.snapshot import build_ranking_snapshot


class RankingSnapshotIncompleteError(RuntimeError):
    pass


def _recs_metadata(html: str) -> tuple[list[dict], dict]:
    soup = BeautifulSoup(html or "", "lxml")
    node = soup.select_one("[data-client-recs-list]")
    entries: list[dict] = []
    if node is not None:
        try:
            value = json.loads(unescape(node.get("data-client-recs-list") or "[]"))
            entries = [x for x in value if isinstance(x, dict)]
        except (TypeError, ValueError):
            entries = []
    root = soup.select_one("[data-acp-path]")
    acp = {}
    if root is not None:
        grid = soup.select_one(".p13n-desktop-grid")
        acp = {
            "path": root.get("data-acp-path") or "",
            "params": unescape(root.get("data-acp-params") or ""),
            "faceout": (grid.get("data-faceoutkataname") if grid else None) or "GeneralFaceout",
            "reftag": (grid.get("data-reftag") if grid else None) or "",
        }
    return entries, acp


def _rank(row: Mapping) -> int | None:
    value = row.get("bestseller_rank", row.get("ranking_rank"))
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def ranking_completeness(rows: list[Mapping], *, expected_count: int,
                         server_rendered_count: int, acp_available: bool,
                         acp_hydrated_count: int, access_state: str = "NORMAL",
                         parser_error: str = "") -> dict:
    raw_asins = [str(row.get("asin") or row.get("ranking_asin") or "").upper() for row in rows]
    raw_asins = [a for a in raw_asins if a]
    counts = Counter(raw_asins)
    unique_asins = set(raw_asins)
    duplicate_asin_count = sum(count - 1 for count in counts.values() if count > 1)
    ranks = [_rank(row) for row in rows]
    valid_ranks = [value for value in ranks if value is not None]
    rank_counter = Counter(valid_ranks)
    rank_duplicate_count = sum(count - 1 for count in rank_counter.values() if count > 1)
    if valid_ranks:
        first_rank = min(valid_ranks)
        expected_range = set(range(first_rank, first_rank + expected_count))
        rank_gap_count = len(expected_range - set(valid_ranks))
    else:
        first_rank = None
        rank_gap_count = expected_count
    state = str(access_state or "UNKNOWN").upper()
    page_complete = bool(expected_count > 0 and unique_asins
                         and len(unique_asins) == expected_count
                         and len(valid_ranks) == expected_count
                         and duplicate_asin_count == 0
                         and rank_duplicate_count == 0
                         and rank_gap_count == 0
                         and state == "NORMAL"
                         and not parser_error)
    reasons = []
    if expected_count <= 0:
        reasons.append("EXPECTED_COUNT_MISSING")
    if len(unique_asins) != expected_count:
        reasons.append("UNIQUE_ASIN_COUNT_MISMATCH")
    if duplicate_asin_count:
        reasons.append("DUPLICATE_ASIN")
    if len(valid_ranks) != expected_count:
        reasons.append("RANK_MISSING")
    if rank_duplicate_count:
        reasons.append("DUPLICATE_RANK")
    if rank_gap_count:
        reasons.append("RANK_GAP")
    if state != "NORMAL":
        reasons.append(f"ACCESS_{state}")
    if parser_error:
        reasons.append("PARSER_ERROR")
    return {
        "server_rendered_count": server_rendered_count,
        "expected_count": expected_count,
        "acp_available": acp_available,
        "acp_hydrated_count": acp_hydrated_count,
        "raw_item_count": len(rows),
        "unique_asin_count": len(unique_asins),
        "duplicate_asin_count": duplicate_asin_count,
        "min_rank": first_rank,
        "max_rank": max(valid_ranks) if valid_ranks else None,
        "rank_gap_count": rank_gap_count,
        "rank_duplicate_count": rank_duplicate_count,
        "page_complete": page_complete,
        "page_authoritative": page_complete,
        "completion_reason": "COMPLETE" if page_complete else ";".join(reasons),
    }


def parse_ranking_snapshot_v2(html: str, source_url: str, collected_at: str,
                              *, acp_hydrator: Callable[[Mapping, int, int], list[dict]] | None = None,
                              status_code: int = 200) -> dict:
    """Parse one saved page and optionally hydrate missing ACP cards.

    The returned ``records`` are deduplicated by ASIN only after the audit
    counters are computed.  The source page itself remains the evidence.
    """
    access = detect_access_status(status_code, html)
    server_records = parse_bestsellers_page(html, source_url, collected_at)
    recs, acp = _recs_metadata(html)
    expected_count = len(recs) or len(server_records)
    hydrated: list[dict] = []
    if access.value == "NORMAL" and acp and acp_hydrator and expected_count > len(server_records):
        hydrated = list(acp_hydrator(acp | {"entries": recs}, len(server_records),
                                     expected_count - len(server_records)) or [])
    all_records = list(server_records) + hydrated
    for row in all_records:
        row.setdefault("ranking_source_url", source_url)
        row.setdefault("collected_at", collected_at)
    audit = ranking_completeness(
        all_records, expected_count=expected_count,
        server_rendered_count=len(server_records), acp_available=bool(acp),
        acp_hydrated_count=len(hydrated), access_state=access.value,
    )
    seen = set()
    unique = []
    for row in sorted(all_records, key=lambda item: (_rank(item) or 10**9, str(item.get("asin") or ""))):
        asin = str(row.get("asin") or "").upper()
        if asin and asin not in seen:
            seen.add(asin)
            unique.append(row)
    return {"records": unique, "raw_records": all_records, "audit": audit,
            "access_state": access.value, "acp": acp or None,
            "expected_count_source": "ACP_RECS_LIST" if recs else "SERVER_RENDERED_FALLBACK"}


def require_authoritative_page(result: Mapping) -> list[dict]:
    if not (result.get("audit") or {}).get("page_authoritative"):
        raise RankingSnapshotIncompleteError(
            "ranking page is incomplete: " + str((result.get("audit") or {}).get("completion_reason")))
    return list(result.get("records") or [])


def build_ranking_snapshot_v2(result: Mapping, output_root, *,
                              planned_sources=None, source_statuses=None,
                              snapshot_id=None, started_at=None,
                              completed_at=None, access_state_summary=None,
                              html_files=None, offline_frozen=False) -> dict:
    """Persist a V2 parse result through the existing immutable snapshot gate.

    The V2 audit is additive metadata.  Existing snapshot authority rules still
    decide whether the persisted directory can become authoritative; callers
    must call :func:`require_authoritative_page` before treating one page as a
    complete source.
    """
    return build_ranking_snapshot(
        list(result.get("records") or []), output_root,
        planned_sources=planned_sources,
        source_statuses=source_statuses,
        snapshot_id=snapshot_id,
        started_at=started_at,
        completed_at=completed_at,
        access_state_summary=access_state_summary,
        parser_version="collection.ranking_v2",
        html_files=html_files,
        ranking_audit=result.get("audit") or {},
        offline_frozen=offline_frozen,
    )
