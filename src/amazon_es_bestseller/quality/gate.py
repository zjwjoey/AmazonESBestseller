"""Final research-readiness decision."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

from .models import QualityStatus, ResearchStatus


def evaluate_research_readiness(
    checks: Mapping[str, Mapping] | Iterable[Mapping],
    *, required_checks: Iterable[str] | None = None, scope_complete: bool = True,
) -> dict:
    if isinstance(checks, Mapping):
        rows = list(checks.values())
    else:
        rows = list(checks or [])
    issues = [issue for row in rows for issue in (row.get("issues") or [])]
    statuses = [str(row.get("status") or "").upper() for row in rows]
    allowed = {status.value for status in QualityStatus}
    invalid = sorted({str(row.get("check") or "<unnamed>") for row in rows
                      if not str(row.get("check") or "").strip()
                      or str(row.get("status") or "").upper() not in allowed
                      or any(str(item.get("status") or "").upper() not in allowed
                             for item in (row.get("issues") or []))})
    counts = Counter(str(issue.get("status") or "").upper() for issue in issues)
    if invalid or "BLOCK" in statuses or counts.get(QualityStatus.BLOCK.value, 0):
        final = ResearchStatus.BLOCKED
    elif "REVIEW" in statuses or counts.get(QualityStatus.REVIEW.value, 0):
        final = ResearchStatus.REVIEW_REQUIRED
    elif rows:
        final = ResearchStatus.RESEARCH_READY
    else:
        final = ResearchStatus.PARTIAL_REVIEW
    scope_status = final.value
    completed = {str(row.get("check") or "") for row in rows}
    missing = sorted(set(required_checks or ()) - completed)
    if final is ResearchStatus.RESEARCH_READY and (missing or not scope_complete):
        final = ResearchStatus.PARTIAL_REVIEW
    return {
        "final_quality_status": final.value,
        "research_status": final.value,
        "research_ready": final is ResearchStatus.RESEARCH_READY,
        "scope_research_status": scope_status,
        "checks_complete": bool(rows) and not missing and not invalid,
        "scope_complete": scope_complete,
        "missing_checks": missing,
        "invalid_checks": invalid,
        "checks": {str(row.get("check") or ""): str(row.get("status") or "").upper() for row in rows},
        "issue_counts": {key: counts.get(key, 0) for key in ("PASS", "WARN", "REVIEW", "BLOCK")},
        "warn_allowed": True,
    }
