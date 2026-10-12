from copy import deepcopy
import hashlib

from amazon_es_bestseller.quality.parent_scope import review_parent_scope
from amazon_es_bestseller.quality.detail_identity import extract_detail_identity_signals
from amazon_es_bestseller.quality.source_fields import audit_source_fields


def test_family_scripts_do_not_prove_attribute_scope():
    row = {
        "asin": "B000000001",
        "parent_asin": "B000000002",
        "attributes": [
            {"section": "technical_details", "label_raw": "Color", "value_raw": "Rojo"}
        ],
    }
    before = deepcopy(row)
    html = '<script>{"currentAsin":"B000000001","parentAsin":"B000000002"}</script><table><tr><td>Color</td><td>Rojo</td></tr></table>'
    result = review_parent_scope(html, row)
    assert result["decision"] == "CHILD_SCOPE_EVIDENCE_INSUFFICIENT"
    assert not result["all_child_scope_confirmed"]
    assert row == before


def test_only_same_child_table_pairs_are_bound():
    row = {
        "asin": "B000000001",
        "attributes": [
            {"section": "technical_details", "label_raw": "Color", "value_raw": "Rojo"},
            {"section": "product_overview", "label_raw": "Color", "value_raw": "Rojo"},
            {
                "section": "technical_details",
                "label_raw": "Tamaño",
                "value_raw": "50 L",
            },
        ],
    }
    html = "<table><tr><th>ASIN</th><td>B000000001</td></tr><tr><th>Color</th><td>Rojo</td></tr></table><table><tr><th>Tamaño</th><td>50 L</td></tr></table>"
    result = review_parent_scope(html, row)
    assert result["attribute_scope_counts"] == {
        "CHILD_ASIN_ANCHORED_TABLE": 1,
        "SCOPE_NOT_CONFIRMED": 2,
    }
    assert not result["all_child_scope_confirmed"]


def test_parent_table_cannot_bind_child():
    row = {
        "asin": "B000000001",
        "parent_asin": "B000000002",
        "attributes": [
            {"section": "technical_details", "label_raw": "Color", "value_raw": "Rojo"}
        ],
    }
    html = "<table><tr><th>ASIN</th><td>B000000002</td></tr><tr><th>Color</th><td>Rojo</td></tr></table>"
    assert review_parent_scope(html, row)["attribute_scope_counts"] == {
        "SCOPE_NOT_CONFIRMED": 1
    }


def test_scope_hash_comes_from_actual_html_not_a_loose_row_flag():
    row = {"asin": "B000000001", "html_sha256": "forged", "attributes": []}
    html = b'<table>\r\n<tr><th>ASIN</th><td>B000000001</td></tr></table>'
    result = review_parent_scope(html, row)
    assert result["html_sha256"] == hashlib.sha256(html).hexdigest()
    assert not result["all_child_scope_confirmed"]


def test_complete_attribute_only_child_scope_is_not_hardcoded_false():
    row = {"asin": "B000000001", "attributes": [
        {"section": "technical_details", "label_raw": "ASIN", "value_raw": "B000000001"},
        {"section": "technical_details", "label_raw": "Color", "value_raw": "Rojo"}]}
    html = '<table><tr><th>ASIN</th><td>B000000001</td></tr><tr><th>Color</th><td>Rojo</td></tr></table>'
    result = review_parent_scope(html, row)
    assert result["all_child_scope_confirmed"]
    assert result["decision"] == "CHILD_SCOPE_CONFIRMED"
    assert not review_parent_scope(html, {**row, "feature_bullets_raw": ["Fácil de usar"]})["all_child_scope_confirmed"]
    assert not review_parent_scope(html, {**row, "detail_bullets_raw": ["Color : Azul"]})["all_child_scope_confirmed"]


def test_duplicate_raw_occurrence_cannot_reuse_one_child_table_pair():
    row = {"asin": "B000000001", "attributes": [
        {"section": "technical_details", "label_raw": "Color", "value_raw": "Rojo"},
        {"section": "technical_details", "label_raw": "Color", "value_raw": "Rojo"}]}
    html = '<table><tr><th>ASIN</th><td>B000000001</td></tr><tr><th>Color</th><td>Rojo</td></tr></table>'
    assert review_parent_scope(html, row)["attribute_scope_counts"] == {"SCOPE_NOT_CONFIRMED": 2}


def test_source_audit_requires_fresh_bound_html_for_complete_scope_proof():
    html = ('<input id="ASIN" value="B000000001"><link rel="canonical" href="https://www.amazon.es/dp/B000000002">'
            '<script>{"currentAsin":"B000000001","parentAsin":"B000000002"}</script>'
            '<table><tr><th>ASIN</th><td>B000000001</td></tr><tr><th>Color</th><td>Rojo</td></tr></table>')
    row = {"asin": "B000000001", "title_es_raw": "Artículo rojo", "parent_asin": "B000000002", "attributes": [
        {"section": "technical_details", "label_raw": "ASIN", "value_raw": "B000000001"},
        {"section": "technical_details", "label_raw": "Color", "value_raw": "Rojo"}],
        "offline_replay_evidence": {"html_sha256": hashlib.sha256(html.encode()).hexdigest()},
        "detail_identity_signals": extract_detail_identity_signals(html)}
    before = deepcopy(row)
    assert audit_source_fields([row])["sku_status"][row["asin"]] == "REVIEW_REQUIRED"
    assert audit_source_fields([row], parent_scope_html={row["asin"]: html})["sku_status"][row["asin"]] == "SOURCE_READY"
    for bad_html in (html + "changed", html.replace("Rojo", "Azul")):
        assert audit_source_fields([row], parent_scope_html={row["asin"]: bad_html})["sku_status"][row["asin"]] == "REVIEW_REQUIRED"
    assert row == before
    with_bullets = {**row, "feature_bullets_raw": ["Producto familiar"]}
    assert audit_source_fields([with_bullets], parent_scope_html={row["asin"]: html})["sku_status"][row["asin"]] == "REVIEW_REQUIRED"
