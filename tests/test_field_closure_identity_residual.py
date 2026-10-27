from amazon_es_bestseller.qa.field_closure import (
    TRANSLATION_INCOMPLETE,
    _translation_residual,
    audit_field_closure,
)


COMPANY = "ADI para el hogar y la hosteleria iberia SL"


def _company_record(asin, *, source, target, manufacturer=COMPANY):
    return {
        "asin": asin,
        "brand_raw": "Luminarc",
        "manufacturer_raw": manufacturer,
        "product_details_es": source,
        "product_details_zh": target,
    }


def test_real_manufacturer_legal_name_bound_to_source_is_not_translation_residual():
    source = f"Marca: Luminarc\nMaterial: Vidrio\nFabricante: {COMPANY}"
    target = f"\u54c1\u724c\uff1aLuminarc\n\u6750\u8d28\uff1a\u73bb\u7483\n\u5236\u9020\u5546\uff1a{COMPANY}"

    report = audit_field_closure([_company_record("B0HGY9QB59", source=source, target=target)])

    assert not [r for r in report["records"]
                if r["field"] == "product_details_zh"
                and r["classification"] == TRANSLATION_INCOMPLETE]


def test_identity_value_absent_from_this_source_cannot_be_exempted():
    source = "Marca: Luminarc\nMaterial: Vidrio\nFabricante: Otro fabricante"
    target = f"\u54c1\u724c\uff1aLuminarc\n\u6750\u8d28\uff1a\u73bb\u7483\n\u5236\u9020\u5546\uff1a{COMPANY}"

    assert _translation_residual(source, target, identity_values=[COMPANY])


def test_ordinary_untranslated_span_still_triggers_translation_residual():
    source = "Material: Vidrio"
    target = "\u989c\u8272\uff1a\u84dd\u8272\nEste producto es apto para uso diario en casa"

    assert _translation_residual(source, target, identity_values=[COMPANY])


def test_identity_value_from_another_asin_does_not_hide_residual():
    first = _company_record(
        "B0HGY9QB59",
        source=f"Marca: Luminarc\nFabricante: {COMPANY}",
        target=f"\u54c1\u724c\uff1aLuminarc\n\u5236\u9020\u5546\uff1a{COMPANY}",
    )
    second = _company_record(
        "B0HGYCLYPM",
        source="Marca: Luminarc\nFabricante: Otro fabricante",
        target=f"\u54c1\u724c\uff1aLuminarc\n\u5236\u9020\u5546\uff1a{COMPANY}",
    )

    report = audit_field_closure([first, second])

    assert not [r for r in report["records"]
                if r["asin"] == first["asin"]
                and r["field"] == "product_details_zh"
                and r["classification"] == TRANSLATION_INCOMPLETE]
    assert [r for r in report["records"]
            if r["asin"] == second["asin"]
            and r["field"] == "product_details_zh"
            and r["classification"] == TRANSLATION_INCOMPLETE]
