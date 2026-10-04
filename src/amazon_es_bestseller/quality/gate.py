"""Final research-readiness decision."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

from .models import QualityStatus, ResearchStatus


def evaluate_research_readiness(
    checks: Mapping[str, Mapping] | Iterable[Mapping],
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
    return {
        "final_quality_status": final.value,
        "research_status": final.value,
        "research_ready": final is ResearchStatus.RESEARCH_READY,
        "checks": {str(row.get("check") or ""): str(row.get("status") or "PASS") for row in rows},
        "issue_counts": {key: counts.get(key, 0) for key in ("PASS", "WARN", "REVIEW", "BLOCK")},
        "warn_allowed": True,
    }
