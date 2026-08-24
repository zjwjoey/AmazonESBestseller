from pathlib import Path

from amazon_es_bestseller.page_inspector import inspect_html


def kitchen_html() -> str:
    return Path("tests/fixtures/kitchen_sample.html").read_text(encoding="utf-8")


def test_inspector_counts_repeated_product_card_candidates():
    result = inspect_html(kitchen_html())
    assert result.product_card_candidate_count == 2
    assert "json_ld" in result.structured_data_kinds
