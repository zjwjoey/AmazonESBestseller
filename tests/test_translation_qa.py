import pytest

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
    canonical = qa_field(protect("Linea uno\nLinea dos"), "第一条",
                         "Linea uno\nLinea dos", field="feature_bullets")
    assert any(x["code"] == "BULLET_COUNT_MISMATCH" for x in canonical["issues"])


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
    compact = qa_field(protect("10,3 Bar"), "10,3bar", "10,3 Bar")
    assert compact["qa_status"] == "pass"
    assert qa_field(protect("2.5 Inch"), "2.5英寸", "2.5 Inch")["qa_status"] == "pass"


def test_unit_aliases_cover_amazon_spanish_and_chinese_variants():
    assert qa_field(protect("217 Gramos"), "217克", "217 Gramos")["qa_status"] == "pass"
    assert qa_field(protect("5000.0 Millilitros"), "5000.0毫升", "5000.0 Millilitros")["qa_status"] == "pass"
    assert qa_field(protect("9 Voltios"), "9伏特", "9 Voltios")["qa_status"] == "pass"
    assert qa_field(protect("15 vatios"), "15瓦特", "15 vatios")["qa_status"] == "pass"
    assert qa_field(protect("48l. x 32an. x 25al. centímetros"),
                    "48厘米×32厘米×25厘米", "48l. x 32an. x 25al. centímetros")["qa_status"] == "pass"
    assert qa_field(protect("9 Voltios"), "信标9伏特已包含", "9 Voltios")["qa_status"] == "pass"
    assert qa_field(protect("10x15cm"), "10×15厘米", "10x15cm")["qa_status"] == "pass"
    assert qa_field(protect("Visibilidad 1km"), "可视距离1公里", "Visibilidad 1km")["qa_status"] == "pass"
    assert qa_field(protect("4PCS"), "4件装", "4PCS")["qa_status"] == "pass"
    assert qa_field(protect("2 Unidades"), "2件", "2 Unidades")["qa_status"] == "pass"
    assert qa_field(protect("6.0 Conteo"), "6件", "6.0 Conteo")["qa_status"] == "pass"
    assert qa_field(protect("500 ml (Paquete de 1)"), "500ml（1件装）",
                    "500 ml (Paquete de 1)")["qa_status"] == "pass"
    assert qa_field(protect("Pack de 2 Unidades (XL)"), "2件装（XL）",
                    "Pack de 2 Unidades (XL)")["qa_status"] == "pass"
    assert qa_field(protect("64000LM"), "64000流明", "64000LM")["qa_status"] == "pass"
    assert qa_field(protect("1600 lúmenes"), "1600流明", "1600 lúmenes")["qa_status"] == "pass"
    assert qa_field(protect("2 Unidades"), "2包装", "2 Unidades")["qa_status"] == "pass"
    assert qa_field(protect("3,5Grosor centímetros"), "3.5厘米厚", "3,5Grosor centímetros")["qa_status"] == "pass"
    assert qa_field(protect("Pack x2"), "2个装", "Pack x2")["qa_status"] == "pass"


@pytest.mark.parametrize('source,target,expected',[
    ('Pack de 2 Unidades (XL)', '2\u4ef6\u88c5\uff08XL\uff09', 'pass'),
    ('Paquete de 2 piezas (XL)', '2\u4ef6\u88c5\uff08XL\uff09', 'pass'),
    ('Pack de 2 Unidades (XL)', '3\u4ef6\u88c5\uff08XL\uff09', 'qa_failed'),
    ('Pack de 2 Unidades (XL)', '2\u4ef6\u88c5', 'qa_failed'),
    ('Pack de 2 Unidades (XL)', '2\u4ef6\u88c5\uff08L\uff09', 'qa_failed'),
    ('Pack de 2 Unidades (XL)', '2\u6beb\u5347\uff08XL\uff09', 'qa_failed'),
    ('Pack de 2 Unidades (XL), 500 ml', '2\u4ef6\u88c5\uff08XL\uff09 250\u6beb\u5347', 'qa_failed'),
    ('Pack de 2 Unidades (XL), 500 ml', '2\u4ef6\u88c5\uff08XL\uff09 500\u514b', 'qa_failed'),
    ('Pack de 2 Unidades (XL), 2 Unidades', '2\u4ef6\u88c5\uff08XL\uff09', 'qa_failed'),
])
def test_noun_first_pack_counts_once_and_preserves_quantity_size_and_units(source,target,expected):
    assert qa_field(protect(source,protected_values=['XL']),target,source)['qa_status']==expected


def test_lowercase_spanish_preposition_is_not_ampere_unit():
    assert qa_field(protect("De 3 a 5 días"), "3至5天", "De 3 a 5 días")["qa_status"] == "pass"
    assert qa_field(protect("5 A"), "5安", "5 A")["qa_status"] == "pass"
    assert qa_field(protect("2 en 1"), "2合1安全带切割器", "2 en 1")["qa_status"] == "pass"


def test_compact_multipack_numbers_and_count_nouns_are_semantically_equivalent():
    assert qa_field(protect("Producto 3en1"), "产品3合1", "Producto 3en1")["qa_status"] == "pass"
    assert qa_field(protect("5 juguetes"), "5件玩具", "5 juguetes")["qa_status"] == "pass"
    assert qa_field(protect("160gr"), "160克", "160gr")["qa_status"] == "pass"


def test_compact_slash_sizes_and_hyphen_pack_keep_numeric_and_unit_facts():
    assert qa_field(protect("6/8/10/12mm8pcs"), "6/8/10/12\u6beb\u7c73 8\u4ef6\u88c5",
                    "6/8/10/12mm8pcs", field="specification_es")["qa_status"] == "pass"
    assert qa_field(protect("400 GR / 1-Pack"), "400\u514b/1\u5305\u88c5",
                    "400 GR / 1-Pack", field="specification_es")["qa_status"] == "pass"
    for source, target in (("9L", "25.4L"), ("30L", "20L"), ("10x15cm", "10x10mm")):
        assert qa_field(protect(source), target, source, field="specification_es")["qa_status"] == "qa_failed"


def test_qa_detects_reversed_or_missing_spanish_negation():
    result = qa_field(protect("Etiquetas sin laminado protector"),
                      "带保护性覆膜的标签", "Etiquetas sin laminado protector")
    assert any(issue["code"] == "NEGATION_MISMATCH" for issue in result["issues"])
    assert qa_field(protect("Sin alcohol"), "不含酒精", "Sin alcohol")["qa_status"] == "pass"


def test_common_spanish_connectivity_residual_is_detected():
    source = "Conectividad EN TIEMPO REAL, SIM INTEGRADA HASTA 2 años"
    result = qa_field(protect(source), "连接 EN TIEMPO REAL，SIM INTEGRADA HASTA 2年", source)
    issue = next(item for item in result["issues"] if item["code"] == "SPANISH_RESIDUAL")
    assert {"tiempo", "real", "integrada", "hasta"} <= set(issue["tokens"])


def test_spanish_month_to_chinese_date_number_is_allowed_but_hallucination_is_not():
    assert qa_field(protect("24 octubre 2025"), "2025年10月24日", "24 octubre 2025")["qa_status"] == "pass"
    result = qa_field(protect("16 noviembre 2007"), "2016年11月16日 2007", "16 noviembre 2007")
    assert any(issue["code"] == "ADDED_NUMBER" for issue in result["issues"])


def test_fidelity_qa_flags_drift_without_rewriting_candidate():
    protected = protect("Incluye LED 9V")
    result = qa_field(protected, "包含", "Incluye LED 9V")
    assert any(item["code"] == "PROTECTED_TOKEN_MISSING" for item in result["issues"])
    assert "技术参数" not in "包含"


def test_name_brand_removal_is_allowed_by_no_brand_policy():
    protected = protect("Chicco Toallitas para bebés", protected_values=["Chicco"])
    result = qa_field(protected, "婴儿湿巾", "Chicco Toallitas para bebés",
                      field="title_es_raw", brand="Chicco")
    assert result["qa_status"] == "pass"


def test_uv_abbreviation_accepts_explicit_chinese_equivalent():
    source = "Protección UV"
    assert qa_field(protect(source), "紫外线防护", source)["qa_status"] == "pass"


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


def test_extended_detail_label_dictionary_translates_common_amazon_labels():
    rendered = render_details_zh([
        {"label_raw": "Estilo", "value_raw": "Moderno"},
        {"label_raw": "Fuente de alimentación", "value_raw": "Batería"},
        {"label_raw": "Número de bombillas", "value_raw": "3"},
    ])
    assert "风格：" in rendered
    assert "电源：" in rendered
    assert "灯泡数量：3" in rendered


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
