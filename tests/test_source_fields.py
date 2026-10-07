from amazon_es_bestseller.quality.source_fields import audit_source_fields, source_gate


def _row(**extra):
    row = {"asin": "B012345678", "product_url": "https://www.amazon.es/dp/B012345678",
           "bestseller_rank": 3, "current_price": "10,00", "rating": "4.2"}
    row.update(extra)
    return row


def test_source_audit_blocks_identity_url_and_rank_bsr_mixing():
    report = audit_source_fields([_row(detail_bsr=3), _row(asin="bad")])
    codes = {item["issue_code"] for item in report["issues"]}
    assert {"RANK_BSR_MIXED", "IDENTITY_CONFLICT"} <= codes
    assert source_gate(report) == (False, "SOURCE_BLOCKED")


def test_source_audit_blocks_invalid_price_and_detects_junk_without_guessing():
    report = audit_source_fields([_row(current_price="10", original_price="9", title_es_raw="<script>x</script>")])
    assert {item["issue_code"] for item in report["issues"]} == {"SOURCE_FIELD_INVALID", "MISPLACED"}


def test_source_audit_ready_for_valid_evidence():
    report = audit_source_fields([_row()])
    assert report["sku_status"] == {"B012345678": "SOURCE_READY"}
    assert source_gate(report) == (True, "SOURCE_READY")


def test_source_audit_does_not_treat_product_angle_text_as_html():
    report = audit_source_fields([_row(
        title_es_raw="&lt; MULTIFUNCIÓN &gt; con control <50% RH / >70% RH",
    )])
    assert "MISPLACED" not in {item["issue_code"] for item in report["issues"]}


def test_source_audit_uses_labeled_values_for_unit_semantics():
    report = audit_source_fields([_row(
        attributes=[
            {"label_raw": "Ventajas del producto", "value_raw": "Potenciador reafirmante (15 ml)"},
            {"label_raw": "Volumen del producto", "value_raw": "15 Mililitros"},
            {"label_raw": "Dimensiones del artículo L x A", "value_raw": "14,6l. x 6,7an. centímetros"},
        ],
        specification="Ventajas: Potenciador reafirmante (15 ml) / Volumen: 15 Mililitros / "
                      "Dimensiones: 14,6l. x 6,7an. centímetros",
    )])
    assert "SPEC_UNIT_TYPE_MISMATCH" not in {item["issue_code"] for item in report["issues"]}


def test_source_audit_checks_compact_specification_even_when_attributes_exist():
    report = audit_source_fields([_row(
        attributes=[{"label_raw": "Marca", "value_raw": "Acme"}],
        specification="Potencia: 10 ml",
    )])
    assert "SPEC_UNIT_TYPE_MISMATCH" in {item["issue_code"] for item in report["issues"]}
