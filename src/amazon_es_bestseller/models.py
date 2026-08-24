from dataclasses import dataclass
from enum import StrEnum


class AccessState(StrEnum):
    NORMAL = "NORMAL"
    BLOCKED = "BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    CHALLENGE = "CHALLENGE"
    NETWORK_ERROR = "NETWORK_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class AccessResult:
    state: AccessState
    reason: str | None = None


@dataclass(frozen=True)
class ProbeEvent:
    requested_url: str
    final_url: str | None
    page_title: str | None
    timestamp: str
    load_duration: float
    navigation_result: str
    access_state: AccessState
    body_length: int
    reason: str | None = None
