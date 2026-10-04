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
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from ..access.detector import AccessStopError, detect_access_status
from ..collection.ranking import parse_bestsellers_page
from ..monitoring.snapshot import build_ranking_snapshot


class RankingSnapshotIncompleteError(RuntimeError):
    pass


def _page_number(source_url: str) -> int:
    try:
        value = int(parse_qs(urlsplit(str(source_url or "")).query).get("pg", ["1"])[0])
        return value if value >= 1 else 1
    except (TypeError, ValueError):
        return 1


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


def ranking_completeness(rows: list[Mapping], *, expected_count: int | None,
                         server_rendered_count: int, acp_available: bool,
                         acp_hydrated_count: int, access_state: str = "NORMAL",
                         parser_error: str = "", expected_count_source: str = "UNKNOWN") -> dict:
    raw_asins = [str(row.get("asin") or row.get("ranking_asin") or "").upper() for row in rows]
    raw_asins = [a for a in raw_asins if a]
    counts = Counter(raw_asins)
    unique_asins = set(raw_asins)
    duplicate_asin_count = sum(count - 1 for count in counts.values() if count > 1)
    ranks = [_rank(row) for row in rows]
    valid_ranks = [value for value in ranks if value is not None]
    rank_counter = Counter(valid_ranks)
    rank_duplicate_count = sum(count - 1 for count in rank_counter.values() if count > 1)
    if valid_ranks and expected_count is not None:
        first_rank = min(valid_ranks)
        expected_range = set(range(first_rank, first_rank + expected_count))
        rank_gap_count = len(expected_range - set(valid_ranks))
    else:
        first_rank = min(valid_ranks) if valid_ranks else None
        rank_gap_count = None
    state = str(access_state or "UNKNOWN").upper()
    page_complete = bool(expected_count is not None and expected_count > 0 and unique_asins
                         and len(unique_asins) == expected_count
                         and len(valid_ranks) == expected_count
                         and duplicate_asin_count == 0
                         and rank_duplicate_count == 0
                         and rank_gap_count == 0
                         and state == "NORMAL"
                         and not parser_error)
    reasons = []
    if expected_count is None:
        reasons.append("EXPECTED_COUNT_UNKNOWN")
    elif expected_count <= 0:
        reasons.append("EXPECTED_COUNT_MISSING")
    if expected_count is not None and len(unique_asins) != expected_count:
        reasons.append("UNIQUE_ASIN_COUNT_MISMATCH")
    if duplicate_asin_count:
        reasons.append("DUPLICATE_ASIN")
    if expected_count is not None and len(valid_ranks) != expected_count:
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
        "observed_count": len(rows),
        "observed_server_count": server_rendered_count,
        "server_rendered_count": server_rendered_count,
        "expected_count": expected_count,
        "expected_count_source": expected_count_source if expected_count is not None else "UNKNOWN",
        "acp_available": acp_available,
        "acp_hydrated_count": acp_hydrated_count,
        "access_state": state,
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
                              status_code: int = 200, expected_count: int | None = None,
                              expected_count_source: str | None = None,
                              ranking_source_url: str | None = None,
                              page_instance_id: str | None = None,
                              page_number: int | None = None) -> dict:
    """Parse one saved page and optionally hydrate missing ACP cards.

    The returned ``records`` are deduplicated by ASIN only after the audit
    counters are computed.  The source page itself remains the evidence.
    """
    access = detect_access_status(status_code, html)
    server_records = parse_bestsellers_page(html, source_url, collected_at)
    recs, acp = _recs_metadata(html)
    if expected_count is None and recs:
        expected_count = len(recs)
        expected_count_source = expected_count_source or "ACP_RECS_LIST"
    if expected_count is None:
        # A server-rendered prefix is an observation, not proof of the page's
        # total.  Keep the expected count unknown until Amazon exposes an
        # explicit total or the reviewed task supplies one.
        expected_count_source = expected_count_source or "UNKNOWN"
    hydrated: list[dict] = []
    if (access.value == "NORMAL" and acp and acp_hydrator
            and expected_count is not None and expected_count > len(server_records)):
        context = {
            "page_instance_id": page_instance_id or
            "page:%s|url:%s" % (page_number or 1, ranking_source_url or source_url),
            "ranking_source_url": ranking_source_url or source_url,
            "ranking_page_url": source_url,
            "ranking_page_number": page_number or _page_number(source_url),
            "expected_count": expected_count,
            "offset": len(server_records),
            "count": expected_count - len(server_records),
        }
        hydrated = list(acp_hydrator(acp | {"entries": recs, "source_url": source_url,
                                            "context": context},
                                     len(server_records),
                                     expected_count - len(server_records)) or [])
    all_records = list(server_records) + hydrated
    for row in all_records:
        row["ranking_source_url"] = ranking_source_url or row.get("ranking_source_url") or source_url
        row["ranking_page_url"] = source_url
        row.setdefault("collected_at", collected_at)
    audit = ranking_completeness(
        all_records, expected_count=expected_count,
        server_rendered_count=len(server_records), acp_available=bool(acp),
        acp_hydrated_count=len(hydrated), access_state=access.value,
        expected_count_source=expected_count_source or "UNKNOWN",
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
            "expected_count_source": expected_count_source or "UNKNOWN"}


def make_acp_hydrator(transport, *, max_records: int | None = None,
                      evidence_dir=None):
    """Create the explicit ACP continuation callback for a transport.

    The callback is deliberately injected by the caller.  It performs one
    bounded continuation request, preserves the response as transport
    evidence, and returns only parser records; authority is still decided by
    :func:`ranking_completeness`.
    """
    restricted_states = {
        "BLOCKED", "CHALLENGE", "RATE_LIMITED", "CAPTCHA", "BOT_BLOCK",
        "INTERSTITIAL", "ACCESS_DENIED",
    }

    def raise_if_restricted(response) -> None:
        try:
            status_code = int(response.status_code) if response.status_code is not None else None
        except (TypeError, ValueError):
            status_code = None
        failure = response.failure or {}
        failure_kind = str(failure.get("kind") or "").upper()
        access_state = str(response.access_state or "").upper()
        if status_code in {403, 429} or failure_kind in restricted_states \
                or access_state in restricted_states:
            raise AccessStopError(
                "ACP 续页访问受限（HTTP %s，状态 %s），按策略停止采集"
                % (status_code, access_state or failure_kind or "UNKNOWN"))

    evidence_counts: dict[str, int] = {}

    def hydrate(metadata: Mapping, offset: int, count: int) -> list[dict]:
        path = str(metadata.get("path") or "")
        source_url = str(metadata.get("source_url") or "")
        if not path or not source_url or count <= 0:
            return []
        entries = [entry for entry in (metadata.get("entries") or [])
                   if isinstance(entry, Mapping)]
        ids = [str(entry.get("id") or entry.get("asin") or "").strip()
               for entry in entries]
        ids = [value for value in ids if value]
        indexes = list(range(max(0, int(offset)), max(0, int(offset)) + int(count)))
        body = urlencode({
            "faceoutkataname": str(metadata.get("faceout") or "GeneralFaceout"),
            "ids": json.dumps(ids, ensure_ascii=False, separators=(",", ":")),
            "indexes": json.dumps(indexes, separators=(",", ":")),
            "offset": str(max(0, int(offset))),
            "reftagprefix": str(metadata.get("reftag") or ""),
        })
        endpoint = urljoin(source_url, path.rstrip("/") + "/nextPage")
        try:
            response = transport.fetch_ajax(
                endpoint, method="POST", referer=source_url, payload=body,
                headers={
                    "accept": "text/html,application/xhtml+xml",
                    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "x-amz-acp-params": str(metadata.get("params") or ""),
                })
        except AccessStopError:
            raise
        except Exception as exc:
            error_kind = str(getattr(exc, "kind", "") or "").upper()
            error_text = str(exc).upper()
            if error_kind in restricted_states or any(marker in error_text for marker in (
                    "CAPTCHA", "ROBOT CHECK", "ACCESS DENIED", "HTTP 403", "HTTP 429")):
                raise AccessStopError("ACP 续页访问受限：%s" % exc) from exc
            # The page remains saved evidence; failure to continue ACP must
            # leave the completeness audit incomplete rather than fabricate
            # the missing cards or abort before persisting the snapshot.
            return []
        if evidence_dir is not None:
            from pathlib import Path
            from ..monitoring.ranking_identity.evidence import save_evidence_snapshot
            from ..transport.base import raw_response_evidence
            root = Path(evidence_dir)
            root.mkdir(parents=True, exist_ok=True)
            index = len(list(root.glob("acp_response_evidence_*.json")))
            payload = raw_response_evidence(
                response, request_method="POST", request_url=endpoint,
                request_headers={
                    "accept": "text/html,application/xhtml+xml",
                    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "x-amz-acp-params": str(metadata.get("params") or ""),
                }, request_payload=body)
            context = dict(metadata.get("context") or {})
            if context:
                payload["context"] = context
            page_instance_id = str(context.get("page_instance_id")
                                    or metadata.get("page_instance_id") or "")
            if page_instance_id:
                safe_page = re.sub(r"[^A-Za-z0-9_.-]+", "_", page_instance_id).strip("_")
                evidence_index = evidence_counts.get(page_instance_id, 0)
                evidence_counts[page_instance_id] = evidence_index + 1
                evidence_name = "%s_acp_%03d.json" % (safe_page or "page", evidence_index)
            else:
                index = len(list(root.glob("acp_response_evidence_*.json")))
                evidence_name = "acp_response_evidence_%03d.json" % index
            save_evidence_snapshot(
                root, acp_response_evidence=payload,
                acp_response_evidence_name=evidence_name)
        raise_if_restricted(response)
        if response.failure or str(response.access_state or "").upper() != "NORMAL":
            return []
        records = parse_bestsellers_page(response.text, source_url, "")
        limit = int(max_records) if max_records is not None else int(count)
        return records[:max(0, min(int(count), limit))]

    return hydrate


def require_authoritative_page(result: Mapping) -> list[dict]:
    if not (result.get("audit") or {}).get("page_authoritative"):
        raise RankingSnapshotIncompleteError(
            "ranking page is incomplete: " + str((result.get("audit") or {}).get("completion_reason")))
    return list(result.get("records") or [])


def replay_acp_response_evidence(evidence, *, source_url: str, collected_at: str = "") -> list[dict]:
    """Parse a saved ACP raw-response envelope without contacting Amazon."""
    from pathlib import Path
    payload = evidence
    if isinstance(evidence, (str, Path)):
        payload = json.loads(Path(evidence).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("ACP evidence 必须是 JSON object")
    response = payload.get("response") if isinstance(payload.get("response"), Mapping) else payload
    body = str(response.get("body") or "")
    return parse_bestsellers_page(body, source_url, collected_at)


def replay_ranking_snapshot_v2(snapshot_dir) -> dict:
    """Replay a persisted V2 snapshot using only its saved evidence files."""
    from pathlib import Path
    root = Path(snapshot_dir)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    pages = list((manifest.get("ranking_v2_audit") or {}).get("pages") or [])
    html_paths = sorted((root / "html").glob("ranking_*.html"))
    acp_paths = sorted((root / "evidence" / "acp").glob("*.json"))
    acp_by_page: dict[str, list[dict]] = {}
    legacy_acp_records: list[dict] = []
    for path in acp_paths:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        context = envelope.get("context") if isinstance(envelope, Mapping) else {}
        context = context if isinstance(context, Mapping) else {}
        page_key = str(context.get("page_instance_id") or "")
        page_url = str(context.get("ranking_page_url") or
                       (envelope.get("request") or {}).get("url") or "")
        parsed = replay_acp_response_evidence(path, source_url=page_url)
        if page_key:
            acp_by_page.setdefault(page_key, []).extend(parsed)
        else:
            legacy_acp_records.extend(parsed)
    records = []
    audits = []
    for index, path in enumerate(html_paths):
        page = pages[index] if index < len(pages) else {}
        page_url = str(page.get("ranking_page_url") or page.get("source_url") or "")
        ranking_source_url = str(page.get("ranking_source_url") or page_url)
        page_key = str(page.get("page_instance_id") or
                       "page:%s|url:%s" % (page.get("page_number", index + 1), ranking_source_url))
        expected = page.get("expected_count")
        collected_at = str(page.get("collected_at") or "")
        server_records = parse_bestsellers_page(path.read_text(encoding="utf-8"),
                                                 page_url, collected_at)
        page_acp = acp_by_page.get(page_key)
        if page_acp is None and len(pages) == 1:
            page_acp = legacy_acp_records
        page_acp = page_acp or []
        hydrated = page_acp[:max(0, int(expected or 0) - len(server_records))]
        all_records = server_records + hydrated
        for row in all_records:
            row["ranking_source_url"] = ranking_source_url
            row["ranking_page_url"] = page_url
        audit = ranking_completeness(
            all_records, expected_count=expected,
            server_rendered_count=len(server_records),
            acp_available=bool(page_acp), acp_hydrated_count=len(hydrated),
            expected_count_source=str(page.get("expected_count_source") or "UNKNOWN"))
        seen = set()
        unique = []
        for row in all_records:
            asin = str(row.get("asin") or "").upper()
            if asin and asin not in seen:
                seen.add(asin)
                unique.append(row)
        records.extend(unique)
        audits.append(audit)
    return {"records": records, "audits": audits,
            "record_count": len(records),
            "unique_asin_count": len({str(row.get("asin") or "").upper() for row in records})}


def build_ranking_snapshot_v2(result: Mapping, output_root, *,
                              planned_sources=None, source_statuses=None,
                              snapshot_id=None, started_at=None,
                              completed_at=None, access_state_summary=None,
                              html_files=None, identity_audit=None,
                              publish_authoritative_pointer=True,
                              offline_frozen=False) -> dict:
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
        identity_audit=identity_audit,
        publish_authoritative_pointer=publish_authoritative_pointer,
        offline_frozen=offline_frozen,
    )
