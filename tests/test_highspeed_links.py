"""Offline link-first regressions: no product identity required or inferred."""
from pathlib import Path
import json
import pytest

from amazon_es_bestseller.highspeed.links import normalize_product_url, candidate_id
from amazon_es_bestseller.highspeed.adapters import parse_page
from amazon_es_bestseller.highspeed.sources import source_coverage, discover_sources

BASE = Path(__file__).parent / "fixtures" / "multisource"


def parse(html, kind="BEST_SELLERS", **kwargs):
    paths = {"BEST_SELLERS": "bestsellers", "MOVERS_SHAKERS": "movers-and-shakers",
        "NEW_RELEASES": "new-releases", "MOST_WISHED_FOR": "most-wished-for", "MOST_GIFTED": "most-gifted"}
    url = "https://www.amazon.es/gp/" + paths[kind]
    return parse_page(html, source={"source_id": kind, "source_type": kind, "actual_source_url": url},
        run_id="r", snapshot_id="s", page_url=url, page_number=1,
        observed_at="2026-10-13T08:00:00Z", evidence_path="saved.html", **kwargs)


@pytest.mark.parametrize("url", ["/dp/token", "/product/opaque-id", "/gp/product/long-unstructured-token", "/gp/aw/d/t"])
def test_product_tokens_without_identity(url):
    row = normalize_product_url(url)
    assert row["is_valid"]
    assert candidate_id(row["normalized_product_url"]) == candidate_id(row["normalized_product_url"])
    assert row["url_identity_status"] == "IDENTITY_UNCONFIRMED"


@pytest.mark.parametrize("url", [None, "", "#top", "javascript:alert(1)", "https://evil.example/dp/token",
    "https://www.amazon.es@evil.example/dp/token", "/gp/bestsellers", "/s?k=toy", "https://www.amazon.es:123/dp/token",
    "https://www.amazon.es:80/dp/token", "http://www.amazon.es:443/dp/token"])
def test_invalid_links_not_synthesized(url):
    row = normalize_product_url(url)
    assert not row["is_valid"] and row["normalized_product_url"] is None


def test_only_tracking_removed_query_bytes_order_repetition_and_variant_kept():
    first = normalize_product_url("/dp/token/ref=abc?tag=x&th=1&psc=1&opaque=%2f%2F&v=2&v=1&blank=&redirect=%2Fdp%2Fother#variant")
    assert first["normalized_product_url"].endswith("/dp/token?th=1&psc=1&opaque=%2f%2F&v=2&v=1&blank=&redirect=%2Fdp%2Fother#variant")
    second = normalize_product_url("/dp/token?th=2&psc=1")
    assert candidate_id(first["normalized_product_url"]) != candidate_id(second["normalized_product_url"])
    assert first["possible_duplicate_key"] == second["possible_duplicate_key"]


def test_redirect_remains_opaque_no_unwrap():
    row = normalize_product_url("/sspa/click?url=%2Fdp%2Fother&opaque=x")
    assert row["is_valid"] and "/sspa/click" in row["normalized_product_url"]
    assert row["reason"] == "REDIRECT_DESTINATION_UNVERIFIED"


def test_opaque_route_aliases_share_review_group_never_candidate_id():
    paths = ["/dp/arbitrary-token-without-ASIN", "/description/dp/arbitrary-token-without-ASIN",
        "/gp/product/arbitrary-token-without-ASIN", "/gp/aw/d/arbitrary-token-without-ASIN"]
    rows = [normalize_product_url(path) for path in paths]
    assert len({r["possible_duplicate_key"] for r in rows}) == 1
    assert len({candidate_id(r["normalized_product_url"]) for r in rows}) == len(paths)
    changed = normalize_product_url("/dp/another-opaque-token")
    assert changed["possible_duplicate_key"] != rows[0]["possible_duplicate_key"]
    assert all(r["url_identity_status"] == "IDENTITY_UNCONFIRMED" for r in rows)


def test_unrecognized_suffixes_and_redirects_are_not_route_alias_groups():
    plain = normalize_product_url("/dp/token")
    additional_path = normalize_product_url("/dp/token/unknown-action")
    generic = normalize_product_url("/product/token")
    redirect = normalize_product_url("/sspa/click?url=%2Fdp%2Ftoken")
    assert len({r["possible_duplicate_key"] for r in [plain, additional_path, generic, redirect]}) == 4


def test_all_cards_missing_link_and_identity_fields_preserved():
    html = '<h1>Los más vendidos</h1><div id="zg" data-expected-count="2"><div class="ranking-card"><span class="zg-bdg-text">#1</span><a href="/product/opaque"><span data-product-title>Item</span></a></div><div class="ranking-card"><span class="zg-bdg-text">#2</span><span data-product-title>No link</span></div></div>'
    result = parse(html)
    assert len(result["observations"]) == 2
    assert result["observations"][0]["candidate_id"]
    assert result["observations"][1]["candidate_id"] is None
    assert "PRODUCT_LINK_MISSING" in result["page"]["flags"]
    assert all("asin" not in key.lower() for row in result["observations"] for key in row)
    assert parse(html)["observations"][0]["observation_id"] == result["observations"][0]["observation_id"]


def test_carousel_ranks_are_local_and_recommendations_outside_context_excluded():
    card = '<div class="ranking-card"><span class="zg-bdg-text">#1</span><a href="/dp/token">Item</a></div>'
    result = parse('<h1>Los más vendidos</h1><div id="zg" data-expected-count="2"><div class="carousel" data-carousel-id="kitchen">' + card + '</div><div class="carousel" data-carousel-id="office">' + card + '</div></div>' + card)
    assert len(result["observations"]) == 2
    assert len({r["rank_context"] for r in result["observations"]}) == 2
    assert result["page"]["status"] == "COMPLETE"


def test_native_carousel_wrapper_scopes_more_than_four_levels():
    card = '<li class="a-carousel-card"><span class="zg-bdg-text">#1</span><a href="/dp/token">Item</a></li>'
    carousel = '<div data-card-metrics-id="{name}" data-acp-path="/acp/carousel/"><div><div><div><div><ol class="a-carousel">' + card + '</ol></div></div></div></div></div>'
    result = parse('<h1>Los más vendidos</h1><div id="zg">' + carousel.format(name="list_0") + carousel.format(name="list_1") + '</div>')
    assert len({r["rank_context"] for r in result["observations"]}) == 2
    assert result["page"]["actual_count"] == 2
    assert "DUPLICATE_RANK_IN_CONTEXT" not in result["page"]["flags"]
    assert not result["acp_pending"]


def test_real_native_carousel_fixture_preserves12localrankedcards():
    fixture = Path(__file__).parent / "fixtures" / "highspeed" / "p0_native_carousels.html"
    result = parse(fixture.read_text(encoding="utf-8"))
    assert len(result["observations"]) == result["page"]["actual_count"] == 12
    assert len({r["rank_context"] for r in result["observations"]}) == 2
    assert result["page"]["status"] == "UNVERIFIED"
    assert all(r["is_valid"] for r in result["observations"])
    assert len(result["discovered_sources"]) == 2
    assert all("/gp/bestsellers/" in r["rank_context"] for r in result["observations"])
    assert all(r["amazon_category"] for r in result["observations"])


def test_real_page2_missing_path_still_requires_native_continuation():
    fixture = Path(__file__).parent / "fixtures" / "highspeed" / "night5000_page2_missing_acp_path.html"
    html = fixture.read_text(encoding="utf-8")
    url = "https://www.amazon.es/gp/bestsellers/kitchen/2822691031?ie=UTF8&pg=2"
    result = parse_page(html, source={"source_id": "furniture", "source_type": "BEST_SELLERS", "actual_source_url": url},
        run_id="night", page_url=url, page_number=2, observed_at="2026-10-13T15:00:00Z", evidence_path=str(fixture))
    assert len(result["observations"]) == 30
    assert [r["ranking_position"] for r in result["observations"]] == list(range(51, 81))
    descriptor = result["acp_pending"][0]
    assert descriptor["url"] is None
    assert descriptor["metadata"]["path"] is None
    assert descriptor["offset"] == 30 and descriptor["count"] == 20
    assert descriptor["metadata"]["index_offset_raw"] == "30"
    assert len(descriptor["metadata"]["entries"]) == 50
    assert descriptor["request_contract"] == "NATIVE_REQUEST_DISCOVERY_REQUIRED"
    assert result["page"]["page_complete"] is False


def test_page2_saved_rendered_continuation_finishes_local50_not_absolute100():
    fixture = Path(__file__).parent / "fixtures" / "highspeed" / "night5000_page2_missing_acp_path.html"
    url = "https://www.amazon.es/gp/bestsellers/kitchen/2822691031?ie=UTF8&pg=2"
    body = "".join('<div class="ranking-card"><span class="zg-bdg-text">#' + str(rank) + '</span><a href="/product/opaque-' + str(rank) + '">Item</a></div>' for rank in range(81, 101))
    result = parse_page(fixture.read_text(encoding="utf-8"), source={"source_id": "furniture", "source_type": "BEST_SELLERS", "actual_source_url": url},
        run_id="night", page_url=url, page_number=2, observed_at="2026-10-13T15:00:00Z", evidence_path=str(fixture),
        acp_responses=[{"body": body, "evidence_path": "saved-continuation.html"}])
    assert result["page"]["actual_count"] == result["page"]["expected_count"] == 50
    assert result["page"]["status"] == "COMPLETE"
    assert not result["acp_pending"]
    assert [r["ranking_position"] for r in result["observations"]] == list(range(51, 101))


def test_acp_metadata_never_constructs_links_and_native_request_required():
    entries = [{"id": "opaque-one"}, {"id": "opaque-two"}]
    html = '<h1>Los más vendidos</h1><div id="zg" data-acp-path="/acp/native/"><div class="p13n-desktop-grid" data-client-recs-list=\'' + json.dumps(entries) + '\' data-index-offset="1" data-offset="2"><div class="ranking-card"><span class="zg-bdg-text">#1</span><a href="/product/direct-link">Item</a></div></div></div>'
    result = parse(html)
    assert len(result["observations"]) == 1
    descriptor = result["acp_pending"][0]
    assert descriptor["metadata"]["entries"] == entries
    assert descriptor["metadata"]["index_offset_raw"] == "1"
    assert descriptor["request_contract"] == "NATIVE_REQUEST_REQUIRED"
    assert "payload" not in descriptor


def test_acp_representations_all_retained_and_overlap_not_new_slot():
    card = '<div class="ranking-card"><span class="zg-bdg-text">#1</span><a href="/dp/token">Item</a></div>'
    result = parse('<h1>Los más vendidos</h1><div id="zg" data-expected-count="1">' + card + '</div>',
        acp_responses=[{"body": card, "evidence_path": "acp.html"}])
    assert len(result["observations"]) == 2
    assert result["page"]["actual_count"] == 1
    assert result["page"]["raw_card_count"] == 2
    assert result["page"]["status"] == "COMPLETE"


@pytest.mark.parametrize("kind", ["movers_shakers", "most_wished_for", "most_gifted"])
def test_real_saved_roots_header_recognized_missing_cards_remain_missing(kind):
    result = parse((BASE / "p0_saved_roots" / (kind + ".html")).read_text(encoding="utf-8"), kind.upper())
    assert result["page"]["recognized_page_type"]
    assert result["page"]["actual_count"] == 0
    assert "PAGE_CONTENT_MISSING" in result["page"]["flags"]


@pytest.mark.parametrize("code,status", [(403, "BLOCKED"), (429, "RATE_LIMITED"), (500, "NETWORK_ERROR"), (400, "UNKNOWN")])
def test_access_pages_not_counted(code, status):
    result = parse('<h1>Los más vendidos</h1>', http_status=code)
    assert result["page"]["access_status"] == status and not result["observations"]


def test_five_lists_and_unknown_completeness():
    for kind in ("best_sellers", "movers_shakers", "new_releases", "most_wished_for", "most_gifted"):
        result = parse((BASE / (kind + ".html")).read_text(encoding="utf-8"), kind.upper())
        assert result["page"]["recognized_page_type"] and result["observations"]
    result = parse('<h1>Los más vendidos</h1><div id="zg"><div class="ranking-card"><span class="zg-bdg-text">#1</span><a href="/dp/token">Item</a></div></div>')
    assert result["page"]["status"] == "UNVERIFIED"


def test_navigation_no_fabricated_siblings_hierarchy_and75_unverified():
    html = '<nav id="zg_browseRoot"><a href="/gp/bestsellers/kitchen/123">Kitchen</a><a href="/gp/new-releases/kitchen/123">Other list</a></nav>'
    rows = discover_sources(html, "BEST_SELLERS", "https://www.amazon.es/gp/bestsellers", evidence_path="nav.html", observed_at="2026-10-13T08:00:00Z")
    assert len(rows) == 1 and rows[0]["browse_node_id"] == "123"
    assert rows[0]["research_category"] is None
    assert len(source_coverage(rows)) == 75
    assert all(cell["status"] == "UNVERIFIED" for cell in source_coverage(rows))
