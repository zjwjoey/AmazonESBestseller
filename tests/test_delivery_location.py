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


def test_actual_detail_taiwan_header_stops_even_with_spanish_footer():
    import gzip
    import hashlib
    import json
    from pathlib import Path
    root = Path(__file__).parent / "fixtures/detail_bridge5500"
    body = gzip.decompress((root / "actual_taiwan_delivery.html.gz").read_bytes())
    proof = json.loads((root / "actual_taiwan_delivery.provenance.json").read_text(encoding="utf8"))
    assert hashlib.sha256(body).hexdigest() == proof["sha256"]
    observed = inspect_delivery_location(body.decode("utf8"))
    assert observed.text == "Enviar a Taiwán" and observed.is_spain is not True


def test_location_can_use_current_receipted_product_without_home_navigation(monkeypatch):
    import gzip
    from pathlib import Path
    real_body = gzip.decompress((Path(__file__).parent / "fixtures/detail_bridge5500/actual_taiwan_delivery.html.gz").read_bytes()).decode("utf8")
    page = _FakePage()
    original_mock_content = page.content
    page.content = lambda: original_mock_content() if page.done else real_body
    session = BrowserSession(); session.page = page
    monkeypatch.setattr(session, "goto", lambda *args, **kwargs: pytest.fail("unexpected homepage navigation"))
    monkeypatch.setattr(session, "wait_for_product_page", lambda **kwargs: None)
    result = session.ensure_spain_delivery("28001", navigate=False, current_status_code=200)
    assert result.is_spain is True and page.postal_code == "28001"
    # The new destination is a mocked UI result, not a live recovery claim.


def test_current_page_location_requires_actual_response_status(monkeypatch):
    session = BrowserSession(); session.page = _FakePage()
    monkeypatch.setattr(session, "goto", lambda *args, **kwargs: pytest.fail("unbudgeted navigation"))
    with pytest.raises(DeliveryLocationError, match="HTTP"):
        session.ensure_spain_delivery("28001", navigate=False)


def test_real_glux_markup_prefers_actual_submit_input(monkeypatch):
    import gzip
    from pathlib import Path
    from bs4 import BeautifulSoup
    fixture = Path(__file__).parent / "fixtures/detail_bridge5500/actual_failed_glux_UI.html.gz"
    soup = BeautifulSoup(gzip.decompress(fixture.read_bytes()).decode("utf8"), "lxml")
    assert soup.select_one("#GLUXZipUpdate input.a-button-input[type=submit]") is not None
    chosen = []
    class Locator(_FakeLocator):
        def is_visible(self):
            if self.selector == "#GLUXZipUpdate input.a-button-input":
                return self.page.modal and not self.page.applied
            return super().is_visible()
        def click(self, **kwargs):
            chosen.append(self.selector)
            if self.selector == "#GLUXZipUpdate input.a-button-input":
                self.page.applied = True
            else:
                super().click(**kwargs)
    page = _FakePage(); page.locator = lambda selector: Locator(page, selector)
    session = BrowserSession(); session.page = page
    monkeypatch.setattr(session, "wait_for_product_page", lambda **kwargs: None)
    session.ensure_spain_delivery("28001", navigate=False, current_status_code=200)
    assert "#GLUXZipUpdate input.a-button-input" in chosen


@pytest.mark.parametrize("status", [403, 429])
def test_current_page_location_does_not_handle_restricted_response(monkeypatch, status):
    from amazon_es_bestseller.access.detector import AccessStopError
    session = BrowserSession(); session.page = _FakePage()
    monkeypatch.setattr(session, "goto", lambda *args, **kwargs: pytest.fail("restricted navigation"))
    monkeypatch.setattr(session, "_visible_locator", lambda *args, **kwargs: pytest.fail("must not click on restriction"))
    with pytest.raises(AccessStopError):
        session.ensure_spain_delivery("28001", navigate=False, current_status_code=status)
