from types import SimpleNamespace

from amazon_es_bestseller.transport.fallback import BrowserFallbackAdapter, FallbackState
from amazon_es_bestseller.transport.playwright import PlaywrightTransport


class FakePage:
    url = "https://www.amazon.es/test"

    def content(self):
        return '<html lang="es"><body>ok</body></html>'


class FakeSession:
    page = FakePage()

    def goto(self, url):
        self.url = url
        return 200


class RestrictedPage(FakePage):
    def content(self):
        return '<html><body>Robot Check validateCaptcha</body></html>'


class RestrictedSession:
    def __init__(self):
        self.page = RestrictedPage()
        self.lazy_calls = 0

    def goto(self, url):
        self.url = url
        return 403

    def load_lazy_ranking_content(self):
        self.lazy_calls += 1


def test_playwright_is_primary_and_records_locale_evidence():
    transport = PlaywrightTransport(FakeSession())
    response = transport.fetch_page("https://www.amazon.es/test")
    assert transport.primary is True
    assert response.requested_locale == "es_ES"
    assert response.observed_language == "es"
    assert response.language_mismatch is False


def test_browser_fallback_has_no_automatic_authority():
    adapter = BrowserFallbackAdapter()
    decision = adapter.request_assistance("challenge")
    assert decision.state is FallbackState.BROWSER_ASSIST_REQUIRED
    assert decision.manual_action_required is True


def test_playwright_checks_access_before_lazy_loading():
    session = RestrictedSession()
    response = PlaywrightTransport(session).fetch_page("https://www.amazon.es/test")
    assert response.access_state == "CHALLENGE"
    assert session.lazy_calls == 0
