from __future__ import annotations

from amazon_es_bestseller.quality.source_fields import audit_source_fields
from amazon_es_bestseller.quality.source_gate import evaluate_source_gate


ASIN = "B012345678"


def row(**extra):
    value = {
        "asin": ASIN,
        "requested_asin": ASIN,
        "ranking_asin": ASIN,
        "detail_asin": ASIN,
        "final_asin": ASIN,
        "product_url": f"https://www.amazon.es/dp/{ASIN}",
        "final_url": f"https://www.amazon.es/dp/{ASIN}",
        "ranking_contexts": [{
            "ranking_source_url": "https://www.amazon.es/Best-Sellers/zgbs/123",
            "ranking_page_number": 1,
            "bestseller_rank": 3,
            "leaf_category": "Cocina",
            "browse_node_id": "123",
        }],
        "bestseller_rank": 3,
        "current_price": "10,00 EUR",
        "rating": "4,2",
        "review_count": "12",
        "date_first_available_raw": "28 octubre 2023",
        "date_first_available": "2023-10-28",
        "title_es_raw": "Botella de acero 500 ml",
        "specification": "Capacidad: 500 ml / Peso: 200 g",
        "category_l1": "Hogar",
        "category_l2": "Cocina",
        "leaf_category": "Botellas",
        "category_provenance": {"source": "breadcrumb"},
        # These are the actual detail/ranking parser metadata keys, rather
        # than inferred defaults.  A missing key remains missing evidence.
        "detail_schema_version": 2,
        "detail_parser_version": "collection.detail_v1",
        "ranking_parser_version": "collection.ranking_v2",
    }
    value.update(extra)
    return value


def issue_codes(report):
    return {item["issue_code"] for item in report["issues"]}


def field_classes(report):
    return {item.get("field_classification") for item in report["issues"]}


def audit_classes(report):
    return {item.get("classification") for item in report["field_audits"]}


def test_valid_evidence_is_source_ready_and_gate_is_bound_to_audit_hash():
    report = audit_source_fields([row()])
    gate = evaluate_source_gate(report)
    assert report["sku_status"] == {ASIN: "SOURCE_READY"}
    assert gate["status"] == "SOURCE_READY"
    assert gate["audit_hash"]


def test_source_absence_is_not_parser_miss_but_explicit_source_presence_is():
    missing = audit_source_fields([row(title_es_raw=None, source_fields={"title_es_raw": False})])
    parsed_away = audit_source_fields([row(title_es_raw=None, source_fields={"title_es_raw": True})])
    assert "SOURCE_MISSING" in audit_classes(missing)
    assert "PARSER_MISSED" in field_classes(parsed_away)


def test_auxiliary_unit_price_warns_without_becoming_current_price_or_blocking_sku():
    report = audit_source_fields([row(unit_price="not-a-price")])
    assert "WARN" in field_classes(report)
    assert report["sku_status"][ASIN] == "SOURCE_READY"


def test_invalid_locale_requires_review_and_audit_binds_raw_evidence_hashes():
    report = audit_source_fields([row(locale="en-GB")])
    assert report["sku_status"][ASIN] == "REVIEW_REQUIRED"
    binding = report["record_bindings"][ASIN][0]
    assert binding["record_hash"]
    assert binding["raw_evidence_hash"]
    assert binding["detail_schema_version"]
    assert binding["parser_version"]


def test_identity_uses_explicit_variation_evidence_but_rejects_unexplained_redirect():
    related = audit_source_fields([row(
        final_asin="B000000001", final_url="https://www.amazon.es/dp/B000000001",
        variation_family_asins=[ASIN, "B000000001"],
    )])
    assert "IDENTITY_CONFLICT" not in issue_codes(related)
    bad = audit_source_fields([row(final_asin="B000000001", final_url="https://www.amazon.es/dp/B000000001")])
    assert "IDENTITY_CONFLICT" in issue_codes(bad)
    alternate = audit_source_fields([row(variation_asin="B000000001")])
    assert "IDENTITY_CONFLICT" in issue_codes(alternate)


def test_ranking_context_audits_url_duplicate_slot_and_bsr_separation():
    report = audit_source_fields([row(
        detail_bsr=3,
        ranking_contexts=[
            {"ranking_source_url": "https://www.amazon.es/Best-Sellers/zgbs/123", "ranking_page_number": 1,
             "bestseller_rank": 3, "leaf_category": "Cocina", "browse_node_id": "123"},
            {"ranking_source_url": "https://not-amazon.example/x", "ranking_page_number": 1,
             "bestseller_rank": 3, "leaf_category": "Cocina", "browse_node_id": "123"},
            {"ranking_source_url": "https://www.amazon.es/Best-Sellers/zgbs/123", "ranking_page_number": 1,
             "bestseller_rank": 3, "leaf_category": "Cocina", "browse_node_id": "123"},
        ],
    )])
    assert {"RANK_BSR_MIXED", "RANKING_SOURCE_INVALID", "DUPLICATE_RANK_SLOT"} <= issue_codes(report)


def test_price_rating_dates_units_and_text_semantics_are_checked():
    report = audit_source_fields([row(
        current_price="-1", original_price="2", rating="6", review_count="-1",
        date_first_available_raw="31 febrero 2024", specification="Capacidad: 3 W",
        title_es_raw="<div>Robot Check</div>", product_details_es="{bad json",
    )])
    codes = issue_codes(report)
    assert {"SOURCE_FIELD_INVALID", "RATING_INVALID", "REVIEW_COUNT_INVALID", "DATE_INVALID",
            "SPEC_UNIT_TYPE_MISMATCH", "MISPLACED"} <= codes


def test_empty_input_and_p1_blocked_record_cannot_pass_source_gate():
    empty = audit_source_fields([{"asin": ASIN}])
    blocked = audit_source_fields([row(product_url="http://www.amazon.es/dp/B012345678")])
    assert empty["sku_status"][ASIN] != "SOURCE_READY"
    assert evaluate_source_gate(empty)["status"] != "SOURCE_READY"
    assert blocked["sku_status"][ASIN] == "BLOCKED"
    assert evaluate_source_gate(audit_source_fields([]))["status"] == "REVIEW_REQUIRED"
