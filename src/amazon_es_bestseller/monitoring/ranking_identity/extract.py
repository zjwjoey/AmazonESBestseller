"""Saved-evidence extraction entry points; never performs network access."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .adapters import (
    expected_count_from_html,
    extract_embedded_supplemental,
    extract_server_candidates,
    extract_structured_candidates,
)
from .completeness import audit_identity_records
from .resolver import resolve_candidates


def extract_identity_from_html(
    html: str, *, source_url: str = "", page_number: int | None = 1,
    evidence_file: str | None = None, client_recs: object = None,
    acp_response: object = None, expected_count: int | None = None,
) -> dict[str, Any]:
    server = extract_server_candidates(html, source_url=source_url,
                                       page_number=page_number,
                                       evidence_file=evidence_file)
    embedded_client, embedded_acp = extract_embedded_supplemental(
        html, source_url=source_url, page_number=page_number,
        evidence_file=evidence_file)
    client = embedded_client + extract_structured_candidates(
        client_recs, evidence_source="CLIENT_RECS", page_number=page_number,
        source_url=source_url, evidence_file=evidence_file) if client_recs is not None else embedded_client
    acp = embedded_acp + extract_structured_candidates(
        acp_response, evidence_source="ACP", page_number=page_number,
        source_url=source_url, evidence_file=evidence_file) if acp_response is not None else embedded_acp
    records, raw = resolve_candidates(server + client + acp)
    audit = audit_identity_records(
        records, raw, server_rendered_count=len(server), client_recs_count=len(client),
        acp_identity_count=len(acp), expected_count=(
            expected_count if expected_count is not None else expected_count_from_html(html)),
        evidence_files=[evidence_file] if evidence_file else [],
    )
    return {"records": records, "raw_candidates": raw, "audit": audit}


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
        for row_index, row in enumerate(rows):
            name = str(row.get("html_file") or row.get("evidence_file") or "")
            if name:
                result[Path(name).name] = dict(row)
            elif path.name == "page_statuses.json":
                # collect_rankings writes ``ranking_000.html`` in the same
                # order as its append-only page status list.
                result[f"ranking_{row_index:03d}.html"] = dict(row)
    return result


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
    acp_payloads = [_read_json(p) for p in json_paths if "acp" in p.stem.lower()]
    all_server: list[dict[str, Any]] = []
    all_client: list[dict[str, Any]] = []
    all_acp: list[dict[str, Any]] = []
    evidence_files: list[str] = []
    expected_values: list[int] = []
    for index, path in enumerate(html_paths, start=1):
        html = path.read_text(encoding="utf-8", errors="replace")
        meta = metadata.get(path.name, {})
        source_url = str(meta.get("source_url") or meta.get("url") or "")
        page_number = meta.get("page_number", index)
        try:
            page_number = int(page_number)
        except (TypeError, ValueError):
            page_number = index
        result = extract_identity_from_html(
            html, source_url=source_url, page_number=page_number,
            evidence_file=str(path.relative_to(root)) if root.is_dir() else path.name,
            expected_count=meta.get("expected_count"),
        )
        all_server.extend(row for row in result["raw_candidates"]
                          if row.get("evidence_source") == "SERVER_RENDERED_CARD")
        all_client.extend(row for row in result["raw_candidates"]
                          if row.get("evidence_source") == "CLIENT_RECS")
        all_acp.extend(row for row in result["raw_candidates"]
                        if row.get("evidence_source") == "ACP")
        page_expected = meta.get("expected_count") or result["audit"].get("expected_count")
        if page_expected is not None:
            expected_values.append(int(page_expected))
        evidence_files.append(str(path.relative_to(root)) if root.is_dir() else path.name)
    # Standalone supplemental files are intentionally consumed only once and
    # remain offline evidence; embedded payloads are already present above.
    for payload, source, target in ((client_payloads, "CLIENT_RECS", all_client),
                                    (acp_payloads, "ACP", all_acp)):
        for value in payload:
            target.extend(extract_structured_candidates(value, evidence_source=source,
                                                        evidence_file=str(source)))
    records, raw = resolve_candidates(all_server + all_client + all_acp)
    audit = audit_identity_records(
        records, raw, server_rendered_count=len(all_server),
        client_recs_count=len(all_client), acp_identity_count=len(all_acp),
        expected_count=(expected_count if expected_count is not None
                        else sum(expected_values) if expected_values else None),
        evidence_files=evidence_files,
    )
    return {"records": records, "raw_candidates": raw, "audit": audit,
            "evidence_files": evidence_files}


extract_identity = extract_identity_from_html
replay_saved_evidence = extract_identity_from_evidence
