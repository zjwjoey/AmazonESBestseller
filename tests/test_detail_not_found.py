import json
from pathlib import Path

import pytest

from amazon_es_bestseller.access.detector import AccessStopError, detect_access_status
from amazon_es_bestseller.collection import detail
from amazon_es_bestseller.models import AccessState


FIXTURE = Path(__file__).parent / "fixtures" / "detail_not_found"
ASIN = "B0HC1CYC2P"
NEXT = "B000000003"


class Session:
    def __init__(self, status=404, html=None, host="www.amazon.es"):
        self.status = status
        self.html = html if html is not None else (FIXTURE / (ASIN + ".html")).read_text(encoding="utf-8")
        self.host = host
        self.requests = []
        self.waits = 0
        self.page = self
        self.url = ""

    def goto(self, url):
        self.requests.append(url)
        self.url = url.replace("www.amazon.es", self.host)
        return self.status if ASIN in url else 200

    def content(self):
        if ASIN in self.url:
            return self.html
        return f'<input id="ASIN" value="{NEXT}"><h1 id="productTitle">Producto</h1>'

    def wait_for_product_page(self):
        pass

    def wait_for_price_text(self):
        pass

    def wait_between_requests(self):
        self.waits += 1


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(detail.time, "sleep", lambda _: None)


@pytest.mark.parametrize("cached", [False, True])
def test_saved_real_404_is_item_failure_and_next_product_continues(tmp_path, cached):
    session = Session()
    raw = session.html
    if cached:
        directory = tmp_path / "html"
        directory.mkdir()
        for suffix in (".html", ".meta.json"):
            (directory / (ASIN + suffix)).write_bytes((FIXTURE / (ASIN + suffix)).read_bytes())
    events = []
    records = detail.collect_details([ASIN, NEXT], session, tmp_path, on_progress=events.append,
                                    execution_context={ASIN: {"action": "REFETCH_INVALID_CACHE"}})
    assert [r["asin"] for r in records] == [NEXT]
    cp = json.loads((tmp_path / "checkpoints" / (ASIN + ".json")).read_text(encoding="utf-8"))
    assert cp["status"] == "failed" and cp["detail_status"] == "PRODUCT_NOT_FOUND"
    assert cp["http_status"] == 404 and cp["access_state"] == "UNKNOWN"
    assert cp["source"] == ("cache" if cached else "network")
    assert "record" not in cp and "next_retry_at" not in cp
    assert (tmp_path / "html" / (ASIN + ".html")).read_text(encoding="utf-8") == raw
    assert [e["status"] for e in events] == ["failed", "success"]
    assert len(session.requests) == (1 if cached else 2)
    assert session.waits == len(session.requests)
    # The access detector remains conservative; the collector handles only a
    # verified missing-product template, never turns this into NORMAL evidence.
    assert detect_access_status(404, raw) is AccessState.UNKNOWN
    classification, _, record = detail._classify_saved_page(raw, ASIN,
        json.loads((FIXTURE / (ASIN + ".meta.json")).read_text(encoding="utf-8")))
    assert classification == "INVALID_OR_EMPTY" and record is None


@pytest.mark.parametrize("status,extra,host", [
    (404, "<div>Robot Check</div>", "www.amazon.es"),
    (404, "<form action='/errors/validateCaptcha'></form>", "www.amazon.es"),
    (403, "", "www.amazon.es"),
    (429, "", "www.amazon.es"),
    (503, "", "www.amazon.es"),
    (404, "", "amazon.es.example.org"),
])
def test_restricted_or_unverified_404_still_stops(tmp_path, status, extra, host):
    raw = (FIXTURE / (ASIN + ".html")).read_text(encoding="utf-8") + extra
    session = Session(status, raw, host)
    with pytest.raises(AccessStopError):
        detail.collect_details([ASIN, NEXT], session, tmp_path)
    assert len(session.requests) == 1
    assert json.loads((tmp_path / "checkpoints" / (ASIN + ".json")).read_text(encoding="utf-8"))["status"] == "access_stop"


def test_unknown_404_without_verified_template_still_stops(tmp_path):
    with pytest.raises(AccessStopError):
        detail.collect_details([ASIN], Session(html="<html>unexplained error</html>"), tmp_path)
