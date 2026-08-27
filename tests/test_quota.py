import pytest
import json
from pathlib import Path

from amazon_es_bestseller.collection.quota import QuotaError, annotate_groups, select_quota


def test_reviewed_200sku_config_has_150_50_quota_and_real_urls():
    path = Path(__file__).resolve().parents[1] / "configs" / "amazon_es_200sku_categories.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert sum(r["quota"] for r in rows if r["category_group"] == "hogar") == 150
    assert sum(r["quota"] for r in rows if r["category_group"] == "diy") == 50
    assert all(r["url"].startswith("https://www.amazon.es/gp/bestsellers/") for r in rows)


def test_1000sku_scale_config_has_seven_groups_and_candidate_pages():
    path = Path(__file__).resolve().parents[1] / "configs" / "amazon_es_1000sku_categories.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    expected = {"hogar": 245, "diy": 180, "office": 145, "garden": 130,
                "car": 110, "pets": 100, "beauty": 90}
    assert {r["category_group"] for r in rows} == set(expected)
    assert {g: sum(r["quota"] for r in rows if r["category_group"] == g)
            for g in expected} == expected
    assert sum(expected.values()) == 1000
    assert len(rows) >= 40
    assert all(r["url"].startswith("https://www.amazon.es/gp/bestsellers/") for r in rows)


def test_select_quota_deduplicates_by_asin_within_group():
    records = [
        {"asin": "b1", "ranking_source_url": "u1", "category_l1": "Hogar y cocina"},
        {"asin": "B1", "ranking_source_url": "u2", "category_l1": "Hogar y cocina"},
        {"asin": "D1", "ranking_source_url": "u3", "category_group": "diy"},
    ]
    selected = select_quota(records, {"hogar": 1, "diy": 1})
    assert selected["hogar"][0]["asin"] == "B1"
    assert selected["diy"][0]["asin"] == "D1"


def test_select_quota_deduplicates_asins_across_groups_and_reports_shortfall():
    records = [
        {"asin": "SHARED", "category_group": "hogar"},
        {"asin": "SHARED", "category_group": "diy"},
    ]
    with pytest.raises(QuotaError, match="diy.*需要 1.*只有 0"):
        select_quota(records, {"hogar": 1, "diy": 1})


def test_quota_shortfall_has_machine_readable_code():
    with pytest.raises(QuotaError) as exc:
        select_quota([{"asin": "D1", "category_group": "diy"}], {"diy": 2})
    assert exc.value.code == "QUOTA_UNIQUE_SHORTFALL"


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
