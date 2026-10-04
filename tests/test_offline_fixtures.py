from pathlib import Path

from amazon_es_bestseller.collection.detail import parse_detail_page
from amazon_es_bestseller.collection.ranking import parse_bestsellers_page


FIXTURES = Path(__file__).parent / "fixtures" / "html"


def test_existing_saved_html_fixtures_are_offline_parseable():
    ranking = (FIXTURES / "bestsellers_grid.html").read_text(encoding="utf-8")
    product = (FIXTURES / "product_lunchbag.html").read_text(encoding="utf-8")
    assert parse_bestsellers_page(ranking, "https://www.amazon.es/test", "2026-10-04T00:00:00Z")
    assert parse_detail_page(product, "B000000001")["title_es_raw"]


def test_challenge_fixture_is_never_treated_as_product_evidence():
    html = (FIXTURES / "product_captcha.html").read_text(encoding="utf-8")
    assert parse_detail_page(html, "B000000001")["is_captcha"] is True
