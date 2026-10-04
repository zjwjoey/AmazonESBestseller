"""Offline quality gates for the stable V1 research pipeline.

The quality package consumes saved V1 evidence and derived records.  It never
requests Amazon and never mutates its inputs.
"""

from .audit import DEFAULT_CHECKS, run_quality_audit
from .gate import evaluate_research_readiness
from .models import QualityCheckResult, QualityIssue, QualityStatus, ResearchStatus

__all__ = [
    "DEFAULT_CHECKS",
    "QualityCheckResult",
    "QualityIssue",
    "QualityStatus",
    "ResearchStatus",
    "evaluate_research_readiness",
    "run_quality_audit",
]
