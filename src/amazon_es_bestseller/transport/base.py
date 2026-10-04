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
    marketplace: str = "ES"
    requested_locale: str = "es_ES"
    currency: str = "EUR"
    postal_code: str | None = None
    marketplace_id: str | None = None
    fingerprint: str | None = None
    observed_language: str | None = None
    language_mismatch: bool | None = None


def locale_observation(html: str, *, requested_locale: str = "es_ES") -> dict:
    """Return auditable locale evidence without guessing missing language."""
    import re

    match = re.search(r"<html\b[^>]*\blang\s*=\s*[\"']([^\"']+)",
                     html or "", re.I)
    observed = match.group(1).replace("-", "_") if match else None
    if not observed:
        mismatch = None
    else:
        mismatch = not observed.casefold().startswith(str(requested_locale).split("_", 1)[0].casefold())
    return {
        "requested_locale": requested_locale,
        "observed_language": observed,
        "language_mismatch": mismatch,
    }


class AmazonTransport(Protocol):
    """Transport boundary; callers own parsing and authority decisions."""

    def fetch_page(self, url: str, *, referer: str | None = None) -> TransportResponse:
        ...

    def fetch_ajax(self, url: str, *, method: str = "GET", referer: str | None = None,
                   payload: Mapping[str, object] | str | None = None,
                   headers: Mapping[str, str] | None = None) -> TransportResponse:
        ...

    def close(self) -> None:
        ...
