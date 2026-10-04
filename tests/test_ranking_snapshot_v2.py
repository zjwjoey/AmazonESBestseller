import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from amazon_es_bestseller.collection.ranking_v2 import (
    RankingSnapshotIncompleteError, build_ranking_snapshot_v2,
    make_acp_hydrator, parse_ranking_snapshot_v2, require_authoritative_page,
)
from amazon_es_bestseller.monitoring import snapshot as snapshot_module
from amazon_es_bestseller.transport.base import TransportResponse


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


def test_collect_ranking_snapshot_v2_persists_incomplete_page_audit(tmp_path, monkeypatch):
    source = "https://www.amazon.es/test"
    entries = '[{"id":"B000000001"},{"id":"B000000002"}]'
    html = (f"<div data-client-recs-list='{entries}' data-acp-path=\"/acp/\"></div>"
            + _card("B000000001", 1))

    def fake_collect(_urls, _session, _out_dir, *, run_dir, **_kwargs):
        root = Path(run_dir)
        (root / "html").mkdir(parents=True)
        (root / "pages").mkdir()
        (root / "html" / "ranking_000.html").write_text(html, encoding="utf-8")
        status = {"source_url": source, "page_number": 1, "http_status": 200,
                  "access_state": "NORMAL", "parse_status": "PARSE_OK",
                  "parsed_record_count": 1}
        (root / "page_statuses.json").write_text(json.dumps([status]), encoding="utf-8")
        (root / "pages" / "page_000.json").write_text(
            json.dumps({"status": status, "records": []}), encoding="utf-8")
        return []

    monkeypatch.setattr(snapshot_module, "collect_rankings", fake_collect)
    result = snapshot_module.collect_ranking_snapshot(
        [source], object(), tmp_path, parser_version="v2")
    assert result["manifest"]["parser_version"] == "collection.ranking_v2"
    assert result["manifest"]["snapshot_status"] == "INCOMPLETE"
    assert result["manifest"]["ranking_v2_audit"]["page_authoritative"] is False


def test_acp_hydrator_uses_transport_form_request():
    calls = []

    class Transport:
        def fetch_ajax(self, url, **kwargs):
            calls.append((url, kwargs))
            return TransportResponse(
                200, url, _card("B000000002", 2), access_state="NORMAL")

    hydrate = make_acp_hydrator(Transport())
    records = hydrate({
        "path": "/acp/", "params": "token=x", "faceout": "GeneralFaceout",
        "reftag": "zg_bs", "source_url": "https://www.amazon.es/zgbs/test",
        "entries": [{"id": "B000000001"}, {"id": "B000000002"}],
    }, 1, 1)
    assert [row["asin"] for row in records] == ["B000000002"]
    url, kwargs = calls[0]
    assert url == "https://www.amazon.es/acp/nextPage"
    assert kwargs["method"] == "POST"
    assert kwargs["headers"]["x-amz-acp-params"] == "token=x"
    assert "faceoutkataname=GeneralFaceout" in kwargs["payload"]
