"""Evidence-backed page, source and run gates for the isolated snapshot layer."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
from html import unescape
import json
from pathlib import Path
import re
from typing import Iterable

from bs4 import BeautifulSoup

from ..access.detector import detect_access_status
from . import SCHEMA_VERSION
from .contracts import ORIGINS, RANK_SEMANTICS, RESEARCH_CATEGORIES, SOURCE_ROOTS, SOURCE_STATUSES, timestamp
from .sources import _validate_live_receipt


RESTRICTED = {"BLOCKED", "CHALLENGE", "CAPTCHA", "ROBOT_CHECK", "ACCESS_DENIED", "RATE_LIMITED", "HTTP_403", "HTTP_429"}
CONFLICTS = {"CONFLICT", "IDENTITY_CONFLICT", "QUARANTINED"}


def _finding(code, *, severity="ERROR", **context):
    return {"code": code, "severity": severity, **context}


def confirmed(row: dict) -> bool:
    """Never infer an entity from a raw ASIN or a family identifier."""
    return bool(row.get("product_id") and row.get("identity_status") == "CONFIRMED"
                and not row.get("identity_conflict_reason"))


def _evidence(path, checksum, root=None):
    if not path or not checksum:
        return False, "EVIDENCE_METADATA_MISSING", None
    resolved = Path(path)
    if not resolved.is_absolute() and root:
        resolved = Path(root) / resolved
    try:
        data = resolved.read_bytes()
    except OSError:
        return False, "EVIDENCE_MISSING", None
    if not data or hashlib.sha256(data).hexdigest() != checksum:
        return False, "EVIDENCE_CORRUPT", None
    return True, None, data


def _page_key(page):
    return (page.get("source_id"), page.get("page_url"))


def _count_proof(page, data):
    """Verify declared counts against source metadata, never configured targets."""
    expected, proof = page.get("expected_count"), page.get("expected_count_evidence")
    if (not isinstance(expected, int) or isinstance(expected, bool) or expected < 0
            or not isinstance(proof, dict) or proof.get("invalid")
            or proof.get("evidence_sha256") != page.get("evidence_sha256")):
        return False
    try:
        if int(proof.get("value")) != expected:
            return False
    except (TypeError, ValueError):
        return False
    text = data.decode("utf-8", errors="replace")
    soup = BeautifulSoup(text, "lxml")
    kind, raw = proof.get("kind"), proof.get("raw")
    if not isinstance(raw, str) or not raw:
        return False
    if kind == "HTML_ATTRIBUTE" and proof.get("locator") == "[data-expected-count]":
        values = [node.get("data-expected-count") for node in soup.select("[data-expected-count]")]
        return (bool(re.search(r"data-expected-count\s*=\s*(['\"])\d+\1", raw))
                and raw in text and str(expected) in values)
    if kind == "CLIENT_RECS_METADATA" and proof.get("locator") == "[data-client-recs-list]":
        for node in soup.select("[data-client-recs-list]"):
            value = node.get("data-client-recs-list")
            if value != unescape(raw):
                continue
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, list) and len(parsed) == expected:
                return True
    return False


def _status(findings, completeness_verified):
    codes = {item["code"] for item in findings}
    if codes & {"ACCESS_RESTRICTED", "ACCESS_GATE_NOT_STOPPED"}:
        return "BLOCKED"
    if codes & {"EVIDENCE_CORRUPT", "EVIDENCE_MISSING", "EVIDENCE_METADATA_MISSING", "WRONG_PAGE_TYPE", "PAGE_TYPE_UNKNOWN", "NO_PRODUCT_CONTENT", "LIVE_CAPTURE_PROVENANCE_INVALID"}:
        return "FAILED"
    if any(item["severity"] == "ERROR" for item in findings):
        return "PARTIAL"
    return "COMPLETE" if completeness_verified else "UNVERIFIED"


def coverage_matrix(coverage: Iterable[dict] | dict | None = None) -> list[dict]:
    """All 75 logical combinations, including evidence-backed absent sources."""
    if isinstance(coverage, dict):
        coverage = coverage.get("combinations", coverage.get("coverage", coverage.get("sources", [])))
    groups = defaultdict(list)
    for row in coverage or []:
        if isinstance(row, dict):
            groups[(row.get("source_type"), row.get("research_category"))].append(row)
    result = []
    for source_type in SOURCE_ROOTS:
        for category in RESEARCH_CATEGORIES:
            rows = groups.get((source_type, category), [])
            statuses = {row.get("source_status", row.get("status", "UNVERIFIED")) for row in rows}
            # A failed fetch cannot establish that a combination is unsupported.
            available = [row for row in rows if row.get("source_status", row.get("status")) == "AVAILABLE"
                         and row.get("verification_evidence")]
            if available:
                status = "AVAILABLE"
            elif "BLOCKED" in statuses:
                status = "BLOCKED"
            elif "TEMPORARILY_UNAVAILABLE" in statuses:
                status = "TEMPORARILY_UNAVAILABLE"
            elif rows and all(row.get("source_status", row.get("status")) == "UNSUPPORTED"
                              and row.get("unsupported_evidence") for row in rows):
                status = "UNSUPPORTED"
            else:
                status = "UNVERIFIED"
            assert status in SOURCE_STATUSES
            identifiers = {row["source_id"] for row in rows if row.get("source_id")}
            identifiers.update(identifier for row in rows for identifier in row.get("source_ids", []) if identifier)
            proofs = {}
            for field in ("discovery_evidence", "verification_evidence", "unsupported_evidence"):
                proofs[field] = [item for row in rows for item in
                                 (row.get(field, []) if isinstance(row.get(field), list) else [row[field]] if row.get(field) else [])]
            result.append({"source_type": source_type, "research_category": category,
                           "source_status": status, "source_ids": sorted(identifiers),
                           **proofs,
                           "evidence": [row.get("discovery_evidence") or row.get("verification_evidence") or row.get("unsupported_evidence")
                                        for row in rows if row.get("discovery_evidence") or row.get("verification_evidence") or row.get("unsupported_evidence")]})
    return result


class SnapshotQualityGate:
    def audit_page(self, page: dict, observations: Iterable[dict], *, evidence_root=None) -> dict:
        rows = list(observations)
        source_type = page.get("source_type")
        findings = []
        valid, error, data = _evidence(page.get("evidence_path"), page.get("evidence_sha256"), evidence_root)
        if error:
            findings.append(_finding(error))
        access = str(page.get("access_status", page.get("access_state", "UNKNOWN"))).upper()
        code = page.get("http_status", page.get("http_status_code", page.get("status_code")))
        if valid:
            observed_access = detect_access_status(code, data.decode("utf-8", errors="replace")).value
            if observed_access in RESTRICTED:
                access = observed_access
        if access in RESTRICTED or code in (403, 429):
            findings.append(_finding("ACCESS_RESTRICTED", access_status=access, http_status=code))
        elif access not in {"NORMAL", "SUCCESS"}:
            findings.append(_finding("ACCESS_STATUS_UNVERIFIED"))
        live_provenance = False
        if page.get("origin") == "LIVE_CAPTURE":
            try:
                _validate_live_receipt(page, row_id=page.get("source_id"), source_url=page.get("page_url"))
                live_provenance = True
            except (OSError, ValueError, TypeError, KeyError) as exc:
                findings.append(_finding("LIVE_CAPTURE_PROVENANCE_INVALID", reason=str(exc)))
        recognized = page.get("recognized_page_type", page.get("source_type_verified"))
        if recognized is not True:
            findings.append(_finding("WRONG_PAGE_TYPE" if recognized is False else "PAGE_TYPE_UNKNOWN"))
        if source_type not in RANK_SEMANTICS:
            findings.append(_finding("UNKNOWN_SOURCE_TYPE"))
        expected_semantics = RANK_SEMANTICS.get(source_type)
        page_semantics = page.get("rank_semantics", expected_semantics)
        if page_semantics != expected_semantics or any(row.get("rank_semantics") != expected_semantics for row in rows):
            findings.append(_finding("RANK_SEMANTICS_MISMATCH"))
        for row in rows:
            if row.get("source_type") != source_type or row.get("source_id") != page.get("source_id"):
                findings.append(_finding("OBSERVATION_SOURCE_MISMATCH", observation_id=row.get("observation_id")))
            if row.get("research_category") != page.get("research_category"):
                findings.append(_finding("OBSERVATION_CATEGORY_MISMATCH", observation_id=row.get("observation_id")))
        expected = page.get("expected_count")
        proof = page.get("expected_count_evidence")
        explicit_count = isinstance(expected, int) and not isinstance(expected, bool) and expected >= 0
        count_proven = False
        if explicit_count and proof and valid:
            count_proven = _count_proof(page, data)
        if not count_proven:
            findings.append(_finding("COMPLETENESS_UNVERIFIED", severity="WARNING"))
        elif len(rows) != expected:
            findings.append(_finding("EXPECTED_COUNT_MISMATCH", expected_count=expected, actual_count=len(rows)))
        if not rows and expected != 0:
            findings.append(_finding("NO_PRODUCT_CONTENT"))
        if page.get("actual_count") is not None and page["actual_count"] != len(rows):
            findings.append(_finding("PARSER_COUNT_MISMATCH"))
        if page.get("rendered_count") is not None and page["rendered_count"] > len(rows) and not page.get("acp_required"):
            findings.append(_finding("PARSER_MISSED_SLOTS"))
        slots = Counter(row.get("slot_position") for row in rows)
        if any(slot is None for slot in slots):
            findings.append(_finding("SLOT_POSITION_MISSING"))
        duplicates = [slot for slot, count in slots.items() if count > 1]
        if duplicates:
            findings.append(_finding("DUPLICATE_PRODUCT_SLOT", slots=duplicates))
        ranks = []
        for row in rows:
            rank = row.get("rank_value")
            if rank is not None:
                if not isinstance(rank, int) or isinstance(rank, bool) or rank <= 0:
                    findings.append(_finding("INVALID_VISIBLE_RANK", observation_id=row.get("observation_id")))
                else:
                    ranks.append(rank)
            if not (row.get("raw_product_url") or row.get("product_url_raw")):
                findings.append(_finding("PRODUCT_LINK_MISSING", observation_id=row.get("observation_id")))
            identity_status = row.get("identity_status", "UNCONFIRMED")
            if identity_status in CONFLICTS or row.get("identity_conflict_reason"):
                findings.append(_finding("IDENTITY_CONFLICT", observation_id=row.get("observation_id")))
                if row.get("product_id"):
                    findings.append(_finding("CONFLICT_AUTO_ASSIGNED_PRODUCT", observation_id=row.get("observation_id")))
            elif row.get("product_id") and not confirmed(row):
                findings.append(_finding("UNCONFIRMED_ASSIGNED_PRODUCT", observation_id=row.get("observation_id")))
            if row.get("identity_status") == "CONFIRMED" and not row.get("product_id"):
                findings.append(_finding("CONFIRMED_PRODUCT_ID_MISSING", observation_id=row.get("observation_id")))
        duplicated_ranks = [rank for rank, count in Counter(ranks).items() if count > 1]
        if duplicated_ranks:
            findings.append(_finding("DUPLICATE_VISIBLE_RANK", ranks=duplicated_ranks))
        if page.get("contiguous_ranks") is True:
            start, end = page.get("expected_rank_start"), page.get("expected_rank_end")
            if isinstance(start, int) and isinstance(end, int) and 0 < start <= end:
                missing = sorted(set(range(start, end + 1)) - set(ranks))
                excess = sorted(set(ranks) - set(range(start, end + 1)))
                if missing or excess:
                    findings.append(_finding("RANK_COVERAGE_GAP", missing=missing, outside_expected_range=excess))
            else:
                findings.append(_finding("RANK_COVERAGE_UNVERIFIED", severity="WARNING"))
        if page.get("acp_required") and (page.get("acp_complete") is not True or page.get("acp_pending")):
            findings.append(_finding("ACP_INCOMPLETE"))
        if page.get("category_navigation_valid") is False:
            findings.append(_finding("CATEGORY_NAVIGATION_MISMATCH"))
        elif page.get("browse_node_id") and page.get("category_navigation_valid") is not True:
            findings.append(_finding("CATEGORY_NAVIGATION_UNVERIFIED", severity="WARNING"))
        for flag in page.get("flags", []):
            flag_code = flag.get("code") if isinstance(flag, dict) else str(flag)
            if flag_code and flag_code not in {item["code"] for item in findings}:
                findings.append(_finding(flag_code, severity="WARNING" if "UNVERIFIED" in flag_code else "ERROR"))
        return {"source_id": page.get("source_id"), "source_type": source_type,
                "page_url": page.get("page_url"), "page_number": page.get("page_number"),
                "status": _status(findings, count_proven and not any("UNVERIFIED" in item["code"] for item in findings)), "expected_count": expected if explicit_count else None,
                "actual_count": len(rows), "evidence_valid": valid, "completeness_evidence_verified": count_proven,
                "live_provenance_verified": live_provenance,
                "findings": findings}

    def audit_snapshot(self, manifest: dict, pages: Iterable[dict], observations: Iterable[dict]) -> dict:
        pages, rows = list(pages), list(observations)
        findings = []
        grouped = defaultdict(list)
        for row in rows:
            grouped[_page_key(row)].append(row)
        audits = [self.audit_page(page, grouped[_page_key(page)], evidence_root=manifest.get("evidence_root")) for page in pages]
        for audit in audits:
            findings.extend({**item, "source_id": audit["source_id"], "page_url": audit["page_url"]} for item in audit["findings"])
        keys = Counter(_page_key(page) for page in pages)
        if any(count > 1 for count in keys.values()):
            findings.append(_finding("DUPLICATE_PAGE_RECORD"))
        if manifest.get("origin") not in ORIGINS:
            findings.append(_finding("ORIGIN_UNKNOWN"))
        if manifest.get("frozen") is False or manifest.get("in_progress") is True:
            findings.append(_finding("SNAPSHOT_NOT_FROZEN"))
        for page in pages:
            if (page.get("origin") is not None and page["origin"] != manifest.get("origin")) or (manifest.get("origin") == "LIVE_CAPTURE" and page.get("origin") != "LIVE_CAPTURE"):
                findings.append(_finding("PAGE_ORIGIN_MISMATCH", source_id=page.get("source_id"), page_url=page.get("page_url")))
        observation_ids = Counter(row.get("observation_id") for row in rows)
        if None in observation_ids or "" in observation_ids:
            findings.append(_finding("OBSERVATION_ID_MISSING"))
        if any(count > 1 for count in observation_ids.values()):
            findings.append(_finding("DUPLICATE_OBSERVATION_ID"))
        traces = 0
        evidence_map = {str(page.get("evidence_path")): page.get("evidence_sha256") for page in pages}
        for page in pages:
            for item in page.get("evidence_files", []):
                if isinstance(item, dict):
                    item_path = item.get("path", item.get("evidence_path"))
                    item_hash = item.get("sha256", item.get("evidence_sha256"))
                    evidence_map[str(item_path)] = item_hash
                    valid, error, _ = _evidence(item_path, item_hash, manifest.get("evidence_root"))
                    if not valid:
                        findings.append(_finding(error, source_id=page.get("source_id"), evidence_path=item_path))
        for row in rows:
            path = row.get("evidence_path")
            valid, error, _ = _evidence(path, row.get("evidence_sha256") or evidence_map.get(str(path)), manifest.get("evidence_root"))
            if valid:
                traces += 1
            else:
                findings.append(_finding(error, observation_id=row.get("observation_id")))
            if _page_key(row) not in keys:
                findings.append(_finding("OBSERVATION_PAGE_NOT_REGISTERED", observation_id=row.get("observation_id")))
            try:
                if not row.get("observed_at"):
                    raise ValueError("OBSERVATION_TIMESTAMP_MISSING")
                local_date = timestamp(row["observed_at"])["local_date"]
                if row.get("local_date") and row["local_date"] != local_date:
                    raise ValueError("OBSERVATION_DATE_TIMESTAMP_MISMATCH")
                if manifest.get("local_date") and manifest["local_date"] != local_date:
                    raise ValueError("OBSERVATION_OUTSIDE_SNAPSHOT_DATE")
            except (TypeError, ValueError) as exc:
                findings.append(_finding("OBSERVATION_TIME_INVALID", observation_id=row.get("observation_id"), reason=str(exc)))
        # Identical pagination content is a failed coverage attempt, even when
        # its HTTP response and per-page expected count are valid.
        page_content = defaultdict(list)
        page_slots = defaultdict(list)
        for page in pages:
            if page.get("evidence_sha256"):
                page_content[(page.get("source_id"), page["evidence_sha256"])].append(page.get("page_url"))
            signatures = tuple((row.get("rank_value"), row.get("asin_from_card"), row.get("asin_from_raw_url"),
                                row.get("raw_product_url", row.get("product_url_raw")))
                               for row in grouped[_page_key(page)])
            if signatures:
                page_slots[(page.get("source_id"), signatures)].append(page.get("page_url"))
        for (source_id, _), urls in page_content.items():
            if len(set(urls)) > 1:
                findings.append(_finding("DUPLICATE_PAGINATION_CONTENT", source_id=source_id, page_urls=urls))
        for (source_id, _), urls in page_slots.items():
            if len(set(urls)) > 1:
                findings.append(_finding("DUPLICATE_PAGINATION_SLOTS", source_id=source_id, page_urls=urls))
        planned = manifest.get("planned_pages", [])
        if isinstance(planned, int):
            if planned != len(pages):
                findings.append(_finding("PLANNED_PAGE_COUNT_MISMATCH", expected=planned, actual=len(pages)))
        elif planned:
            expected_keys = {_page_key(item) for item in planned}
            if expected_keys - set(keys):
                findings.append(_finding("PLANNED_PAGE_MISSING", missing_pages=sorted(expected_keys - set(keys))))
            if set(keys) - expected_keys:
                findings.append(_finding("UNPLANNED_PAGE", unexpected_pages=sorted(set(keys) - expected_keys)))
        source_audits = []
        for source_id in sorted({page.get("source_id") or "UNKNOWN" for page in pages}):
            selected = [audit for audit in audits if (audit["source_id"] or "UNKNOWN") == source_id]
            source_findings = [item for item in findings if item.get("source_id") == source_id]
            statuses = Counter(item["status"] for item in selected)
            complete = bool(selected) and all(item["status"] == "COMPLETE" for item in selected)
            source_audits.append({"source_id": source_id, "status": _status(source_findings, complete),
                                  "page_counts": dict(statuses), "observations": sum(item["actual_count"] for item in selected)})
        restricted = any(item["code"] == "ACCESS_RESTRICTED" for item in findings)
        counters = manifest.get("counters", {})
        if restricted and manifest.get("origin") == "LIVE_CAPTURE" and manifest.get("access_stop_enforced") is not True:
            findings.append(_finding("ACCESS_GATE_NOT_STOPPED"))
        page_counts = Counter(audit["status"] for audit in audits)
        confirmed_ids = {row["product_id"] for row in rows if confirmed(row)}
        confirmed_count = sum(confirmed(row) for row in rows)
        conflict_count = sum(row.get("identity_status") in CONFLICTS or bool(row.get("identity_conflict_reason")) for row in rows)
        candidates = set()
        for row in rows:
            if row.get("asin_from_card") or row.get("asin_observed") or row.get("observed_asin"):
                candidates.add(("ASIN_CANDIDATE", row.get("asin_from_card") or row.get("asin_observed") or row.get("observed_asin")))
            elif row.get("raw_product_url") or row.get("product_url_raw"):
                candidates.add(("URL_CANDIDATE", row.get("raw_product_url") or row.get("product_url_raw")))
        origin = manifest.get("origin")
        complete = bool(pages) and all(audit["status"] == "COMPLETE" for audit in audits)
        if not pages:
            findings.append(_finding("NO_PAGES"))
        status = _status(findings, complete)
        errors = [item for item in findings if item["severity"] == "ERROR"]
        gate_passed = status == "COMPLETE" and not errors
        matrix = coverage_matrix(manifest.get("source_coverage", manifest.get("coverage", manifest.get("sources"))))
        return {"schema_version": SCHEMA_VERSION, "run_id": manifest.get("run_id"), "snapshot_id": manifest.get("snapshot_id"),
                "origin": origin, "stage": manifest.get("stage", manifest.get("phase")), "status": status,
                "gate_passed": gate_passed, "gate": "PASS" if gate_passed else "FAIL",
                "gate_block_reasons": sorted({item["code"] for item in findings if item["severity"] == "ERROR"}
                                              | ({"PAGE_COMPLETENESS_UNVERIFIED"} if not complete else set())),
                "counts": {"requests": counters.get("requests", counters.get("request_count", manifest.get("request_count", 0))),
                    "proven_live_page_snapshots": sum(item["live_provenance_verified"] for item in audits),
                    "page_snapshots": len(pages), "observations": len(rows), "unique_observation_ids": len(set(observation_ids) - {None, ""}),
                    "candidate_identity_buckets": len(candidates), "confirmed_unique_products": len(confirmed_ids),
                    "confirmed_observations": sum(confirmed(row) for row in rows), "identity_conflicts": conflict_count,
                    "unconfirmed_observations": sum(not confirmed(row) for row in rows),
                    "repeated_confirmed_observations": max(0, sum(confirmed(row) for row in rows) - len(confirmed_ids)),
                    "traceable_observations": traces, "page_statuses": dict(page_counts)},
                "rates": {"evidence_traceability": traces / len(rows) if rows else None,
                          "identity_confirmation": confirmed_count / len(rows) if rows else None,
                          "repeated_confirmed_observation_rate": (confirmed_count - len(confirmed_ids)) / confirmed_count if confirmed_count else None,
                          "page_completeness": page_counts["COMPLETE"] / len(pages) if pages else None},
                "source_coverage": matrix, "coverage_status_counts": dict(Counter(item["source_status"] for item in matrix)),
                "pages": audits, "sources": source_audits, "findings": findings,
                "metric_notes": ["Candidate identity buckets are unverified ASIN/URL clues, not unique product entities.",
                                 "Ranks are visible source-specific list positions; no sales or sales-growth estimate is produced.",
                                 "OFFLINE_FIXTURE and HISTORICAL_REPLAY are not new live captures."]}


def audit_snapshot(manifest, pages, observations):
    return SnapshotQualityGate().audit_snapshot(manifest, pages, observations)
