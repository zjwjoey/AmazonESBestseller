from amazon_es_bestseller.translation.dictionary_service import (
    DictionaryService, PACKAGE_DICTIONARIES, is_identity_attribute, resolve_exact,
)
from amazon_es_bestseller.translation.full_detail import render_details_zh
from amazon_es_bestseller.translation.protection import protect
from amazon_es_bestseller.translation.zh import translate_value


def test_dictionary_service_uses_one_field_aware_lookup_surface():
    service = DictionaryService()
    assert service.lookup_category("Coche y moto") == "汽车与摩托车用品"
    assert service.lookup_attribute_label("Referencia OEM") == "OEM参考号"
    assert service.lookup_unit("Kilogramos") == "千克"
    assert service.lookup_value("true") == "是"
    assert service.lookup_value("Unidad", field="selected_variant_es") == "单件"
    assert resolve_exact(service, "Unidad", kind="packaging")["resolution_source"] == "dictionary"


def test_identity_values_are_not_translated_as_materials():
    rendered = render_details_zh([
        {"label_raw": "Marca", "value_raw": "Metal"},
        {"label_raw": "Fabricante", "value_raw": "Silicona S.L."},
    ])
    assert "品牌：Metal" in rendered
    assert "制造商：Silicona S.L." in rendered


def test_unidad_and_technical_tokens_keep_source_facts():
    assert translate_value("Unidad") == "单件"
    assert "1" not in translate_value("Unidad")
    assert list(protect("5W-30").tokens.values()) == ["5W-30"]
    assert list(protect("5200 mAh").tokens.values()) == ["5200 mAh"]


def test_split_dictionaries_are_present_in_source_package():
    expected = {"categories", "attribute_labels", "units", "materials", "colors",
                "booleans", "packaging", "protected_terms"}
    assert expected == {path.stem for path in PACKAGE_DICTIONARIES.glob("*.json")}


def test_identity_attribute_aliases_are_normalized_once():
    for label in (
        "Marca", "Fabricante", "Modelo", "Nombre del modelo", "Número de modelo",
        "Número de modelo del producto", "Referencia", "Referencia OEM",
        "Referencia del fabricante", "Número pieza", "Número de pieza",
        "Número de pieza del fabricante", "Part Number", "OEM", "UPC", "EAN",
        "ASIN", "ISBN", "Núm. de modelo", "Part-Number",
    ):
        assert is_identity_attribute(label), label
