from amazon_es_bestseller.monitoring.detail_planner import build_detail_plan
import pytest


def test_detail_planner_prefers_identity_snapshot_product_url():
    plan = build_detail_plan({
        "snapshot_status": "IDENTITY_COMPLETE",
        "snapshot_id": "identity_snapshot_test",
        "records": [{
            "asin": "B078C6QR1C",
            "product_url": "https://www.amazon.es/dp/B078C6QR1C",
            "product_url_source": "CANONICAL_FROM_ASIN",
            "identity_status": "ASIN_CONFIRMED_URL_DERIVED",
        }],
    })
    row = plan["records"][0]
    assert row["preferred_request_url"] == "https://www.amazon.es/dp/B078C6QR1C"
    assert row["preferred_request_url_source"] == "ranking_identity_snapshot_product_url"


def test_detail_planner_rejects_non_complete_identity_snapshot():
    with pytest.raises(ValueError, match="非完整 Ranking Identity Snapshot"):
        build_detail_plan({
            "snapshot_kind": "RANKING_IDENTITY",
            "snapshot_status": "IDENTITY_COMPLETENESS_UNKNOWN",
            "identity_complete": False,
            "snapshot_id": "identity_snapshot_test",
            "records": [{"asin": "B078C6QR1C"}],
        })
