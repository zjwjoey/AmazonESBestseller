from amazon_es_bestseller.collection.ranking_v2 import ranking_completeness


def test_rank_gap_and_duplicate_are_not_authoritative():
    result = ranking_completeness(
        [{"asin": "B000000001", "bestseller_rank": 1},
         {"asin": "B000000001", "bestseller_rank": 3}],
        expected_count=3, server_rendered_count=2, acp_available=True,
        acp_hydrated_count=0)
    assert result["duplicate_asin_count"] == 1
    assert result["rank_gap_count"] == 1
    assert result["page_authoritative"] is False
