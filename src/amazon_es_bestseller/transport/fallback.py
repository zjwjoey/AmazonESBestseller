"""Browser-assisted fallback contract, without a Browser-Act dependency."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FallbackState(str, Enum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    BROWSER_ASSIST_REQUIRED = "BROWSER_ASSIST_REQUIRED"
    RECOVERED = "RECOVERED"


@dataclass(frozen=True)
class BrowserFallbackDecision:
    state: FallbackState
    reason: str
    manual_action_required: bool


class BrowserFallbackAdapter:
    """Optional integration boundary for a headed/manual browser session."""

    def __init__(self):
        self.state = FallbackState.NORMAL

    def request_assistance(self, reason: str) -> BrowserFallbackDecision:
        self.state = FallbackState.BROWSER_ASSIST_REQUIRED
        return BrowserFallbackDecision(self.state, reason, True)

    def mark_recovered(self) -> BrowserFallbackDecision:
        self.state = FallbackState.RECOVERED
        return BrowserFallbackDecision(self.state, "manual/browser recovery reported", False)

    def reset(self) -> None:
        self.state = FallbackState.NORMAL
