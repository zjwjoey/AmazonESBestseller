import json

import pytest

from amazon_es_bestseller.access.detector import AccessStopError
from amazon_es_bestseller.collection.detail import audit_saved_detail_cache, collect_details


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


def test_collect_details_keeps_final_asin_mismatch_as_reviewable_evidence(tmp_path, monkeypatch):
    html = '<html><body><input id="ASIN" value="B075JJRFVV"><h1 id="productTitle">Other</h1></body></html>'
    session = FakeSession(200, html, "https://www.amazon.es/dp/B075JJRFVV")
    monkeypatch.setattr("amazon_es_bestseller.collection.detail.time.sleep", lambda _seconds: None)
    records = collect_details(
        ["B078C6QR1C"], session, str(tmp_path),
        request_urls={"B078C6QR1C": "https://www.amazon.es/dp/B075JJRFVV"},
        execution_context={"B078C6QR1C": {
            "planned_request_url": "https://www.amazon.es/dp/B075JJRFVV",
            "preferred_request_url": "https://www.amazon.es/dp/B075JJRFVV",
            "request_source": "RANKING_RAW",
        }},
    )
    assert len(records) == 1
    assert records[0]["detail_status"] == "SUCCESS_WITH_IDENTITY_CHANGE"
    assert records[0]["identity_event"] == "NAVIGATION_IDENTITY_CHANGED"
    assert records[0]["identity_review_required"] is True
    assert (tmp_path / "html" / "B078C6QR1C.html").exists()
    assert not (tmp_path / "quarantine" / "B078C6QR1C").exists()


def test_collect_details_blocks_request_url_binding_mismatch_before_navigation(tmp_path):
    class NoNavigationSession(FakeSession):
        def goto(self, _url):
            raise AssertionError("binding mismatch must not navigate")

    records = collect_details(
        ["B078C6QR1C"], NoNavigationSession(), str(tmp_path),
        request_urls={"B078C6QR1C": "https://www.amazon.es/dp/B078C6QR1C"},
        execution_context={"B078C6QR1C": {
            "planned_request_url": "https://www.amazon.es/dp/B075JJRFVV",
            "preferred_request_url": "https://www.amazon.es/dp/B075JJRFVV",
        }},
    )
    assert records == []
    checkpoint = json.loads((tmp_path / "checkpoints" / "B078C6QR1C.json").read_text(encoding="utf-8"))
    assert checkpoint["detail_status"] == "REQUEST_URL_BINDING_MISMATCH"
    assert checkpoint["identity_event"] == "REQUEST_URL_BINDING_MISMATCH"


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


def test_audit_saved_cache_retains_mislabeled_product_page_for_review(tmp_path):
    html_dir = tmp_path / "html"
    html_dir.mkdir()
    (html_dir / "B078C6QR1C.html").write_text(
        "<html><body><input id='ASIN' value='B075JJRFVV'>"
        "<h1 id='productTitle'>Other product</h1></body></html>", encoding="utf-8")

    result = audit_saved_detail_cache(html_dir)

    assert result["summary"]["VALID_PRODUCT_PAGE"] == 1
    assert result["records"][0]["identity_status"] == "IDENTITY_MISMATCH"


def test_collect_details_preserves_distinct_timeout_status(tmp_path):
    class TimeoutSession(FakeSession):
        def goto(self, url):
            raise TimeoutError("navigation timeout")

    assert collect_details(["B078C6QR1C"], TimeoutSession(), str(tmp_path)) == []
    checkpoint = json.loads(
        (tmp_path / "checkpoints" / "B078C6QR1C.json").read_text(encoding="utf-8"))
    assert checkpoint["status"] == "failed"
    assert checkpoint["detail_status"] == "TIMEOUT"
    assert checkpoint["collected_at"]


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
