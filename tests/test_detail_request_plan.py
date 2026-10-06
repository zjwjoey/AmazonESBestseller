from amazon_es_bestseller.collection.detail_request_plan import build_detail_request_plan


ASIN = "B012345678"


def test_raw_ranking_href_is_primary_even_when_link_asin_differs():
    plan = build_detail_request_plan({
        "asin": ASIN,
        "ranking_product_url_raw": "/Producto/dp/B099999999/ref=zg_bs_x",
        "ranking_product_url_normalized": "https://www.amazon.es/dp/B012345678",
        "ranking_link_asin": "B099999999",
        "ranking_link_identity_status": "LINK_ASIN_MISMATCH",
    })
    assert plan["request_source"] == "RANKING_RAW"
    assert plan["preferred_request_url"] == "https://www.amazon.es/Producto/dp/B099999999/ref=zg_bs_x"
    assert plan["ranking_link_asin"] == "B099999999"


def test_normalized_url_then_asin_fallback_are_deterministic():
    normal = build_detail_request_plan({
        "asin": ASIN,
        "ranking_product_url_raw": "https://example.invalid/dp/B000000000",
        "ranking_product_url_normalized": "https://www.amazon.es/gp/product/B012345678",
    })
    fallback = build_detail_request_plan({"asin": ASIN})
    assert normal["request_source"] == "RANKING_NORMALIZED"
    assert normal["preferred_request_url"] == "https://www.amazon.es/gp/product/B012345678"
    assert fallback["request_source"] == "ASIN_FALLBACK"
    assert fallback["preferred_request_url"] == "https://www.amazon.es/dp/B012345678"


def test_selected_candidate_context_beats_a_better_ranked_secondary_context():
    plan = build_detail_request_plan({
        "asin": ASIN,
        "ranking_product_url_raw": "/selected/dp/B012345678/ref=selected",
        "bestseller_rank": 99,
        "ranking_contexts": [{
            "ranking_product_url_raw": "/other/dp/B012345678/ref=other",
            "bestseller_rank": 1,
        }],
    })
    assert plan["preferred_request_url"] == "https://www.amazon.es/selected/dp/B012345678/ref=selected"
