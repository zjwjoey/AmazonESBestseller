import json
from pathlib import Path

import pytest

from amazon_es_bestseller.collection.detail import parse_detail_page
from amazon_es_bestseller.export.excel import export_workbook


FIXTURES = Path(__file__).parent / "fixtures" / "detail_write_regression"
CASES = json.loads((FIXTURES / "cross_item.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(("asin", "start"), [
    ("B000000001", "Batido en polvo con sabor a vainilla."),
    ("B000000002", "Pegatinas para decorar la pared."),
    ("B000000003", "Características:"),
])
def test_description_from_saved_page_structure(asin, start):
    html = (FIXTURES / f"{asin}.html").read_text(encoding="utf-8")
    assert parse_detail_page(html, asin)["product_description_raw"].startswith(start)


def test_description_uses_only_primary_product_prose():
    asin = "B000000001"
    html = """
      <div id="productDescription_feature_div"><h2>Descripción del producto</h2></div>
      <div id="pqv-description">
        <h2>Descripción del producto</h2>
        <p>Descripción del producto</p>
        <p>Texto de este producto.</p>
        <div class="related-products">Texto de otro producto.</div>
        <div data-asin="B000000002">Texto de otra variante.</div>
        <div hidden>Texto oculto.</div>
      </div>
      <div id="aplus">Texto A+.</div>
    """
    assert parse_detail_page(html, asin)["product_description_raw"] == "Texto de este producto."


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["record"]["asin"])
def test_chinese_export_rejects_cross_item_content(case, tmp_path):
    record = {**case["record"], **case["evidence"], case["field"]: case["bad"]}
    output = tmp_path / "rejected.xlsx"
    with pytest.raises(ValueError, match=rf"CROSS_PRODUCT_CONTENT.*{record['asin']}"):
        export_workbook([record], out_path=output)
    assert not output.exists()


@pytest.mark.parametrize("case", [c for c in CASES if c.get("corrected")],
                         ids=lambda c: c["record"]["asin"])
def test_chinese_export_accepts_matching_content(case):
    record = {**case["record"], **case["evidence"], case["field"]: case["corrected"]}
    workbook = export_workbook([record])
    column = 20 if case["field"] == "product_details_zh" else 21
    assert workbook["中文选品清单"].cell(2, column).value == case["corrected"]


@pytest.mark.parametrize(("source_identity", "code"), [
    ({"source_asin": "B000000002"}, "ASIN_MISMATCH"),
    ({"product_url": "https://www.amazon.es/dp/B000000002"}, "ASIN_MISMATCH"),
    ({"selected_variation_raw": "Sabor Chocolate"}, "VARIANT_MISMATCH"),
    ({"source_html_sha256": "other-snapshot"}, "SOURCE_HASH_MISMATCH"),
    ({}, "VARIANT_UNVERIFIED"),
])
def test_chinese_export_checks_translation_source(source_identity, code):
    asin = "B000000001"
    record = {
        "asin": asin, "selected_variation_raw": "Sabor Vainilla",
        "source_html_sha256": "current-snapshot",
    }
    translation = {"title_zh": "增重蛋白粉", **source_identity}
    with pytest.raises(ValueError, match=rf"{code}.*{asin}"):
        export_workbook([record], translations={asin: translation})


def test_chinese_export_accepts_same_asin_and_snapshot():
    asin = "B000000001"
    record = {
        "asin": asin, "selected_variation_raw": "Sabor Vainilla",
        "source_html_sha256": "current-snapshot",
    }
    translation = {"source_asin": asin, "source_html_sha256": "current-snapshot",
                   "title_zh": "香草味增重蛋白粉"}
    workbook = export_workbook([record], translations={asin: translation})
    assert workbook["中文选品清单"].cell(2, 5).value == "香草味增重蛋白粉"
