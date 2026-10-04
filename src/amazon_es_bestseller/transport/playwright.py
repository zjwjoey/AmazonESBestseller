"""Adapter over the existing BrowserSession.

This is intentionally thin: BrowserSession keeps delivery-location,
challenge, pacing and saved-page behavior owned by the existing collector.
"""
from __future__ import annotations

from ..access.detector import detect_access_status
from .base import TransportResponse, locale_observation
from .failures import classify_failure


class PlaywrightTransport:
    name = "PLAYWRIGHT"
    primary = True

    def __init__(self, session, *, marketplace: str = "ES",
                 requested_locale: str = "es_ES", currency: str = "EUR"):
        self.session = session
        self.marketplace = marketplace
        self.requested_locale = requested_locale
        self.currency = currency

    def fetch_page(self, url: str, *, referer: str | None = None) -> TransportResponse:
        status = self.session.goto(url)
        page = getattr(self.session, "page", None)
        text = page.content() if page is not None else ""
        final_url = str(getattr(page, "url", "") or url)
        state = detect_access_status(status, text).value
        failure = classify_failure(status_code=status, body=text, url=final_url)
        locale = locale_observation(text, requested_locale=self.requested_locale)
        return TransportResponse(status, final_url, text, access_state=state,
                                 failure=failure.to_dict() if failure else None,
                                 marketplace=self.marketplace,
                                 currency=self.currency, **locale)

    def fetch_ajax(self, url: str, *, method: str = "GET", referer: str | None = None,
                   payload=None) -> TransportResponse:
        raise NotImplementedError("Playwright AJAX uses the existing page/session flow")

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()
