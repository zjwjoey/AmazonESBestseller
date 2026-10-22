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
    statuses = [str(row.get("status") or "PASS").upper() for row in rows]
    counts = Counter(str(issue.get("status") or "") for issue in issues)
    if "BLOCK" in statuses or counts.get(QualityStatus.BLOCK.value, 0):
        final = ResearchStatus.BLOCKED
    elif "REVIEW" in statuses or counts.get(QualityStatus.REVIEW.value, 0):
        final = ResearchStatus.REVIEW_REQUIRED
    else:
        final = ResearchStatus.RESEARCH_READY
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
        "checks_complete": not missing,
        "scope_complete": scope_complete,
        "missing_checks": missing,
        "checks": {str(row.get("check") or ""): str(row.get("status") or "PASS") for row in rows},
        "issue_counts": {key: counts.get(key, 0) for key in ("PASS", "WARN", "REVIEW", "BLOCK")},
        "warn_allowed": True,
    }
