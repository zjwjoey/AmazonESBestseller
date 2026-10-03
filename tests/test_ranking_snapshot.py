# -*- coding: utf-8 -*-
import json

import pytest

from amazon_es_bestseller.collection.ranking import parse_bestsellers_page
from amazon_es_bestseller.monitoring.snapshot import build_ranking_snapshot, collect_ranking_snapshot


def _card(asin="B078C6QR1C", href="/Producto/dp/B078C6QR1C/ref=zg_bs_x?x=1"):
    return f'''<div id="gridItemRoot" data-asin="{asin}">
      <span class="zg-bdg-text">#1</span>
      {('<a href="' + href + '">Producto</a>') if href is not None else ''}
    </div>'''


def test_parser_preserves_raw_url_normalizes_and_matches_link():
    raw = "/Producto/dp/B078C6QR1C/ref=zg_bs_x?x=1"
    records = parse_bestsellers_page("<html><body>" + _card(href=raw) +
                                     "</body></html>", "https://www.amazon.es/zgbs/1", "now")
    row = records[0]
    assert row["ranking_product_url_raw"] == raw
    assert row["ranking_product_url_normalized"] == "https://www.amazon.es/dp/B078C6QR1C"
    assert row["ranking_link_asin"] == "B078C6QR1C"
    assert row["ranking_link_identity_status"] == "MATCH"


def test_parser_keeps_ranking_asin_when_href_asin_mismatches():
    records = parse_bestsellers_page("<html><body>" + _card(
        asin="B078C6QR1C", href="/other/dp/B075JJRFVV/ref=x") +
        "</body></html>", "https://www.amazon.es/zgbs/1", "now")
    row = records[0]
    assert row["ranking_asin"] == "B078C6QR1C"
    assert row["ranking_link_asin"] == "B075JJRFVV"
    assert row["ranking_link_identity_status"] == "LINK_ASIN_MISMATCH"


def test_parser_distinguishes_missing_and_unparseable_product_url():
    missing = parse_bestsellers_page("<html><body>" + _card(href=None) +
                                     "</body></html>", "https://www.amazon.es/zgbs/1", "now")[0]
    assert missing["ranking_product_url_raw"] == ""
    assert missing["ranking_link_asin"] is None
    assert missing["ranking_link_identity_status"] == "NO_PRODUCT_URL"

    invalid = parse_bestsellers_page("<html><body>" + _card(
        href="/Producto/sin-dp/ref=x") + "</body></html>",
        "https://www.amazon.es/zgbs/1", "now")[0]
    assert invalid["ranking_product_url_raw"] == "/Producto/sin-dp/ref=x"
    assert invalid["ranking_link_asin"] is None
    assert invalid["ranking_link_identity_status"] == "NO_ASIN_IN_URL"


@pytest.mark.parametrize("href", [
    "/gp/product/B078C6QR1C/ref=x",
    "/gp/aw/d/B078C6QR1C?ref=x",
])
def test_parser_supports_additional_amazon_product_href_forms(href):
    row = parse_bestsellers_page("<html><body>" + _card(href=href) +
                                 "</body></html>", "https://www.amazon.es/zgbs/1", "now")[0]
    assert row["ranking_product_url_raw"] == href
    assert row["ranking_link_asin"] == "B078C6QR1C"
    assert row["ranking_link_identity_status"] == "MATCH"


def test_parser_prefers_product_href_over_an_earlier_non_product_anchor():
    html = '''<div id="gridItemRoot" data-asin="B078C6QR1C">
      <a href="/promo">promo</a>
      <a href="/gp/product/B078C6QR1C/ref=x">product</a>
      <span class="zg-bdg-text">#1</span>
    </div>'''
    row = parse_bestsellers_page(html, "https://www.amazon.es/zgbs/1", "now")[0]
    assert row["ranking_product_url_raw"] == "/gp/product/B078C6QR1C/ref=x"
    assert row["ranking_link_asin"] == "B078C6QR1C"


def test_snapshot_is_authoritative_and_updates_pointer(tmp_path):
    raw = "/Producto/dp/B078C6QR1C/ref=tracking"
    result = build_ranking_snapshot([{
        "asin": "B078C6QR1C", "bestseller_rank": 1,
        "ranking_source_url": "https://www.amazon.es/zgbs/1",
        "ranking_product_url_raw": raw,
    }], tmp_path, planned_sources=["https://www.amazon.es/zgbs/1"],
        source_statuses={"https://www.amazon.es/zgbs/1": "NORMAL"},
        snapshot_id="snapshot_test_authoritative", started_at="2026-10-03T00:00:00Z")
    manifest = result["manifest"]
    assert manifest["snapshot_status"] == "AUTHORITATIVE"
    assert manifest["latest_authoritative"] is True
    assert json.loads((tmp_path / "latest_authoritative_snapshot.json").read_text())[
        "snapshot_id"] == "snapshot_test_authoritative"
    saved = json.loads((result["path"] / "rankings.json").read_text())[0]
    assert saved["ranking_product_url_raw"] == raw

    with pytest.raises(FileExistsError):
        build_ranking_snapshot([], tmp_path, snapshot_id="snapshot_test_authoritative",
                               started_at="2026-10-03T00:00:00Z")


def test_incomplete_snapshot_does_not_replace_authoritative_pointer(tmp_path):
    first = build_ranking_snapshot([{"asin": "B000000001", "ranking_source_url": "u1",
                                    "ranking_page_number": 1}], tmp_path, planned_sources=["u1"],
                                   source_statuses={"u1": "NORMAL"},
                                   snapshot_id="snapshot_good", started_at="2026-10-03T00:00:00Z")
    pointer = json.loads((tmp_path / "latest_authoritative_snapshot.json").read_text())
    assert pointer["snapshot_id"] == first["manifest"]["snapshot_id"]
    bad = build_ranking_snapshot([], tmp_path, planned_sources=["u1", "u2"],
                                 source_statuses={"u1": "NORMAL", "u2": "CHALLENGE"},
                                 snapshot_id="snapshot_bad", started_at="2026-10-03T00:01:00Z")
    assert bad["manifest"]["snapshot_status"] == "INCOMPLETE"
    assert json.loads((tmp_path / "latest_authoritative_snapshot.json").read_text())[
        "snapshot_id"] == "snapshot_good"


def test_zero_record_normal_page_is_incomplete(tmp_path):
    result = build_ranking_snapshot([], tmp_path, planned_sources=["u1"],
                                    source_statuses={"u1": "NORMAL"},
                                    snapshot_id="snapshot_empty", started_at="2026-10-03T00:00:00Z")
    assert result["manifest"]["snapshot_status"] == "INCOMPLETE"
    assert not (tmp_path / "latest_authoritative_snapshot.json").exists()


def test_page_level_completeness_requires_all_pages_and_nonempty_parse(tmp_path):
    rows = [{"asin": "B000000001", "ranking_source_url": "u1", "ranking_page_number": 1}]
    statuses = [
        {"source_url": "u1", "page_number": 1, "access_state": "NORMAL",
         "parse_status": "PARSE_OK", "parsed_record_count": 1},
        {"source_url": "u1", "page_number": 2, "access_state": "NORMAL",
         "parse_status": "PARSE_EMPTY", "parsed_record_count": 0},
    ]
    result = build_ranking_snapshot(rows, tmp_path, planned_sources=statuses,
                                    source_statuses=statuses,
                                    snapshot_id="snapshot_partial", started_at="2026-10-03T00:00:00Z")
    manifest = result["manifest"]
    assert manifest["expected_page_count"] == 2
    assert manifest["completed_page_count"] == 2
    assert manifest["empty_page_count"] == 1
    assert manifest["snapshot_status"] == "INCOMPLETE"


def test_failed_snapshot_preserves_partial_page_records_and_evidence(tmp_path):
    class Page:
        def __init__(self):
            self.html = ""

        def content(self):
            return self.html

    class Session:
        def __init__(self):
            self.page = Page()

        def goto(self, url):
            if "pg=2" in url:
                self.page.html = "<html><body><div>empty</div></body></html>"
            else:
                self.page.html = "<html><body>" + _card() + "</body></html>"
            return 200

        def wait_between_requests(self):
            pass

    result = collect_ranking_snapshot(["https://www.amazon.es/zgbs/1"], Session(),
                                      tmp_path, pages_per_url=2)
    assert result["manifest"]["snapshot_status"] == "INCOMPLETE"
    assert result["manifest"]["record_count"] == 1
    assert result["manifest"]["expected_page_count"] == 2
    assert result["manifest"]["empty_page_count"] == 1
    assert len(list((result["path"] / "html").glob("ranking_*.html"))) == 2
