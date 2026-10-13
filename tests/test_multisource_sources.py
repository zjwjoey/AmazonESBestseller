"""Offline contracts, adversarial layouts and a genuine saved-page replay."""
from __future__ import annotations

from pathlib import Path
import json

import pytest

from amazon_es_bestseller.multisource.adapters import get_adapter
from amazon_es_bestseller.multisource.contracts import RANK_SEMANTICS, SOURCE_ROOTS
from amazon_es_bestseller.multisource.sources import (SourceRegistry, browse_node_id,
    discover_sources, normalize_source_url, source_id, source_type_from_url)
from amazon_es_bestseller.multisource.storage import atomic_json, sealed

FIXTURES = Path(__file__).parent / "fixtures" / "multisource"
FILES = dict(zip(SOURCE_ROOTS, ("best_sellers.html", "movers_shakers.html", "new_releases.html",
                              "most_wished_for.html", "most_gifted.html")))
INSTANT = "2026-10-12T10:00:00Z"


def source(kind="BEST_SELLERS"):
    url = SOURCE_ROOTS[kind] + "/kitchen/12345"
    return {"source_id": source_id(kind, url, "厨房用品"), "source_type": kind,
        "root_url": SOURCE_ROOTS[kind], "actual_source_url": url,
        "research_category": "厨房用品", "amazon_category": "Cocina",
        "browse_node_id": "12345", "mapping_evidence": {"kind": "REVIEWED_NODE_MAPPING",
            "research_category": "厨房用品", "evidence": "node evidence"}}


def parse(kind="BEST_SELLERS", html=None, **kwargs):
    row = source(kind)
    path = FIXTURES / FILES[kind]
    if html is None:
        html = path.read_bytes().decode("utf-8")
    return get_adapter(kind).parse(html, source=row, run_id="fixture-run", snapshot_id="fixture-snapshot",
        page_url=row["actual_source_url"], page_number=1, observed_at=INSTANT,
        evidence_path=str(path), **kwargs)


@pytest.mark.parametrize("kind", list(SOURCE_ROOTS))
def test_five_adapters_recognize_own_fixture_and_separate_rank_semantics(kind):
    result = parse(kind)
    assert result["page"]["recognized_page_type"] is True
    assert result["page"]["source_type_verified"] is True
    assert result["page"]["status"] == "COMPLETE"
    assert result["observations"]
    assert {row["rank_semantics"] for row in result["observations"]} == {RANK_SEMANTICS[kind]}
    assert all(row["product_id"] is None and row["identity_status"] == "UNCONFIRMED"
               for row in result["observations"])
    assert "bestseller_rank" not in result["observations"][0]
    assert result["page"]["origin"] == "OFFLINE_FIXTURE"


@pytest.mark.parametrize("kind", list(SOURCE_ROOTS))
def test_url_replacement_does_not_fake_adapter_recognition(kind):
    html = "<h1>Unrelated product detail</h1><div id='gridItemRoot' data-asin='B000000001'></div>"
    result = parse(kind, html)
    assert result["page"]["status"] == "FAILED"
    assert result["observations"] == []


@pytest.mark.parametrize("kind", list(SOURCE_ROOTS))
def test_discovery_is_real_same_type_link_and_explicit_mapping(kind):
    row = source(kind)
    html = (FIXTURES / FILES[kind]).read_bytes().decode("utf-8")
    html += "<nav><a href='https://evil.example/gp/bestsellers/111'>Fake</a>"
    html += "<a href='/gp/not-a-list/222'>Invented</a></nav>"
    result = discover_sources(html, kind, row["actual_source_url"],
        research_mapping={"12345": {"research_category": "厨房用品", "evidence": "reviewed node"}},
        evidence_path="fixture.html", observed_at=INSTANT)
    assert len(result) == 1
    assert result[0]["actual_source_url"] == row["actual_source_url"]
    assert result[0]["source_status"] == "UNVERIFIED"
    assert result[0]["research_category"] == "厨房用品"
    assert result[0]["discovery_evidence"][0]["raw_href"] in html
    assert result[0]["mapping_evidence"]["evidence"]


def test_unmapped_navigation_not_invented_from_title():
    result = parse()["discovered_sources"]
    assert len(result) == 1 and result[0]["research_category"] is None
    assert result[0]["amazon_category"] == "Cocina"
    with pytest.raises(ValueError, match="EXPLICIT_RESEARCH_MAPPING"):
        discover_sources("<nav><a href='/gp/bestsellers/kitchen/12345'>Cocina</a></nav>",
            "BEST_SELLERS", SOURCE_ROOTS["BEST_SELLERS"], research_mapping={"12345": "厨房用品"})


def test_movers_separates_list_position_sales_rank_and_percent():
    row = parse("MOVERS_SHAKERS")["observations"][0]
    assert row["rank_value"] == 4
    assert row["current_sales_rank"] == 25
    assert row["previous_sales_rank"] == 425
    assert row["sales_rank_improvement_percent"] == 1600
    assert "NOT_SALES_GROWTH" in row["sales_rank_change_semantics"]


def test_noncontinuous_wish_rank_does_not_apply_best_seller_gap_rule():
    result = parse("MOST_WISHED_FOR")
    assert result["observations"][0]["rank_value"] == 9
    assert result["page"]["contiguous_ranks"] is False
    assert "RANK_GAP" not in result["page"]["flags"]


def test_unknown_expected_count_stays_unverified():
    html = (FIXTURES / "best_sellers.html").read_bytes().decode("utf-8").replace('data-expected-count="2"', "")
    result = parse(html=html)
    assert result["page"]["expected_count"] is None
    assert result["page"]["status"] == "UNVERIFIED"


def test_structural_change_cannot_approve_empty_page():
    html = "<h1>Los más vendidos</h1><div data-expected-count='10' class='unknown-layout'>Changed markup</div>"
    result = parse(html=html)
    assert result["page"]["status"] == "FAILED"
    assert "PAGE_CONTENT_MISSING" in result["page"]["flags"]


def test_card_link_conflict_kept_as_observation_without_confirmation():
    html = (FIXTURES / "best_sellers.html").read_bytes().decode("utf-8").replace('/dp/B000000001?', '/dp/B000000099?')
    result = parse(html=html)
    row = result["observations"][0]
    assert row["asin_from_card"] == "B000000001" and row["asin_from_raw_url"] == "B000000099"
    assert row["identity_status"] == "CONFLICT" and row["product_id"] is None
    assert result["page"]["status"] == "PARTIAL"


def test_missing_identity_keeps_actual_ranked_slot():
    html = "<h1>Los más vendidos</h1><div id='gridItemRoot'><span class='zg-bdg-text'>#1</span><span data-product-title>Sin identidad</span></div>"
    result = parse(html=html)
    assert len(result["observations"]) == 1
    assert result["observations"][0]["asin_observed"] is None
    assert result["observations"][0]["rank_value"] == 1
    assert "PRODUCT_LINK_MISSING" in result["page"]["flags"]


def test_product_url_normalization_preserves_identity_params_and_raw_link():
    row = parse()["observations"][0]
    assert "tag=test" in row["raw_product_url"]
    assert "tag=test" not in row["normalized_product_url"]
    assert "th=1" in row["normalized_product_url"] and "psc=1" in row["normalized_product_url"]
    assert row["canonical_asin_observed"] is None


@pytest.mark.parametrize("status", [403, 429])
def test_http_restrictions_do_not_parse_normal_cards(status):
    result = parse(http_status=status)
    assert result["page"]["status"] == "BLOCKED"
    assert not result["observations"]


def test_deep_challenge_marker_stops_even_with_http_200():
    result = parse(html="<h1>Los más vendidos</h1>" + " " * 10000 + "Robot Check")
    assert result["page"]["status"] == "BLOCKED"


def acp_page():
    recs = json.dumps([{"id": "B000000001"}, {"id": "B000000002"}], separators=(",", ":"))
    return ("<h1>Los más vendidos</h1><nav id='zg_browseRoot'><a href='/gp/bestsellers/kitchen/12345'>Cocina</a></nav>"
        "<div data-acp-path='/acp/p13n/real' data-acp-params='token' data-client-recs-list='" + recs + "'></div>"
        "<div id='gridItemRoot' data-asin='B000000001'><span class='zg-bdg-text'>#1</span><a href='/dp/B000000001'>Taza</a></div>")


def test_acp_metadata_does_not_count_as_loaded_cards():
    result = parse(html=acp_page())
    assert result["page"]["expected_count"] == 2
    assert len(result["observations"]) == 1
    assert result["page"]["status"] == "PARTIAL"
    assert result["page"]["acp_complete"] is False
    descriptor = result["page"]["acp_pending"][0]
    assert descriptor["url"] == "https://www.amazon.es/acp/p13n/real/nextPage"
    assert descriptor["method"] == "POST" and descriptor["count"] == 1
    assert descriptor["headers"]["x-amz-acp-params"] == "token"


def test_saved_acp_response_finishes_missing_actual_slot():
    body = "<div id='gridItemRoot' data-asin='B000000002'><span class='zg-bdg-text'>#2</span><a href='/dp/B000000002'>Cuchara</a></div>"
    result = parse(html=acp_page(), acp_responses=[{"response": {"body": body, "status_code": 200},
        "evidence_path": "saved-acp.html"}])
    assert len(result["observations"]) == 2
    assert result["page"]["acp_complete"] is True
    assert result["page"]["status"] == "COMPLETE"
    assert result["observations"][1]["evidence_path"] == "saved-acp.html"


def test_acp_duplicate_representation_not_duplicate_count_but_conflict_is_preserved():
    body = "<div id='gridItemRoot' data-asin='B000000099'><span class='zg-bdg-text'>#1</span><a href='/dp/B000000099'>Conflict</a></div>"
    result = parse(html=acp_page(), acp_responses=[body])
    assert len(result["observations"]) == 1
    assert result["observations"][0]["identity_status"] == "CONFLICT"
    assert result["observations"][0]["identity_evidence"][-1]["kind"] == "ACP_CONFLICTING_CARD"


def test_pagination_does_not_cross_list_or_category():
    html = (FIXTURES / "best_sellers.html").read_bytes().decode("utf-8")
    html = html.replace("</body>", "<a rel='next' href='/gp/most-gifted/kitchen/12345?pg=2'>Next</a>"
        "<a rel='next' href='/gp/bestsellers/kitchen/99999?pg=2'>Next</a></body>")
    result = parse(html=html)
    assert len(result["pagination"]) == 1
    assert "PAGINATION_SOURCE_TYPE_MISMATCH" in result["page"]["flags"]
    assert "PAGINATION_CATEGORY_MISMATCH" in result["page"]["flags"]


def test_source_url_validation_and_tracking_only_normalization():
    assert source_type_from_url("https://www.amazon.es/gp/new-releases/kitchen/12345") == "NEW_RELEASES"
    assert source_type_from_url("https://fake.amazon.es/gp/bestsellers") is None
    assert source_type_from_url("https://www.amazon.es/gp/bestsellers-wrong") is None
    assert browse_node_id("https://www.amazon.es/gp/bestsellers/kitchen?node=12345") == "12345"
    assert normalize_source_url("https://www.amazon.es/dp/B000000001?th=1&psc=1&tag=test&unknown=2") == "https://www.amazon.es/dp/B000000001?th=1&psc=1&unknown=2"


def test_registry_75_combinations_are_truthful_and_persistent(tmp_path):
    registry = SourceRegistry(tmp_path / "registry.json")
    registry.seed_roots()
    coverage = registry.coverage()
    assert coverage["total"] == 75 and coverage["counts"]["UNVERIFIED"] == 75
    assert len(registry.list()) == 5
    registry.upsert(source())
    reopened = SourceRegistry(tmp_path / "registry.json")
    assert reopened.coverage()["counts"]["UNVERIFIED"] == 75
    assert len(reopened.list()) == 6
    assert any(row["research_category"] == "厨房用品" for row in reopened.list())


def test_registry_rejects_unproven_available_and_one_failure_unsupported(tmp_path):
    registry = SourceRegistry(tmp_path / "registry.json")
    with pytest.raises(ValueError, match="LIVE_SOURCE_VERIFICATION"):
        registry.upsert(dict(source(), source_status="AVAILABLE"))
    with pytest.raises(ValueError, match="REVIEWED_UNSUPPORTED"):
        registry.upsert(dict(source(), source_status="UNSUPPORTED"))


def test_fixture_verification_does_not_claim_current_live_availability(tmp_path):
    registry = SourceRegistry(tmp_path / "registry.json")
    row = registry.upsert(source())
    result = parse()
    verified = registry.verify(row["source_id"], result, origin="OFFLINE_FIXTURE",
        observed_at=INSTANT, evidence_path=str(FIXTURES / "best_sellers.html"))
    assert verified["source_status"] == "UNVERIFIED"
    assert verified["last_verified_at"] is None
    assert verified["tested_record_count"] == 2
    assert registry.coverage()["counts"]["AVAILABLE"] == 0


def test_fixture_cannot_be_relabelled_live_by_verify_argument(tmp_path):
    registry = SourceRegistry(tmp_path / "registry.json")
    row = registry.upsert(source())
    with pytest.raises(ValueError, match="ORIGIN_MISMATCH"):
        registry.verify(row["source_id"], parse(), origin="LIVE_CAPTURE",
            observed_at=INSTANT, evidence_path=str(FIXTURES / "best_sellers.html"))


def test_seeding_preserves_registered_status_and_enabled_flag(tmp_path):
    registry = SourceRegistry(tmp_path / "registry.json")
    root = registry.seed_roots()[0]
    registry.set_enabled(root["source_id"], True)
    registry.seed_roots()
    assert next(row for row in registry.list() if row["source_id"] == root["source_id"])["enabled"] is True


def test_registry_source_verification_rejects_corrupted_evidence(tmp_path):
    registry = SourceRegistry(tmp_path / "registry.json")
    row = registry.upsert(source())
    fake = tmp_path / "damaged.html"
    fake.write_text("corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="EVIDENCE_INVALID"):
        registry.verify(row["source_id"], parse(), origin="OFFLINE_FIXTURE",
            observed_at=INSTANT, evidence_path=str(fake))


def test_genuine_archived_amazon_best_seller_fixture_replay_unknown_capture_time():
    path = Path(__file__).parent / "fixtures" / "amazon_es" / "ranking_identity" / "real_amazon_es_page_0000.html"
    html = path.read_bytes().decode("utf-8")
    row = source()
    row.update(actual_source_url="https://www.amazon.es/gp/bestsellers/kitchen", browse_node_id=None)
    row["source_id"] = source_id("BEST_SELLERS", row["actual_source_url"], "厨房用品")
    result = get_adapter("BEST_SELLERS").parse(html, source=row, run_id="historical",
        snapshot_id="archive-unknown-date", page_url=row["actual_source_url"], page_number=1,
        observed_at=INSTANT, evidence_path=str(path), origin="OFFLINE_FIXTURE")
    assert result["page"]["recognized_page_type"] is True
    assert len(result["observations"]) >= 20
    assert result["page"]["status"] in {"PARTIAL", "UNVERIFIED"}
    # Original saved metadata has no verified timezone. INSTANT is only a
    # synthetic fixture clock, never a claimed historical capture timestamp.
    assert result["page"]["origin"] == "OFFLINE_FIXTURE"
    assert all(row["product_id"] is None for row in result["observations"])


@pytest.mark.parametrize("url", ["https://www.amazon.es:invalid/gp/bestsellers",
    "https://[broken/gp/bestsellers", "https://www.amazon.es:70000/gp/bestsellers"])
def test_malformed_source_urls_are_rejected_without_parser_crash(url):
    assert normalize_source_url(url) == ""
    assert source_type_from_url(url) is None


def test_adapter_binds_observations_to_actual_source_category():
    row = source()
    path = FIXTURES / FILES["BEST_SELLERS"]
    result = get_adapter("BEST_SELLERS").parse(path.read_text(encoding="utf-8"), source=row,
        run_id="wrong-source", snapshot_id="wrong-source", page_url=row["actual_source_url"].replace("12345", "99999"),
        page_number=1, observed_at=INSTANT, evidence_path=str(path))
    assert result["page"]["status"] == "FAILED"
    assert "PAGE_SOURCE_CATEGORY_MISMATCH" in result["page"]["flags"]
    assert not result["observations"]


def test_actual_heading_cannot_be_overridden_by_stale_document_title():
    html = (FIXTURES / "best_sellers.html").read_text(encoding="utf-8").replace(
        "<h1>Los más vendidos en Cocina</h1>", "<h1>Los más regalados en Cocina</h1>")
    assert parse(html=html)["page"]["status"] == "FAILED"


def test_external_product_link_does_not_become_amazon_identity():
    html = (FIXTURES / "best_sellers.html").read_text(encoding="utf-8").replace(
        '/dp/B000000001?', 'https://evil.example/dp/B000000001?')
    result = parse(html=html)
    row = result["observations"][0]
    assert row["raw_product_url"].startswith("https://evil.example")
    assert row["normalized_product_url"] is None
    assert row["identity_status"] == "CONFLICT" and row["product_id"] is None
    assert "PRODUCT_URL_NOT_AMAZON_ES" in row["identity_conflict_reason"]


def test_bad_card_asin_is_preserved_as_raw_evidence_and_never_validated():
    html = (FIXTURES / "best_sellers.html").read_text(encoding="utf-8").replace(
        'data-asin="B000000001"', 'data-asin="NOT-AN-ASIN"')
    row = parse(html=html)["observations"][0]
    assert row["asin_from_card_raw"] == "NOT-AN-ASIN"
    assert row["asin_from_card"] is None
    assert row["identity_status"] != "CONFIRMED" and row["product_id"] is None


def test_duplicate_ranks_inside_one_acp_response_remain_anomaly_slots():
    body = ("<div id='gridItemRoot' data-asin='B000000002'><span class='zg-bdg-text'>#2</span><a href='/dp/B000000002'>Two</a></div>"
        "<div id='gridItemRoot' data-asin='B000000003'><span class='zg-bdg-text'>#2</span><a href='/dp/B000000003'>Three</a></div>")
    result = parse(html=acp_page(), acp_responses=[{"body": body, "evidence_path": "acp.html"}])
    assert len(result["observations"]) == 3
    assert "DUPLICATE_RANK" in result["page"]["flags"]
    assert result["page"]["status"] == "PARTIAL"


def test_acp_metadata_and_loaded_card_identity_set_must_agree():
    body = "<div id='gridItemRoot' data-asin='B000000099'><span class='zg-bdg-text'>#2</span><a href='/dp/B000000099'>Wrong</a></div>"
    result = parse(html=acp_page(), acp_responses=[{"body": body, "evidence_path": "acp.html"}])
    assert result["page"]["actual_count"] == 2
    assert "ACP_METADATA_IDENTITY_MISMATCH" in result["page"]["flags"]
    assert result["page"]["status"] == "PARTIAL"


def test_expected_count_proof_is_exact_raw_token_and_integer():
    page = parse()["page"]
    proof = page["expected_count_evidence"]
    assert proof["value"] == page["expected_count"]
    assert proof["raw"] == 'data-expected-count="2"'
    assert proof["evidence_sha256"] == page["evidence_sha256"]


def test_live_verification_rejects_misbinding_flags_even_with_cards(tmp_path):
    registry = SourceRegistry(tmp_path / "registry.json")
    row = registry.upsert(source())
    result = parse(origin="LIVE_CAPTURE")
    result["page"]["flags"] = ["CATEGORY_NAVIGATION_MISMATCH"]
    result["page"]["category_navigation_valid"] = False
    result["page"]["status"] = "PARTIAL"
    receipt = simulated_capture_receipt(tmp_path, result)
    verified = registry.verify(row["source_id"], result, origin="LIVE_CAPTURE",
        observed_at=INSTANT, evidence_path=str(FIXTURES / "best_sellers.html"),
        capture_receipt_path=str(receipt))
    assert verified["source_status"] != "AVAILABLE"


def simulated_capture_receipt(directory, result):
    """A test-only transport mock; this performs no network operation."""
    path = directory / "simulated-receipt.json"
    page = result["page"]
    atomic_json(path, sealed({"origin": "LIVE_CAPTURE", "kind": "PAGE",
        "source_id": page["source_id"], "access_status": page["access_status"],
        "approval_id": "unit-test-mock-only", "page_url": page["page_url"],
        "observed_at": page["observed_at"], "transport_evidence": {"response": {
            "status_code": 200, "body": Path(page["evidence_path"]).read_bytes().decode("utf-8")}}}))
    return path


@pytest.mark.parametrize("kind", list(SOURCE_ROOTS))
def test_observed_amazon_display_locale_alias_is_classified_without_mutating_url(kind):
    original = SOURCE_ROOTS[kind].replace("/gp/", "/-/en/gp/") + "/kitchen/12345"
    assert source_type_from_url(original) == kind
    assert normalize_source_url(original) == original


def test_real_saved_english_locale_best_seller_url_replays():
    path = Path(__file__).parent / "fixtures" / "amazon_es" / "ranking_identity" / "real_amazon_es_page_0000.html"
    row = source()
    row["actual_source_url"] = "https://www.amazon.es/-/en/gp/bestsellers/kitchen"
    row["source_id"] = source_id("BEST_SELLERS", row["actual_source_url"], "厨房用品")
    row["browse_node_id"] = None
    result = get_adapter("BEST_SELLERS").parse(path.read_bytes().decode("utf-8"), source=row,
        run_id="archive-fixture", snapshot_id="archive-fixture", page_url=row["actual_source_url"],
        page_number=1, observed_at=INSTANT, evidence_path=str(path), origin="OFFLINE_FIXTURE")
    assert result["page"]["recognized_page_type"] is True
    assert len(result["observations"]) >= 20


def test_live_marker_without_saved_transport_receipt_never_marks_available(tmp_path):
    registry = SourceRegistry(tmp_path / "registry.json")
    row = registry.upsert(source())
    with pytest.raises(ValueError, match="CAPTURE_RECEIPT_REQUIRED"):
        registry.verify(row["source_id"], parse(origin="LIVE_CAPTURE"), origin="LIVE_CAPTURE",
            observed_at=INSTANT, evidence_path=str(FIXTURES / "best_sellers.html"))
    assert registry.coverage()["counts"]["AVAILABLE"] == 0


def test_simulated_receipt_verification_is_bound_to_body_timestamp_and_source(tmp_path):
    registry = SourceRegistry(tmp_path / "registry.json")
    row = registry.upsert(source())
    result = parse(origin="LIVE_CAPTURE")
    receipt = simulated_capture_receipt(tmp_path, result)
    verified = registry.verify(row["source_id"], result, origin="LIVE_CAPTURE", observed_at=INSTANT,
        evidence_path=str(FIXTURES / "best_sellers.html"), capture_receipt_path=str(receipt))
    assert verified["source_status"] == "AVAILABLE"
    assert verified["verification_evidence"]["capture_receipt_sha256"]
    payload = json.loads(receipt.read_text(encoding="utf-8"))["payload"]
    payload["transport_evidence"]["response"]["body"] = "modified"
    atomic_json(receipt, sealed(payload))
    with pytest.raises(ValueError, match="CAPTURE_RECEIPT_INVALID"):
        registry.upsert(verified)
