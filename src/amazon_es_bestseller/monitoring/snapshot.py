# -*- coding: utf-8 -*-
"""Immutable ranking snapshots built on the existing offline ranking parser.

The collector remains responsible for browser access and saved HTML.  This
module freezes the records returned by it, records source completeness, and
updates the authoritative pointer only after every planned source succeeded.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urljoin

from ..access.detector import AccessStopError
from ..collection.ranking import collect_rankings
from ..models import is_valid_asin


class SnapshotIncompleteError(RuntimeError):
    """A persisted snapshot is incomplete and cannot be production input."""

RANKING_SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_VERSION = 2
_SUCCESS_STATES = {"NORMAL", "SUCCESS", "200", "AUTHORITATIVE", "COMPLETE"}
_ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})", re.I)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime | str | None) -> str:
    if value is None:
        value = _utc_now()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat(timespec="seconds")
    return str(value)


def new_snapshot_id(now: datetime | None = None) -> str:
    """Create a run-unique, human-readable snapshot id."""
    stamp = now or datetime.now(timezone.utc)
    return "snapshot_%sZ" % stamp.strftime("%Y%m%dT%H%M%S%f")


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _source_url(source: object) -> str:
    if isinstance(source, Mapping):
        return str(source.get("source_url") or source.get("url") or "")
    return str(source or "")


def _status_value(value: object) -> str:
    if isinstance(value, Mapping):
        value = value.get("status") or value.get("access_state") or value.get("state")
    if hasattr(value, "value"):
        value = value.value
    return str(value or "UNKNOWN").upper()


def _source_statuses(planned_sources: Sequence[object] | None,
                     source_statuses: Mapping | Sequence[Mapping] | None,
                     rows: Sequence[Mapping] = ()) -> list[dict]:
    explicit: dict[tuple[str, int | None], dict] = {}
    if isinstance(source_statuses, Mapping):
        for key, value in source_statuses.items():
            if isinstance(value, Mapping):
                item = dict(value)
                item.setdefault("source_url", str(key))
                explicit[(str(key), value.get("page_number"))] = item
            else:
                explicit[(str(key), None)] = {"source_url": str(key), "status": _status_value(value)}
    elif source_statuses:
        for row in source_statuses:
            if not isinstance(row, Mapping):
                continue
            key = _source_url(row)
            explicit[(key, row.get("page_number"))] = dict(row)

    planned = list(planned_sources or [])
    if not planned and explicit:
        planned = [{"source_url": key[0], "page_number": key[1]}
                   for key in explicit]
    result = []
    for source in planned:
        url = _source_url(source)
        page = source.get("page_number") if isinstance(source, Mapping) else None
        item = dict(explicit.get((url, page), explicit.get((url, None), {})))
        item["source_url"] = url
        item["page_number"] = page
        item.setdefault("access_state", item.get("status") or "UNKNOWN")
        item.setdefault("http_status", None)
        matching = [row for row in rows
                    if str(row.get("ranking_source_url") or "") == url
                    and (page is None or row.get("ranking_page_number") == page)]
        item.setdefault("parsed_record_count", len(matching))
        item.setdefault("parse_status", "PARSE_OK" if item["parsed_record_count"] > 0
                        and _status_value(item) in _SUCCESS_STATES else "UNKNOWN")
        item.setdefault("error", "")
        item["status"] = _status_value(item)
        result.append(item)
    return result


def _record_with_snapshot(record: Mapping, snapshot_id: str, observed_at: str) -> dict:
    row = dict(record)
    asin = str(row.get("ranking_asin") or row.get("asin") or "").strip().upper()
    row["asin"] = asin
    row["ranking_asin"] = asin
    row.setdefault("ranking_rank", row.get("bestseller_rank"))
    row.setdefault("ranking_rank_raw", row.get("bestseller_rank_raw"))
    row["observed_at"] = str(row.get("observed_at") or observed_at)
    row["snapshot_id"] = snapshot_id
    raw_url = str(row.get("ranking_product_url_raw") or "")
    link_asin = row.get("ranking_link_asin")
    if not link_asin and raw_url:
        match = _ASIN_RE.search(raw_url)
        link_asin = match.group(1).upper() if match else None
    row["ranking_link_asin"] = link_asin
    if "ranking_product_url_normalized" not in row:
        row["ranking_product_url_normalized"] = (
            "https://www.amazon.es/dp/%s" % link_asin if link_asin else
            urljoin("https://www.amazon.es", raw_url).split("?", 1)[0].split("#", 1)[0]
            if raw_url else "")
    if "ranking_link_identity_status" not in row:
        row["ranking_link_identity_status"] = (
            "NO_PRODUCT_URL" if not raw_url else
            "NO_ASIN_IN_URL" if not link_asin else
            "MATCH" if link_asin == asin else "LINK_ASIN_MISMATCH")
    return row


def _write_rankings_csv(path: Path, rows: list[dict]) -> None:
    preferred = ["snapshot_id", "observed_at", "ranking_asin", "ranking_rank",
                 "ranking_rank_raw", "ranking_product_url_raw",
                 "ranking_product_url_normalized", "ranking_link_asin",
                 "ranking_link_identity_status"]
    keys = list(preferred)
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_ranking_snapshot(records: Sequence[Mapping], output_root: str | Path,
                           *, planned_sources: Sequence[object] | None = None,
                           source_statuses: Mapping | Sequence[Mapping] | None = None,
                           snapshot_id: str | None = None,
                           started_at: datetime | str | None = None,
                           completed_at: datetime | str | None = None,
                           access_state_summary: Mapping | None = None,
                           parser_version: str = "collection.ranking",
                           html_files: Mapping[str, str] | None = None,
                           ranking_audit: Mapping | None = None,
                           identity_audit: Mapping | None = None,
                           publish_authoritative_pointer: bool = True,
                           offline_frozen: bool = False) -> dict:
    """Freeze ranking records and return the manifest/result bundle.

    The target directory is append-only: an existing snapshot id is never
    overwritten.  ``source_statuses`` is deliberately explicit so an
    incomplete run cannot accidentally become authoritative.
    """
    snapshot_id = snapshot_id or new_snapshot_id()
    started = _iso(started_at)
    completed = _iso(completed_at)
    observed_at = started
    rows = [_record_with_snapshot(row, snapshot_id, observed_at) for row in records]
    rows = [row for row in rows if is_valid_asin(row.get("ranking_asin"))]
    statuses = _source_statuses(planned_sources, source_statuses, rows)
    if not statuses:
        statuses = []
    expected_page_count = len(statuses)
    completed_page_count = sum(1 for row in statuses
                               if _status_value(row) in _SUCCESS_STATES)
    parsed_page_count = sum(1 for row in statuses
                            if str(row.get("parse_status") or "").upper() == "PARSE_OK")
    failed_page_count = sum(1 for row in statuses
                            if _status_value(row) not in _SUCCESS_STATES
                            or str(row.get("parse_status") or "").upper()
                            in {"ACCESS_BLOCKED", "NETWORK_ERROR", "PARSER_ERROR"})
    def _record_count(row: Mapping) -> int:
        try:
            return int(row.get("parsed_record_count") or 0)
        except (TypeError, ValueError):
            return 0
    empty_page_count = sum(1 for row in statuses
                           if str(row.get("parse_status") or "").upper() == "PARSE_EMPTY"
                           or _record_count(row) <= 0)
    unique_asins = {row.get("ranking_asin") for row in rows if row.get("ranking_asin")}
    authoritative = (not offline_frozen and expected_page_count > 0
                     and completed_page_count == expected_page_count
                     and parsed_page_count == expected_page_count
                     and failed_page_count == 0 and empty_page_count == 0
                     and bool(rows) and bool(unique_asins))
    base_snapshot_authoritative = authoritative
    # A V2 parser audit is a stronger page-level authority gate than the
    # legacy source-status summary.  An incomplete ACP/rank audit may still be
    # persisted as evidence, but it must never advance the authoritative
    # pointer.
    if ranking_audit is not None and not bool(ranking_audit.get("page_authoritative")):
        authoritative = False
    authority = None
    if identity_audit is not None:
        from .ranking_identity.completeness import evaluate_authority
        identity_audit = dict(identity_audit)
        authority = evaluate_authority(
            ranking_complete=bool(ranking_audit and ranking_audit.get("page_authoritative")),
            slots_complete=bool(identity_audit.get("ranking_slot_complete")),
            identity_ready=bool(identity_audit.get("identity_ready")),
            identity_complete=bool(identity_audit.get("product_identity_complete",
                                                         identity_audit.get("identity_complete"))),
            access_normal=all(_status_value(item) in _SUCCESS_STATES for item in statuses),
            no_conflicts=not bool(identity_audit.get("identity_conflict_count"))
            and not bool(identity_audit.get("ranking_slot_conflict_count")),
            no_rank_gap=not bool((ranking_audit or {}).get("rank_gap_count")),
            no_duplicate_rank_slot=not bool((ranking_audit or {}).get("rank_duplicate_count")),
        )
        authoritative = authoritative and authority["authoritative"]
    snapshot_status = "AUTHORITATIVE" if authoritative else "INCOMPLETE"
    output_root = Path(output_root)
    day_dir = output_root / datetime.fromisoformat(started.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    target = day_dir / snapshot_id
    if target.exists():
        raise FileExistsError("ranking snapshot 已存在，不允许覆盖: %s" % target)
    target.mkdir(parents=True, exist_ok=False)
    if html_files:
        html_dir = target / "html"
        html_dir.mkdir()
        for name, content in html_files.items():
            (html_dir / str(name)).write_text(str(content), encoding="utf-8")
    (target / "rankings.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_rankings_csv(target / "rankings.csv", rows)
    link_statuses = [str(row.get("ranking_link_identity_status") or "") for row in rows]
    pages = {(str(row.get("ranking_source_url") or ""), row.get("ranking_page_number"))
             for row in rows}
    state_summary = dict(access_state_summary or {})
    if not state_summary:
        for source in statuses:
            state = _status_value(source)
            state_summary[state] = state_summary.get(state, 0) + 1
    manifest = {
        "snapshot_id": snapshot_id,
        "snapshot_status": snapshot_status,
        "snapshot_mode": "OFFLINE_FROZEN" if offline_frozen else "LIVE",
        "latest_authoritative": authoritative,
        "started_at": started,
        "completed_at": completed,
        "source_count": len(statuses),
        "page_count": expected_page_count,
        "expected_page_count": expected_page_count,
        "completed_page_count": completed_page_count,
        "parsed_page_count": parsed_page_count,
        "failed_page_count": failed_page_count,
        "empty_page_count": empty_page_count,
        "record_count": len(rows),
        "unique_asin_count": len(unique_asins),
        "product_url_present_count": sum(bool(row.get("ranking_product_url_raw")) for row in rows),
        "product_url_missing_count": sum(not bool(row.get("ranking_product_url_raw")) for row in rows),
        "link_asin_match_count": link_statuses.count("MATCH"),
        "link_asin_mismatch_count": sum(status in {"LINK_ASIN_MISMATCH", "MISMATCH"}
                                         for status in link_statuses),
        "access_state_summary": state_summary,
        "source_statuses": statuses,
        "page_statuses": statuses,
        "parser_version": parser_version,
        "ranking_schema_version": RANKING_SCHEMA_VERSION,
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
    }
    if ranking_audit is not None:
        manifest["ranking_v2_audit"] = dict(ranking_audit)
    if authority is not None:
        final_authority_reasons = list(authority["authority_block_reasons"])
        if not base_snapshot_authoritative:
            final_authority_reasons.append("BASE_SNAPSHOT_GATE")
        final_authority_gates = dict(authority["authority_gates"])
        final_authority_gates["base_snapshot_complete"] = bool(base_snapshot_authoritative)
        manifest.update({"authority_status": "AUTHORITATIVE" if authoritative else "BLOCKED",
                         "authority_gates": final_authority_gates,
                         "authority_block_reasons": final_authority_reasons,
                         "ranking_complete": authority["authority_gates"]["ranking_complete"],
                         "ranking_identity_ready": authority["authority_gates"]["identity_ready"],
                         "ranking_identity_complete": authority["authority_gates"]["identity_complete"],
                         "ranking_slot_complete": authority["authority_gates"]["ranking_slots_complete"],
                         "identity_conflict_count": int(identity_audit.get("identity_conflict_count") or 0),
                         "final_authoritative": authoritative,
                         "authority_reasons": final_authority_reasons})
    (target / "audit.json").write_text(json.dumps({"records": len(rows),
        "link_identity_statuses": {state: link_statuses.count(state)
                                    for state in sorted(set(link_statuses))}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    if authoritative and publish_authoritative_pointer:
        relative = target.relative_to(output_root).as_posix()
        _atomic_json(output_root / "latest_authoritative_snapshot.json",
                     {"snapshot_id": snapshot_id, "path": relative})
    return {"manifest": manifest, "path": target, "records": rows}


def collect_ranking_snapshot(urls: Sequence[str], session, output_root: str | Path,
                             *, pages_per_url: int = 1,
                             parser_version: str = "v1",
                             acp_hydrator=None, transport=None, **kwargs) -> dict:
    """Collect through the existing serial collector and always persist a snapshot.

    ``v1`` keeps the historical parser path.  ``v2`` reparses each saved page
    through the ACP-aware completeness contract after collection, so a page
    with only the server-rendered prefix is persisted as evidence but cannot
    become authoritative.  ACP network hydration is explicit via the
    ``acp_hydrator`` callback; the default never adds an unreviewed request.
    """
    started = _utc_now()
    records = []
    source_statuses = []
    error = None
    run_dir = Path(output_root) / "runs" / (started.strftime("%Y%m%d_%H%M%S_%f")
                                            + "_" + new_snapshot_id(started).split("_", 1)[1])
    planned_pages = [{"source_url": str(url), "page_number": page}
                     for url in urls for page in range(1, int(pages_per_url) + 1)]
    try:
        records = collect_rankings(list(urls), session, str(output_root),
                                   pages_per_url=pages_per_url, run_dir=str(run_dir),
                                   transport=transport)
    except Exception as exc:
        error = str(exc)
        if isinstance(exc, AccessStopError):
            message = error.upper()
            state = "RATE_LIMITED" if "429" in message else "BLOCKED" if "403" in message else "CHALLENGE"
            source_statuses = [{"source_url": str(url), "page_number": page,
                                "access_state": state, "parse_status": "ACCESS_BLOCKED",
                                "error": error}
                               for url in urls for page in range(1, int(pages_per_url) + 1)]
    status_file = run_dir / "page_statuses.json"
    if status_file.exists():
        try:
            source_statuses = json.loads(status_file.read_text(encoding="utf-8"))
            records = []
            for page_file in sorted((run_dir / "pages").glob("*.json")):
                page_data = json.loads(page_file.read_text(encoding="utf-8"))
                records.extend(page_data.get("records") or [])
        except (OSError, ValueError):
            pass
    if not source_statuses:
        source_statuses = [{"source_url": row["source_url"], "page_number": row["page_number"],
                            "access_state": "NORMAL", "parse_status": "PARSE_OK",
                            "parsed_record_count": 0}
                           for row in planned_pages]
    html_files = {}
    html_dir = run_dir / "html"
    if html_dir.is_dir():
        for path in sorted(html_dir.glob("ranking_*.html")):
            try:
                html_files[path.name] = path.read_text(encoding="utf-8")
            except OSError:
                continue
    ranking_audit = None
    effective_parser_version = str(parser_version or "v1").casefold()
    if effective_parser_version in {"v2", "collection.ranking_v2"}:
        from ..collection.ranking_v2 import make_acp_hydrator, parse_ranking_snapshot_v2

        if acp_hydrator is None and transport is not None:
            acp_hydrator = make_acp_hydrator(transport, evidence_dir=run_dir / "acp")

        v2_records = []
        page_audits = []
        html_paths = sorted(html_dir.glob("ranking_*.html")) if html_dir.is_dir() else []
        for index, path in enumerate(html_paths):
            if index < len(source_statuses):
                status_row = dict(source_statuses[index])
            elif index < len(planned_pages):
                status_row = dict(planned_pages[index])
            else:
                status_row = {}
            source_url = str(status_row.get("page_url") or status_row.get("source_url") or "")
            ranking_source_url = str(status_row.get("source_url") or source_url)
            collected_at = str(status_row.get("collected_at") or started.isoformat())
            access_stopped = False
            try:
                page_number = status_row.get("page_number")
                try:
                    page_number = int(page_number)
                except (TypeError, ValueError):
                    page_number = None
                page_instance_id = str(
                    status_row.get("page_instance_id")
                    or "page:%s|url:%s" % (page_number or index + 1, ranking_source_url))
                page_result = parse_ranking_snapshot_v2(
                    path.read_text(encoding="utf-8"), source_url, collected_at,
                    acp_hydrator=acp_hydrator,
                    status_code=int(status_row.get("http_status") or 200),
                    expected_count=status_row.get("expected_count"),
                    expected_count_source=status_row.get("expected_count_source"),
                    ranking_source_url=ranking_source_url,
                    page_instance_id=page_instance_id,
                    page_number=page_number)
            except AccessStopError as exc:
                error = str(exc)
                access_stopped = True
                page_result = {"records": [], "audit": {
                    "page_authoritative": False,
                    "completion_reason": "ACCESS_BLOCKED",
                    "access_state": "BLOCKED",
                    "error": error}}
            except (OSError, ValueError, TypeError) as exc:
                page_result = {"records": [], "audit": {
                    "page_authoritative": False,
                    "completion_reason": "PARSER_ERROR",
                    "parser_error": str(exc)}}
            audit = dict(page_result.get("audit") or {})
            audit["access_state"] = page_result.get("access_state", "UNKNOWN")
            audit["page_index"] = index
            audit["source_url"] = source_url
            audit["page_number"] = status_row.get("page_number")
            audit["ranking_source_url"] = ranking_source_url
            audit["ranking_page_url"] = source_url
            audit["page_instance_id"] = str(
                status_row.get("page_instance_id")
                or "page:%s|url:%s" % (status_row.get("page_number") or index + 1,
                                         ranking_source_url))
            page_audits.append(audit)
            v2_records.extend(page_result.get("records") or [])
            status_row["parsed_record_count"] = len(page_result.get("records") or [])
            status_row["parse_status"] = ("ACCESS_BLOCKED" if access_stopped else
                                            "PARSE_OK" if audit.get("page_authoritative")
                                            else "PARSE_INCOMPLETE")
            status_row["v2_completion_reason"] = audit.get("completion_reason", "")
            status_row["html_file"] = path.name
            status_row["page_instance_id"] = audit["page_instance_id"]
            status_row["expected_count"] = audit.get("expected_count")
            status_row["expected_count_source"] = audit.get("expected_count_source", "UNKNOWN")
            if access_stopped:
                status_row["access_state"] = "BLOCKED"
                status_row["error"] = error
            if index < len(source_statuses):
                source_statuses[index] = status_row
            else:
                source_statuses.append(status_row)
            if access_stopped:
                break
        records = v2_records
        ranking_audit = {
            "page_authoritative": bool(page_audits) and all(
                bool(item.get("page_authoritative")) for item in page_audits),
            "page_count": len(page_audits),
            "pages": page_audits,
            "parser_version": "collection.ranking_v2",
            "ranking_complete": bool(page_audits) and all(
                bool(item.get("page_complete")) for item in page_audits),
            "rank_gap_count": sum(int(item.get("rank_gap_count") or 0)
                                   for item in page_audits),
            "rank_duplicate_count": sum(int(item.get("rank_duplicate_count") or 0)
                                         for item in page_audits),
            "access_state": ("NORMAL" if page_audits and all(
                str(item.get("access_state") or "UNKNOWN").upper() == "NORMAL"
                for item in page_audits) else "BLOCKED"),
        }
        (run_dir / "page_statuses.json").write_text(
            json.dumps(source_statuses, ensure_ascii=False, indent=2), encoding="utf-8")
    final_identity_audit = None
    if effective_parser_version in {"v2", "collection.ranking_v2"}:
        from ..monitoring.ranking_identity.extract import extract_identity_from_evidence
        try:
            final_identity = extract_identity_from_evidence(run_dir)
            final_identity_audit = dict(final_identity.get("audit") or {})
            final_identity_audit["evidence_files"] = list(
                final_identity.get("evidence_files") or final_identity_audit.get("evidence_files") or [])
        except (OSError, ValueError, TypeError) as exc:
            final_identity_audit = {
                "identity_ready": False,
                "identity_complete": False,
                "product_identity_complete": False,
                "ranking_slot_complete": False,
                "identity_conflict_count": 0,
                "ranking_slot_conflict_count": 0,
                "attribution_unknown_count": 1,
                "status": "IDENTITY_FAILED",
                "error": str(exc),
            }
    result = build_ranking_snapshot(records, output_root, planned_sources=planned_pages,
                                    source_statuses=source_statuses,
                                    started_at=started, html_files=html_files,
                                    parser_version=("collection.ranking_v2"
                                                     if effective_parser_version in {"v2", "collection.ranking_v2"}
                                                     else "collection.ranking"),
                                   ranking_audit=ranking_audit,
                                   identity_audit=final_identity_audit,
                                    publish_authoritative_pointer=False,
                                    **kwargs)
    acp_evidence = run_dir / "acp"
    if acp_evidence.is_dir() and any(acp_evidence.iterdir()):
        target_evidence = result["path"] / "evidence" / "acp"
        shutil.copytree(acp_evidence, target_evidence, dirs_exist_ok=True)
        result["manifest"]["acp_evidence_files"] = [
            str(path.relative_to(result["path"]).as_posix())
            for path in sorted(target_evidence.glob("*.json"))]
        (result["path"] / "manifest.json").write_text(
            json.dumps(result["manifest"], ensure_ascii=False, indent=2), encoding="utf-8")
    if result["manifest"].get("final_authoritative", result["manifest"].get("latest_authoritative")):
        relative = result["path"].relative_to(Path(output_root)).as_posix()
        _atomic_json(Path(output_root) / "latest_authoritative_snapshot.json",
                     {"snapshot_id": result["manifest"]["snapshot_id"],
                      "path": relative})
    if error:
        result["error"] = error
    return result
