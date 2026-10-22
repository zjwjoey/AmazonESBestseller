"""Regressions reduced from the 2026-10-22 live detail-test evidence."""
import copy
from pathlib import Path

import pytest

from amazon_es_bestseller.collection.detail import _classify_saved_page, _resolve_page_identity
from amazon_es_bestseller.collection.detail_v2 import parse_detail_evidence_v2
from amazon_es_bestseller.identity import resolve_identity
from amazon_es_bestseller.models import is_valid_asin
from amazon_es_bestseller.quality.detail_identity import audit_detail_identity
from amazon_es_bestseller.quality.detail_structure import audit_detail_structure


FIXTURES = Path(__file__).parent / "fixtures/html/detail500_20261022"


@pytest.mark.parametrize("url", [
    "https://www.amazon.es/dp/B07QHCNQ7M",
    "HTTPS://WWW.AMAZON.ES/DP/B07QHCNQ7M?psc=1#details",
    "/gp/product/B07QHCNQ7M?th=1",
    "B07QHCNQ7M",
])
def test_final_url_is_extracted_before_asin_normalization(url):
    result = resolve_identity(ranking_asin="B07QHCNQ7M", requested_asin="B07QHCNQ7M",
                              final_url_asin=url, embedded_asins=["B07QHCNQ7M"])
    assert result["resolved_asin"] == "B07QHCNQ7M"
    assert result["identity_status"] == "IDENTITY_MATCH"
    assert all(is_valid_asin(item["asin"]) for item in result["identity_evidence"])


def test_canonical_url_is_extracted_and_invalid_url_is_not_asin_evidence():
    result = resolve_identity(ranking_asin="B07QHCNQ7M",
                              final_url_asin="https://www.amazon.es/not-a-product",
                              canonical_asin="https://www.amazon.es/dp/B07QHCNQ7M?th=1")
    assert result["resolved_asin"] == "B07QHCNQ7M"
    assert all(is_valid_asin(item["asin"]) for item in result["identity_evidence"])
    assert not any(item["source"] == "final_url_asin" for item in result["identity_evidence"])


def test_actual_quarantined_child_page_recovers_without_a_request():
    asin = "B07QHCNQ7M"
    html = (FIXTURES / (asin + ".html")).read_text(encoding="utf-8")
    url = "https://www.amazon.es/dp/" + asin
    classification, _, record = _classify_saved_page(
        html, asin, {"status_code": 200, "final_url": url}, parser_version="v2")
    assert classification == "VALID_PRODUCT_PAGE"
    identity = _resolve_page_identity(asin, html, url, record)
    assert identity["identity_status"] == "IDENTITY_MATCH"
    assert identity["resolved_asin"] == asin
    assert all(is_valid_asin(item["asin"]) for item in identity["identity_evidence"])

    unrelated = _resolve_page_identity(asin, html, "https://www.amazon.es/dp/B000000099", record)
    assert unrelated["identity_status"] == "IDENTITY_MISMATCH"

    unexplained = dict(record, variation_family_asins=[])
    conflict = _resolve_page_identity(asin, html, url, unexplained)
    assert conflict["identity_status"] == "IDENTITY_UNCONFIRMED"
    assert conflict["resolved_asin"] == ""


@pytest.mark.parametrize("asin", ["B07QHCNQ7M", "B0B8NPVTC4"])
def test_actual_saved_sections_pass_quality_without_changing_raw_positions(asin):
    html = (FIXTURES / (asin + ".html")).read_text(encoding="utf-8")
    url = "https://www.amazon.es/dp/" + asin
    record = parse_detail_evidence_v2(html, asin, requested_url=url, final_url=url)
    record.update({"final_url": url, "requested_url": url})
    before = copy.deepcopy(record)
    assert audit_detail_identity([record]).status == "PASS"
    assert audit_detail_structure([record]).status == "PASS"
    assert record == before
    sections = {item["section"] for item in record["attributes"]}
    assert len(sections) >= 2


def test_duplicate_position_within_the_same_section_still_blocks():
    record = {"asin": "B07QHCNQ7M", "attributes": [
        {"section": "technical_details", "source": "prodDetails", "position": 0,
         "label_raw": "Color", "value_raw": "Rojo"},
        {"section": "technical_details", "source": "prodDetails", "position": 0,
         "label_raw": "Color", "value_raw": "Azul"},
    ]}
    assert audit_detail_structure([record]).status == "BLOCK"


def test_descending_positions_within_one_section_still_require_review():
    record = {"asin": "B07QHCNQ7M", "attributes": [
        {"section": "product_overview", "source": "productOverview", "position": 1,
         "label_raw": "Color", "value_raw": "Rojo"},
        {"section": "technical_details", "source": "prodDetails", "position": 0,
         "label_raw": "Material", "value_raw": "Acero"},
        {"section": "product_overview", "source": "productOverview", "position": 0,
         "label_raw": "Color", "value_raw": "Azul"},
    ]}
    assert audit_detail_structure([record]).status == "REVIEW"


def test_unrelated_final_url_cannot_be_hidden_by_a_success_label():
    record = {"asin": "B07QHCNQ7M", "requested_asin": "B07QHCNQ7M",
              "parsed_detail_asin": "B07QHCNQ7M", "identity_status": "MATCH",
              "final_url": "https://www.amazon.es/dp/B000000099"}
    assert audit_detail_identity([record]).status == "BLOCK"
