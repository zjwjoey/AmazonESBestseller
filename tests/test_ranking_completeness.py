from amazon_es_bestseller.collection.ranking_v2 import ranking_completeness
from amazon_es_bestseller.collection.ranking_v2 import build_ranking_snapshot_v2


def test_rank_gap_and_duplicate_are_not_authoritative():
    result = ranking_completeness(
        [{"asin": "B000000001", "bestseller_rank": 1},
         {"asin": "B000000001", "bestseller_rank": 3}],
        expected_count=3, server_rendered_count=2, acp_available=True,
        acp_hydrated_count=0)
    assert result["duplicate_asin_count"] == 1
    assert result["rank_gap_count"] == 1
    assert result["page_authoritative"] is False


def test_incomplete_v2_audit_cannot_advance_snapshot_pointer(tmp_path):
    result = {
        "records": [{"asin": "B000000001", "bestseller_rank": 1}],
        "audit": {"page_authoritative": False, "completion_reason": "RANK_GAP"},
    }
    frozen = build_ranking_snapshot_v2(
        result, tmp_path,
        planned_sources=[{"source_url": "https://www.amazon.es/test", "page_number": 1}],
        source_statuses=[{"source_url": "https://www.amazon.es/test", "page_number": 1,
                          "status": "NORMAL", "parse_status": "PARSE_OK", "parsed_record_count": 1}],
        snapshot_id="snapshot_incomplete_v2", offline_frozen=False)
    assert frozen["manifest"]["snapshot_status"] == "INCOMPLETE"
    assert not (tmp_path / "latest_authoritative_snapshot.json").exists()
