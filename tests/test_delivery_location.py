# -*- coding: utf-8 -*-
"""Offline tests for Amazon delivery-destination enforcement."""
import pytest

from amazon_es_bestseller.access.browser import BrowserSession
from amazon_es_bestseller.access.detector import AccessStopError
from amazon_es_bestseller.access.location import (
    DeliveryLocationError,
    classify_delivery_location,
    extract_delivery_location_text,
    inspect_delivery_location,
)


def test_location_parser_uses_header_not_footer_country_selector():
    html = (
        '<button id="nav-global-location-popover-link">'
        'Enviar a Estados Unidos</button>'
        '<footer><button>España</button></footer>'
    )
    assert extract_delivery_location_text(html) == "Enviar a Estados Unidos"
    observed = inspect_delivery_location(html)
    assert observed.is_spain is False


@pytest.mark.parametrize("text", [
    "Enviar a España",
    "Enviar a Madrid 28001",
    "Deliver to Barcelona 08001",
])
def test_location_parser_accepts_spain_destination(text):
    observed = classify_delivery_location(text)
    assert observed.is_spain is True


def test_location_parser_unknown_is_not_treated_as_spain():
    assert classify_delivery_location("Enviar a").is_spain is None


class _FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector
        self.first = self

    def is_visible(self):
        if self.selector in ("#nav-global-location-data-modal-action", "#nav-global-location-popover-link", "#contextualIngressPtLink"):
            return not self.page.modal
        if self.selector == "#GLUXZipUpdateInput":
            return self.page.modal and not self.page.applied
        if self.selector == "#GLUXZipUpdate":
            return self.page.modal and not self.page.applied
        if self.selector in ("#a-autoid-67", "#GLUXConfirmClose", "#a-autoid-67-announce"):
            return self.page.modal and self.page.applied
        if self.selector == "button[name='glowDoneButton']":
            return self.page.modal and self.page.applied
        return False

    def click(self, **kwargs):
        if self.selector == "#nav-global-location-data-modal-action":
            if self.page.new_action_works:
                self.page.modal = True
        elif self.selector in ("#nav-global-location-popover-link", "#contextualIngressPtLink"):
            self.page.modal = True
        elif self.selector == "#GLUXZipUpdate":
            self.page.applied = True
        elif self.selector in ("#a-autoid-67", "#GLUXConfirmClose", "#a-autoid-67-announce", "button[name='glowDoneButton']"):
            self.page.modal = False
            self.page.done = True

    def fill(self, value, **kwargs):
        self.page.postal_code = value


class _FakePage:
    def __init__(self):
        self.modal = False
        self.applied = False
        self.done = False
        self.postal_code = ""
        self.new_action_works = True

    def locator(self, selector):
        return _FakeLocator(self, selector)

    def content(self):
        destination = "Enviar a Madrid 28001" if self.done else "Enviar a Estados Unidos"
        return '<button id="nav-global-location-popover-link">%s</button>' % destination


def test_browser_session_changes_non_spain_destination_and_verifies_header(monkeypatch):
    session = BrowserSession()
    session.page = _FakePage()
    monkeypatch.setattr(session, "goto", lambda url, timeout_ms=45000: 200)
    monkeypatch.setattr(session, "wait_for_product_page", lambda timeout_ms=20000: None)

    observed = session.ensure_spain_delivery("28001")

    assert observed.is_spain is True
    assert observed.postal_code == "28001"
    assert session.page.postal_code == "28001"
    # The check is idempotent after successful verification.
    assert session.ensure_spain_delivery("28001") == observed


def test_browser_session_falls_back_when_new_modal_action_does_not_open(monkeypatch):
    page = _FakePage()
    page.new_action_works = False
    session = BrowserSession()
    session.page = page
    monkeypatch.setattr(session, "goto", lambda url, timeout_ms=45000: 200)
    monkeypatch.setattr(session, "wait_for_product_page", lambda timeout_ms=20000: None)
    visible = session._visible_locator
    monkeypatch.setattr(session, "_visible_locator",
                        lambda selectors, timeout_seconds=5: visible(
                            selectors, timeout_seconds=0))
    assert session.ensure_spain_delivery("28001").is_spain is True
    assert page.postal_code == "28001"


def test_browser_session_accepts_rendered_amazon_202_shell(monkeypatch):
    session = BrowserSession()
    session.page = _FakePage()
    monkeypatch.setattr(session, "goto", lambda url, timeout_ms=45000: 202)
    monkeypatch.setattr(session, "wait_for_product_page", lambda timeout_ms=20000: None)

    assert session.ensure_spain_delivery("28001").is_spain is True


def test_browser_session_rejects_invalid_postal_code():
    session = BrowserSession()
    with pytest.raises(DeliveryLocationError, match="5 位数字"):
        session.ensure_spain_delivery("2800")


def _gate_session(monkeypatch, page):
    session = BrowserSession()
    session.page = page
    monkeypatch.setattr(session, "goto", lambda *a, **kw: 200)
    monkeypatch.setattr(session, "wait_for_product_page", lambda **kw: None)
    visible = session._visible_locator
    monkeypatch.setattr(session, "_visible_locator",
                        lambda selectors, timeout_seconds=5: visible(
                            selectors, timeout_seconds=0))
    # Advance bounded polling deterministically without real sleeps.
    ticks = iter(range(0, 1000, 10))
    monkeypatch.setattr("amazon_es_bestseller.access.browser.time.monotonic",
                        lambda: next(ticks))
    return session


def test_spain_destination_passes_without_changing_postcode(monkeypatch):
    page = _FakePage()
    page.done = True
    session = _gate_session(monkeypatch, page)
    assert session.ensure_spain_delivery("08001").is_spain is True
    assert page.postal_code == ""
    assert page.applied is False


class _ImmediateApplyPage(_FakePage):
    """Popover variant with no Done button; apply updates the header directly."""

    def locator(self, selector):
        locator = super().locator(selector)
        if selector in ("#a-autoid-67", "#GLUXConfirmClose", "#a-autoid-67-announce"):
            locator.is_visible = lambda: False
        return locator

    def content(self):
        if self.applied:
            return '<button id="nav-global-location-popover-link">Enviar a Barcelona 08001</button>'
        return super().content()


def test_apply_without_done_button_passes_only_with_verified_spain_header(monkeypatch):
    page = _ImmediateApplyPage()
    session = _gate_session(monkeypatch, page)
    assert session.ensure_spain_delivery("08001").postal_code == "08001"
    assert page.postal_code == "08001"


def test_apply_without_done_button_blocks_unchanged_destination(monkeypatch):
    page = _ImmediateApplyPage()
    page.content = lambda: '<button id="nav-global-location-popover-link">Enviar a Estados Unidos</button>'
    session = _gate_session(monkeypatch, page)
    with pytest.raises(DeliveryLocationError, match="配送地点检查失败"):
        session.ensure_spain_delivery()
    assert session._delivery_location_checked is False


def test_challenge_after_apply_blocks_even_with_spain_header(monkeypatch):
    page = _ImmediateApplyPage()
    content = page.content
    page.content = lambda: content() + ("Robot Check" if page.applied else "")
    session = _gate_session(monkeypatch, page)
    with pytest.raises(AccessStopError, match="CHALLENGE"):
        session.ensure_spain_delivery()
    assert session._delivery_location_checked is False


def test_previously_verified_session_repairs_changed_destination(monkeypatch):
    page = _FakePage()
    page.done = True
    session = _gate_session(monkeypatch, page)
    session.ensure_spain_delivery()
    page.done = False  # The same browser now shows a foreign destination.

    assert session.ensure_spain_delivery().is_spain is True
    assert page.postal_code == "28001"
    assert page.applied is True


def test_previously_verified_session_cannot_hide_current_challenge(monkeypatch):
    page = _FakePage()
    page.done = True
    session = _gate_session(monkeypatch, page)
    session.ensure_spain_delivery()
    page.content = lambda: '<div>Robot Check</div>'
    session.goto = lambda *a, **kw: pytest.fail("must stop before another navigation")

    with pytest.raises(AccessStopError, match="CHALLENGE"):
        session.ensure_spain_delivery()
    assert session._delivery_location_checked is False


@pytest.mark.parametrize("status", [403, 429, 503])
def test_previously_verified_session_preserves_http_access_gate(monkeypatch, status):
    from types import SimpleNamespace

    page = _FakePage()
    page.done = True
    session = _gate_session(monkeypatch, page)
    session.ensure_spain_delivery()
    session.last_document_response = SimpleNamespace(status=status)
    session.goto = lambda *a, **kw: pytest.fail("must not retry a restricted response")
    with pytest.raises(AccessStopError):
        session.ensure_spain_delivery()
    assert session._delivery_location_checked is False
