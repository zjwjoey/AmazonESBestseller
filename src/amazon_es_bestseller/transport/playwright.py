"""Adapter over the existing BrowserSession.

This is intentionally thin: BrowserSession keeps delivery-location,
challenge, pacing and saved-page behavior owned by the existing collector.
"""
from __future__ import annotations
from urllib.parse import urlsplit
import hashlib
import json
from uuid import uuid4

from ..access.detector import detect_access_status
from .base import TransportResponse, locale_observation
from .failures import classify_failure

_CONTROLLED_ACP_NONCE_HEADER = "x-codex-acp-request-nonce"


class PlaywrightTransport:
    name = "PLAYWRIGHT"
    primary = True

    @staticmethod
    def _native_batch_descriptor(descriptor: dict, captured: dict) -> dict:
        """Advance only the narrowly observed JSON/rec-object batch contract.

        The native first request remains byte-exact. Subsequent indexes and
        serialized rec objects come from this page's saved metadata, retaining
        the native batch width, JSON types, headers, and dynamic endpoint.
        """
        if "offset" not in descriptor:
            return dict(descriptor, **captured)
        try:
            body = json.loads(captured["payload"])
            metadata = descriptor["metadata"]
            entries = metadata["entries"]
            indexes, ids = body["indexes"], body["ids"]
            initial = int(body["offset"])
            start, remaining = descriptor["offset"], descriptor["count"]
            width = len(indexes)
            valid = (set(body) == {"faceoutkataname", "ids", "indexes", "linkparameters", "offset", "reftagprefix"}
                and isinstance(entries, list) and isinstance(indexes, list) and isinstance(ids, list)
                and 0 < width <= 50 and len(ids) == width
                and all(type(index) is int for index in indexes)
                and indexes == list(range(initial, initial + width))
                and type(start) is int and type(remaining) is int
                and start >= initial and (start - initial) % width == 0
                and 0 < remaining == len(entries) - start
                and int(metadata["index_offset_raw"]) == initial
                and int(metadata["offset_raw"]) == len(entries)
                and initial + width <= len(entries)
                and body["faceoutkataname"] == metadata["faceout"]
                and body["reftagprefix"] == metadata["reftag"]
                and body["linkparameters"] == ""
                and isinstance(body["offset"], (str, int)) and type(body["offset"]) is not bool
                and all(isinstance(value, str) for value in ids)
                and [json.loads(value) for value in ids] == entries[initial:initial + width]
                and "application/json" in captured["headers"].get("content-type", "").lower())
            if not valid:
                raise ValueError("invalid native batch proof")
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise ValueError("ACP_NATIVE_BATCH_PROOF_INVALID") from exc
        if start == initial:
            return dict(descriptor, **captured)
        stop = start + min(width, remaining)
        body["indexes"] = list(range(start, stop))
        body["ids"] = [json.dumps(entry, ensure_ascii=False, separators=(",", ":")) for entry in entries[start:stop]]
        body["offset"] = str(start) if isinstance(body["offset"], str) else start
        advanced = dict(captured, payload=json.dumps(body, ensure_ascii=False, separators=(",", ":")),
            request_contract_source="NATIVE_BROWSER_CONTRACT_DERIVED_BATCH",
            native_payload_sha256=hashlib.sha256(captured["payload"].encode("utf-8")).hexdigest())
        return dict(descriptor, **advanced)

    def __init__(self, session, *, marketplace: str = "ES",
                 requested_locale: str = "es_ES", currency: str = "EUR",
                 postal_code: str | None = None, marketplace_id: str | None = None):
        self.session = session
        self.marketplace = marketplace
        self.requested_locale = requested_locale
        self.currency = currency
        self.postal_code = postal_code
        self.marketplace_id = marketplace_id
        self._native_acp_requests = []
        self._manual_acp_allowance = None
        self._acp_capture_installed = False

    def install_bounded_acp_capture(self, *, on_capture=None) -> None:
        """Observe the site's request contract while aborting native ACP traffic.

        Only the scheduler's exact, one-shot metered request may pass this route.
        No cookies or unrelated browser headers are copied into request evidence.
        """
        page = getattr(self.session, "page", None)
        if page is None:
            raise ValueError("ACP_CAPTURE_REQUIRES_PAGE")
        if self._acp_capture_installed:
            return
        def capture(route):
            request = route.request
            route_headers = {k.lower(): v for k, v in request.headers.items()}
            key = (request.url, request.method.upper(), request.post_data or "",
                route_headers.get(_CONTROLLED_ACP_NONCE_HEADER))
            if request.resource_type in {"xhr", "fetch"} and key == self._manual_acp_allowance:
                self._manual_acp_allowance = None
                # This local marker distinguishes the metered fetch from an
                # identical automatic retry. It must never reach Amazon.
                all_headers = getattr(request, "all_headers", None)
                wire_headers = all_headers() if callable(all_headers) else request.headers
                route.continue_(headers={k: v for k, v in wire_headers.items()
                    if k.lower() != _CONTROLLED_ACP_NONCE_HEADER})
                return
            route.abort()
            if request.resource_type not in {"xhr", "fetch"} or len(self._native_acp_requests) >= 64:
                return
            headers = {k.lower(): v for k, v in request.headers.items()
                if k.lower() in {"accept", "content-type", "referer"}
                or k.lower().startswith("x-amz-acp-")}
            evidence = {"url": request.url,
                "method": request.method.upper(), "payload": request.post_data or "",
                "headers": headers, "parent_page_url": str(getattr(page, "url", "")),
                "resource_type": request.resource_type,
                "request_contract_source": "NATIVE_BROWSER_REQUEST_ABORTED"}
            self._native_acp_requests.append(evidence)
            if on_capture is not None:
                on_capture(evidence)
        page.route("**/acp/**", capture)
        on = getattr(page, "on", None)
        if callable(on):
            def clear_on_navigation(frame):
                if frame == getattr(page, "main_frame", None):
                    self._native_acp_requests.clear()
            on("framenavigated", clear_on_navigation)
        self._acp_capture_installed = True

    def prepare_ajax_descriptor(self, descriptor: dict, *, referer: str) -> dict:
        """Use a captured native request, never a guessed request-body contract."""
        if not self._acp_capture_installed:
            raise ValueError("ACP_NATIVE_CAPTURE_NOT_INSTALLED")
        page = getattr(self.session, "page", None)
        if page is None or str(getattr(page, "url", "")) != referer:
            raise ValueError("ACP_NATIVE_CAPTURE_REQUIRES_CURRENT_SOURCE_PAGE")
        expected = str(descriptor.get("url") or "")
        expected_parts = urlsplit(expected)
        parent = urlsplit(referer)
        discover = not expected and (descriptor.get("native_discovery") is True
            or descriptor.get("request_contract") == "NATIVE_REQUEST_DISCOVERY_REQUIRED")
        metadata = descriptor.get("metadata") or {}
        if discover and (parent.scheme != "https" or parent.hostname != "www.amazon.es"
                or not isinstance(metadata.get("entries"), list) or not metadata["entries"]
                or type(descriptor.get("offset")) is not int or type(descriptor.get("count")) is not int
                or descriptor["count"] <= 0):
            raise ValueError("ACP_NATIVE_DISCOVERY_METADATA_REQUIRED")
        if not discover and (expected_parts.scheme != "https" or expected_parts.netloc != parent.netloc
                or not expected_parts.path.startswith("/acp/") or not expected_parts.path.endswith("/nextPage")):
            raise ValueError("ACP_NATIVE_CAPTURE_ENDPOINT_INVALID")
        expected_params = (descriptor.get("headers") or {}).get("x-amz-acp-params")
        for attempt in range(7):
            for captured in self._native_acp_requests:
                actual_parts = urlsplit(captured["url"])
                endpoint_matches = (actual_parts.scheme == "https" and actual_parts.netloc == parent.netloc
                    and actual_parts.path.startswith("/acp/") and actual_parts.path.endswith("/nextPage")) if discover else (
                        (actual_parts.scheme, actual_parts.netloc, actual_parts.path)
                        == (expected_parts.scheme, expected_parts.netloc, expected_parts.path))
                if (endpoint_matches
                        and captured["method"] == "POST"
                        and captured["parent_page_url"] == referer
                        and captured["payload"] and captured["headers"].get("x-amz-acp-params")
                        and (not expected_params or captured["headers"]["x-amz-acp-params"] == expected_params)):
                    try:
                        return self._native_batch_descriptor(dict(descriptor, url=captured["url"]), captured)
                    except ValueError:
                        if not discover:
                            raise
                        # Another ACP widget on this page is not this saved grid.
                        # Discovery requires the complete opaque rec/index proof.
                        continue
            if attempt == 6:
                break
            expected_path = metadata.get("path") or (expected_parts.path.removesuffix("nextPage") if expected else None)
            expected_recs = metadata.get("client_recs_raw")
            page.evaluate("""(() => {
                const expectedPath = """ + json.dumps(expected_path) + """;
                const expectedRecs = """ + json.dumps(expected_recs) + """;
                const containers = Array.from(document.querySelectorAll('[data-acp-path], [data-client-recs-list]'));
                const grid = containers.find(el => expectedPath && el.getAttribute('data-acp-path') === expectedPath)
                    || containers.find(el => expectedRecs && el.getAttribute('data-client-recs-list') === expectedRecs)
                    || document.querySelector('#zg, #bestsellers, .zg-list') || document;
                const cards = grid.querySelectorAll('#gridItemRoot, [id^="p13n-asin-index-"], .zg-grid-general-faceout, [data-ranking-card="true"]');
                if (cards.length) {
                    cards[cards.length - 1].scrollIntoView({block:'end', inline:'nearest', behavior:'instant'});
                    window.scrollBy(0, Math.max(80, Math.floor(window.innerHeight * 0.35)));
                } else {
                    window.scrollBy(0, Math.max(80, Math.floor(window.innerHeight * 0.8)));
                }
            })()""")
            page.wait_for_timeout(500)
        raise ValueError("ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED")

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
        request_headers = {k: v for k, v in (headers or {}).items()
            if k.lower() != _CONTROLLED_ACP_NONCE_HEADER}
        if referer:
            request_headers.setdefault("referer", referer)
        body = payload if isinstance(payload, str) else None
        if body is None and payload is not None:
            import json
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            request_headers.setdefault("content-type", "application/json")
        browser_headers = dict(request_headers)
        if self._acp_capture_installed and urlsplit(url).path.startswith("/acp/"):
            nonce = uuid4().hex
            # Keep the marker outside the actual request/evidence headers.
            browser_headers[_CONTROLLED_ACP_NONCE_HEADER] = nonce
            self._manual_acp_allowance = (url, str(method or "GET").upper(), body or "", nonce)
        try:
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
             "headers": browser_headers, "body": body},
            )
        finally:
            self._manual_acp_allowance = None
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

    def requires_ajax_page_context(self, page_url: str) -> bool:
        """A saved HTML response cannot establish a fresh browser's origin.

        This is a local check only. The snapshot scheduler owns any metered
        navigation needed before resuming a page-specific ACP continuation.
        """
        page = getattr(self.session, "page", None)
        return page is None or str(getattr(page, "url", "")) != page_url

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()
