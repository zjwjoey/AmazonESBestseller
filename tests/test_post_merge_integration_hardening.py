import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from amazon_es_bestseller.categories.crawler import CategoryCrawler, CategoryCrawlerState
from amazon_es_bestseller.categories.models import PlacementStatus
from amazon_es_bestseller.categories.provenance import category_evidence_from_detail
from amazon_es_bestseller.collection.ranking import parse_bestsellers_page
from amazon_es_bestseller.collection.ranking_v2 import (
    make_acp_hydrator, parse_ranking_snapshot_v2, replay_acp_response_evidence,
)
from amazon_es_bestseller.monitoring.detail_planner import build_detail_plan
from amazon_es_bestseller.monitoring.ranking_identity.completeness import (
    audit_identity_records, evaluate_authority,
)
from amazon_es_bestseller.monitoring.ranking_identity.snapshot import _snapshot_id
from amazon_es_bestseller.monitoring.ranking_identity.extract import extract_identity_from_evidence
from amazon_es_bestseller.transport.base import TransportResponse


def _card(asin, rank):
    return (f'<div id="gridItemRoot"><span class="a-badge-text">#{rank}</span>'
            f'<a href="/dp/{asin}">Item</a></div>')


def test_ranking_v2_never_uses_observed_server_count_as_expected_count():
    html = "".join(_card(f"B{index:09d}", index) for index in range(1, 4))
    result = parse_ranking_snapshot_v2(html, "https://www.amazon.es/gp/bestsellers/test", "now")
    audit = result["audit"]
    assert audit["observed_count"] == 3
    assert audit["expected_count"] is None
    assert audit["expected_count_source"] == "UNKNOWN"
    assert audit["page_complete"] is False
    assert "EXPECTED_COUNT_UNKNOWN" in audit["completion_reason"]


def test_acp_raw_response_is_saved_and_replayable_without_credentials(tmp_path):
    body = _card("B000000002", 2)

    class Transport:
        def fetch_ajax(self, url, **kwargs):
            return TransportResponse(200, url, body, headers={"content-type": "text/html"},
                                     access_state="NORMAL", request_method="POST",
                                     request_url=url, request_headers={"cookie": "secret", **kwargs["headers"]},
                                     request_payload=kwargs["payload"])

    evidence_dir = tmp_path / "acp"
    hydrate = make_acp_hydrator(Transport(), evidence_dir=evidence_dir)
    records = hydrate({"path": "/acp", "source_url": "https://www.amazon.es/test",
                       "entries": [{"id": "B000000002"}]}, 1, 1)
    assert records[0]["asin"] == "B000000002"
    evidence = next(evidence_dir.glob("acp_response_evidence_*.json"))
    saved = json.loads(evidence.read_text(encoding="utf-8"))
    assert saved["response"]["body"] == body
    assert saved["response"]["status_code"] == 200
    assert "cookie" not in {key.casefold() for key in saved["request"]["headers"]}
    assert replay_acp_response_evidence(evidence, source_url="https://www.amazon.es/test")[0]["asin"] == "B000000002"


def test_authority_gate_requires_ranking_and_identity_slot_closure():
    allowed = evaluate_authority(ranking_complete=True, slots_complete=True,
                                 identity_ready=True, identity_complete=True)
    assert allowed["authority_status"] == "AUTHORITATIVE"
    blocked = evaluate_authority(ranking_complete=True, slots_complete=True,
                                 identity_ready=True, identity_complete=True,
                                 no_rank_gap=False)
    assert blocked["authority_status"] == "BLOCKED"
    assert "no_rank_gap" in blocked["authority_block_reasons"]


def test_identity_product_and_ranking_slot_completeness_are_separate():
    rows = [
        {"asin": "B000000001", "product_url": "https://www.amazon.es/dp/B000000001",
         "identity_status": "CONFIRMED", "product_url_source": "RAW_HREF_CONFIRMED"},
    ]
    raw = [{"asin": "B000000001", "rank": 1, "page_instance_id": "page-1"},
           {"asin": "B000000001", "rank": 1, "page_instance_id": "page-2"}]
    audit = audit_identity_records(rows, raw, expected_count=1)
    assert audit["product_identity_complete"] is True
    assert audit["ranking_slot_complete"] is True
    conflict = audit_identity_records(
        rows + [{"asin": "B000000002", "product_url": "https://www.amazon.es/dp/B000000002",
                 "identity_status": "CONFIRMED", "product_url_source": "RAW_HREF_CONFIRMED"}],
        raw + [{"asin": "B000000002", "rank": 1, "page_instance_id": "page-1"}],
        expected_count=2)
    assert conflict["product_identity_complete"] is True
    assert conflict["ranking_slot_complete"] is False
    assert conflict["ranking_slot_conflict_count"] == 1


def test_same_asin_in_two_page_contexts_is_not_two_products(tmp_path):
    for name in ("page_one.html", "page_two.html"):
        (tmp_path / name).write_text(
            '<div id="gridItemRoot" data-asin="B000000001">'
            '<span class="zg-bdg-text">#1</span>'
            '<a href="/dp/B000000001">Item</a></div>', encoding="utf-8")
    (tmp_path / "page_statuses.json").write_text(json.dumps([
        {"html_file": "page_one.html", "source_url": "https://www.amazon.es/a",
         "page_number": 1, "expected_count": 1},
        {"html_file": "page_two.html", "source_url": "https://www.amazon.es/b",
         "page_number": 1, "expected_count": 1},
    ]), encoding="utf-8")
    audit = extract_identity_from_evidence(tmp_path)["audit"]
    assert audit["unique_asin_count"] == 1
    assert audit["expected_slot_count"] == 2
    assert audit["expected_count"] is None
    assert audit["product_identity_complete"] is True
    assert audit["ranking_slot_complete"] is True


def test_page_two_keeps_source_and_actual_page_url():
    base = "https://www.amazon.es/gp/bestsellers/tools"
    page = base + "?pg=2"
    row = parse_bestsellers_page(_card("B000000001", 31), page, "now",
                                 ranking_source_url=base, ranking_page_url=page)[0]
    assert row["ranking_source_url"] == base
    assert row["ranking_page_url"] == page
    assert row["ranking_page_number"] == 2


def test_detail_planner_requires_v2_contract_upgrade_from_saved_html(tmp_path):
    asin = "B000000001"
    ranking = {"snapshot_status": "AUTHORITATIVE", "snapshot_id": "s",
               "records": [{"snapshot_id": "s", "ranking_asin": asin,
                            "ranking_rank": 1, "ranking_link_identity_status": "MATCH",
                            "ranking_product_url_normalized": "https://www.amazon.es/dp/" + asin}]}
    cache = [{"asin": asin, "title_es_raw": "Old", "detail_schema_version": 2,
              "detail_parser_version": "collection.detail_v1",
              "cache_classification": "VALID_PRODUCT_PAGE"}]
    (tmp_path / (asin + ".html")).write_text("<html></html>", encoding="utf-8")
    plan = build_detail_plan(ranking, cache, saved_html=tmp_path,
                             target_parser_version="v2")
    assert plan["records"][0]["detail_action"] == "REPARSE_SAVED_HTML"
    assert plan["records"][0]["target_detail_parser_version"] == "collection.detail_v2"


def test_category_retry_wait_is_future_and_not_retried_in_same_run(tmp_path):
    state = CategoryCrawlerState(tmp_path / "state.json")
    calls = []

    def fail(_row):
        calls.append(1)
        raise RuntimeError("temporary")

    crawler = CategoryCrawler(state, fail, max_attempts=3, retry_delay_seconds=60)
    crawler.seed(marketplace="ES", category_id="root", category_name="Root",
                 canonical_url="https://www.amazon.es/root")
    crawler.run()
    row = next(iter(state.graph.placements.values()))
    assert calls == [1]
    assert row.status is PlacementStatus.RETRY_WAIT
    assert datetime.fromisoformat(row.next_retry_at) > datetime.now(timezone.utc)


def test_search_category_path_survives_category_evidence_serialization():
    evidence = category_evidence_from_detail({"search_category_path": ["Hogar", "Búsqueda"]})
    assert evidence.to_dict()["search_category_path"] == ["Hogar", "Búsqueda"]


def test_identity_snapshot_ids_differ_within_same_second():
    now = datetime(2026, 10, 4, 0, 0, 0, tzinfo=timezone.utc)
    assert _snapshot_id(now) != _snapshot_id(now)
