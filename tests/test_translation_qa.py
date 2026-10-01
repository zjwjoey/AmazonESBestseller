from amazon_es_bestseller.translation.protection import ProtectedText, protect
from amazon_es_bestseller.translation.qa import build_qa_report, qa_field
from amazon_es_bestseller.translation.full_detail import render_details_zh
from amazon_es_bestseller.translation.terminology import postprocess
from amazon_es_bestseller.translation.terminology import deterministic_specification
from amazon_es_bestseller.translation.terminology import specification_is_deterministic
from amazon_es_bestseller.translation.zh import translate_value


def test_qa_detects_numeric_mismatch_and_residual_spanish():
    protected = protect("Botella 500 ml")
    result = qa_field(protected, "producto para 250 ml", "Botella 500 ml")
    assert result["qa_status"] == "qa_failed"
    assert any(x["code"] in {"NUMERIC_MISMATCH", "SPANISH_RESIDUAL"} for x in result["issues"])


def test_qa_report_counts_field_statuses():
    report = build_qa_report([{"asin": "A", "fields": {"title_zh": {
        "translation_status": "success", "qa_issues": []}}}])
    assert report["counts"]["success"] == 1


def test_qa_report_alias_counts_follow_canonical_issue_codes():
    report = build_qa_report([{"asin": "A", "fields": {"title_zh": {
        "translation_status": "qa_failed",
        "qa_issues": [{"code": "NUMERIC_MISMATCH"}, {"code": "ADDED_NUMBER"}],
    }}}])
    assert report["counts"]["numeric_errors"] == 1
    assert report["counts"]["added_numbers"] == 1


def test_bullet_count_is_preserved():
    protected = protect("Linea uno\nLinea dos")
    result = qa_field(protected, "第一条", "Linea uno\nLinea dos", field="feature_bullets_es")
    assert any(x["code"] == "BULLET_COUNT_MISMATCH" for x in result["issues"])


def test_qa_detects_added_number_and_brand_change():
    protected = protect("Pack de 6", protected_values=["HOVVIDA"])
    number_result = qa_field(protected, "6件装，500毫升", "Pack de 6")
    assert any(x["code"] == "ADDED_NUMBER" for x in number_result["issues"])
    brand_protected = protect("HOVVIDA", protected_values=["HOVVIDA"])
    brand_result = qa_field(brand_protected, "其他品牌", "HOVVIDA", field="brand", brand="HOVVIDA")
    assert any(x["code"] == "BRAND_ABNORMAL" for x in brand_result["issues"])


def test_unit_conversion_to_chinese_is_allowed_when_number_matches():
    protected = ProtectedText("500 ml")
    result = qa_field(protected, "500毫升", "500 ml")
    assert result["qa_status"] == "pass"


def test_decimal_comma_and_full_spanish_unit_are_equivalent():
    protected = protect("4,25 Kilogramos")
    result = qa_field(protected, "4.25千克", "4,25 Kilogramos")
    assert result["qa_status"] == "pass"


def test_identity_and_boolean_labels_have_deterministic_chinese_names():
    assert specification_is_deterministic("Referencia OEM: 20002")
    assert specification_is_deterministic("Modelo: Cera Tec")
    assert specification_is_deterministic("Modelo: 26431")
    assert deterministic_specification("Referencia OEM: 20002") == "OEM参考号：20002"
    assert deterministic_specification("Modelo: Cera Tec") == "型号：Cera Tec"
    assert deterministic_specification("Modelo: 26431") == "型号：26431"
    assert deterministic_specification("Frecuencia: 50 Hz") == "频率：50 Hz"


def test_boolean_dictionary_values_are_stable():
    attrs = [
        {"label_raw": "Requiere montaje", "value_raw": "No"},
        {"label_raw": "Incluye batería", "value_raw": "true"},
    ]
    rendered = render_details_zh(attrs)
    assert "No" not in rendered
    assert "true" not in rendered
    assert "否" in rendered
    assert "是" in rendered


def test_unidad_does_not_invent_a_quantity():
    translated = translate_value("Unidad")
    assert translated == "单件"
    assert "1" not in translated


def test_legal_company_name_is_preserved():
    rendered = render_details_zh([{
        "label_raw": "Fabricante",
        "value_raw": "Energía Eléctrica Eficiente SL",
    }])
    assert "Energía Eléctrica Eficiente SL" in rendered


def test_milliamp_hour_suffix_is_not_duplicated():
    assert translate_value("5200 mAh") == "5200 mAh"
    assert postprocess("product_details_es", "5200 mAhmAh", "5200 mAh") == "5200 mAh"
