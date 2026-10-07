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


def test_source_audit_accepts_leaf_equal_l3_only_with_matching_ranking_provenance():
    supported = _row(
        category_l1="Hogar", category_l2="Cocina", category_l3="Botes", leaf_category="Botes",
        category_provenance={
            "source": "ranking_context", "ranking_source_category_path": "Hogar > Cocina > Botes",
            "levels": {"category_l1": "Hogar", "category_l2": "Cocina", "category_l3": "Botes"},
            "leaf_category": "Botes",
        },
    )
    missing_provenance = dict(supported, category_provenance=None)
    forged_duplicate = dict(supported, category_l2="Botes")
    assert "CATEGORY_COPIED" not in {item["issue_code"] for item in audit_source_fields([supported])["issues"]}
    missing_codes = {item["issue_code"] for item in audit_source_fields([missing_provenance])["issues"]}
    assert {"CATEGORY_PROVENANCE_MISSING", "CATEGORY_COPIED"} <= missing_codes
    assert "CATEGORY_COPIED" in {item["issue_code"] for item in audit_source_fields([forged_duplicate])["issues"]}


def test_source_audit_uses_specific_unit_labels_and_reviews_ambiguous_size_or_count():
    report = audit_source_fields([_row(attributes=[
        {"label_raw": "Capacidad de carga", "value_raw": "150 Kg"},
        {"label_raw": "Capacidad de peso máxima", "value_raw": "20 kg"},
        {"label_raw": "Número de unidades", "value_raw": "1000.0 Gramos"},
        {"label_raw": "Tamaño", "value_raw": "100 ml"},
        {"label_raw": "Tamaño", "value_raw": "22 x 25 x 45 cm"},
        {"label_raw": "Dimensiones del producto", "value_raw": "22 x 25 x 45 cm; 282 g"},
    ])])
    mismatches = [item for item in report["issues"] if item["issue_code"] == "SPEC_UNIT_TYPE_MISMATCH"]
    ambiguous = [item for item in report["issues"] if item["issue_code"] == "UNIT_SEMANTICS_AMBIGUOUS"]
    assert not mismatches
    assert {item["evidence"]["label"] for item in ambiguous} == {"Número de unidades", "Tamaño"}
    assert len(ambiguous) == 2


def test_source_audit_keeps_explicit_power_and_voltage_unit_mismatches_blocking():
    report = audit_source_fields([_row(attributes=[
        {"label_raw": "Potencia", "value_raw": "10 ml"},
        {"label_raw": "Voltaje", "value_raw": "2 kg"},
    ])])
    assert len([item for item in report["issues"] if item["issue_code"] == "SPEC_UNIT_TYPE_MISMATCH"]) == 2


def test_source_audit_keeps_visible_repetition_and_selects_as_product_evidence():
    report = audit_source_fields([_row(
        asin="B0CNQ4DJRV",
        attributes=[
            {"label_raw": "Idioma", "value_raw": "Inglés"},
            {"label_raw": "Idioma", "value_raw": "Inglés"},
        ],
        product_details_es="Idioma: Inglés; Idioma: Inglés",
        feature_bullets_es="Selecciona automáticamente el modo adecuado para cada uso.",
        selected_variation_raw="CoreBlack CoreBlack",
        title_es_raw="Black&Black bolsillo {dict} para accesorios",
    )])
    assert "MISPLACED" not in {item["issue_code"] for item in report["issues"]}


def test_source_audit_does_not_force_selected_variation_display_text_to_json():
    report = audit_source_fields([_row(
        asin="B07NGG8GTN", selected_variation_raw="['24 cm']",
    ), _row(
        asin="B0FJM3WXX6", selected_variation_raw="[Nuevo] 17-in-1",
    )])
    assert "MISPLACED" not in {item["issue_code"] for item in report["issues"]}


def test_source_audit_strict_text_findings_have_locators_and_brand_kg_is_not_spec():
    report = audit_source_fields([_row(
        asin="B0CRTYZG5C", brand="KG KITGARDEN", title_es_raw="Antes <script>alert(1)</script>",
    ), _row(
        asin="B0CFL41KG8", title_es_raw="Producto � dañado",
    ), _row(
        asin="B0CKXT51LV", title_es_raw="Producto ?? dañado",
    )])
    misplaced = [item for item in report["issues"] if item["issue_code"] == "MISPLACED"]
    assert len(misplaced) == 3
    assert all(item["field"] == "title_es_raw" for item in misplaced)
    assert {item["evidence"]["match_kind"] for item in misplaced} == {"html_script", "mojibake"}
    assert all(item["evidence"].get("offset") is not None and item["evidence"].get("snippet_hash") for item in misplaced)


def test_source_audit_recognizes_explicit_domain_unit_labels_and_battery_components():
    report = audit_source_fields([_row(
        title_es_raw="Ejercitador de mano y taladro 13 mm con ventilador 120 m³/h",
        attributes=[
            {"label_raw": "Caudal de aire", "value_raw": "120 m³/h"},
            {"label_raw": "Capacidad de perforación", "value_raw": "13 mm"},
            {"label_raw": "Tensión", "value_raw": "60 kg"},
            {"label_raw": "Capacidad", "value_raw": "250 cc"},
            {"label_raw": "Cantidad de pilas", "value_raw": "2 pilas, 1.5 V"},
        ],
    )])
    assert "SPEC_UNIT_TYPE_MISMATCH" not in {item["issue_code"] for item in report["issues"]}
    assert "SOURCE_SEMANTIC_CONFLICT" not in {item["issue_code"] for item in report["issues"]}


def test_source_audit_reviews_conflicting_label_value_semantics_without_rewriting_raw():
    report = audit_source_fields([_row(attributes=[
        {"label_raw": "Capacidad de la batería", "value_raw": "1.5 V"},
        {"label_raw": "Capacidad de peso", "value_raw": "68 L"},
        {"label_raw": "Volumen líquido", "value_raw": "7 kg"},
        {"label_raw": "Voltaje máximo", "value_raw": "30 W"},
    ])])
    conflicts = [item for item in report["issues"] if item["issue_code"] == "SOURCE_SEMANTIC_CONFLICT"]
    assert len(conflicts) == 4
    assert all(item["status"] == "REVIEW" and item["evidence"].get("source_hash") for item in conflicts)
    assert "SPEC_UNIT_TYPE_MISMATCH" not in {item["issue_code"] for item in report["issues"]}


def test_source_audit_explains_generic_size_only_when_title_or_variation_proves_measure():
    explained = audit_source_fields([_row(
        title_es_raw="Botella de viaje 500 ml", selected_variation_raw="500 ml",
        attributes=[{"label_raw": "Tamaño", "value_raw": "500 ml"}],
    )])
    unknown = audit_source_fields([_row(
        attributes=[{"label_raw": "Tamaño", "value_raw": "500 ml"}],
    )])
    explained_fields = [item for item in explained["field_audits"] if item["message"].startswith("generic label measure")]
    assert explained_fields and explained_fields[0]["classification"] == "PASS"
    assert "UNIT_SEMANTICS_AMBIGUOUS" in {item["issue_code"] for item in unknown["issues"]}
