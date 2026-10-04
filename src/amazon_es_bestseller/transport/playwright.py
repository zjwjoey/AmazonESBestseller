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
                 requested_locale: str = "es_ES", currency: str = "EUR",
                 postal_code: str | None = None, marketplace_id: str | None = None):
        self.session = session
        self.marketplace = marketplace
        self.requested_locale = requested_locale
        self.currency = currency
        self.postal_code = postal_code
        self.marketplace_id = marketplace_id

    def fetch_page(self, url: str, *, referer: str | None = None) -> TransportResponse:
        status = self.session.goto(url)
        page = getattr(self.session, "page", None)
        text = page.content() if page is not None else ""
        # Access Gate must see the initial response before any scroll/lazy
        # loading is attempted.  A challenge/403/429 page must stop promptly;
        # it is never treated as a normal page that may be interacted with.
        initial_state = detect_access_status(status, text)
        if initial_state.value == "NORMAL":
            load_lazy = getattr(self.session, "load_lazy_ranking_content", None)
            if callable(load_lazy):
                load_lazy()
                text = page.content() if page is not None else text
        final_url = str(getattr(page, "url", "") or url)
        state = detect_access_status(status, text).value
        failure = classify_failure(status_code=status, body=text, url=final_url)
        locale = locale_observation(text, requested_locale=self.requested_locale)
        return TransportResponse(status, final_url, text, access_state=state,
                                 failure=failure.to_dict() if failure else None,
                                 marketplace=self.marketplace,
                                 currency=self.currency, postal_code=self.postal_code,
                                 marketplace_id=self.marketplace_id, request_method="GET",
                                 request_url=url, request_headers={"referer": referer or ""}, **locale)

    def fetch_ajax(self, url: str, *, method: str = "GET", referer: str | None = None,
                   payload=None, headers=None) -> TransportResponse:
        page = getattr(self.session, "page", None)
        if page is None:
            raise RuntimeError("Playwright AJAX requires an active page")
        request_headers = dict(headers or {})
        if referer:
            request_headers.setdefault("referer", referer)
        body = payload if isinstance(payload, str) else None
        if body is None and payload is not None:
            import json
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            request_headers.setdefault("content-type", "application/json")
        result = page.evaluate(
            """
            async ({url, method, headers, body}) => {
              const response = await fetch(url, {
                method,
                headers,
                body: method === "GET" || method === "HEAD" ? undefined : body,
                credentials: "include"
              });
              return {
                status: response.status,
                url: response.url,
                text: await response.text(),
                headers: Object.fromEntries(response.headers.entries())
              };
            }
            """,
            {"url": url, "method": str(method or "GET").upper(),
             "headers": request_headers, "body": body},
        )
        status = result.get("status")
        text = result.get("text") or ""
        final_url = str(result.get("url") or url)
        state = detect_access_status(status, text).value
        failure = classify_failure(status_code=status, body=text, url=final_url)
        locale = locale_observation(text, requested_locale=self.requested_locale)
        return TransportResponse(
            status, final_url, text, headers=result.get("headers") or {},
            access_state=state,
            failure=failure.to_dict() if failure else None,
            marketplace=self.marketplace, currency=self.currency,
            postal_code=self.postal_code, marketplace_id=self.marketplace_id,
            request_method=str(method or "GET").upper(), request_url=url,
            request_headers=request_headers, request_payload=payload,
            **locale)

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()
