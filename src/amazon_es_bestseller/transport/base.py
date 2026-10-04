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
    request_method: str = "GET"
    request_url: str | None = None
    request_headers: Mapping[str, str] = field(default_factory=dict)
    request_payload: object = None


_SENSITIVE_HEADERS = {"authorization", "cookie", "proxy-authorization", "set-cookie"}


def raw_response_evidence(response: TransportResponse, *, request_method: str | None = None,
                         request_url: str | None = None,
                         request_headers: Mapping[str, str] | None = None,
                         request_payload: object = None) -> dict:
    """Build a JSON-safe immutable-evidence envelope without credentials."""
    def safe_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
        return {str(key): str(value) for key, value in (headers or {}).items()
                if str(key).casefold() not in _SENSITIVE_HEADERS}

    return {
        "request": {
            "method": str(request_method or response.request_method or "GET").upper(),
            "url": str(request_url or response.request_url or response.url),
            "headers": safe_headers(request_headers or response.request_headers),
            "payload": request_payload if request_payload is not None else response.request_payload,
        },
        "response": {
            "status_code": response.status_code,
            "url": response.url,
            "headers": safe_headers(response.headers),
            "body": response.text,
            "access_state": response.access_state,
            "failure": dict(response.failure or {}),
        },
        "marketplace": response.marketplace,
        "requested_locale": response.requested_locale,
        "currency": response.currency,
        "postal_code": response.postal_code,
        "marketplace_id": response.marketplace_id,
        "fingerprint": response.fingerprint,
        "observed_language": response.observed_language,
        "language_mismatch": response.language_mismatch,
    }


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
