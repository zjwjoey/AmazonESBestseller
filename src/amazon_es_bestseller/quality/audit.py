"""Unified offline Quality Gate orchestration."""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from .access import audit_access_evidence
from .category_provenance import audit_category_provenance
from .detail_identity import audit_detail_identity
from .detail_structure import audit_detail_structure
from .field_closure import audit_field_closure_quality
from .gate import evaluate_research_readiness
from .models import QUALITY_CONTRACT_VERSION
from .ranking_identity import audit_ranking_identity
from .ranking_integrity import audit_ranking_integrity
from .replay import audit_offline_replay


DEFAULT_CHECKS = (
    "ranking_identity", "ranking_integrity", "access_evidence", "detail_identity",
    "offline_replay", "detail_structure", "category_provenance", "field_closure",
)


def _rows(value: Iterable[Mapping] | None) -> list[dict]:
    return [copy.deepcopy(dict(row)) for row in (value or ()) if isinstance(row, Mapping)]


def _hash_rows(rows: list[dict]) -> str:
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _manifest_rows(source_manifest: object) -> tuple[list[dict], list[dict]]:
    if isinstance(source_manifest, Mapping):
        statuses = source_manifest.get("page_statuses") or source_manifest.get("source_statuses") or []
        pages = source_manifest.get("planned_pages") or source_manifest.get("pages") or statuses
    elif isinstance(source_manifest, list):
        statuses = source_manifest
        pages = source_manifest
    else:
        statuses, pages = [], []
    return ([dict(row) for row in statuses if isinstance(row, Mapping)],
            [dict(row) for row in pages if isinstance(row, Mapping)])


def _source_urls(rankings: list[dict]) -> list[str]:
    return sorted({str(row.get("ranking_source_url") or row.get("source_url") or "")
                   for row in rankings if str(row.get("ranking_source_url") or row.get("source_url") or "")})


def run_quality_audit(
    rankings: Iterable[Mapping] | None, details: Iterable[Mapping] | None = None,
    *, products: Iterable[Mapping] | None = None,
    ranking_html_dirs=None, detail_html_dirs=None, run_dir=None,
    source_manifest: object = None, translations: Mapping | None = None,
    workbook_path=None, asins: Iterable[str] | None = None,
    checks: Iterable[str] | None = None, profile: str = "stable-research",
    git_sha: str = "", network_mode: str = "OFFLINE_QUALITY_AUDIT",
    run_id: str = "",
) -> dict[str, Any]:
    ranking_rows = _rows(rankings)
    detail_rows = _rows(details)
    product_rows = _rows(products) if products is not None else None
    selected = tuple(checks or DEFAULT_CHECKS)
    asin_filter = {str(value).strip().upper() for value in (asins or ()) if str(value).strip()}
    status_rows, planned_pages = _manifest_rows(source_manifest)
    results = {}
    check_args = {
        "ranking_identity": lambda: audit_ranking_identity(ranking_rows, asins=asin_filter or None),
        "ranking_integrity": lambda: audit_ranking_integrity(
            ranking_rows, source_statuses=status_rows, asins=asin_filter or None),
        "access_evidence": lambda: audit_access_evidence(
            ranking_rows, detail_rows, source_statuses=status_rows, asins=asin_filter or None),
        "detail_identity": lambda: audit_detail_identity(
            detail_rows, ranking_rows, asins=asin_filter or None),
        "offline_replay": lambda: audit_offline_replay(
            ranking_rows, detail_rows, ranking_html_dirs=ranking_html_dirs,
            detail_html_dirs=detail_html_dirs, run_dir=run_dir,
            asins=asin_filter or None),
        "detail_structure": lambda: audit_detail_structure(
            detail_rows, asins=asin_filter or None),
        "category_provenance": lambda: audit_category_provenance(
            ranking_rows, detail_rows, asins=asin_filter or None),
        "field_closure": lambda: audit_field_closure_quality(
            product_rows, detail_rows, ranking_rows, html_dirs=detail_html_dirs,
            run_dir=run_dir, translations=translations, workbook_path=workbook_path,
            asins=asin_filter or None),
    }
    for name in selected:
        if name not in check_args:
            raise ValueError("unknown quality check: %s" % name)
        results[name] = check_args[name]().to_dict()
    gate = evaluate_research_readiness(results)
    all_issues = [row for value in results.values() for row in value.get("issues", [])]
    issue_counts = Counter(str(row.get("status") or "") for row in all_issues)
    summary = {
        "total_skus": len({str(row.get("asin") or "").upper() for row in ranking_rows + detail_rows
                            if str(row.get("asin") or "").strip()}),
        "ranking_records": len(ranking_rows),
        "detail_records": len(detail_rows),
        "pass": issue_counts.get("PASS", 0),
        "warn": issue_counts.get("WARN", 0),
        "review": issue_counts.get("REVIEW", 0),
        "block": issue_counts.get("BLOCK", 0),
        "issue_codes": dict(Counter(str(row.get("issue_code") or "") for row in all_issues)),
        "checks": gate["checks"],
        "final_quality_status": gate["final_quality_status"],
        "network_requests": 0,
    }
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    source_hashes = {
        "rankings": _hash_rows(ranking_rows),
        "details": _hash_rows(detail_rows),
        "products": _hash_rows(product_rows or []),
    }
    return {
        "run_id": run_id,
        "git_sha": git_sha,
        "profile": profile,
        "ranking_parser": "V1",
        "detail_parser": "V1",
        "quality_contract_version": QUALITY_CONTRACT_VERSION,
        "quality_checks_enabled": list(selected),
        "source_urls": _source_urls(ranking_rows),
        "planned_pages": planned_pages,
        "started_at": now,
        "completed_at": now,
        "network_mode": network_mode,
        "final_quality_status": gate["final_quality_status"],
        "gate": gate,
        "summary": summary,
        "checks": results,
        "issues": all_issues,
        "source_hashes": source_hashes,
    }
