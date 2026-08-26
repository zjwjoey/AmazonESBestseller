import pytest

from amazon_es_bestseller.collection.quota import QuotaError, annotate_groups, select_quota


def test_select_quota_deduplicates_by_asin_within_group():
    records = [
        {"asin": "b1", "ranking_source_url": "u1", "category_l1": "Hogar y cocina"},
        {"asin": "B1", "ranking_source_url": "u2", "category_l1": "Hogar y cocina"},
        {"asin": "D1", "ranking_source_url": "u3", "category_group": "diy"},
    ]
    selected = select_quota(records, {"hogar": 1, "diy": 1})
    assert selected["hogar"][0]["asin"] == "B1"
    assert selected["diy"][0]["asin"] == "D1"


def test_select_quota_fails_when_group_is_short():
    with pytest.raises(QuotaError, match="diy.*需要 2.*只有 1"):
        select_quota([{"asin": "D1", "category_group": "diy"}], {"diy": 2})


def test_select_quota_preserves_source_context_and_stops_at_quota():
    records = [
        {"asin": "H1", "category_group": "hogar", "ranking_source_url": "u1"},
        {"asin": "H2", "category_group": "hogar", "ranking_source_url": "u2"},
    ]
    selected = select_quota(records, {"hogar": 1})
    assert selected == {
        "hogar": [{"asin": "H1", "category_group": "hogar", "ranking_source_url": "u1"}]
    }


def test_annotate_groups_matches_configured_url_without_guessing():
    config = [{"url": "https://www.amazon.es/gp/bestsellers/kitchen/123/?x=1", "group": "hogar"}]
    records = [
        {"asin": "H1", "ranking_source_url": "https://www.amazon.es/gp/bestsellers/kitchen/123/"},
        {"asin": "X1", "ranking_source_url": "https://www.amazon.es/gp/bestsellers/kitchen/999/"},
    ]
    tagged = annotate_groups(records, config)
    assert tagged[0]["category_group"] == "hogar"
    assert "category_group" not in tagged[1]
