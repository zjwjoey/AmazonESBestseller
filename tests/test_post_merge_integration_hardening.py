import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from amazon_es_bestseller.categories.crawler import CategoryCrawler, CategoryCrawlerState
from amazon_es_bestseller.categories.models import PlacementStatus
from amazon_es_bestseller.categories.provenance import category_evidence_from_detail
from amazon_es_bestseller.collection.ranking import parse_bestsellers_page
from amazon_es_bestseller.collection.ranking_v2 import (
    build_ranking_snapshot_v2, make_acp_hydrator, parse_ranking_snapshot_v2,
    replay_acp_response_evidence, replay_ranking_snapshot_v2,
)
from amazon_es_bestseller.monitoring import snapshot as snapshot_module
from amazon_es_bestseller.monitoring.detail_planner import build_detail_plan
from amazon_es_bestseller.monitoring.ranking_identity.completeness import (
    audit_identity_records, evaluate_authority,
)
from amazon_es_bestseller.monitoring.ranking_identity.snapshot import _snapshot_id
from amazon_es_bestseller.monitoring.ranking_identity.extract import extract_identity_from_evidence
from amazon_es_bestseller.transport.base import TransportResponse, raw_response_evidence
from amazon_es_bestseller.monitoring.ranking_identity.evidence import save_evidence_snapshot


def _card(asin, rank):
    return (f'<div id="gridItemRoot"><span class="a-badge-text">#{rank}</span>'
            f'<a href="/dp/{asin}">Item</a></div>')


def test_ranking_v2_never_uses_observed_server_count_as_expected_count():
    html = "".join(_card(f"B{index:09d}", index) for index in range(1, 4))
    result = parse_ranking_snapshot_v2(html, "https://www.amazon.es/gp/bestsellers/test", "now")
    audit = result["audit"]
    assert audit["observed_count"] == 3
    assert audit["access_state"] == "NORMAL"
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


def test_persisted_snapshot_replays_same_server_plus_acp_records(tmp_path):
    source = "https://www.amazon.es/gp/bestsellers/test"
    server_html = "".join(_card(f"B{index:09d}", index) for index in range(1, 31))
    acp_html = "".join(_card(f"B{index:09d}", index) for index in range(31, 51))
    rows = [{"asin": f"B{index:09d}", "bestseller_rank": index,
             "ranking_source_url": source, "ranking_page_url": source,
             "ranking_page_number": 1} for index in range(1, 51)]
    result = build_ranking_snapshot_v2(
        {"records": rows, "audit": {"page_authoritative": True,
                                      "expected_count": 50,
                                      "expected_count_source": "ACP_RECS_LIST",
                                      "page_complete": True,
                                      "pages": [{"source_url": source,
                                                 "expected_count": 50,
                                                 "expected_count_source": "ACP_RECS_LIST",
                                                 "page_authoritative": True}]}},
        tmp_path, planned_sources=[{"source_url": source, "page_number": 1}],
        source_statuses=[{"source_url": source, "page_number": 1,
                          "status": "NORMAL", "parse_status": "PARSE_OK",
                          "parsed_record_count": 50}],
        snapshot_id="snapshot_replay_test", started_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
        html_files={"ranking_000.html": server_html}, offline_frozen=False)
    evidence_root = result["path"] / "evidence" / "acp"
    save_evidence_snapshot(
        evidence_root,
        acp_response_evidence=raw_response_evidence(
            TransportResponse(200, source + "/acp/nextPage", acp_html,
                              access_state="NORMAL"),
            request_method="POST", request_url=source + "/acp/nextPage"),
        acp_response_evidence_name="acp_response_evidence_000.json")
    replay = replay_ranking_snapshot_v2(result["path"])
    assert replay["record_count"] == 50
    assert {(row["asin"], row["bestseller_rank"]) for row in replay["records"]} == {
        (f"B{index:09d}", index) for index in range(1, 51)}


def test_final_authority_respects_offline_frozen_base_gate(tmp_path):
    source = "https://www.amazon.es/gp/bestsellers/test"
    status = {"source_url": source, "page_number": 1,
              "access_state": "NORMAL", "parse_status": "PARSE_OK",
              "parsed_record_count": 1}
    result = snapshot_module.build_ranking_snapshot(
        [{"asin": "B000000001", "bestseller_rank": 1,
          "ranking_source_url": source, "ranking_page_number": 1,
          "ranking_product_url_raw": source + "/dp/B000000001"}],
        tmp_path,
        planned_sources=[status], source_statuses=[status],
        ranking_audit={"page_authoritative": True, "rank_gap_count": 0,
                       "rank_duplicate_count": 0},
        identity_audit={"ranking_slot_complete": True, "identity_ready": True,
                        "product_identity_complete": True,
                        "identity_conflict_count": 0,
                        "ranking_slot_conflict_count": 0},
        offline_frozen=True)
    assert result["manifest"]["snapshot_status"] == "INCOMPLETE"
    assert result["manifest"]["latest_authoritative"] is False
    assert result["manifest"]["final_authoritative"] is False
    assert result["manifest"]["authority_status"] == "BLOCKED"
    assert result["manifest"]["authority_gates"]["base_snapshot_complete"] is False
    assert "BASE_SNAPSHOT_GATE" in result["manifest"]["authority_block_reasons"]
    assert not (tmp_path / "latest_authoritative_snapshot.json").exists()


def _run_fake_live_v2(tmp_path, monkeypatch, *, acp_html, include_client_recs=True,
                      conflict=False):
    source = "https://www.amazon.es/gp/bestsellers/test"
    page_url = source
    entries = ",".join('{"id":"B%09d"}' % index for index in range(1, 51))
    metadata = ("<div data-client-recs-list='[%s]' " % entries
                if include_client_recs else "<div")
    server_html = (metadata + ' data-acp-path="/acp/" data-acp-params="token=x"></div>') \
        + "".join(_card(f"B{index:09d}", index) for index in range(1, 31))

    def fake_collect(_urls, _session, _out_dir, *, run_dir, **_kwargs):
        root = Path(run_dir)
        (root / "html").mkdir(parents=True)
        (root / "pages").mkdir()
        (root / "html" / "ranking_000.html").write_text(server_html, encoding="utf-8")
        status = {"source_url": source, "page_url": page_url, "page_number": 1,
                  "http_status": 200, "access_state": "NORMAL",
                  "parse_status": "PARSE_OK", "parsed_record_count": 30,
                  "expected_count": 50,
                  "expected_count_source": "REVIEWED_SOURCE_MANIFEST"}
        (root / "page_statuses.json").write_text(json.dumps([status]), encoding="utf-8")
        (root / "pages" / "page_000.json").write_text(
            json.dumps({"status": status, "records": []}), encoding="utf-8")
        return []

    class FakeTransport:
        def fetch_ajax(self, url, **kwargs):
            return TransportResponse(200, url, acp_html, access_state="NORMAL",
                                     request_method="POST", request_url=url,
                                     request_headers=kwargs.get("headers") or {},
                                     request_payload=kwargs.get("payload"))

    monkeypatch.setattr(snapshot_module, "collect_rankings", fake_collect)
    result = snapshot_module.collect_ranking_snapshot(
        [source], object(), tmp_path, parser_version="v2", transport=FakeTransport())
    if conflict:
        result["manifest"]["test_conflict"] = True
    return result


def test_live_v2_recomputes_identity_after_acp_before_promotion(tmp_path, monkeypatch):
    acp_html = "".join(_card(f"B{index:09d}", index) for index in range(31, 51))
    result = _run_fake_live_v2(tmp_path, monkeypatch, acp_html=acp_html)
    manifest = result["manifest"]
    assert manifest["record_count"] == 50
    assert manifest["ranking_complete"] is True
    assert manifest["ranking_identity_ready"] is True
    assert manifest["ranking_identity_complete"] is True
    assert manifest["ranking_slot_complete"] is True
    assert manifest["identity_conflict_count"] == 0
    assert manifest["final_authoritative"] is True
    assert manifest["authority_status"] == "AUTHORITATIVE"
    assert manifest["snapshot_status"] == "AUTHORITATIVE"
    assert manifest["ranking_v2_audit"]["access_state"] == "NORMAL"
    assert (tmp_path / "latest_authoritative_snapshot.json").exists()
    assert manifest["acp_evidence_files"]


def test_live_v2_missing_acp_identity_blocks_final_promotion(tmp_path, monkeypatch):
    result = _run_fake_live_v2(tmp_path, monkeypatch, acp_html="",
                               include_client_recs=False)
    manifest = result["manifest"]
    assert manifest["record_count"] == 30
    assert manifest["ranking_complete"] is False
    assert manifest["ranking_identity_complete"] is False
    assert manifest["final_authoritative"] is False
    assert manifest["snapshot_status"] == "INCOMPLETE"
    assert not (tmp_path / "latest_authoritative_snapshot.json").exists()


def test_live_v2_identity_conflict_blocks_even_when_ranking_is_complete(tmp_path, monkeypatch):
    conflict_card = ('<div id="gridItemRoot" data-asin="B999999999">'
                     '<span class="a-badge-text">#31</span>'
                     '<a href="/dp/B000000031">Conflict</a></div>')
    acp_html = conflict_card + "".join(
        _card(f"B{index:09d}", index) for index in range(32, 51))
    result = _run_fake_live_v2(tmp_path, monkeypatch, acp_html=acp_html)
    manifest = result["manifest"]
    assert manifest["ranking_complete"] is True
    assert manifest["identity_conflict_count"] > 0
    assert manifest["final_authoritative"] is False
    assert manifest["snapshot_status"] == "INCOMPLETE"
    assert not (tmp_path / "latest_authoritative_snapshot.json").exists()


def test_live_v2_complete_ranking_with_incomplete_final_identity_blocks(tmp_path, monkeypatch):
    from amazon_es_bestseller.monitoring.ranking_identity import extract as identity_extract

    def stale_identity(_evidence_dir):
        return {"records": [], "evidence_files": [], "audit": {
            "identity_ready": True,
            "identity_complete": False,
            "product_identity_complete": False,
            "ranking_slot_complete": False,
            "identity_conflict_count": 0,
            "ranking_slot_conflict_count": 0,
        }}

    monkeypatch.setattr(identity_extract, "extract_identity_from_evidence", stale_identity)
    acp_html = "".join(_card(f"B{index:09d}", index) for index in range(31, 51))
    result = _run_fake_live_v2(tmp_path, monkeypatch, acp_html=acp_html)
    manifest = result["manifest"]
    assert manifest["ranking_complete"] is True
    assert manifest["ranking_identity_complete"] is False
    assert manifest["final_authoritative"] is False
    assert manifest["snapshot_status"] == "INCOMPLETE"
    assert not (tmp_path / "latest_authoritative_snapshot.json").exists()


def test_live_v2_does_not_publish_pointer_before_acp_evidence_copy(tmp_path, monkeypatch):
    def fail_copy(*_args, **_kwargs):
        raise OSError("copy failed")

    monkeypatch.setattr(snapshot_module.shutil, "copytree", fail_copy)
    acp_html = "".join(_card(f"B{index:09d}", index) for index in range(31, 51))
    with pytest.raises(OSError, match="copy failed"):
        _run_fake_live_v2(tmp_path, monkeypatch, acp_html=acp_html)
    assert not (tmp_path / "latest_authoritative_snapshot.json").exists()


def test_two_page_live_v2_replay_equality_uses_saved_page_context(tmp_path, monkeypatch):
    source = "https://www.amazon.es/gp/bestsellers/test"
    page2 = source + "?pg=2"

    def page_html(first_rank):
        entries = ",".join('{"id":"B%09d"}' % index
                             for index in range(first_rank, first_rank + 50))
        cards = "".join(_card(f"B{index:09d}", index)
                         for index in range(first_rank, first_rank + 30))
        return ("<div data-client-recs-list='[%s]' data-acp-path='/acp/' "
                "data-acp-params='token=x'></div>" % entries) + cards

    def fake_collect(_urls, _session, _out_dir, *, run_dir, **_kwargs):
        root = Path(run_dir)
        (root / "html").mkdir(parents=True)
        (root / "pages").mkdir()
        (root / "html" / "ranking_000.html").write_text(page_html(1), encoding="utf-8")
        (root / "html" / "ranking_001.html").write_text(page_html(51), encoding="utf-8")
        statuses = [
            {"source_url": source, "page_url": source, "page_number": 1,
             "http_status": 200, "access_state": "NORMAL", "parse_status": "PARSE_OK",
             "parsed_record_count": 30, "expected_count": 50,
             "expected_count_source": "REVIEWED_SOURCE_MANIFEST"},
            {"source_url": source, "page_url": page2, "page_number": 2,
             "http_status": 200, "access_state": "NORMAL", "parse_status": "PARSE_OK",
             "parsed_record_count": 30, "expected_count": 50,
             "expected_count_source": "REVIEWED_SOURCE_MANIFEST"},
        ]
        (root / "page_statuses.json").write_text(json.dumps(statuses), encoding="utf-8")
        for index, status in enumerate(statuses):
            (root / "pages" / ("page_%03d.json" % index)).write_text(
                json.dumps({"status": status, "records": []}), encoding="utf-8")
        return []

    class FakeTransport:
        def fetch_ajax(self, url, **kwargs):
            first_rank = 81 if "pg=2" in str(kwargs.get("referer") or "") else 31
            body = "".join(_card(f"B{index:09d}", index)
                            for index in range(first_rank, first_rank + 20))
            return TransportResponse(200, url, body, access_state="NORMAL",
                                     request_method="POST", request_url=url,
                                     request_headers=kwargs.get("headers") or {},
                                     request_payload=kwargs.get("payload"))

    monkeypatch.setattr(snapshot_module, "collect_rankings", fake_collect)
    result = snapshot_module.collect_ranking_snapshot(
        [source], object(), tmp_path, pages_per_url=2, parser_version="v2",
        transport=FakeTransport())
    replay = replay_ranking_snapshot_v2(result["path"])
    online = {(row["asin"], row["bestseller_rank"], row["ranking_source_url"],
               row["ranking_page_url"], row["ranking_page_number"])
              for row in result["records"]}
    offline = {(row["asin"], row["bestseller_rank"], row["ranking_source_url"],
                row["ranking_page_url"], row["ranking_page_number"])
               for row in replay["records"]}
    assert result["manifest"]["snapshot_status"] == "AUTHORITATIVE"
    assert result["manifest"]["ranking_v2_audit"]["access_state"] == "NORMAL"
    assert replay["record_count"] == 100
    assert online == offline
    assert all(row["ranking_source_url"] == source for row in replay["records"])
    assert {row["ranking_page_number"] for row in replay["records"]} == {1, 2}


def test_two_page_acp_replay_is_bound_by_page_instance_and_preserves_provenance(tmp_path):
    base = "https://www.amazon.es/gp/bestsellers/test"
    page1 = base
    page2 = base + "?pg=2"
    page1_id = "page:1|url:" + base
    page2_id = "page:2|url:" + base
    rows = []
    for index in range(1, 101):
        actual = page1 if index <= 50 else page2
        rows.append({"asin": f"B{index:09d}", "bestseller_rank": index,
                     "ranking_source_url": base, "ranking_page_url": actual,
                     "ranking_page_number": 1 if index <= 50 else 2})
    pages = [
        {"page_instance_id": page1_id, "ranking_source_url": base,
         "ranking_page_url": page1, "ranking_page_number": 1,
         "expected_count": 50, "expected_count_source": "REVIEWED_SOURCE_MANIFEST",
         "page_authoritative": True, "page_complete": True},
        {"page_instance_id": page2_id, "ranking_source_url": base,
         "ranking_page_url": page2, "ranking_page_number": 2,
         "expected_count": 50, "expected_count_source": "REVIEWED_SOURCE_MANIFEST",
         "page_authoritative": True, "page_complete": True},
    ]
    result = build_ranking_snapshot_v2(
        {"records": rows, "audit": {"page_authoritative": True, "pages": pages}},
        tmp_path,
        planned_sources=[{"source_url": base, "page_number": 1},
                         {"source_url": base, "page_number": 2}],
        source_statuses=[{"source_url": base, "page_url": page1, "page_number": 1,
                          "status": "NORMAL", "parse_status": "PARSE_OK",
                          "parsed_record_count": 50},
                         {"source_url": base, "page_url": page2, "page_number": 2,
                          "status": "NORMAL", "parse_status": "PARSE_OK",
                          "parsed_record_count": 50}],
        snapshot_id="snapshot_two_page_replay", started_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
        html_files={"ranking_000.html": "".join(_card(f"B{index:09d}", index)
                                                    for index in range(1, 31)),
                    "ranking_001.html": "".join(_card(f"B{index:09d}", index)
                                                    for index in range(51, 81))},
        offline_frozen=False)
    acp_root = result["path"] / "evidence" / "acp"
    for name, context, first, last, actual in (
            ("page1_acp_000.json", page1_id, 31, 50, page1),
            ("page2_acp_000.json", page2_id, 81, 100, page2)):
        save_evidence_snapshot(
            acp_root,
            acp_response_evidence=raw_response_evidence(
                TransportResponse(200, actual + "/acp/nextPage",
                                  "".join(_card(f"B{index:09d}", index)
                                          for index in range(first, last + 1)),
                                  access_state="NORMAL"),
                request_method="POST", request_url=actual + "/acp/nextPage"),
            acp_response_evidence_name=name)
        payload_path = acp_root / name
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        payload["context"] = {"page_instance_id": context,
                               "ranking_source_url": base,
                               "ranking_page_url": actual,
                               "ranking_page_number": 1 if actual == page1 else 2,
                               "offset": 30, "count": 20, "expected_count": 50}
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
    replay = replay_ranking_snapshot_v2(result["path"])
    online = {(row["asin"], row["bestseller_rank"], row["ranking_source_url"],
               row["ranking_page_url"], row["ranking_page_number"]) for row in rows}
    offline = {(row["asin"], row["bestseller_rank"], row["ranking_source_url"],
                row["ranking_page_url"], row["ranking_page_number"])
               for row in replay["records"]}
    assert replay["record_count"] == 100
    assert online == offline


def test_ambiguous_legacy_multi_page_acp_attribution_fails_closed(tmp_path):
    for index, url in enumerate(("https://www.amazon.es/a", "https://www.amazon.es/b")):
        (tmp_path / f"ranking_{index:03d}.html").write_text(
            _card(f"B00000000{index + 1}", 1), encoding="utf-8")
    (tmp_path / "page_statuses.json").write_text(json.dumps([
        {"html_file": "ranking_000.html", "source_url": "https://www.amazon.es/a",
         "page_number": 1, "expected_count": 1},
        {"html_file": "ranking_001.html", "source_url": "https://www.amazon.es/b",
         "page_number": 1, "expected_count": 1},
    ]), encoding="utf-8")
    save_evidence_snapshot(
        tmp_path,
        acp_response_evidence={"response": {"body": _card("B000000003", 2),
                                             "status_code": 200}},
        acp_response_evidence_name="acp_response_evidence_000.json")
    from amazon_es_bestseller.monitoring.ranking_identity.extract import extract_identity_from_evidence
    audit = extract_identity_from_evidence(tmp_path)["audit"]
    assert audit["attribution_unknown_count"] == 1
    assert audit["identity_ready"] is False


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
