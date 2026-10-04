from amazon_es_bestseller.monitoring.ranking_identity.resolver import resolve_candidates


def test_missing_href_derives_canonical_url():
    records, _ = resolve_candidates([{
        "asin": "B078C6QR1C", "card_asin": "B078C6QR1C", "href_asin": "",
        "asin_source": "CARD_DATA_ASIN", "evidence_source": "SERVER_RENDERED_CARD",
    }])
    assert records[0]["product_url"] == "https://www.amazon.es/dp/B078C6QR1C"
    assert records[0]["identity_status"] == "ASIN_CONFIRMED_URL_DERIVED"


def test_card_href_conflict_is_preserved_and_not_overwritten():
    records, _ = resolve_candidates([{
        "asin": "B078C6QR1C", "card_asin": "B078C6QR1C", "href_asin": "B075JJRFVV",
        "raw_href": "/x/dp/B075JJRFVV", "asin_source": "CARD_DATA_ASIN",
        "evidence_source": "SERVER_RENDERED_CARD",
    }])
    assert records[0]["identity_status"] == "IDENTITY_CONFLICT"
    assert records[0]["product_url"] is None
    assert records[0]["conflict_asins"] == {"card_asin": "B078C6QR1C", "href_asin": "B075JJRFVV"}
