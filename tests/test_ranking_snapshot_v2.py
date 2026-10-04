from datetime import datetime, timezone
from pathlib import Path

import pytest

from amazon_es_bestseller.collection.ranking_v2 import (
    RankingSnapshotIncompleteError, build_ranking_snapshot_v2,
    parse_ranking_snapshot_v2, require_authoritative_page,
)


def _card(asin, rank):
    return (f'<div id="gridItemRoot"><span class="a-badge-text">#{rank}</span>'
            f'<a href="/dp/{asin}"><span class="a-size-base-plus">Item {rank}</span></a>'
            '<span class="a-price"><span class="a-offscreen">1,00 €</span></span>'
            '</div>')


def test_ranking_v2_counts_duplicate_asin_before_deduplication():
    html = _card("B000000001", 1) + _card("B000000001", 2)
    result = parse_ranking_snapshot_v2(html, "https://www.amazon.es/test", "2026-10-04T00:00:00Z")
    assert len(result["records"]) == 1
    assert result["audit"]["duplicate_asin_count"] == 1
    assert result["audit"]["page_authoritative"] is False
    with pytest.raises(RankingSnapshotIncompleteError):
        require_authoritative_page(result)


def test_ranking_v2_can_hydrate_acp_and_persist_audit(tmp_path: Path):
    entries = '[{"id":"B000000001"},{"id":"B000000002"},{"id":"B000000003"}]'
    html = (f"<div data-client-recs-list='{entries}' data-acp-path=\"/acp/\" "
            'data-acp-params="token=x"><div class="p13n-desktop-grid" '
            'data-faceoutkataname="GeneralFaceout" data-reftag="x"></div></div>'
            + _card("B000000001", 1))

    def hydrate(_metadata, offset, count):
        assert offset == 1 and count == 2
        return [
            {"asin": "B000000002", "bestseller_rank": 2},
            {"asin": "B000000003", "bestseller_rank": 3},
        ]

    result = parse_ranking_snapshot_v2(html, "https://www.amazon.es/test", "2026-10-04T00:00:00Z",
                                       acp_hydrator=hydrate)
    assert result["audit"]["page_authoritative"] is True
    assert len(require_authoritative_page(result)) == 3
    frozen = build_ranking_snapshot_v2(
        result, tmp_path, planned_sources=[{"source_url": "https://www.amazon.es/test", "page_number": 1}],
        source_statuses=[{"source_url": "https://www.amazon.es/test", "page_number": 1,
                          "status": "NORMAL", "parse_status": "PARSE_OK", "parsed_record_count": 3}],
        snapshot_id="snapshot_v2_test", started_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
        offline_frozen=True)
    assert frozen["manifest"]["parser_version"] == "collection.ranking_v2"
    assert frozen["manifest"]["ranking_v2_audit"]["unique_asin_count"] == 3
