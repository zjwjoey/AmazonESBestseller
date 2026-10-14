"""Adapter over the existing BrowserSession.

This is intentionally thin: BrowserSession keeps delivery-location,
challenge, pacing and saved-page behavior owned by the existing collector.
"""
from __future__ import annotations
from urllib.parse import urlsplit
import hashlib
import json
import time
import math
from pathlib import Path
from uuid import uuid4

from ..access.detector import detect_access_status
from .base import TransportResponse, locale_observation
from .failures import classify_failure

_CONTROLLED_ACP_NONCE_HEADER = "x-codex-acp-request-nonce"


class PlaywrightTransport:
    name = "PLAYWRIGHT"
    primary = True

    @staticmethod
    def _resource_network_seconds(entries, actual_url, *, minimum_start=None):
        """Browser requestStart→responseEnd phase, including server wait.

        Missing/ambiguous/cache entries remain unknown. This is not wire-only
        transfer time or a new request, and never reconstructs unseen timings.
        """
        if not isinstance(entries,list):return None
        matches=[e for e in entries if isinstance(e,dict) and e.get("name")==actual_url]
        if len(matches)!=1:return None
        item=matches[0]
        values=[item.get(k) for k in ("startTime","requestStart","responseEnd","transferSize")]
        if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in values):return None
        start,request,end,transfer=values
        if start<0 or request<=0 or end<=request or request<start or transfer<=0:return None
        if minimum_start is not None and (not isinstance(minimum_start,(int,float)) or isinstance(minimum_start,bool)
                or not math.isfinite(minimum_start) or start<minimum_start):return None
        return (end-request)/1000

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
                 postal_code: str | None = None, marketplace_id: str | None = None,
                 measure_timings: bool = False):
        self.session = session
        self.marketplace = marketplace
        self.requested_locale = requested_locale
        self.currency = currency
        self.postal_code = postal_code
        self.marketplace_id = marketplace_id
        self.measure_timings = measure_timings
        self._native_acp_requests = []
        self._manual_acp_allowance = None
        self._acp_capture_installed = False
        self._active_carousel_owner = None
        self._used_carousel_contracts = set()
        self._prepared_carousel_requests = {}
        self._pending_carousel_responses = {}
        self._carousel_ui_replay = {}
        self._on_local_replay = None

    def install_bounded_acp_capture(self, *, on_capture=None, on_local_replay=None) -> None:
        """Observe the site's request contract while aborting native ACP traffic.

        Only the scheduler's exact, one-shot metered request may pass this route.
        No cookies or unrelated browser headers are copied into request evidence.
        """
        page = getattr(self.session, "page", None)
        if page is None:
            raise ValueError("ACP_CAPTURE_REQUIRES_PAGE")
        if self._acp_capture_installed:
            return
        self._on_local_replay=on_local_replay
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
            if self._fulfill_saved_carousel_response(route):
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
            owner = self._active_carousel_owner
            if owner and self._carousel_request_matches(evidence, owner):
                evidence.update(ranking_context_binding=dict(owner["ranking_context_binding"]),
                    native_carousel_owner={k:owner[k] for k in ("owner_id", "owner_path", "owner_params", "carousel_options_sha256")})
            self._native_acp_requests.append(evidence)
            if on_capture is not None:
                on_capture(evidence)
        page.route("**/acp/**", capture)
        on = getattr(page, "on", None)
        if callable(on):
            def clear_on_navigation(frame):
                if frame == getattr(page, "main_frame", None):
                    self._native_acp_requests.clear()
                    self._active_carousel_owner = None
                    self._used_carousel_contracts.clear()
                    self._prepared_carousel_requests.clear()
                    self._pending_carousel_responses.clear()
                    self._carousel_ui_replay.clear()
            on("framenavigated", clear_on_navigation)
        self._acp_capture_installed = True

    def prepare_ajax_descriptor(self, descriptor: dict, *, referer: str) -> dict:
        """Use a captured native request, never a guessed request-body contract."""
        self.last_prepare_browser_wait_seconds = 0.0
        if not self._acp_capture_installed:
            raise ValueError("ACP_NATIVE_CAPTURE_NOT_INSTALLED")
        page = getattr(self.session, "page", None)
        if page is None or str(getattr(page, "url", "")) != referer:
            raise ValueError("ACP_NATIVE_CAPTURE_REQUIRES_CURRENT_SOURCE_PAGE")
        if (descriptor.get("metadata") or {}).get("carousel") is True:
            return self._prepare_carousel_descriptor(descriptor, referer=referer)
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
            wait_started=time.perf_counter()
            page.wait_for_timeout(500)
            self.last_prepare_browser_wait_seconds+=time.perf_counter()-wait_started
        raise ValueError("ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED")

    @staticmethod
    def _carousel_request_matches(captured, owner):
        actual, parent = urlsplit(captured.get("url", "")), urlsplit(owner["referer"])
        return bool(actual.scheme == "https" and actual.netloc == parent.netloc
            and actual.path.startswith(owner["owner_path"].rstrip("/") + "/")
            and captured.get("parent_page_url") == owner["referer"]
            and captured.get("method") == "POST" and captured.get("payload")
            and captured.get("headers", {}).get("x-amz-acp-params") == owner["owner_params"])

    @staticmethod
    def _saved_carousel_owner(descriptor, referer):
        """Owner identity comes from exact saved DOM, not a guessed endpoint."""
        from bs4 import BeautifulSoup
        from ..highspeed.adapters import _cards, _scope
        from ..highspeed.sources import normalize_source_url
        metadata = descriptor.get("metadata") or {}
        try:
            body = Path(descriptor["evidence_path"]).read_bytes()
            if hashlib.sha256(body).hexdigest() != metadata["evidence_sha256"]:
                raise ValueError()
            soup = BeautifulSoup(body.decode("utf-8"), "lxml")
            nodes = [n for n in soup.select("[data-card-metrics-id^='p13n-zg-list-carousel'] [data-a-carousel-options]")
                     if n.get("data-a-carousel-options") == metadata["carousel_options_raw"]]
            if len(nodes) != 1:
                raise ValueError()
            node = nodes[0]
            wrapper = next(p for p in node.parents if p.get("data-card-metrics-id", "").startswith("p13n-zg-list-carousel"))
            cards = [c for c in _cards(soup) if any(p is node for p in c.parents)]
            if not cards or _scope(cards[0], referer) != metadata["ranking_context_url"]:
                raise ValueError()
            path, params = wrapper.get("data-acp-path"), wrapper.get("data-acp-params")
            if not path or not path.startswith("/acp/") or not params or not wrapper.get("id"):
                raise ValueError()
            binding = {"source_id": descriptor["source_id"], "page_evidence_sha256": metadata["evidence_sha256"],
                       "ranking_context_url": metadata["ranking_context_url"]}
            if not binding["source_id"]:
                raise ValueError()
            return {"referer":referer, "owner_id":wrapper["id"], "owner_path":path,
                "owner_card_metrics_id":wrapper["data-card-metrics-id"],
                "owner_category_href":next((a['href'] for a in wrapper.select('a[href]')
                    if normalize_source_url(a['href'],base_url=referer)==metadata['ranking_context_url']),None),
                "owner_params":params, "carousel_options_raw":metadata["carousel_options_raw"],
                "carousel_options_sha256":hashlib.sha256(metadata["carousel_options_raw"].encode()).hexdigest(),
                "ranking_context_binding":binding}
        except (KeyError, ValueError, TypeError, OSError, StopIteration) as exc:
            raise ValueError("ACP_CAROUSEL_OWNER_EVIDENCE_UNVERIFIED") from exc

    @staticmethod
    def _live_carousel_owner_verified(current_html, owner):
        """Initialized DOM identity; it never supplies endpoint, headers or POST."""
        from bs4 import BeautifulSoup
        from ..highspeed.adapters import _scope
        if not isinstance(current_html,str):return False
        soup=BeautifulSoup(current_html,'lxml')
        wrappers=soup.find_all(id=owner['owner_id'])
        if len(wrappers)!=1:return False
        wrapper=wrappers[0]
        if wrapper.get('data-card-metrics-id')!=owner['owner_card_metrics_id']:return False
        nodes=[n for n in wrapper.select('[data-a-carousel-options]')
            if n.get('data-a-carousel-options')==owner['carousel_options_raw']]
        if len(nodes)!=1:return False
        node=nodes[0];classes=set(node.get('class',[]))
        if not {'a-carousel-initialized','p13n-carousel-initialized'}<=classes:return False
        path,params=wrapper.get('data-acp-path'),wrapper.get('data-acp-params')
        retained=path==owner['owner_path'] and params==owner['owner_params']
        consumed=path is None and params is None and wrapper.get('data-mix-claimed')=='true'
        if not retained and not consumed:return False
        badge=node.select_one('.zg-bdg-text, span.a-badge-text')
        return bool(badge and _scope(badge,owner['referer'])==owner['ranking_context_binding']['ranking_context_url'])

    def _prepare_carousel_descriptor(self, descriptor, *, referer):
        owner = self._saved_carousel_owner(descriptor, referer)
        page = self.session.page
        self._active_carousel_owner = owner
        try:
            for attempt in range(7):
                matches = []
                for captured in self._native_acp_requests:
                    key = (owner["owner_id"], hashlib.sha256(captured.get("payload", "").encode()).hexdigest())
                    if (key not in self._used_carousel_contracts and self._carousel_request_matches(captured, owner)
                            and captured.get("ranking_context_binding") == owner["ranking_context_binding"]
                            and captured.get("native_carousel_owner", {}).get("owner_id") == owner["owner_id"]):
                        matches.append((key,captured))
                if len(matches) > 1:
                    matches = self._ordered_carousel_contracts(matches, owner)
                if matches:
                    key,captured = matches[0]
                    self._used_carousel_contracts.add(key)
                    self._prepared_carousel_requests[(captured['url'],captured['method'],captured['payload'])]={
                        'owner':dict(owner),'descriptor':dict(captured)}
                    # Exact observed endpoint/body; no grid batch derivation.
                    return dict(descriptor, **captured)
                if attempt == 6:
                    break
                # Normal visibility is necessary for below-fold AUI carousel
                # initialization. Only serialize this exact owner, never invoke
                # site factories or fabricate a native request.
                current_html=page.evaluate("""(owner => {
                    const wrapper=document.getElementById(owner.owner_id);
                    if (!wrapper) return null;
                    wrapper.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
                    return wrapper.outerHTML;
                })""",owner)
                if not self._live_carousel_owner_verified(current_html,owner):
                    wait_started=time.perf_counter();page.wait_for_timeout(500)
                    self.last_prepare_browser_wait_seconds+=time.perf_counter()-wait_started
                    continue
                result = page.evaluate("""(owner => {
                    const wrapper = document.getElementById(owner.owner_id);
                    if (!wrapper || wrapper.getAttribute('data-card-metrics-id') !== owner.owner_card_metrics_id) return false;
                    const carousel = Array.from(wrapper.querySelectorAll('[data-a-carousel-options]'))
                        .filter(n => n.getAttribute('data-a-carousel-options') === owner.carousel_options_raw);
                    if (carousel.length !== 1) return false;
                    if (!carousel[0].classList.contains('a-carousel-initialized')
                        || !carousel[0].classList.contains('p13n-carousel-initialized')) return false;
                    const path=wrapper.getAttribute('data-acp-path'), params=wrapper.getAttribute('data-acp-params');
                    const retained=path===owner.owner_path && params===owner.owner_params;
                    const consumed=path===null && params===null && wrapper.getAttribute('data-mix-claimed')==='true';
                    if (!retained && !consumed) return false;
                    if (owner.owner_category_href && !Array.from(wrapper.querySelectorAll('a[href]'))
                        .some(a=>a.getAttribute('href')===owner.owner_category_href)) return false;
                    const next = carousel[0].querySelector('.a-carousel-goto-nextpage:not(.a-button-disabled) a, a.a-carousel-goto-nextpage, button.a-carousel-goto-nextpage');
                    if (!next || next.getAttribute('aria-disabled') === 'true') return false;
                    next.click(); return true;
                })""", owner)
                if result is not True:
                    raise ValueError("ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED")
                wait_started=time.perf_counter()
                page.wait_for_timeout(500)
                self.last_prepare_browser_wait_seconds+=time.perf_counter()-wait_started
            raise ValueError("ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED")
        finally:
            self._active_carousel_owner = None

    @staticmethod
    def _ordered_carousel_contracts(matches, owner):
        """Distinct real batches may overlap after visibility and Next events.

        Accept FIFO only when every literal batch maps to the exact saved id
        list. Never construct a batch, change bytes, or disambiguate unrelated
        owners. Two different bodies for the same index range remain ambiguous.
        """
        try:
            ids = json.loads(owner['carousel_options_raw'])['ajax']['id_list']
            ranges = {}; selected = []
            for key, captured in matches:
                body = json.loads(captured['payload']); indexes = body['indexes']
                if (not indexes or not all(type(i) is int and 0 <= i < len(ids) for i in indexes)
                        or indexes != list(range(indexes[0], indexes[-1] + 1))
                        or body['ids'] != [ids[i] for i in indexes]):
                    raise ValueError('invalid literal batch')
                signature = (captured['url'], captured['method'], captured['payload'],
                             json.dumps(captured.get('headers', {}), sort_keys=True))
                previous = ranges.get(tuple(indexes))
                if previous is not None and previous != signature:
                    raise ValueError('same range with different native bytes')
                if previous is None:
                    ranges[tuple(indexes)] = signature; selected.append((key, captured))
            return selected
        except (ValueError, KeyError, TypeError, IndexError):
            raise ValueError('ACP_CAROUSEL_NATIVE_CONTRACT_AMBIGUOUS') from None

    def fetch_page(self, url: str, *, referer: str | None = None) -> TransportResponse:
        navigation_started=time.perf_counter()
        status = self.session.goto(url)
        navigation_seconds=time.perf_counter()-navigation_started
        page = getattr(self.session, "page", None)
        content_started=time.perf_counter()
        text = page.content() if page is not None else ""
        content_seconds=time.perf_counter()-content_started
        lazy_seconds=None
        # Access Gate must see the initial response before any scroll/lazy
        # loading is attempted.  A challenge/403/429 page must stop promptly;
        # it is never treated as a normal page that may be interacted with.
        initial_state = detect_access_status(status, text)
        if initial_state.value == "NORMAL":
            load_lazy = getattr(self.session, "load_lazy_ranking_content", None)
            if callable(load_lazy):
                lazy_started=time.perf_counter()
                load_lazy()
                lazy_seconds=time.perf_counter()-lazy_started
                content_started=time.perf_counter()
                text = page.content() if page is not None else text
                content_seconds+=time.perf_counter()-content_started
        final_url = str(getattr(page, "url", "") or url)
        state = detect_access_status(status, text).value
        failure = classify_failure(status_code=status, body=text, url=final_url)
        locale = locale_observation(text, requested_locale=self.requested_locale)
        network_seconds=None
        if self.measure_timings and state=="NORMAL" and callable(getattr(page,"evaluate",None)):
            try:
                entries=page.evaluate("() => performance.getEntriesByType('navigation').map(e => ({name:e.name,startTime:e.startTime,requestStart:e.requestStart,responseEnd:e.responseEnd,transferSize:e.transferSize}))")
                network_seconds=self._resource_network_seconds(entries,final_url)
            except Exception:
                network_seconds=None
        timings={'goto_network_and_browser_seconds':navigation_seconds,'content_serialization_seconds':content_seconds,
            'lazy_browser_operation_seconds':lazy_seconds,'pure_network_seconds':None,
            'network_request_response_seconds':network_seconds,'network_measurement_source':'Browser PerformanceNavigationTiming requestStart→responseEnd; includes server wait, cache/ambiguous unknown',
            'measurement_scope':'Client operation wall; navigation includes browser/DOM/network and cannot be decomposed.'} if self.measure_timings else {}
        return TransportResponse(status, final_url, text, access_state=state, timings=timings,
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
            fetch_started=time.perf_counter()
            result = page.evaluate(
            """
            async ({url, method, headers, body, measureTimings=false}) => {
              const fetchStarted = measureTimings ? performance.now() : null;
              const response = await fetch(url, {
                method,
                headers,
                body: method === "GET" || method === "HEAD" ? undefined : body,
                credentials: "include"
              });
              const text = await response.text();
              const networkEntries = measureTimings ? performance.getEntriesByName(response.url)
                .filter(e => e.startTime >= fetchStarted)
                .map(e => ({name:e.name,startTime:e.startTime,requestStart:e.requestStart,responseEnd:e.responseEnd,transferSize:e.transferSize})) : [];
              return {
                status: response.status,
                url: response.url,
                text,
                fetchStarted,
                networkEntries,
                headers: Object.fromEntries(response.headers.entries())
              };
            }
            """,
            {"url": url, "method": str(method or "GET").upper(),
             "headers": browser_headers, "body": body, **({"measureTimings":True} if self.measure_timings else {})},
            )
        except Exception:
            self._pending_carousel_responses.clear();self._carousel_ui_replay.clear()
            raise
        finally:
            self._manual_acp_allowance = None
        fetch_seconds=time.perf_counter()-fetch_started
        status = result.get("status")
        text = result.get("text") or ""
        final_url = str(result.get("url") or url)
        state = detect_access_status(status, text).value
        prepared=self._prepared_carousel_requests.pop((url,str(method or 'GET').upper(),body or ''),None)
        if state!='NORMAL':
            self._pending_carousel_responses.clear();self._carousel_ui_replay.clear()
        elif prepared and status==200 and final_url==url and str(getattr(page,'url',''))==prepared['owner']['referer']:
            self._pending_carousel_responses[(url,str(method or 'GET').upper(),body or '')]={
                **prepared,'text':text,'status_code':status,'final_url':final_url,
                'actual_response_headers':dict(result.get('headers') or {})}
        failure = classify_failure(status_code=status, body=text, url=final_url)
        locale = locale_observation(text, requested_locale=self.requested_locale)
        fetch_start=result.get("fetchStarted")
        network_seconds=self._resource_network_seconds(result.get("networkEntries"),final_url,
            minimum_start=fetch_start) if (self.measure_timings and isinstance(fetch_start,(int,float))
                and not isinstance(fetch_start,bool) and math.isfinite(fetch_start)) else None
        return TransportResponse(
            status, final_url, text, headers=result.get("headers") or {},
            access_state=state,
            failure=failure.to_dict() if failure else None,
            marketplace=self.marketplace, currency=self.currency,
            postal_code=self.postal_code, marketplace_id=self.marketplace_id,
            request_method=str(method or "GET").upper(), request_url=url,
            request_headers=request_headers, request_payload=payload,
            timings={'js_fetch_and_response_body_seconds':fetch_seconds,'pure_network_seconds':None,
                'network_request_response_seconds':network_seconds,'network_measurement_source':'Browser ResourceTiming requestStart→responseEnd from this fetch; includes server wait, cache/ambiguous unknown',
                'measurement_scope':'Browser JS fetch through complete body serialization; not pure network.'} if self.measure_timings else {},
            **locale)

    @staticmethod
    def _carousel_replay_key(owner,url,method,payload,headers):
        selected={k.lower():v for k,v in headers.items() if k.lower() in {'accept','content-type','referer'}
            or k.lower().startswith('x-amz-acp-')}
        return (owner['referer'],owner['owner_id'],owner['owner_path'],owner['owner_params'],
            owner['carousel_options_sha256'],json.dumps(owner['ranking_context_binding'],sort_keys=True),
            url,method.upper(),payload,json.dumps(selected,sort_keys=True))

    def acknowledge_native_response(self,receipt):
        """Activate local UI delivery only after the actual response is durable.

        No receipt is written here. A subsequent exact native retry can consume
        these already captured bytes; it is not another HTTP response/candidate.
        """
        from ..multisource.storage import read_sealed
        key=(receipt.get('request_url'),str(receipt.get('request_method') or '').upper(),receipt.get('request_payload') or '')
        pending=self._pending_carousel_responses.pop(key,None)
        if not pending:return False
        try:
            owner=pending['owner'];descriptor=pending['descriptor']
            receipt_path=Path(receipt['evidence_path']).resolve();raw_receipt=receipt_path.read_bytes()
            proof=read_sealed(receipt_path)
            checked=('run_id','source_id','source_type','origin','status_code','access_state','request_url','request_method',
                'request_payload','html_path','sha256','observed_at','page_key','native_acp_descriptor','ranking_context_binding')
            if any(proof.get(k)!=receipt.get(k) for k in checked):return False
            if (proof.get('origin')!='LIVE_CAPTURE' or proof.get('status_code')!=200 or proof.get('access_state')!='NORMAL'
                    or proof.get('run_id')!=descriptor.get('run_id')
                    or proof.get('source_id')!=owner['ranking_context_binding']['source_id']
                    or proof.get('ranking_context_binding')!=owner['ranking_context_binding']
                    or proof.get('request_url')!=pending['final_url'] or proof.get('final_url')!=pending['final_url']
                    or str(getattr(self.session.page,'url',''))!=owner['referer']):return False
            body=Path(proof['html_path']).read_bytes()
            if hashlib.sha256(body).hexdigest()!=proof['sha256'] or body.decode('utf8')!=pending['text']:return False
            if detect_access_status(proof['status_code'],pending['text']).value!='NORMAL':return False
            native_path=Path(descriptor['native_contract_evidence_path']).resolve();native_bytes=native_path.read_bytes()
            if hashlib.sha256(native_bytes).hexdigest()!=descriptor['native_contract_evidence_sha256']:return False
            native=json.loads(native_bytes)
            if 'payload_sha256' in native:native=read_sealed(native_path)
            for k in ('url','method','payload','headers','parent_page_url','ranking_context_binding','native_carousel_owner','run_id'):
                if native.get(k)!=descriptor.get(k):return False
            if (native.get('request_contract_source')!='NATIVE_BROWSER_REQUEST_ABORTED'
                    or native.get('resource_type') not in {'xhr','fetch'}
                    or not self._carousel_request_matches(native,owner)):return False
            supplied=proof.get('native_acp_descriptor') or {}
            for k in ('url','method','payload','headers','ranking_context_binding','native_carousel_owner',
                      'native_contract_evidence_path','native_contract_evidence_sha256'):
                if supplied.get(k)!=descriptor.get(k):return False
            parent=Path(supplied['evidence_path']).read_bytes()
            if hashlib.sha256(parent).hexdigest()!=owner['ranking_context_binding']['page_evidence_sha256']:return False
            verified_owner=self._saved_carousel_owner(supplied,owner['referer'])
            if verified_owner!=owner:return False
            self._carousel_ui_replay[self._carousel_replay_key(owner,native['url'],native['method'],native['payload'],native['headers'])]={
                **pending,'receipt_reference':{'path':str(receipt_path),'sha256':hashlib.sha256(raw_receipt).hexdigest()},
                'body_reference':{'path':proof['html_path'],'sha256':proof['sha256']}}
            return True
        except (OSError,ValueError,KeyError,TypeError):return False

    def _fulfill_saved_carousel_response(self,route):
        owner=self._active_carousel_owner
        request=route.request
        if not owner or request.resource_type not in {'xhr','fetch'}:return False
        if str(getattr(self.session.page,'url',''))!=owner['referer']:return False
        headers={k.lower():v for k,v in request.headers.items()}
        if headers.get(_CONTROLLED_ACP_NONCE_HEADER):return False
        key=self._carousel_replay_key(owner,request.url,request.method,request.post_data or '',headers)
        entry=self._carousel_ui_replay.get(key)
        if not entry:return False
        try:
            for reference in (entry['receipt_reference'],entry['body_reference']):
                if hashlib.sha256(Path(reference['path']).read_bytes()).hexdigest()!=reference['sha256']:return False
            if entry['status_code']!=200 or detect_access_status(200,entry['text']).value!='NORMAL':return False
            # This content type is a local delivery instruction, not fabricated
            # original HTTP header evidence. Original response headers stay raw.
            original_headers=entry['actual_response_headers']
            content_type=next((v for k,v in original_headers.items() if k.lower()=='content-type'),'text/html;charset=UTF-8')
            route.fulfill(status=200,body=entry['text'],headers={'content-type':content_type})
            self._carousel_ui_replay.pop(key,None)
            if self._on_local_replay:
                self._on_local_replay({'origin':'LOCAL_UI_REPLAY','new_network_request':False,
                    'request_url':request.url,'request_payload_sha256':hashlib.sha256((request.post_data or '').encode()).hexdigest(),
                    'owner_id':owner['owner_id'],'ranking_context_binding':owner['ranking_context_binding'],
                    'original_receipt_reference':entry['receipt_reference'],'body_reference':entry['body_reference'],
                    'local_delivery_content_type':content_type,'new_HTTP_receipt':False,'new_observations':0})
            return True
        except (OSError,ValueError,KeyError,TypeError):return False

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
