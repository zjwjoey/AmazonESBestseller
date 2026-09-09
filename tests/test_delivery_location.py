# -*- coding: utf-8 -*-
"""Offline tests for Amazon delivery-destination enforcement."""
import pytest

from amazon_es_bestseller.access.browser import BrowserSession
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
        if self.selector in ("#nav-global-location-popover-link", "#contextualIngressPtLink"):
            return not self.page.modal
        if self.selector == "#GLUXZipUpdateInput":
            return self.page.modal and not self.page.applied
        if self.selector == "#GLUXZipUpdate":
            return self.page.modal and not self.page.applied
        if self.selector in ("#a-autoid-67", "#GLUXConfirmClose", "#a-autoid-67-announce"):
            return self.page.modal and self.page.applied
        return False

    def click(self, **kwargs):
        if self.selector in ("#nav-global-location-popover-link", "#contextualIngressPtLink"):
            self.page.modal = True
        elif self.selector == "#GLUXZipUpdate":
            self.page.applied = True
        elif self.selector in ("#a-autoid-67", "#GLUXConfirmClose", "#a-autoid-67-announce"):
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
