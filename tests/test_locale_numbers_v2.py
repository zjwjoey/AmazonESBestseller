from amazon_es_bestseller.normalization.locale_numbers import (
    parse_price_es, parse_rating_es, parse_review_count_es,
)


def test_spanish_price_and_decimal_formats():
    assert parse_price_es("19,99 €") == 19.99
    assert parse_price_es("1.299,99 €") == 1299.99


def test_spanish_rating_and_review_count():
    assert parse_rating_es("4,5 de 5 estrellas") == 4.5
    assert parse_review_count_es("1.234 reseñas") == 1234
