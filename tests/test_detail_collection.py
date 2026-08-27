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


def test_repair_cached_products_merges_only_matching_page_evidence(tmp_path):
    from amazon_es_bestseller.collection.repair import repair_cached_products

    html_dir = tmp_path / "html"
    html_dir.mkdir()
    (html_dir / "page_01.html").write_text(
        """
        <html><body>
          <input id="ASIN" value="B078C6QR1C">
          <div id="productTitle">Fiambrera</div>
          <div id="corePrice_feature_div"><div class="a-price"><span class="a-offscreen">12,62 €</span></div>
            <span class="a-text-price" data-a-strike="true"><span class="a-offscreen">13,29 €</span></span>
          </div>
          <div id="social-proofing-faceout">1,5 mil+ comprados el mes pasado</div>
          <div id="merchantInfoFeature_feature_div"><a>Utopia Brands</a></div>
        </body></html>
        """,
        encoding="utf-8",
    )
    # This page is a different ASIN and must not enrich the target record.
    (html_dir / "page_02.html").write_text(
        '<input id="ASIN" value="B075JJRFVV"><div id="productTitle">Other</div>',
        encoding="utf-8",
    )
    records = [{"asin": "B078C6QR1C", "title_es_raw": "Fiambrera"}]
    repaired, report = repair_cached_products(records, html_dir)
    assert repaired[0]["current_price"] == 12.62
    assert repaired[0]["original_price"] == 13.29
    assert repaired[0]["discount_rate"] == round((13.29 - 12.62) / 13.29, 4)
    assert repaired[0]["monthly_bought_min"] == 1500
    assert repaired[0]["seller_raw"] == "Utopia Brands"
    assert report["matched_pages"] == 1
    assert report["ignored_pages"] == 1
