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
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urljoin

from ..access.detector import AccessStopError
from ..collection.ranking import collect_rankings

RANKING_SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_VERSION = 1
_SUCCESS_STATES = {"NORMAL", "SUCCESS", "200", "AUTHORITATIVE", "COMPLETE"}
_ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})", re.I)


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
                     source_statuses: Mapping | Sequence[Mapping] | None) -> list[dict]:
    explicit: dict[tuple[str, int | None], str] = {}
    if isinstance(source_statuses, Mapping):
        for key, value in source_statuses.items():
            if isinstance(value, Mapping):
                explicit[(str(key), value.get("page_number"))] = _status_value(value)
            else:
                explicit[(str(key), None)] = _status_value(value)
    elif source_statuses:
        for row in source_statuses:
            if not isinstance(row, Mapping):
                continue
            key = _source_url(row)
            explicit[(key, row.get("page_number"))] = _status_value(row)

    planned = list(planned_sources or [])
    if not planned and explicit:
        planned = [{"source_url": key[0], "page_number": key[1]}
                   for key in explicit]
    result = []
    for source in planned:
        url = _source_url(source)
        page = source.get("page_number") if isinstance(source, Mapping) else None
        status = explicit.get((url, page), explicit.get((url, None), "UNKNOWN"))
        result.append({"source_url": url, "page_number": page, "status": status})
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
                           html_files: Mapping[str, str] | None = None) -> dict:
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
    statuses = _source_statuses(planned_sources, source_statuses)
    if not statuses:
        statuses = [{"source_url": str(row.get("ranking_source_url") or ""),
                     "page_number": row.get("ranking_page_number"),
                     "status": str(row.get("access_state") or "NORMAL").upper()}
                    for row in rows]
    authoritative = bool(statuses) and all(
        _status_value(row) in _SUCCESS_STATES for row in statuses)
    if not statuses and rows:
        authoritative = True
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
        "latest_authoritative": authoritative,
        "started_at": started,
        "completed_at": completed,
        "source_count": len(statuses),
        "page_count": len(pages),
        "record_count": len(rows),
        "unique_asin_count": len({row.get("ranking_asin") for row in rows if row.get("ranking_asin")}),
        "product_url_present_count": sum(bool(row.get("ranking_product_url_raw")) for row in rows),
        "product_url_missing_count": sum(not bool(row.get("ranking_product_url_raw")) for row in rows),
        "link_asin_match_count": link_statuses.count("MATCH"),
        "link_asin_mismatch_count": sum(status in {"LINK_ASIN_MISMATCH", "MISMATCH"}
                                         for status in link_statuses),
        "access_state_summary": state_summary,
        "source_statuses": statuses,
        "parser_version": parser_version,
        "ranking_schema_version": RANKING_SCHEMA_VERSION,
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
    }
    (target / "audit.json").write_text(json.dumps({"records": len(rows),
        "link_identity_statuses": {state: link_statuses.count(state)
                                    for state in sorted(set(link_statuses))}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    if authoritative:
        relative = target.relative_to(output_root).as_posix()
        _atomic_json(output_root / "latest_authoritative_snapshot.json",
                     {"snapshot_id": snapshot_id, "path": relative})
    return {"manifest": manifest, "path": target, "records": rows}


def collect_ranking_snapshot(urls: Sequence[str], session, output_root: str | Path,
                             *, pages_per_url: int = 1, **kwargs) -> dict:
    """Collect through the existing serial collector and always persist a snapshot."""
    started = _utc_now()
    records = []
    source_statuses = {str(url): "UNKNOWN" for url in urls}
    error = None
    try:
        records = collect_rankings(list(urls), session, str(output_root),
                                   pages_per_url=pages_per_url)
        source_statuses = {str(url): "NORMAL" for url in urls}
    except Exception as exc:
        error = str(exc)
        if isinstance(exc, AccessStopError):
            message = error.upper()
            state = "RATE_LIMITED" if "429" in message else "BLOCKED" if "403" in message else "CHALLENGE"
            source_statuses = {str(url): state for url in urls}
    html_files = {}
    run_root = Path(output_root) / "runs"
    if run_root.is_dir():
        run_dirs = sorted((path for path in run_root.iterdir() if path.is_dir()),
                          key=lambda path: path.stat().st_mtime, reverse=True)
        if run_dirs:
            html_dir = run_dirs[0] / "html"
            if html_dir.is_dir():
                for path in sorted(html_dir.glob("ranking_*.html")):
                    try:
                        html_files[path.name] = path.read_text(encoding="utf-8")
                    except OSError:
                        continue
    result = build_ranking_snapshot(records, output_root, planned_sources=list(urls),
                                    source_statuses=source_statuses,
                                    started_at=started, html_files=html_files, **kwargs)
    if error:
        result["error"] = error
    return result
