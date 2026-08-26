# -*- coding: utf-8 -*-
import copy
import json
from pathlib import Path

from amazon_es_bestseller.qa.field_closure import audit_field_closure, render_markdown


def _base_product(**overrides):
    p = {
        "asin": "B000000001",
        "title_es_raw": "Caja de almacenamiento",
        "title_zh": "",
        "brand_raw": "",
        "brand": "",
        "current_price_raw": "12,00 €",
        "current_price": 12.0,
        "original_price_raw": "",
        "original_price": None,
        "discount_rate": None,
        "rating_raw": "",
        "rating": None,
        "review_count_raw": "",
        "review_count": None,
        "monthly_bought_raw": "",
        "monthly_bought_min": None,
        "category_l1": "",
        "category_l2": "",
        "category_l3": "",
        "leaf_category": "",
        "bestseller_rank": None,
        "selected_variation_raw": "",
        "spec_v2": "",
        "attributes": [],
        "product_details_zh": "",
        "feature_bullets_raw": [],
        "feature_bullets_zh": "",
        "date_first_available_raw": "",
        "date_first_available": None,
        "seller_raw": "",
        "seller": "",
        "product_url": "https://www.amazon.es/dp/B000000001",
        "image_url": "https://img.example/1.jpg",
        "notes": "",
    }
    p.update(overrides)
    return p


def _issue(report, asin, field):
    return next(r for r in report["records"] if r["asin"] == asin and r["field"] == field)


def test_attribute_brand_without_canonical_is_mapping_missed():
    p = _base_product(attributes=[{"label_raw": "Marca", "value_raw": "De'Longhi"}])
    report = audit_field_closure([p])
    assert _issue(report, p["asin"], "brand")["classification"] == "MAPPING_MISSED"


def test_title_raw_without_chinese_title_is_derived_missing():
    report = audit_field_closure([_base_product()])
    assert _issue(report, "B000000001", "title_zh")["classification"] == "DERIVED_MISSING"


def test_attributes_without_chinese_details_is_derived_missing():
    p = _base_product(attributes=[{"label_raw": "Material", "value_raw": "Acero"}])
    assert _issue(audit_field_closure([p]), p["asin"], "product_details_zh")["classification"] == "DERIVED_MISSING"


def test_bullets_without_chinese_bullets_is_derived_missing():
    p = _base_product(feature_bullets_raw=["Resistente y fácil de limpiar"])
    assert _issue(audit_field_closure([p]), p["asin"], "feature_bullets_zh")["classification"] == "DERIVED_MISSING"


def test_no_source_is_source_missing_and_notes_are_not_audit_field():
    p = _base_product(title_es_raw="", notes="人工备注")
    report = audit_field_closure([p])
    assert _issue(report, p["asin"], "title_zh")["classification"] == "SOURCE_MISSING"
    assert not any(r["field"] in ("notes", "备注") for r in report["records"])


def test_html_evidence_without_raw_is_parser_missed(tmp_path):
    p = _base_product(brand_raw="", brand="")
    (tmp_path / "B000000001.html").write_text(
        '<html><body><div id="bylineInfo">Marca: DeLonghi</div></body></html>',
        encoding="utf-8")
    assert _issue(audit_field_closure([p], html_dir=tmp_path), p["asin"], "brand")["classification"] == "PARSER_MISSED"


def test_category_without_source_is_source_missing_but_ranking_without_detail_bsr_fallback():
    p = _base_product(detail_bsr_raw="n.º 1 en Hogar y cocina")
    r = {"asin": p["asin"], "bestseller_rank": None, "ranking_source_url": ""}
    report = audit_field_closure([p], rankings=[r])
    assert _issue(report, p["asin"], "category_l1")["classification"] == "SOURCE_MISSING"
    assert _issue(report, p["asin"], "bestseller_rank")["classification"] == "SOURCE_MISSING"


def test_category_source_without_canonical_is_parser_missed():
    p = _base_product()
    r = {"asin": p["asin"], "bestseller_rank": 1,
         "ranking_source_url": "https://www.amazon.es/zgbs/123", "category_path_raw": "Hogar y cocina"}
    assert _issue(audit_field_closure([p], rankings=[r]), p["asin"], "category_l1")["classification"] == "PARSER_MISSED"


def test_audit_does_not_mutate_records_and_output_is_deterministic():
    p1 = _base_product(asin="B000000002")
    p2 = _base_product(asin="B000000001")
    before = copy.deepcopy([p1, p2])
    one = audit_field_closure([p1, p2])
    two = audit_field_closure([p1, p2])
    assert [r["asin"] for r in one["records"]] == [r["asin"] for r in two["records"]]
    assert one == two
    assert [p1, p2] == before
    assert "Field Closure Audit" in render_markdown(one)


def test_unknown_attribute_evidence_is_preserved_in_audit():
    p = _base_product(attributes=[{"label_raw": "Campo nuevo", "value_raw": "Valor nuevo"}], product_details_zh="Campo nuevo：Valor nuevo")
    report = audit_field_closure([p])
    rec = _issue(report, p["asin"], "product_details_zh")
    assert "Campo nuevo" in json.dumps(rec, ensure_ascii=False)


def test_five_sku_golden_fixture_covers_real_issue_shapes():
    fixture = Path(__file__).parent / "fixtures" / "field_closure_golden_5sku.json"
    products = json.loads(fixture.read_text(encoding="utf-8"))["products"]
    report = audit_field_closure(products)
    assert report["summary"]["total_skus"] == 5
    # Detail BSR values are deliberately present, but cannot close bestseller rank.
    no_rank = dict(products[0])
    no_rank["bestseller_rank"] = None
    rank = _issue(audit_field_closure([no_rank]), "B008YETL18", "bestseller_rank")
    assert rank["classification"] == "SOURCE_MISSING"
    invalid = _issue(report, "B07RN64P2R", "original_price")
    assert invalid["classification"] == "ORIGINAL_PRICE_INVALID"
    brand = _issue(report, "B008YETL18", "brand")
    assert brand["classification"] == "MAPPING_MISSED"
