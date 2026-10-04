"""Experimental curl_cffi transport.

The dependency is optional and this adapter is never selected by the current
collector automatically.  It has conservative retry behavior and stops on
Amazon access-restriction signals; it does not rotate proxies or cookies.
"""
from __future__ import annotations

from .base import TransportResponse, locale_observation
from .failures import classify_failure


class CurlCffiUnavailable(RuntimeError):
    pass


class CurlCffiTransport:
    name = "CURL_CFFI"
    primary = False
    experimental = True

    def __init__(self, *, domain: str = "www.amazon.es", language: str = "es-ES",
                 currency: str = "EUR", fingerprint: str = "safari17_0",
                 postal_code: str | None = None, marketplace_id: str | None = None):
        try:
            from curl_cffi import requests
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise CurlCffiUnavailable("curl_cffi is optional; install the transport extra") from exc
        self._requests = requests
        self.domain = domain
        self.base = f"https://{domain}"
        self.language = language
        self.postal_code = postal_code
        self.marketplace_id = marketplace_id
        self.fingerprint = fingerprint
        self.session = requests.Session(impersonate=fingerprint, timeout=45)
        self.session.cookies.set("i18n-prefs", currency, domain=f".{domain}")
        self.session.cookies.set("lc-main", language.replace("-", "_"), domain=f".{domain}")
        self.warmed = False

    def _request(self, method: str, url: str, *, referer: str | None = None,
                 payload=None, headers=None):
        request_headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "accept-language": f"{self.language},{self.language.split('-')[0]};q=0.9",
            "referer": referer or f"{self.base}/",
        }
        request_headers.update(headers or {})
        request_kwargs = {"headers": request_headers}
        if isinstance(payload, str):
            request_kwargs["data"] = payload
        elif payload is not None:
            request_kwargs["json"] = payload
        response = self.session.request(method, url, **request_kwargs)
        failure = classify_failure(status_code=response.status_code, body=response.text,
                                   url=str(response.url))
        locale = locale_observation(response.text or "", requested_locale=self.language.replace("-", "_"))
        return TransportResponse(response.status_code, str(response.url), response.text or "",
                                 headers=dict(response.headers),
                                 access_state=("NORMAL" if failure is None else failure.kind.value),
                                 failure=failure.to_dict() if failure else None,
                                 marketplace="ES",
                                 currency=self.session.cookies.get("i18n-prefs") or "EUR",
                                 postal_code=self.postal_code, marketplace_id=self.marketplace_id,
                                 fingerprint=self.fingerprint,
                                 **locale)

    def fetch_page(self, url: str, *, referer: str | None = None) -> TransportResponse:
        if not self.warmed:
            warm = self._request("GET", self.base + "/", referer=referer)
            if warm.failure:
                return warm
            self.warmed = True
        return self._request("GET", url if url.startswith("http") else self.base + url,
                             referer=referer)

    def fetch_ajax(self, url: str, *, method: str = "GET", referer: str | None = None,
                   payload=None, headers=None) -> TransportResponse:
        return self._request(method, url if url.startswith("http") else self.base + url,
                             referer=referer, payload=payload, headers=headers)

    def close(self) -> None:
        self.session.close()
