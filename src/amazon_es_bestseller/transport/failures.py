"""Shared transport failure taxonomy.

The collector's AccessController remains authoritative.  This module gives
new adapters a stable vocabulary and retry decision without making every
failure retryable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class FailureKind(str, Enum):
    TRANSIENT_NETWORK = "TRANSIENT_NETWORK"
    UPSTREAM_5XX = "UPSTREAM_5XX"
    BOT_BLOCK = "BOT_BLOCK"
    CAPTCHA = "CAPTCHA"
    INTERSTITIAL = "INTERSTITIAL"
    RATE_LIMIT = "RATE_LIMIT"
    NOT_FOUND = "NOT_FOUND"
    BAD_REQUEST = "BAD_REQUEST"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    PARSE_ERROR = "PARSE_ERROR"
    STRUCTURE_INCOMPLETE = "STRUCTURE_INCOMPLETE"
    UNKNOWN = "UNKNOWN"


_RETRYABLE = {
    FailureKind.TRANSIENT_NETWORK,
    FailureKind.UPSTREAM_5XX,
}


@dataclass(frozen=True)
class TransportFailure:
    kind: FailureKind
    message: str
    retryable: bool | None = None
    status_code: int | None = None
    url: str = ""
    details: Mapping[str, object] = field(default_factory=dict)

    def should_retry(self) -> bool:
        return self.retryable if self.retryable is not None else self.kind in _RETRYABLE

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "message": self.message,
            "retryable": self.should_retry(),
            "status_code": self.status_code,
            "url": self.url,
            "details": dict(self.details),
        }


def classify_failure(*, status_code: int | None = None, body: str = "",
                     error: BaseException | None = None, url: str = "",
                     context: Mapping[str, object] | None = None) -> TransportFailure | None:
    """Classify one response/error without attempting recovery or bypass."""
    text = str(body or "")
    low = text.casefold()
    if status_code == 403:
        kind = FailureKind.BOT_BLOCK
    elif status_code == 429:
        kind = FailureKind.RATE_LIMIT
    elif status_code == 404:
        kind = FailureKind.NOT_FOUND
    elif status_code == 400:
        kind = FailureKind.BAD_REQUEST
    elif status_code is not None and status_code >= 500:
        kind = FailureKind.UPSTREAM_5XX
    elif any(marker in low for marker in ("validatecaptcha", "captcha", "robot check", "unusual traffic")):
        kind = FailureKind.CAPTCHA
    elif "bm-verify" in low or "interstitial" in low:
        kind = FailureKind.INTERSTITIAL
    elif "access denied" in low or "sorry! something went wrong" in low:
        kind = FailureKind.BOT_BLOCK
    elif error is not None:
        kind = FailureKind.TRANSIENT_NETWORK
    else:
        return None
    message = str(error) if error is not None else (f"HTTP {status_code}" if status_code else kind.value)
    return TransportFailure(kind=kind, message=message, status_code=status_code,
                            url=url, details=dict(context or {}))
