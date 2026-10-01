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
