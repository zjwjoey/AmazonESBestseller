from amazon_es_bestseller.monitoring.ranking_identity.urls import (
    asin_from_product_url, canonical_product_url, is_valid_asin,
)


def test_amazon_es_canonical_url_is_deterministic():
    assert is_valid_asin(" b078c6qr1c ")
    assert asin_from_product_url("/Producto/dp/b078c6qr1c/ref=zg") == "B078C6QR1C"
    assert canonical_product_url("b078c6qr1c") == "https://www.amazon.es/dp/B078C6QR1C"


def test_invalid_asin_never_gets_executable_url():
    assert not is_valid_asin("B078C6QR1")
    assert canonical_product_url("B078C6QR1") == ""
