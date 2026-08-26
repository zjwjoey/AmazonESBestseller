import json

import pytest

from amazon_es_bestseller.access.detector import AccessStopError
from amazon_es_bestseller.collection.detail import collect_details


class _Page:
    def __init__(self, html, url=""):
        self._html = html
        self.url = url

    def content(self):
        return self._html


class FakeSession:
    def __init__(self, status=200, html="<html><body></body></html>", url=""):
        self.status = status
        self.page = _Page(html, url)

    def goto(self, url):
        return self.status

    def wait_for_product_page(self):
        return None

    def wait_for_price_text(self):
        return None

    def wait_between_requests(self):
        return None


def test_collect_details_stops_on_final_asin_mismatch(tmp_path):
    html = '<html><body><input id="ASIN" value="B075JJRFVV"></body></html>'
    session = FakeSession(200, html, "https://www.amazon.es/dp/B075JJRFVV")
    with pytest.raises(AccessStopError, match="ASIN"):
        collect_details(["B078C6QR1C"], session, str(tmp_path))
    assert (tmp_path / "html" / "B078C6QR1C.html").exists()
    assert not (tmp_path / "details.json").exists()


def test_collect_details_rechecks_cached_blocked_page(tmp_path):
    html_dir = tmp_path / "html"
    html_dir.mkdir()
    html = "<html><body>Access denied. Unusual traffic</body></html>"
    (html_dir / "B078C6QR1C.html").write_text(html, encoding="utf-8")
    (html_dir / "B078C6QR1C.meta.json").write_text(
        json.dumps({"status_code": 403, "final_url": "https://www.amazon.es/dp/B078C6QR1C"}),
        encoding="utf-8")
    with pytest.raises(AccessStopError):
        collect_details(["B078C6QR1C"], FakeSession(), str(tmp_path))
