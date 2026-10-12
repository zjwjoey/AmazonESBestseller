"""Offline upstream regressions; no production evidence is mutated."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.collection.detail import _classify_saved_page, parse_detail_page
from amazon_es_bestseller.pipeline import normalize_product
from amazon_es_bestseller.translation.full_detail import render_details_es, render_details_zh

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "issue_register_source_regressions.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FIXTURE["brand_cases"], ids=lambda row: row["asin"])
def test_byline_does_not_override_explicit_brand_or_become_author_brand(case):
    html = '<span id="productTitle">Source product</span><div id="bylineInfo">%s</div>' % case["byline"]
    if case["explicit_brand"]:
        html += '<div id="prodDetails"><table><tr><th>Marca</th><td>%s</td></tr></table></div>' % case["explicit_brand"]
    parsed = parse_detail_page(html, case["asin"])
    assert parsed["brand_raw"] == case["expected"]
    assert parsed["brand_byline_raw"] == case["byline"]
    legacy = {"asin": case["asin"], "brand_raw": case["byline"], "attributes": parsed["attributes"]}
    before = deepcopy(legacy)
    assert normalize_product(legacy)["brand"] == case["expected"]
    assert legacy == before


def test_unlabelled_author_byline_is_not_brand():
    parsed = parse_detail_page('<span id="productTitle">Book</span><a id="bylineInfo">de Someone (Autor)</a>', "B000000001")
    assert parsed["brand_raw"] == ""


@pytest.mark.parametrize("detail_trail", [
    ["Hogar y cocina", "Muebles", "Dormitorio"],
    ["Productos para mascotas", "Acuáticos", "Accesorios para acuarios"],
])
def test_category_breadcrumb_never_splices_a_conflicting_path(detail_trail):
    source = {"asin": "B000255PFI", "category_l1": "Productos para mascotas",
              "category_l2": "Peces y mascotas acuáticas", "leaf_category": "Peces y mascotas acuáticas",
              "detail_category_trail": detail_trail}
    before = deepcopy(source)
    out = normalize_product(source)
    assert not out.get("category_l3")
    assert out["leaf_category"] == source["leaf_category"]
    assert out["category_enrichment_status"] == "CONFLICT"
    assert source == before


def test_display_preserves_different_values_of_the_same_label():
    attributes = deepcopy(FIXTURE["display_conflicts"])
    before = deepcopy(attributes)
    for render in (render_details_es, render_details_zh):
        text = render(attributes)
        assert "AH0003400R" in text and "Replacement mechanism Roca" in text
        assert len(text.splitlines()) == 2
    assert attributes == before


def test_chinese_label_aliases_do_not_drop_distinct_facts():
    attributes = [{"label_raw": "Función especial", "value_raw": "Impermeable"},
                  {"label_raw": "Características especiales", "value_raw": "Elástico"}]
    assert len(render_details_zh(attributes).splitlines()) == 2


def test_unbound_self_parent_family_dictionary_is_not_confirmation():
    source = {"asin": "B000000001", "parent_asin": "B000000001", "parent_asin_status": "confirmed",
              "variation_evidence": {"current_asin": "B000000001", "parent_asin": "B000000001",
                                     "family_asins": ["B000000001", "B000000002"]}}
    assert normalize_product(source)["parent_asin"] == ""


@pytest.mark.parametrize("newlines", ["\n", "\r\n"])
def test_parser_family_proof_is_bound_to_saved_html(tmp_path, newlines):
    html = '<span id="productTitle">Product</span><input id="ASIN" value="B000000001"><script>' + json.dumps({
        "currentAsin": "B000000001", "parentAsin": "B000000001",
        "dimensionValuesDisplayData": {"B000000001": ["A"], "B000000002": ["B"]}}, indent=2) + '</script>'
    path = tmp_path / "B000000001.html"
    path.write_bytes(html.replace("\n", newlines).encode("utf-8"))
    classification, _, parsed = _classify_saved_page(html, "B000000001", {"cache_html_path": str(path)}, parser_version="v2")
    assert classification == "VALID_PRODUCT_PAGE"
    evidence = parsed["variation_evidence"]
    assert evidence["producer"] == "parser"
    assert evidence["cache_html_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert normalize_product(parsed)["parent_asin"] == "B000000001"
    forged = deepcopy(parsed)
    forged["variation_evidence"]["family_asins"].append("B000000003")
    assert normalize_product(forged)["parent_asin"] == ""
    path.write_text("changed saved evidence", encoding="utf-8")
    assert normalize_product(parsed)["parent_asin"] == ""


def test_empty_detail_breadcrumb_does_not_create_a_category_conflict():
    out = normalize_product({"asin": "B000000001", "category_l1": "Hogar", "detail_category_trail": []})
    assert "category_enrichment_status" not in out


def test_display_does_not_replace_an_unrelated_truncated_value():
    attributes = [{"label_raw": "Modelo", "value_raw": "AH0003400R… Ver más"},
                  {"label_raw": "Modelo", "value_raw": "Different complete model"}]
    assert len(render_details_es(attributes).splitlines()) == 2


def test_saved_repair_does_not_manufacture_confirmed_parent_status(tmp_path):
    from amazon_es_bestseller.collection.repair import repair_cached_products
    (tmp_path / "B000000001.html").write_text(
        '<span id="productTitle">Product</span><input id="ASIN" value="B000000001">'
        '<input id="parentASIN" value="B000000002">', encoding="utf-8")
    result, _ = repair_cached_products([{"asin": "B000000001"}], tmp_path)
    assert result[0]["parent_asin_raw"] == "B000000002"
    assert result[0]["parent_asin_status"] != "confirmed"
    existing = {"asin": "B000000001", "parent_asin": "B000000001", "parent_asin_raw": "B000000001"}
    repaired, _ = repair_cached_products([existing], tmp_path)
    assert repaired[0]["parent_asin_raw"] == existing["parent_asin_raw"]
    assert repaired[0]["parent_asin_observation"]["value_raw"] == "B000000002"
    assert "parent_asin_observation" not in existing


def test_source_candidate_does_not_reintroduce_an_editorial_brand():
    from amazon_es_bestseller.production.spanish_source_closure import _explicit_brand
    queue = []
    assert _explicit_brand({"brand_raw": "de Someone (Autor)"}, "B000000001", queue) == ""
    assert queue
