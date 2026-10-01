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
