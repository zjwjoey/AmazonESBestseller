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
