from amazon_es_bestseller.translation.terminology import (
    deterministic_specification,
    contextual_postprocess,
    strip_display_brand,
    normalize_unit_display,
    postprocess,
    specification_is_deterministic,
)


def test_v2_reuses_deterministic_terms_without_touching_source():
    source = "Acero inoxidable 500 ml"
    assert postprocess("title_es_raw", source, source) == "不锈钢 500 毫升"


def test_brand_postprocess_is_identity_preserving():
    assert postprocess("brand", "翻译后的品牌", "HOVVIDA") == "HOVVIDA"


def test_restored_spanish_units_are_normalized_only_in_numeric_context():
    source = "电压：9 Voltios / 重量：495 Gramos / 尺寸：4,5 centímetros / 型号 Voltios"
    assert normalize_unit_display(source) == "电压：9V / 重量：495克 / 尺寸：4,5厘米 / 型号 Voltios"
    assert normalize_unit_display("流量：25 liters per minute") == "流量：25升/分钟"


def test_structured_numeric_specification_is_rendered_deterministically():
    source = ("Peso del producto: 490 Gramos / Dimensiones del producto: "
              "7,5l. x 4,5an. x 12,3al. centímetros / Voltaje: 220 Voltios")
    assert specification_is_deterministic(source)
    assert deterministic_specification(source) == (
        "产品重量：490克；产品尺寸：7.5×4.5×12.3厘米；电压：220V")


def test_structured_specification_uses_packaging_and_milligram_dictionaries():
    source = ("Capacidad: 750 ml / Peso Artículo: 750 Miligramos / "
              "Tamaño: 750 ml (Paquete de 1)")
    assert specification_is_deterministic(source)
    assert deterministic_specification(source) == (
        "容量：750毫升；商品重量：750毫克；规格：750毫升 (1件装)")

    size_source = ("Peso Artículo: 430 Gramos / Dimensiones del producto: "
                   "90l. x 60an. centímetros / Tamaño: XX-Large")
    assert specification_is_deterministic(size_source)
    assert deterministic_specification(size_source) == (
        "商品重量：430克；产品尺寸：90×60厘米；尺码：XXL")


def test_known_lubrica_residual_is_translated_deterministically():
    assert postprocess("title_es_raw", "喷雾 400ml-Lubrica", "") == "喷雾 400ml-润滑"


def test_dimension_detail_labels_are_deterministic():
    source = ("Peso del producto: 265 Gramos / Dimensiones del producto: "
              "21,1l. x 8,9an. x 21,1al. centímetros / Tamaño: "
              "21.1 x 8.9 x 4.8 cm / Cantidad de compartimentos: 2")
    assert specification_is_deterministic(source)
    result = deterministic_specification(source)
    assert "21.1×8.9×21.1厘米" in result
    assert "21.1×8.9×4.8厘米" in result
    assert "隔层数量：2" in result


def test_dimension_axis_markers_keep_thickness_and_translate_labels():
    source = ("Dimensiones del producto: 19,1l. x 7,2an. x 3,5Grosor centímetros / "
              "Número de productos: 2")
    assert deterministic_specification(source) == "产品尺寸：19.1×7.2×3.5厘米；产品数量：2"

    thin = "Dimensiones del producto: 120l. x 80an. x 0,5Grosor centímetros"
    assert deterministic_specification(thin) == "产品尺寸：120×80×0.5厘米"


def test_attribute_label_dictionary_is_used_for_variants():
    assert deterministic_specification(
        "Dimensiones Artículo: 8,6 x 8,6 x 5 centímetros") == "商品尺寸：8.6×8.6×5厘米"
    assert deterministic_specification("numero_de_piezas: 010106") == "件数：010106"


def test_contextual_automotive_terms_are_source_conditioned():
    assert contextual_postprocess(
        "title_es_raw", "汽车雨刮器 - 驱蚊型", "Líquido Limpiaparabrisas Coche Anti Mosquitos"
    ) == "汽车挡风玻璃清洗液 - 去除蚊虫污渍"
    assert contextual_postprocess(
        "title_es_raw", "汽车雨刮器", "Escobilla limpiaparabrisas Bosch"
    ) == "汽车雨刮器"
    assert contextual_postprocess(
        "title_es_raw", "蒸馏水", "Agua Desionizada para Baterías"
    ) == "去离子水"


def test_display_name_removes_explicit_brand_only():
    assert strip_display_brand("HOVVIDA 便携式空气压缩机 150PSI", "HOVVIDA") == \
        "便携式空气压缩机 150PSI"
    assert strip_display_brand("汽车启动电源 UTRAI 8000A", "UTRAI") == \
        "汽车启动电源 8000A"
    assert strip_display_brand("help flash IoT+，V16应急灯", "help flash IoT") == \
        "V16应急灯"
