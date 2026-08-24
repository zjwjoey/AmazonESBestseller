from pathlib import Path

from amazon_es_bestseller.page_inspector import inspect_html, inspect_navigation


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
