from amazon_es_bestseller.translation.protection import protect
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
