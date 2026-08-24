from pathlib import Path

from amazon_es_bestseller.page_inspector import inspect_detail_fields, inspect_html, inspect_navigation


def kitchen_html() -> str:
    return Path("tests/fixtures/kitchen_sample.html").read_text(encoding="utf-8")


def test_inspector_counts_repeated_product_card_candidates():
    result = inspect_html(kitchen_html())
    assert result.product_card_candidate_count == 2
    assert "json_ld" in result.structured_data_kinds


def test_navigation_inspection_detects_page_two_without_following_it():
    html = '<nav aria-label="pagination"><a href="/gp/bestsellers/kitchen?pg=2">2</a></nav>'
    result = inspect_navigation(html)
    assert result.pagination_present is True
    assert result.page_two_url_present is True


def test_inspector_reports_structured_data_fields():
    html = '<script type="application/ld+json">{"name":"Product","brand":{"name":"Brand"}}</script>'

    result = inspect_html(html)

    assert "name" in result.structured_data_fields
    assert "brand" in result.structured_data_fields


def test_detail_field_inspector_reports_observed_fields():
    html = """
    <span id="productTitle">Product</span>
    <a id="bylineInfo">Brand</a>
    <div id="availability">In Stock</div>
    <div id="detailBullets_feature_div">ASIN B012345678</div>
    <div id="variation_size_name">Large</div>
    """

    result = inspect_detail_fields(html)

    assert result["title"] is True
    assert result["brand"] is True
    assert result["availability"] is True
    assert result["asin"] is True
    assert result["variant_information"] is True
