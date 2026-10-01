from amazon_es_bestseller.translation.protection import ProtectedText, protect
from amazon_es_bestseller.translation.qa import build_qa_report, qa_field


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
