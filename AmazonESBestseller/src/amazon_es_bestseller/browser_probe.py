import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from .access_detector import detect_access_state, visible_text_from_html
from .models import AccessResult, AccessState, ProbeEvent


def _same_expected_host(requested_url: str, final_url: str | None) -> bool:
    if not final_url:
        return False
    requested_host = (urlparse(requested_url).hostname or "").lower()
    final_host = (urlparse(final_url).hostname or "").lower()
    if requested_host == final_host:
        return True
    amazon_hosts = {requested_host, final_host}
    return all(host == "amazon.es" or host.endswith(".amazon.es") for host in amazon_hosts)


def _same_expected_path(requested_url: str, final_url: str | None) -> bool:
    if not final_url:
        return False
    requested_path = urlparse(requested_url).path.rstrip("/") or "/"
    final_path = urlparse(final_url).path.rstrip("/") or "/"
    return requested_path == final_path


def probe_urls(
    page,
    store,
    targets: list[str],
    delay_seconds: float = 3.0,
    start_index: int = 1,
) -> list[ProbeEvent]:
    """Visit targets once, preserving evidence and stopping on access restrictions."""
    events: list[ProbeEvent] = []
    for offset, requested_url in enumerate(targets):
        index = start_index + offset
        name = f"page_{index:02d}"
        started = time.perf_counter()
        html = ""
        title = None
        final_url = None
        navigation_result = "ok"
        status = None
        try:
            response = page.goto(
                requested_url,
                wait_until="domcontentloaded",
                timeout=45_000,
            )
            status = getattr(response, "status", None)
            final_url = page.url
            title = page.title()
            html = page.content()
            access = detect_access_state(title, visible_text_from_html(html), status)
            if access.state is AccessState.NORMAL:
                if not _same_expected_host(requested_url, final_url):
                    access = AccessResult(AccessState.UNKNOWN, "redirected to unexpected host")
                elif not _same_expected_path(requested_url, final_url):
                    access = AccessResult(AccessState.UNKNOWN, "redirected to unexpected page")
        except Exception as exc:  # Playwright errors are recorded, never retried.
            navigation_result = "error"
            access = type("ErrorResult", (), {
                "state": AccessState.NETWORK_ERROR,
                "reason": str(exc)[:300],
            })()
            try:
                final_url = page.url
                title = page.title()
                html = page.content()
            except Exception:
                pass

        store.save_html(name, html)
        try:
            store.save_screenshot(name, page)
        except Exception:
            navigation_result = "screenshot_error" if navigation_result == "ok" else navigation_result

        if access.state is not AccessState.NORMAL:
            store.save_html(name, html, failure=True)
            try:
                store.save_screenshot(name, page, failure=True)
            except Exception:
                pass

        event = ProbeEvent(
            requested_url=requested_url,
            final_url=final_url,
            page_title=title,
            timestamp=datetime.now(timezone.utc).isoformat(),
            load_duration=time.perf_counter() - started,
            navigation_result=navigation_result,
            access_state=access.state,
            body_length=len(html),
            status=status,
            reason=access.reason,
        )
        store.record_event(event)
        events.append(event)
        if access.state is not AccessState.NORMAL:
            break
        if delay_seconds > 0 and offset < len(targets) - 1:
            time.sleep(delay_seconds)
    return events
