import asyncio
import time
from datetime import datetime, timezone

from .access_detector import detect_access_state, visible_text_from_html
from .browser_probe import _same_expected_host, _same_expected_path
from .models import AccessState, ProbeEvent


def batch_targets(targets: list[str], workers: int) -> list[list[str]]:
    if workers < 1:
        raise ValueError("workers must be positive")
    return [targets[index:index + workers] for index in range(0, len(targets), workers)]


async def _probe_one(page, store, requested_url: str, index: int) -> ProbeEvent:
    started = time.perf_counter()
    html = ""
    title = None
    final_url = None
    status = None
    navigation_result = "ok"
    try:
        response = await page.goto(requested_url, wait_until="domcontentloaded", timeout=45_000)
        status = response.status if response else None
        final_url = page.url
        title = await page.title()
        html = await page.content()
        access = detect_access_state(title, visible_text_from_html(html), status)
        if access.state is AccessState.NORMAL:
            if not _same_expected_host(requested_url, final_url):
                access = type("Access", (), {"state": AccessState.UNKNOWN, "reason": "redirected to unexpected host"})()
            elif not _same_expected_path(requested_url, final_url):
                access = type("Access", (), {"state": AccessState.UNKNOWN, "reason": "redirected to unexpected page"})()
    except Exception as exc:
        navigation_result = "error"
        access = type("Access", (), {"state": AccessState.NETWORK_ERROR, "reason": str(exc)[:300]})()
    name = f"page_{index:02d}"
    store.save_html(name, html, failure=access.state is not AccessState.NORMAL)
    try:
        await page.screenshot(path=str(store.screenshots_dir / f"{name}.png"), full_page=True)
    except Exception:
        navigation_result = "screenshot_error" if navigation_result == "ok" else navigation_result
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
    return event


async def probe_urls_parallel(store, targets: list[str], delay_seconds: float, workers: int = 2, start_index: int = 1) -> list[ProbeEvent]:
    """Probe batches of at most `workers`; stop scheduling after any non-normal event."""
    from playwright.async_api import async_playwright

    events: list[ProbeEvent] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            for batch_number, batch in enumerate(batch_targets(targets, workers)):
                pages = [await context.new_page() for _ in batch]
                offset = start_index + batch_number * workers
                batch_events = await asyncio.gather(*[
                    _probe_one(page, store, target, offset + index)
                    for index, (page, target) in enumerate(zip(pages, batch))
                ])
                for page in pages:
                    await page.close()
                events.extend(batch_events)
                if any(event.access_state is not AccessState.NORMAL for event in batch_events):
                    break
                if delay_seconds > 0 and batch_number < len(batch_targets(targets, workers)) - 1:
                    await asyncio.sleep(delay_seconds)
        finally:
            await browser.close()
    return events
