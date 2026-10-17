from types import SimpleNamespace
import pytest


def test_window_mode_requires_exact_reviewed_serial_visible_mode():
    from amazon_es_bestseller.collection.detail_visible_windows5500 import validate_windows
    validate_windows(3, True, 3, {'visible_browser_windows': 3, 'network_parallelism': 1})
    for count, visible, lanes, auth in [
        (2, True, 3, {}), (3, False, 3, {}), (3, True, 1, {}),
        (3, True, 3, {}), (3, True, 3, {'visible_browser_windows': 3, 'network_parallelism': 3})]:
        with pytest.raises(ValueError):
            validate_windows(count, visible, lanes, auth)


def test_round_robin_selects_pages_without_navigation_and_rejects_replay():
    from amazon_es_bestseller.collection.detail_visible_windows5500 import WindowDispatcher
    pages = [object(), object(), object()]
    session = SimpleNamespace(page=None)
    dispatcher = WindowDispatcher(session, pages)
    for i in range(7):
        dispatcher.select({'task_id': str(i)})
        assert session.page is pages[i % 3]
        assert session.visible_window_lane == i % 3
    with pytest.raises(ValueError, match='ALREADY_DISPATCHED'):
        dispatcher.select({'task_id': '0'})


def test_wrong_window_count_rejected():
    from amazon_es_bestseller.collection.detail_visible_windows5500 import WindowDispatcher
    with pytest.raises(ValueError):
        WindowDispatcher(SimpleNamespace(), [object()])


def test_only_current_reserved_window_chain_may_navigate():
    from amazon_es_bestseller.collection.detail_visible_windows5500 import dispatch_navigation_allowed
    frame = object()
    session = SimpleNamespace(page=SimpleNamespace(main_frame=frame), active_request_url='https://www.amazon.es/dp/B012345678')
    first = SimpleNamespace(frame=frame, method='GET', url=session.active_request_url, redirected_from=None)
    assert dispatch_navigation_allowed(session, first)
    redirect = SimpleNamespace(frame=frame, method='GET', url='https://www.amazon.es/redirect', redirected_from=first)
    assert dispatch_navigation_allowed(session, redirect)
    for name, value in [('frame', object()), ('method', 'POST'), ('url', 'https://www.amazon.es/dp/B000000001')]:
        request = SimpleNamespace(**vars(first));setattr(request, name, value)
        assert not dispatch_navigation_allowed(session, request)
    session.active_request_url = None
    assert not dispatch_navigation_allowed(session, first)
