"""Shared immutable-ish JSON-friendly quality result contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


QUALITY_CONTRACT_VERSION = "1"


class QualityStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"


class ResearchStatus(str, Enum):
    RESEARCH_READY = "RESEARCH_READY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class QualityIssue:
    check: str
    status: str
    severity: str
    issue_code: str
    asin: str = ""
    source: str = ""
    message: str = ""
    source_file: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asin": self.asin,
            "stage": self.source or self.check,
            "check": self.check,
            "severity": self.severity,
            "status": self.status,
            "issue_code": self.issue_code,
            "message": self.message,
            "source_file": self.source_file,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class QualityCheckResult:
    check: str
    status: str
    issues: tuple[QualityIssue, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "status": self.status,
            "summary": dict(self.summary),
            "evidence": dict(self.evidence),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def issue(
    check: str, status: QualityStatus, severity: str, issue_code: str, *,
    asin: object = "", source: str = "", message: str = "",
    source_file: object = "", evidence: Mapping[str, Any] | None = None,
) -> QualityIssue:
    """Construct a normalized issue without modifying supplied evidence."""
    return QualityIssue(
        check=check,
        status=status.value,
        severity=str(severity),
        issue_code=issue_code,
        asin=str(asin or "").strip().upper(),
        source=source,
        message=message,
        source_file=str(source_file or ""),
        evidence=dict(evidence or {}),
    )


def status_from_issues(issues: list[QualityIssue]) -> QualityStatus:
    if any(row.status == QualityStatus.BLOCK.value for row in issues):
        return QualityStatus.BLOCK
    if any(row.status == QualityStatus.REVIEW.value for row in issues):
        return QualityStatus.REVIEW
    if any(row.status == QualityStatus.WARN.value for row in issues):
        return QualityStatus.WARN
    return QualityStatus.PASS


def check_result(
    check: str, issues: list[QualityIssue], *, summary: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> QualityCheckResult:
    status = status_from_issues(issues)
    return QualityCheckResult(
        check=check, status=status.value, issues=tuple(issues),
        summary=dict(summary or {}), evidence=dict(evidence or {}),
    )
