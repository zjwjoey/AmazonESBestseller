from amazon_es_bestseller.translation.protection import protect, restore
from amazon_es_bestseller.translation.validators import validate_translation


def test_protection_preserves_numbers_units_models_brand_and_asin():
    source = "Acme USB-C 500 ml 30×20×10 cm IPX7 B00000001"
    protected = protect(source, protected_values=["Acme", "B00000001"])
    restored, issues = restore(protected, "中文 " + protected.text)
    assert restored == "中文 " + source
    assert not issues


def test_missing_placeholder_is_qa_failure():
    protected = protect("500 ml USB-C")
    issues = validate_translation(protected, "仅中文", "500 ml USB-C")
    assert {x["code"] for x in issues} & {"PROTECTED_TOKEN_MISSING", "NUMERIC_MISMATCH"}


def test_literal_echo_of_protected_token_is_preserved():
    """A provider may echo a protected value instead of the placeholder."""
    protected = protect("Referencia OEM: 20002", protected_values=[])
    restored, issues = restore(protected, "OEM参考号：20002")
    assert restored == "OEM参考号：20002"
    assert not issues


def test_oem_and_model_identifiers_are_not_numericized():
    for source, translated in (
        ("Referencia OEM: 20002", "OEM参考号：20002"),
        ("Modelo: Cera Tec", "型号：Cera Tec"),
        ("Modelo: 26431", "型号：26431"),
    ):
        protected = protect(source)
        assert not validate_translation(protected, translated, source)


def test_model_value_and_legal_entity_are_protected_as_identity():
    model = protect("Modelo: Cera Tec")
    assert "Cera Tec" in model.tokens.values()
    company = protect("Energía Eléctrica Eficiente SL")
    assert "Energía Eléctrica Eficiente SL" in company.tokens.values()


def test_bare_numeric_tokens_are_protected_without_swallowing_units():
    protected = protect("Pack de 6, 500 ml")
    assert "6" in protected.tokens.values()
    assert "500 ml" in protected.tokens.values()


def test_composite_technical_version_is_protected_as_one_token():
    protected = protect("Conectada DGT 3.0")
    assert "DGT 3.0" in protected.tokens.values()
    issues = validate_translation(protected, "Conectada DGT 3", "Conectada DGT 3.0")
    assert any(item["code"] == "PROTECTED_TOKEN_MISSING" for item in issues)


def test_decimal_inch_value_is_protected_atomically():
    protected = protect("Pantalla 2.5 Inch LCD")
    assert "2.5 Inch" in protected.tokens.values()
    assert "2" not in protected.tokens.values()


def test_numeric_unit_does_not_swallow_hyphenated_spanish_term():
    protected = protect("Spray 400ml-Lubrica")
    assert "400ml" in protected.tokens.values()
    assert "400ml-Lubrica" not in protected.tokens.values()


def test_decibel_unit_is_protected_and_counted_as_a_unit():
    protected = protect("Alarma 110dB")
    assert "110dB" in protected.tokens.values()
    assert not validate_translation(protected, "报警 110dB", "Alarma 110dB")


def test_uppercase_spanish_bullet_headings_are_not_protected_as_models():
    protected = protect("PARA GUARDAR LOS CHUPETES. MATERIAL DE ALTA CALIDAD. LED H-GUARD")
    assert "PARA" not in protected.tokens.values()
    assert "MATERIAL" not in protected.tokens.values()
    assert "LED" in protected.tokens.values()
    assert "H-GUARD" in protected.tokens.values()
