"""Do not mistake an audit-policy change for repaired source facts."""
import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "tools" / "offline_regression_review.py"
spec = importlib.util.spec_from_file_location("offline_review", MODULE_PATH)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def record(value="12 cm"):
    return {"asin": "B000000001", "attributes": [
        {"label_raw": "Volumen", "value_raw": value, "section": "technical_details", "position": 0}
    ], "offline_replay_status": "REPARSED_IDENTITY_MATCH"}


def issue(label="Volumen", value="12 cm", code="SPEC_UNIT_TYPE_MISMATCH"):
    return {"asin": "B000000001", "field": "attributes", "issue_code": code,
            "message": f"{label} has the wrong unit type", "evidence": {"label": label, "value": value}}


def test_unchanged_source_without_current_finding_is_rule_change_only():
    result = review.compare_issue(issue(), record(), record(), [])
    assert result["resolution"] == "RULE_CHANGE_ONLY"
    assert result["confirmed_repair"] is False


def test_other_attribute_same_asin_code_does_not_match():
    result = review.compare_issue(issue(), record(), record(), [issue("Peso", "12 kg")])
    assert result["resolution"] == "RULE_CHANGE_ONLY"
    assert result["current_matching_count"] == 0


def test_reclassified_bad_value_is_still_an_issue():
    result = review.compare_issue(issue(), record(), record(), [issue(code="SOURCE_SEMANTIC_CONFLICT")])
    assert result["resolution"] == "STILL_ISSUE"


def test_failed_replay_is_evidence_insufficient_not_repaired():
    current = record("12 L")
    current["offline_replay_status"] = "NOT_REPARSED_OR_IDENTITY_NOT_CONFIRMED"
    result = review.compare_issue(issue(), record(), current, [])
    assert result["resolution"] == "EVIDENCE_INSUFFICIENT"


def test_changed_attribute_without_positive_validation_is_not_confirmed():
    result = review.compare_issue(issue(), record(), record("12 L"), [])
    assert result["resolution"] == "EVIDENCE_INSUFFICIENT"


def test_brand_repair_requires_explicit_brand_evidence():
    old = {**record(), "brand": "Editorial", "attributes": []}
    current = {**record(), "brand": "Tayg", "attributes": [
        {"label_raw": "Marca", "value_raw": "Tayg"}]}
    finding = {"asin": old["asin"], "field": "brand", "issue_code": "FIELD_MISPLACED",
               "message": "brand contains spec or price", "evidence": {}}
    result = review.compare_issue(finding, old, current, [])
    assert result["resolution"] == "CONFIRMED_FIXED"


def test_missing_historical_field_binding_is_not_silently_confirmed():
    finding = {"asin": "B000000001", "issue_code": "SPEC_UNIT_TYPE_MISMATCH", "evidence": {}}
    assert review.compare_issue(finding, record(), record(), [])["resolution"] == "EVIDENCE_INSUFFICIENT"


def test_changed_source_still_finding_is_not_confirmed():
    assert review.compare_issue(issue(), record(), record("13 cm"), [issue(value="13 cm")])["resolution"] == "STILL_ISSUE"


def test_independent_dom_inventory_retains_unknown_attribute_and_bullet_order():
    html = '<div id="productOverview_feature_div"><table><tr><td>Unknown fact</td><td>42</td></tr></table></div><div id="feature-bullets"><ul><li>First <b>useful</b> fact</li><li>Second</li></ul></div>'
    inventory = review.html_inventory(html)
    assert inventory["attribute_pairs"] == [("Unknown fact", "42")]
    assert inventory["feature_bullets"] == ["First useful fact", "Second"]


def test_independent_inventory_inspects_both_additional_blocks():
    html = '<div id="detailBullets_feature_div"><dl><dt>Color</dt><dd>Red</dd></dl></div><table id="productDetails_detailBullets_sections1"><tr><th>Material</th><td>Steel</td></tr></table>'
    assert review.html_inventory(html)["attribute_pairs"] == [("Color", "Red"), ("Material", "Steel")]


def test_inventory_does_not_treat_invisible_script_as_a_missing_fact():
    html = '<div id="productOverview_feature_div"><table><tr><td>Rating</td><td>4.4<script>invisible()</script>stars</td></tr></table></div>'
    assert review.html_inventory(html)["attribute_pairs"] == [("Rating", "4.4 stars")]


def test_candidate_details_include_visible_supplemental_bullet_facts():
    row = {"attributes": [{"label_raw": "Material", "value_raw": "Steel"}],
           "detail_bullets_raw": ["País de origen : España", "Número de modelo del producto : M123"]}
    rendered = review.complete_display_details(row)
    assert "Steel" in rendered and "España" in rendered and "M123" in rendered


def test_gate_summary_does_not_put_thousands_of_evidence_rows_in_one_cell():
    gate = {"status": "BLOCKED", "ready": False, "evidence": {"sku_status": {str(i): "REVIEW_REQUIRED" for i in range(5480)}}}
    compact = review.gate_summary(gate)
    assert compact["sku_status_counts"] == {"REVIEW_REQUIRED": 5480}
    assert len(review.canonical(compact)) < 500


def price_review(original_price, refreshed_price, candidate_price, candidate_discount):
    asin = "B000000001"
    original = {asin: {"asin": asin, "current_price_raw": "5,99 €", "original_price_raw": original_price}}
    before = {asin: {"asin": asin, "original_price": None, "discount_rate": None}}
    after = {asin: {"asin": asin, "original_price": candidate_price, "discount_rate": candidate_discount}}
    refreshed = {"asin": asin, "original_price_raw": refreshed_price,
        "offline_replay_evidence": {"html_sha256": "verified-html"},
        "offline_price_refresh": {"producer": "saved_html_price_parser", "html_sha256": "verified-html",
            "original_price_raw_before": original_price, "original_price_raw_after": refreshed_price}}
    return review.register_fact_checks(original, before, after, [refreshed])[0]


def test_price_register_accepts_recovered_real_reference_after_old_false_equal_price():
    result = price_review("5,99 €", "10,99 €", 10.99, round(5 / 10.99, 4))
    assert result["passed"] is True
    assert result["resolution"] == "CONFIRMED_FIXED"


def test_price_register_accepts_excluded_new_price_even_when_old_price_was_higher():
    result = price_review("10,99 €", None, None, None)
    assert result["passed"] is True
    assert result["resolution"] == "CONFIRMED_FIXED"


def test_price_register_does_not_claim_unchanged_invalid_source_was_repaired():
    result = price_review("5,99 €", "5,99 €", None, None)
    assert result["passed"] is True
    assert result["resolution"] == "STILL_ISSUE"
