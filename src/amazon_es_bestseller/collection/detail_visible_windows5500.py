"""Three visible Chromium windows in the original context; one network owner."""
from __future__ import annotations


def validate_windows(count, visible, lanes, auth):
    if count == 1:
        return
    if (count != 3 or not visible or lanes != 3
            or auth.get('visible_browser_windows') != 3
            or auth.get('network_parallelism') != 1):
        raise ValueError('REVIEWED_THREE_VISIBLE_SERIAL_WINDOWS_REQUIRED')


def dispatch_navigation_allowed(session, request):
    """Only the dispatcher's currently reserved document chain may navigate."""
    initial = request
    while initial.redirected_from:
        initial = initial.redirected_from
    return bool(getattr(session, 'active_request_url', None)
                and request.frame == session.page.main_frame
                and initial.url == session.active_request_url
                and request.method == 'GET')


class WindowDispatcher:
    def __init__(self, session, pages):
        if len(pages) != 3 or len({id(p) for p in pages}) != 3:
            raise ValueError('THREE_DISTINCT_WINDOW_PAGES_REQUIRED')
        self.session, self.pages = session, pages
        self.claimed = set()

    def select(self, task):
        if task['task_id'] in self.claimed:
            raise ValueError('WINDOW_TASK_ALREADY_DISPATCHED')
        lane = len(self.claimed) % 3
        self.claimed.add(task['task_id'])
        self.session.page = self.pages[lane]
        self.session.visible_window_lane = lane


def open_windows(session, configure_page):
    """Create only local about:blank targets; never navigate a product here.

    CDP creates real windows in the existing persistent browser context. No
    extra profile, browser identity, network dispatcher or collection lock.
    Every page receives the original routing and response guards before use.
    """
    context = session.context
    root = session.page
    control = context.new_cdp_session(root)
    pages, windows = [root], []
    for _ in range(2):
        target = control.send('Target.createTarget', {'url': 'about:blank', 'newWindow': True})['targetId']
        found = None
        for _ in range(100):
            for page in context.pages:
                if page in pages:
                    continue
                cdp = context.new_cdp_session(page)
                try:
                    info = cdp.send('Target.getTargetInfo')['targetInfo']
                    if info['targetId'] == target:
                        found = page
                        break
                finally:
                    cdp.detach()
            if found is not None:
                break
            root.wait_for_timeout(50)
        if found is None:
            raise ValueError('VISIBLE_WINDOW_TARGET_NOT_OBSERVED')
        configure_page(found)
        pages.append(found)
    for lane, page in enumerate(pages):
        cdp = context.new_cdp_session(page)
        try:
            window = cdp.send('Browser.getWindowForTarget')['windowId']
            windows.append(window)
            cdp.send('Browser.setWindowBounds', {'windowId': window,
                'bounds': {'windowState': 'normal'}})
            cdp.send('Browser.setWindowBounds', {'windowId': window,
                'bounds': {'left': lane * 600, 'top': 0, 'width': 600, 'height': 900}})
        finally:
            cdp.detach()
    if len(set(windows)) != 3:
        raise ValueError('THREE_DISTINCT_VISIBLE_WINDOWS_NOT_CONFIRMED')
    dispatcher = WindowDispatcher(session, pages)
    session.prepare_task = dispatcher.select
    session.visible_window_ids = windows
    return windows
