"""Offline access-state audit; it never attempts recovery or a request."""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from .models import QualityStatus, check_result, issue


_BLOCKED = {"BLOCKED", "RATE_LIMITED", "CHALLENGE", "CAPTCHA", "BOT_BLOCK",
            "INTERSTITIAL", "ACCESS_DENIED"}
_UNKNOWN = {"", "UNKNOWN", "NONE", "NULL"}


def _effective_state(row: Mapping) -> str:
    for key in ("final_access_state", "access_state", "access_status", "status"):
        value = str(row.get(key) or "").upper()
        if value:
            return value
    return "UNKNOWN"


def audit_access_evidence(
    rankings: Iterable[Mapping], details: Iterable[Mapping],
    *, source_statuses: Iterable[Mapping] | None = None,
    asins: set[str] | None = None,
) -> object:
    issues = []
    observations = 0
    blocked = 0
    unknown = 0
    rows = [*(rankings or []), *(details or []), *(source_statuses or [])]
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        asin = str(row.get("asin") or row.get("ranking_asin") or "").upper()
        if asins and asin and asin not in asins:
            continue
        observations += 1
        state = _effective_state(row)
        status_code = row.get("http_status", row.get("status_code"))
        try:
            normalized_status_code = int(status_code) if status_code is not None else None
        except (TypeError, ValueError):
            normalized_status_code = None
        is_blocked = state in _BLOCKED or normalized_status_code in (403, 429)
        if is_blocked:
            blocked += 1
            issues.append(issue(
                "access_evidence", QualityStatus.BLOCK, "P0", "ACCESS_BLOCKED",
                asin=asin, source="access", source_file=row.get("evidence_file") or row.get("html_file") or "",
                message="原始采集证据显示访问受限，不能由本地解析洗白。",
                evidence={"access_state": state, "http_status": status_code,
                          "error": row.get("error")}))
        elif state in _UNKNOWN or normalized_status_code is None:
            unknown += 1
        elif state not in {"NORMAL", "SUCCESS", "COMPLETE", "AUTHORITATIVE", "200"}:
            unknown += 1
            issues.append(issue(
                "access_evidence", QualityStatus.REVIEW, "P2", "ACCESS_STATE_UNKNOWN",
                asin=asin, source="access", message="采集状态无法明确归类为 NORMAL。",
                evidence={"access_state": state, "http_status": status_code}))
        if row.get("recovered_from_challenge") and state in {"NORMAL", "SUCCESS"}:
            issues.append(issue(
                "access_evidence", QualityStatus.WARN, "P2", "CHALLENGE_RECOVERED",
                asin=asin, source="access", message="页面曾经历挑战后恢复；保留该运行提示。",
                evidence={"access_state": state}))
    if observations == 0:
        issues.append(issue(
            "access_evidence", QualityStatus.REVIEW, "P2", "ACCESS_EVIDENCE_MISSING",
            source="access", message="输入没有可核验的访问状态证据。"))
    elif unknown and not blocked:
        issues.append(issue(
            "access_evidence", QualityStatus.REVIEW, "P2", "ACCESS_EVIDENCE_INCOMPLETE",
            source="access", message="部分记录没有明确访问状态；需要人工确认运行证据。",
            evidence={"unknown_count": unknown, "observations": observations}))
    return check_result(
        "access_evidence", issues,
        summary={"observations": observations, "blocked_count": blocked,
                 "unknown_count": unknown},
    )
