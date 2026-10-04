"""Saved-evidence extraction entry points; never performs network access."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .adapters import (
    expected_count_from_html,
    extract_embedded_supplemental_with_status,
    extract_server_candidates,
    extract_structured_candidates_with_status,
)
from .completeness import audit_identity_records
from .resolver import resolve_candidates


def extract_identity_from_html(
    html: str, *, source_url: str = "", page_number: int | None = 1,
    evidence_file: str | None = None, client_recs: object = None,
    acp_response: object = None, expected_count: int | None = None,
    expected_count_source: str | None = None,
    page_instance_id: str | None = None,
    representation_type: str = "RENDERED",
) -> dict[str, Any]:
    server = extract_server_candidates(html, source_url=source_url,
                                       page_number=page_number,
                                       evidence_file=evidence_file,
                                       page_instance_id=page_instance_id,
                                       representation_type=representation_type)
    embedded_client, embedded_acp, parse_statuses = extract_embedded_supplemental_with_status(
        html, source_url=source_url, page_number=page_number,
        evidence_file=evidence_file, page_instance_id=page_instance_id)
    client = list(embedded_client)
    if client_recs is not None:
        rows, status = extract_structured_candidates_with_status(
            client_recs, evidence_source="CLIENT_RECS", page_number=page_number,
            source_url=source_url, evidence_file=evidence_file,
            page_instance_id=page_instance_id, representation_type="CLIENT_RECS")
        client.extend(rows)
        parse_statuses.append({"evidence_source": "CLIENT_RECS", "status": status,
                               "evidence_file": evidence_file})
    acp = list(embedded_acp)
    if acp_response is not None:
        rows, status = extract_structured_candidates_with_status(
            acp_response, evidence_source="ACP", page_number=page_number,
            source_url=source_url, evidence_file=evidence_file,
            page_instance_id=page_instance_id, representation_type="ACP")
        acp.extend(rows)
        parse_statuses.append({"evidence_source": "ACP", "status": status,
                               "evidence_file": evidence_file})
    records, raw = resolve_candidates(server + client + acp)
    if expected_count is not None:
        count_source = expected_count_source or "CLI_ARGUMENT"
    else:
        count_from_html = expected_count_from_html(html)
        expected_count = count_from_html
        inferred_source = ("CLIENT_RECS_METADATA" if count_from_html is not None
                           and "data-client-recs-list" in html else
                           "HTML_METADATA" if count_from_html is not None else "UNKNOWN")
        count_source = expected_count_source or inferred_source
    audit = audit_identity_records(
        records, raw, server_rendered_count=len(server), client_recs_count=len(client),
        acp_identity_count=len(acp), expected_count=expected_count,
        expected_count_source=count_source,
        evidence_files=[evidence_file] if evidence_file else [],
        supplemental_parse_statuses=parse_statuses,
    )
    return {"records": records, "raw_candidates": raw, "audit": audit,
            "supplemental_parse_status": parse_statuses}


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _metadata(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*.json")):
        payload = _read_json(path)
        if isinstance(payload, list):
            rows = [row for row in payload if isinstance(row, Mapping)]
        else:
            rows = []
        if not isinstance(payload, Mapping):
            if not rows:
                continue
        else:
            for key in ("pages", "page_statuses", "source_statuses"):
                if isinstance(payload.get(key), list):
                    rows.extend(row for row in payload[key] if isinstance(row, Mapping))
            if not rows and any(key in payload for key in ("html_file", "source_url")):
                rows = [payload]
        named_row = False
        for row_index, row in enumerate(rows):
            name = str(row.get("html_file") or row.get("evidence_file") or "")
            if name:
                named_row = True
                result[Path(name).name] = dict(row)
            elif path.name == "page_statuses.json":
                # collect_rankings writes ``ranking_000.html`` in the same
                # order as its append-only page status list.
                result[f"ranking_{row_index:03d}.html"] = dict(row)
        # A compact evidence manifest may describe the directory as a whole
        # instead of naming each representation. Both initial/rendered files
        # must then resolve to the same page instance.
        if isinstance(payload, Mapping) and not named_row and any(
                key in payload for key in ("source_url", "page_number", "page_instance_id")):
            result["__default__"] = dict(payload)
    return result


def _page_instance(meta: Mapping[str, Any], path: Path, index: int,
                   evidence_root: Path | None = None) -> str:
    explicit = str(meta.get("page_instance_id") or "").strip()
    if explicit:
        return explicit
    page_number = meta.get("page_number")
    source_url = str(meta.get("source_url") or meta.get("url") or "")
    if page_number is not None or source_url:
        return "page:%s|url:%s" % (page_number if page_number is not None else index,
                                   source_url)
    # Legacy evidence often stores initial_html.html and rendered_html.html
    # side by side without metadata. Normalize the representation marker so
    # both files resolve to one page instance.
    stem = path.stem.lower()
    normalized = re.sub(r"(?:^|[_-])(initial|rendered)(?=[_-]|$)", "_", stem)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if normalized in {"", "html"}:
        normalized = "page"
    if normalized != stem:
        relative = path
        if evidence_root is not None and evidence_root.is_dir():
            try:
                relative = path.relative_to(evidence_root)
            except ValueError:
                pass
        return "legacy:%s:%s" % (relative.parent.as_posix(), normalized)
    # Stable fallback for other old evidence. The path is stable within a
    # saved evidence directory and does not depend on filesystem iteration.
    return "file:%s" % path.as_posix()


def _representation(path: Path, meta: Mapping[str, Any]) -> str:
    value = str(meta.get("representation_type") or "").upper()
    if value:
        return value
    name = path.stem.lower()
    if "initial" in name:
        return "INITIAL"
    if "rendered" in name:
        return "RENDERED"
    return "RENDERED"


def extract_identity_from_evidence(evidence_dir: str | Path, *, expected_count: int | None = None) -> dict[str, Any]:
    """Replay saved HTML/JSON evidence in deterministic directory order."""
    root = Path(evidence_dir)
    if root.is_file():
        html_paths = [root] if root.suffix.lower() in {".html", ".htm"} else []
    else:
        html_paths = sorted(root.rglob("*.html")) + sorted(root.rglob("*.htm"))
    if not html_paths:
        raise FileNotFoundError(f"没有找到 saved HTML evidence: {root}")
    metadata = _metadata(root if root.is_dir() else root.parent)
    json_paths = [path for path in sorted(root.rglob("*.json")) if path.name not in {
        "manifest.json", "audit.json", "identity.json"
    }] if root.is_dir() else []
    client_payloads = [_read_json(p) for p in json_paths if "client" in p.stem.lower() or "recs" in p.stem.lower()]
    acp_paths = [p for p in json_paths if "acp" in p.stem.lower()]
    all_server: list[dict[str, Any]] = []
    all_client: list[dict[str, Any]] = []
    all_acp: list[dict[str, Any]] = []
    evidence_files: list[str] = []
    expected_by_page: dict[str, int] = {}
    expected_sources: dict[str, str] = {}
    all_parse_statuses: list[dict[str, Any]] = []
    page_contexts: dict[str, dict[str, Any]] = {}
    for index, path in enumerate(html_paths, start=1):
        html = path.read_text(encoding="utf-8", errors="replace")
        meta = metadata.get(path.name, metadata.get("__default__", {}))
        source_url = str(meta.get("source_url") or meta.get("url") or "")
        page_number = meta.get("page_number", index)
        try:
            page_number = int(page_number)
        except (TypeError, ValueError):
            page_number = index
        page_instance_id = _page_instance(meta, path, index,
                                           root if root.is_dir() else root.parent)
        page_contexts.setdefault(page_instance_id, {
            "page_instance_id": page_instance_id,
            "ranking_source_url": source_url,
            "ranking_page_url": str(meta.get("page_url") or source_url),
            "ranking_page_number": page_number,
        })
        representation_type = _representation(path, meta)
        result = extract_identity_from_html(
            html, source_url=source_url, page_number=page_number,
            evidence_file=str(path.relative_to(root)) if root.is_dir() else path.name,
            expected_count=meta.get("expected_count"),
            expected_count_source=(str(meta.get("expected_count_source") or "RUN_MANIFEST")
                                   if meta.get("expected_count") is not None else None),
            page_instance_id=page_instance_id,
            representation_type=representation_type,
        )
        all_server.extend(row for row in result["raw_candidates"]
                          if row.get("evidence_source") == "SERVER_RENDERED_CARD")
        all_client.extend(row for row in result["raw_candidates"]
                          if row.get("evidence_source") == "CLIENT_RECS")
        all_acp.extend(row for row in result["raw_candidates"]
                        if row.get("evidence_source") == "ACP")
        all_parse_statuses.extend(result.get("supplemental_parse_status") or [])
        page_expected = meta.get("expected_count")
        if page_expected is None:
            page_expected = result["audit"].get("expected_count")
        if page_expected is not None:
            expected_by_page.setdefault(page_instance_id, int(page_expected))
            expected_sources.setdefault(
                page_instance_id,
                str(meta.get("expected_count_source") or result["audit"].get(
                    "expected_count_source") or "HTML_METADATA"))
        evidence_files.append(str(path.relative_to(root)) if root.is_dir() else path.name)
    # Standalone supplemental files are consumed only once. ACP response
    # envelopes carry page context; legacy single-page ACP payloads retain a
    # deterministic one-page fallback, while ambiguous multi-page payloads
    # fail closed instead of being assigned by file order.
    for value in client_payloads:
        rows, status = extract_structured_candidates_with_status(
            value, evidence_source="CLIENT_RECS", evidence_file="CLIENT_RECS",
            page_instance_id="supplemental:CLIENT_RECS",
            representation_type="CLIENT_RECS")
        all_client.extend(rows)
        all_parse_statuses.append({"evidence_source": "CLIENT_RECS", "status": status,
                                   "evidence_file": "CLIENT_RECS"})
    attribution_unknown_count = 0
    for path in acp_paths:
        payload = _read_json(path)
        context = payload.get("context") if isinstance(payload, Mapping) else None
        context = dict(context) if isinstance(context, Mapping) else {}
        page_instance_id = str(context.get("page_instance_id") or "")
        if not page_instance_id and len(page_contexts) == 1:
            page_instance_id = next(iter(page_contexts))
            context = {**page_contexts[page_instance_id], **context}
        if not page_instance_id or page_instance_id not in page_contexts:
            attribution_unknown_count += 1
            all_parse_statuses.append({"evidence_source": "ACP",
                                       "status": "ACP_PAGE_ATTRIBUTION_UNKNOWN",
                                       "evidence_file": str(path.relative_to(root))})
            continue
        page_context = {**page_contexts[page_instance_id], **context}
        response = payload.get("response") if isinstance(payload, Mapping) else None
        body = response.get("body") if isinstance(response, Mapping) else None
        if not isinstance(body, str):
            all_parse_statuses.append({"evidence_source": "ACP", "status": "UNSUPPORTED_SCHEMA",
                                       "evidence_file": str(path.relative_to(root))})
            continue
        page_url = str(page_context.get("ranking_page_url")
                       or page_context.get("ranking_source_url") or "")
        rows = extract_server_candidates(
            body, source_url=page_url,
            page_number=page_context.get("ranking_page_number"),
            evidence_file=str(path.relative_to(root)),
            page_instance_id=page_instance_id, representation_type="ACP")
        for row in rows:
            row["evidence_source"] = "ACP"
            row["representation_type"] = "ACP"
        if not rows:
            rows, status = extract_structured_candidates_with_status(
                body, evidence_source="ACP",
                page_number=page_context.get("ranking_page_number"),
                source_url=page_url, evidence_file=str(path.relative_to(root)),
                page_instance_id=page_instance_id, representation_type="ACP")
        else:
            status = "OK"
        all_acp.extend(rows)
        evidence_files.append(str(path.relative_to(root)))
        all_parse_statuses.append({"evidence_source": "ACP", "status": status,
                                   "evidence_file": str(path.relative_to(root)),
                                   "page_instance_id": page_instance_id})
    records, raw = resolve_candidates(all_server + all_client + all_acp)
    if expected_count is not None:
        final_expected = expected_count
        final_source = "CLI_ARGUMENT"
    elif expected_by_page:
        expected_slots = sum(expected_by_page.values())
        # A product identity is global by ASIN, while ranking slots are
        # page/context scoped. Never turn two contexts containing the same
        # ASIN into two required products.
        if len(expected_by_page) == 1:
            final_expected = expected_slots
            sources = set(expected_sources.values())
            final_source = next(iter(sources)) if len(sources) == 1 else "RUN_MANIFEST"
        else:
            final_expected = None
            final_source = "MULTI_PAGE_SLOT_COUNT_ONLY"
    else:
        final_expected = None
        final_source = "UNKNOWN"
    audit = audit_identity_records(
        records, raw, server_rendered_count=len(all_server),
        client_recs_count=len(all_client), acp_identity_count=len(all_acp),
        expected_count=final_expected, expected_count_source=final_source,
        expected_slot_count=(sum(expected_by_page.values()) if expected_by_page else None),
        evidence_files=evidence_files, supplemental_parse_statuses=all_parse_statuses,
        attribution_unknown_count=attribution_unknown_count,
    )
    return {"records": records, "raw_candidates": raw, "audit": audit,
            "evidence_files": evidence_files,
            "supplemental_parse_status": all_parse_statuses}


extract_identity = extract_identity_from_html
replay_saved_evidence = extract_identity_from_evidence
