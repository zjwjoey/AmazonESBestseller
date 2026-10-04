import sys
from types import ModuleType, SimpleNamespace

from amazon_es_bestseller.transport.curl_cffi import CurlCffiTransport


class FakeCookies:
    def __init__(self):
        self.values = {}

    def set(self, key, value, **kwargs):
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)


class FakeResponse:
    status_code = 200
    url = "https://www.amazon.es/"
    text = '<html lang="es"><body>ok</body></html>'
    headers = {"content-type": "text/html"}


class FakeSession:
    def __init__(self, **kwargs):
        self.cookies = FakeCookies()
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeResponse()

    def close(self):
        pass


def test_curl_cffi_adapter_is_optional_and_locale_aware(monkeypatch):
    fake_requests = SimpleNamespace(Session=FakeSession)
    fake_module = ModuleType("curl_cffi")
    fake_module.requests = fake_requests
    monkeypatch.setitem(sys.modules, "curl_cffi", fake_module)
    transport = CurlCffiTransport()
    response = transport.fetch_page("https://www.amazon.es/test")
    assert transport.experimental is True
    assert response.requested_locale == "es_ES"
    assert response.currency == "EUR"
    assert response.language_mismatch is False
    transport.close()
