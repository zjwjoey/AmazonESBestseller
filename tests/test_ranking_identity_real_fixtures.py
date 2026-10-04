from pathlib import Path

from amazon_es_bestseller.monitoring.ranking_identity.extract import extract_identity_from_evidence


def test_real_amazon_es_saved_fixture_has_executable_urls():
    result = extract_identity_from_evidence(Path("tests/fixtures/amazon_es/ranking_identity"))
    assert result["audit"]["server_rendered_count"] > 0
    assert result["audit"]["valid_asin_count"] > 0
    assert result["audit"]["product_url_count"] == result["audit"]["unique_asin_count"]
    assert all(row["product_url"].startswith("https://www.amazon.es/dp/") for row in result["records"])
