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
