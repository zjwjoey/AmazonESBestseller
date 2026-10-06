from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.collection.detail import parse_detail_page
from amazon_es_bestseller.collection.ranking import parse_bestsellers_page
from amazon_es_bestseller.quality.access import audit_access_evidence
from amazon_es_bestseller.quality.audit import run_quality_audit
from amazon_es_bestseller.quality.category_provenance import audit_category_provenance
from amazon_es_bestseller.quality.detail_identity import audit_detail_identity
from amazon_es_bestseller.quality.detail_structure import audit_detail_structure
from amazon_es_bestseller.quality.gate import evaluate_research_readiness
from amazon_es_bestseller.quality.ranking_identity import audit_ranking_identity
from amazon_es_bestseller.quality.ranking_integrity import audit_ranking_integrity


ASIN = "B078C6QR1C"
URL = "https://www.amazon.es/dp/B078C6QR1C"


def ranking(**overrides):
    row = {
        "asin": ASIN,
        "ranking_asin": ASIN,
        "card_asin": ASIN,
        "ranking_link_asin": ASIN,
        "ranking_product_url_raw": URL,
        "ranking_link_identity_status": "MATCH",
        "ranking_source_url": "https://www.amazon.es/Best-Sellers/zgbs/123",
        "ranking_page_number": 1,
        "bestseller_rank": 1,
        "access_state": "NORMAL",
    }
    row.update(overrides)
    return row


def detail(**overrides):
    row = {
        "asin": ASIN,
        "requested_asin": ASIN,
        "parsed_detail_asin": ASIN,
        "identity_status": "IDENTITY_MATCH",
        "access_state": "NORMAL",
        "attributes": [],
        "ordered_detail_evidence": [],
        "feature_bullets_raw": [],
    }
    row.update(overrides)
    return row


def test_ranking_identity_blocks_link_mismatch_and_warns_when_link_missing():
    blocked = audit_ranking_identity([ranking(ranking_link_asin="B000000001")])
    assert blocked.status == "BLOCK"
    assert blocked.issues[0].issue_code == "LINK_ASIN_MISMATCH"

    warned = audit_ranking_identity([ranking(ranking_link_asin="", ranking_product_url_raw="")])
    assert warned.status == "WARN"
    assert warned.issues[0].issue_code == "LIMITED_IDENTITY_EVIDENCE"


def test_ranking_integrity_detects_duplicate_slots_and_gaps():
    rows = [
        ranking(asin="B000000001", ranking_asin="B000000001", bestseller_rank=1),
        ranking(asin="B000000002", ranking_asin="B000000002", bestseller_rank=1),
        ranking(asin="B000000003", ranking_asin="B000000003", bestseller_rank=3),
    ]
    result = audit_ranking_integrity(rows)
    codes = {item.issue_code for item in result.issues}
    assert result.status == "BLOCK"
    assert "DUPLICATE_RANK_SLOT" in codes
    assert "RANK_GAP" in codes


def test_access_gate_normalizes_string_http_status():
    result = audit_access_evidence([ranking(status_code="429")], [])
    assert result.status == "BLOCK"
    assert result.issues[0].issue_code == "ACCESS_BLOCKED"


def test_detail_identity_and_structure_rules():
    assert audit_detail_identity([detail()]).status == "PASS"
    mismatch = audit_detail_identity([detail(parsed_detail_asin="B000000001", identity_status="IDENTITY_MISMATCH")])
    assert mismatch.status == "BLOCK"

    # Repeated labels are valid when they are distinct ordered facts.
    repeated = detail(ordered_detail_evidence=[
        {"section": "technical", "source": "fixture", "label_raw": "Color", "value_raw": "Rojo", "position": 1},
        {"section": "technical", "source": "fixture", "label_raw": "Color", "value_raw": "Azul", "position": 2},
    ])
    assert audit_detail_structure([repeated]).status == "PASS"
    duplicate_position = detail(ordered_detail_evidence=[
        {"source": "fixture", "label_raw": "Color", "value_raw": "Rojo", "position": 1},
        {"source": "fixture", "label_raw": "Size", "value_raw": "M", "position": 1},
    ])
    assert audit_detail_structure([duplicate_position]).status == "BLOCK"


def test_detail_identity_distinguishes_navigation_review_from_request_binding_error():
    navigated = audit_detail_identity([detail(
        identity_status="IDENTITY_MISMATCH",
        detail_status="SUCCESS_WITH_IDENTITY_CHANGE",
        identity_event="NAVIGATION_IDENTITY_CHANGED",
    )])
    assert navigated.status == "REVIEW"
    assert navigated.issues[0].issue_code == "NAVIGATION_IDENTITY_CHANGED"
    binding = audit_detail_identity([detail(
        detail_status="REQUEST_URL_BINDING_MISMATCH",
        identity_event="REQUEST_URL_BINDING_MISMATCH",
    )])
    assert binding.status == "BLOCK"
    assert binding.issues[0].issue_code == "REQUEST_URL_BINDING_MISMATCH"


def test_category_provenance_only_reviews_conflicts():
    result = audit_category_provenance(
        [ranking(ranking_category_path="Hogar > Cocina")],
        [detail(detail_category_trail=["Hogar", "Jardín"])],
    )
    assert result.status == "REVIEW"
    assert result.issues[0].issue_code == "CATEGORY_EVIDENCE_CONFLICT"


def test_gate_allows_warn_but_not_review_or_block():
    assert evaluate_research_readiness({"a": {"check": "a", "status": "WARN", "issues": []}})[
        "final_quality_status"] == "RESEARCH_READY"
    assert evaluate_research_readiness({"a": {"check": "a", "status": "REVIEW", "issues": []}})[
        "final_quality_status"] == "REVIEW_REQUIRED"
    assert evaluate_research_readiness({"a": {"check": "a", "status": "BLOCK", "issues": []}})[
        "final_quality_status"] == "BLOCKED"


def test_offline_replay_reuses_v1_parsers_and_never_mutates_inputs(tmp_path: Path):
    ranking_html = Path("tests/fixtures/html/bestsellers_grid.html").read_text(encoding="utf-8")
    detail_html = Path("tests/fixtures/html/product_lunchbag.html").read_text(encoding="utf-8")
    source_url = "https://www.amazon.es/Best-Sellers/zgbs/123"
    rankings = parse_bestsellers_page(ranking_html, source_url, "")
    details = [parse_detail_page(detail_html, "B075JJRFVV")]
    rankings[1]["access_state"] = "NORMAL"
    details[0]["access_state"] = "NORMAL"
    before = (copy.deepcopy(rankings), copy.deepcopy(details))
    ranking_dir = tmp_path / "ranking"
    detail_dir = tmp_path / "detail"
    ranking_dir.mkdir()
    detail_dir.mkdir()
    (ranking_dir / "ranking_001.html").write_text(ranking_html, encoding="utf-8")
    (detail_dir / "B075JJRFVV.html").write_text(detail_html, encoding="utf-8")
    result = run_quality_audit(
        rankings, details, ranking_html_dirs=[ranking_dir], detail_html_dirs=[detail_dir],
        checks=("offline_replay",),
    )
    assert result["summary"]["network_requests"] == 0
    assert result["checks"]["offline_replay"]["status"] == "PASS"
    assert rankings == before[0]
    assert details == before[1]


def test_quality_audit_scales_to_5000_synthetic_records():
    rows = []
    for index in range(5000):
        asin = "B%09d" % index
        rows.append({
            "asin": asin,
            "ranking_asin": asin,
            "ranking_link_asin": asin,
            "ranking_product_url_raw": "https://www.amazon.es/dp/%s" % asin,
            "ranking_link_identity_status": "MATCH",
        })
    original = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    report = run_quality_audit(rows, checks=("ranking_identity",))
    assert report["summary"]["total_skus"] == 5000
    assert report["checks"]["ranking_identity"]["status"] == "PASS"
    assert json.dumps(rows, ensure_ascii=False, sort_keys=True) == original
