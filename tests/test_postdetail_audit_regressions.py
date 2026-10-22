"""Read-only boundary probes: failures reproduce review findings, not production edits."""
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.collection.ranking import parse_bestsellers_page
from amazon_es_bestseller.qa.field_closure import audit_field_closure
from amazon_es_bestseller.qa.validators import validate_spec, validate_monthly_bought
from amazon_es_bestseller.quality.audit import run_quality_audit
from amazon_es_bestseller.quality.detail_identity import audit_detail_identity
from amazon_es_bestseller.quality.detail_structure import audit_detail_structure
from amazon_es_bestseller.quality.replay import audit_offline_replay
from amazon_es_bestseller.translation.qa import build_qa_report
from amazon_es_bestseller.translation.production import (
    build_production_input, build_production_state, records_for_preclean,
)
from amazon_es_bestseller.translation.preclean import audit_records

ASIN = "B078C6QR1C"
URL = "https://www.amazon.es/dp/" + ASIN


def test_subset_is_not_full_readiness():
    report = run_quality_audit([], [{"asin": ASIN, "attributes": []}],
                               checks=["detail_structure"])
    assert report["final_quality_status"] != "RESEARCH_READY"


def test_wrong_canonical_price_is_detected():
    row = {"asin": ASIN, "current_price_raw": "10,00 EUR", "current_price": 999}
    report = audit_field_closure([row], details=[row])
    result = next(item for item in report["records"] if item["field"] == "current_price")
    assert result["classification"] != "PASS"


def test_correct_separate_translation_is_used():
    row = {"asin": ASIN, "title_es_raw": "Botella de agua"}
    report = audit_field_closure([row], details=[row],
                                 translations={ASIN: {"title_zh": "水瓶"}})
    assert not [item for item in report["records"]
                if item["field"] == "title_zh" and item["severity"] == "P1"]


def test_shallow_category_does_not_prove_deeper_levels(tmp_path):
    (tmp_path / (ASIN + ".html")).write_text(
        '<html><div id="wayfinding-breadcrumbs_feature_div"><a>Hogar y cocina</a></div></html>',
        encoding="utf-8")
    report = audit_field_closure([{"asin": ASIN}], html_dir=tmp_path)
    assert not [item for item in report["records"]
                if item["field"] in {"category_l2", "category_l3"}
                and item["classification"] == "PARSER_MISSED"]


def test_replay_respects_selected_asins(tmp_path):
    html = Path("tests/fixtures/html/bestsellers_grid.html").read_text(encoding="utf-8")
    rankings = parse_bestsellers_page(html, "https://www.amazon.es/Best-Sellers/zgbs/123", "")
    (tmp_path / "ranking_001.html").write_text(html, encoding="utf-8")
    selected = {rankings[0]["asin"]}
    report = audit_offline_replay(rankings, [], ranking_html_dirs=[tmp_path], asins=selected)
    assert not [item for item in report.issues if item.asin and item.asin not in selected]


@pytest.mark.parametrize("variation,spec", [("900 ml", "900 L"), ("9 L", "29 L")])
def test_variant_values_are_not_substring_checks(variation, spec):
    _, issues = validate_spec({"asin": ASIN, "selected_variation_raw": variation, "spec_v2": spec})
    assert issues


def test_invalid_monthly_count_is_reported_without_crash():
    _, issues = validate_monthly_bought({"monthly_bought_raw": "50+ comprados el mes pasado",
                                        "monthly_bought_min": "invalid"})
    assert issues


def test_stale_identity_status_does_not_mask_conflict():
    report = audit_detail_identity([{"asin": ASIN, "requested_asin": ASIN,
        "parsed_detail_asin": "B000000002", "identity_status": "IDENTITY_MATCH",
        "requested_url": URL}])
    assert report.status != "PASS"


def test_matching_final_url_is_not_mismatched_asin():
    report = audit_detail_identity([{"asin": ASIN, "requested_asin": ASIN,
        "parsed_detail_asin": ASIN, "identity_status": "IDENTITY_MATCH",
        "requested_url": URL, "final_url": URL}])
    assert report.status != "BLOCK"


def test_missing_attributes_does_not_skip_bullet_validation():
    report = audit_detail_structure([{"asin": ASIN, "feature_bullets_raw": {"not": "a list"}}])
    assert report.status != "PASS"


@pytest.mark.parametrize("status", ["pending", "failed", "partial"])
def test_translation_report_does_not_call_unfinished_fields_pass(status):
    report = build_qa_report([{"asin": ASIN, "fields": {
        "title_zh": {"translation_status": status, "qa_issues": []}}}])
    assert report["status"] != "pass"


def test_empty_translation_is_not_promoted_ready():
    production = build_production_input([{"asin": ASIN, "title_es_raw": "Botella de agua"}])
    clean = audit_records(records_for_preclean(production))
    results = {ASIN: {"fields": {"title_zh": {
        "translated_text": "", "translation_status": "success", "qa_status": "pass",
        "qa_issues": [], "source_hash": production["records"][0]["fields"]["title_es_raw"]["source_hash"],
    }}}}
    state = build_production_state(production, clean["translation_input_records"], results)
    assert state["release_candidate"]["release_status"] != "READY"
