"""Offline regression tests for recovered challenge-page state semantics."""
import json

import pytest

from amazon_es_bestseller.access.detector import AccessStopError, AccessState
from amazon_es_bestseller.collection.detail import _classify_saved_page, collect_details
from amazon_es_bestseller.collection.ranking import collect_rankings


ASIN = "B078C6QR1C"
CHALLENGE_HTML = "<html><body>validateCaptcha Type the characters</body></html>"
NORMAL_HTML = (
    "<html><head><link rel='canonical' href='https://www.amazon.es/dp/%s'></head>"
    "<body><input id='ASIN' value='%s'><h1 id='productTitle'>Producto normal</h1></body></html>"
) % (ASIN, ASIN)


class _Page:
    def __init__(self, html, url=""):
        self.html = html
        self.url = url

    def content(self):
        return self.html


class _RecoveringSession:
    def __init__(self, status, final_html=NORMAL_HTML, recover=True):
        self.status = status
        self.final_html = final_html
        self.recover = recover
        self.page = _Page(CHALLENGE_HTML, "https://www.amazon.es/dp/%s" % ASIN)
        self.wait_between_requests_calls = 0

    def goto(self, url):
        return self.status

    def wait_for_product_page(self):
        pass

    def wait_for_price_text(self):
        pass

    def wait_between_requests(self):
        self.wait_between_requests_calls += 1

    def wait_for_challenge_clear(self, html, status=None):
        if self.recover:
            self.page.html = self.final_html
            return AccessState.NORMAL, self.final_html, True
        return AccessState.CHALLENGE, html, False


@pytest.mark.parametrize("status", [403, 429, 200])
def test_detail_challenge_recovery_accepts_final_page_and_preserves_initial_status(tmp_path, status):
    session = _RecoveringSession(status)
    details = collect_details([ASIN], session, str(tmp_path))

    assert details[0]["access_state"] == "NORMAL"
    assert details[0]["initial_access_state"] == "CHALLENGE"
    assert details[0]["status_code"] == status
    assert details[0]["recovered_from_challenge"] is True
    meta = json.loads((tmp_path / "html" / (ASIN + ".meta.json")).read_text(encoding="utf-8"))
    assert meta["status_code"] == status
    assert meta["access_state"] == "NORMAL"
    assert meta["recovered_from_challenge"] is True
    assert (tmp_path / "html" / (ASIN + ".html.challenge")).exists()
    assert "Producto normal" in (tmp_path / "html" / (ASIN + ".html")).read_text(encoding="utf-8")
    assert (tmp_path / "details.json").exists()


@pytest.mark.parametrize("status", [403, 429])
def test_unrecovered_challenge_still_stops(status, tmp_path):
    with pytest.raises(AccessStopError):
        collect_details([ASIN], _RecoveringSession(status, recover=False), str(tmp_path))
    assert not (tmp_path / "details.json").exists()


def test_recovered_saved_html_resumes_using_final_state(tmp_path):
    html_dir = tmp_path / "html"
    html_dir.mkdir()
    (html_dir / (ASIN + ".html")).write_text(NORMAL_HTML, encoding="utf-8")
    (html_dir / (ASIN + ".meta.json")).write_text(json.dumps({
        "status_code": 403,
        "initial_access_state": "CHALLENGE",
        "access_state": "NORMAL",
        "recovered_from_challenge": True,
        "final_url": "https://www.amazon.es/dp/%s" % ASIN,
    }), encoding="utf-8")
    session = _RecoveringSession(200)
    session.goto = lambda url: (_ for _ in ()).throw(AssertionError("resume must not navigate"))

    details = collect_details([ASIN], session, str(tmp_path))
    assert details[0]["resumed_from_html"] is True
    assert details[0]["status_code"] == 403
    assert details[0]["access_state"] == "NORMAL"
    assert details[0]["recovered_from_challenge"] is True


def test_meta_normal_cannot_override_challenge_html(tmp_path):
    classification, state, record = _classify_saved_page(CHALLENGE_HTML, ASIN, {
        "status_code": 200,
        "access_state": "NORMAL",
        "recovered_from_challenge": True,
    })
    assert classification == "CHALLENGE"
    assert state is AccessState.CHALLENGE
    assert record is None


def test_invalid_recovered_html_is_quarantined(tmp_path):
    html_dir = tmp_path / "html"
    html_dir.mkdir()
    (html_dir / (ASIN + ".html")).write_text("<html><body>no title</body></html>", encoding="utf-8")
    (html_dir / (ASIN + ".meta.json")).write_text(json.dumps({
        "status_code": 403,
        "initial_access_state": "CHALLENGE",
        "access_state": "NORMAL",
        "recovered_from_challenge": True,
        "final_url": "https://www.amazon.es/dp/%s" % ASIN,
    }), encoding="utf-8")

    assert collect_details([ASIN], _RecoveringSession(200), str(tmp_path)) == []
    assert (tmp_path / "quarantine" / ASIN / (ASIN + ".html")).exists()


def test_recovered_asin_mismatch_is_quarantined(tmp_path):
    other = "B000000001"
    html_dir = tmp_path / "html"
    html_dir.mkdir()
    mismatch_html = NORMAL_HTML.replace(ASIN, other)
    (html_dir / (ASIN + ".html")).write_text(mismatch_html, encoding="utf-8")
    (html_dir / (ASIN + ".meta.json")).write_text(json.dumps({
        "status_code": 403,
        "access_state": "NORMAL",
        "recovered_from_challenge": True,
        "final_url": "https://www.amazon.es/dp/%s" % ASIN,
    }), encoding="utf-8")

    assert collect_details([ASIN], _RecoveringSession(200), str(tmp_path)) == []
    assert (tmp_path / "quarantine" / ASIN / (ASIN + ".html")).exists()


def test_ranking_recovery_keeps_initial_status_and_final_state(tmp_path):
    ranking_html = (
        "<html><body><div id='gridItemRoot'><a href='/dp/%s'>item</a>"
        "<span class='zg-bdg-text'>#1</span></div></body></html>"
    ) % ASIN
    session = _RecoveringSession(403, ranking_html)
    records = collect_rankings(["https://www.amazon.es/gp/bestsellers/electronics/"], session, str(tmp_path))

    assert records[0]["status_code"] == 403
    assert records[0]["initial_access_state"] == "CHALLENGE"
    assert records[0]["access_state"] == "NORMAL"
    assert records[0]["recovered_from_challenge"] is True
