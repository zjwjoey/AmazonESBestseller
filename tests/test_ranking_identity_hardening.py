import json
from pathlib import Path

import pytest

from amazon_es_bestseller.monitoring.ranking_identity.adapters import (
    extract_structured_candidates_with_status,
)
from amazon_es_bestseller.monitoring.ranking_identity.completeness import (
    audit_identity_records,
)
from amazon_es_bestseller.monitoring.ranking_identity.extract import (
    extract_identity_from_evidence,
)


def _record(index: int) -> dict:
    asin = f"B{index:09d}"
    return {"asin": asin, "product_url": f"https://www.amazon.es/dp/{asin}",
            "identity_status": "CONFIRMED", "product_url_source": "RAW_HREF_CONFIRMED"}


def test_unknown_expected_count_is_ready_but_not_complete():
    audit = audit_identity_records([_record(index) for index in range(30)],
                                   [_record(index) for index in range(30)])
    assert audit["identity_ready"] is True
    assert audit["identity_complete"] is False
    assert audit["expected_count"] is None
    assert audit["expected_count_source"] == "UNKNOWN"
    assert audit["status"] == "IDENTITY_COMPLETENESS_UNKNOWN"


def test_supplemental_parser_does_not_scan_random_tokens():
    rows, status = extract_structured_candidates_with_status(
        "ABC1234567", evidence_source="ACP")
    assert rows == []
    assert status == "INVALID_JSON"

    rows, status = extract_structured_candidates_with_status(
        "https://www.amazon.es/dp/B012345678", evidence_source="ACP")
    assert [row["asin"] for row in rows] == ["B012345678"]
    assert status == "OK_URL_EVIDENCE"


def test_invalid_supplemental_json_does_not_fallback_to_token_scan():
    rows, status = extract_structured_candidates_with_status(
        '{"note":"ABC1234567"', evidence_source="ACP")
    assert rows == []
    assert status == "INVALID_JSON"


def test_initial_and_rendered_representations_share_expected_count(tmp_path):
    initial = "".join(
        f"<div id='gridItemRoot' data-asin='B{index:09d}'></div>"
        for index in range(30))
    rendered = "".join(
        f"<div id='gridItemRoot' data-asin='B{index:09d}'></div>"
        for index in range(50))
    (tmp_path / "initial_html.html").write_text(initial, encoding="utf-8")
    (tmp_path / "rendered_html.html").write_text(rendered, encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "source_url": "https://www.amazon.es/gp/bestsellers/tools",
        "page_number": 1,
        "expected_count": 50,
        "expected_count_source": "RUN_MANIFEST",
    }), encoding="utf-8")

    result = extract_identity_from_evidence(tmp_path)
    audit = result["audit"]
    assert audit["server_rendered_count"] == 80
    assert audit["unique_asin_count"] == 50
    assert audit["expected_count"] == 50
    assert audit["expected_count_source"] == "RUN_MANIFEST"
    assert audit["identity_complete"] is True
    assert {item["page_instance_id"] for item in result["records"][0]["identity_evidence"]} == {
        "page:1|url:https://www.amazon.es/gp/bestsellers/tools"
    }
