from amazon_es_bestseller.normalization.locale_numbers import (
    parse_locale_number, parse_price_es, parse_rating_es, parse_review_count_es,
)
from amazon_es_bestseller.transport.base import locale_observation


def test_amazon_es_numeric_contract():
    assert parse_locale_number("19,99 €") == 19.99
    assert parse_price_es("1.299,99 €") == 1299.99
    assert parse_rating_es("4,5 de 5 estrellas") == 4.5
    assert parse_review_count_es("1.234 reseñas") == 1234


def test_locale_evidence_distinguishes_missing_and_mismatch():
    assert locale_observation('<html lang="es"><body/></html>') == {
        "requested_locale": "es_ES", "observed_language": "es", "language_mismatch": False,
    }
    assert locale_observation('<html lang="en-US"><body/></html>')["language_mismatch"] is True
    assert locale_observation("<html><body/></html>")["language_mismatch"] is None
