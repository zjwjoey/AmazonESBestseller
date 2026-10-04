"""Small transport protocol shared by source-layer adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol


@dataclass(frozen=True)
class TransportResponse:
    status_code: int | None
    url: str
    text: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    access_state: str = "UNKNOWN"
    failure: Mapping[str, object] | None = None


class AmazonTransport(Protocol):
    """Transport boundary; callers own parsing and authority decisions."""

    def fetch_page(self, url: str, *, referer: str | None = None) -> TransportResponse:
        ...

    def fetch_ajax(self, url: str, *, method: str = "GET", referer: str | None = None,
                   payload: Mapping[str, object] | None = None) -> TransportResponse:
        ...

    def close(self) -> None:
        ...
