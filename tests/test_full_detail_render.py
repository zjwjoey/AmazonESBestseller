# -*- coding: utf-8 -*-
"""translation/full_detail.py 测试：无损全量详情 → 西语原文 / 中文派生渲染。"""
from amazon_es_bestseller.collection.detail import parse_detail_page
from amazon_es_bestseller.translation.full_detail import (
    detail_bullets_to_attributes, render_bullets_es, render_bullets_zh,
    render_details_es, render_details_zh)


def test_render_details_es_from_fixture(delonghi_html):
    d = parse_detail_page(delonghi_html, "B008YETL18")
    es = render_details_es(d["attributes"])
    assert "Marca: De'Longhi" in es
    assert "Capacidad: 500 mililitros" in es
    assert "Aroma: Limón" in es
    # 元信息（ASIN）剔除；西语原文不翻译
    assert "ASIN" not in es


def test_render_details_zh_from_fixture(delonghi_html):
    d = parse_detail_page(delonghi_html, "B008YETL18")
    zh = render_details_zh(d["attributes"])
    assert "品牌：De'Longhi" in zh
    assert "容量：500毫升" in zh
    assert "香型：柠檬" in zh or "香型：Limón" in zh   # 值走词典，未知词保留西语


def test_render_details_zh_unknown_label_keeps_spanish():
    attrs = [{"section": "technical_details", "label_raw": "LabelInventadoNuevo",
              "value_raw": "Valor inventado", "position": 0, "source": "prodDetails"}]
    # 未知标签 → 保留西语原文，不臆造中文
    assert render_details_zh(attrs) == "LabelInventadoNuevo：Valor inventado"


def test_render_details_dedup_identical_rows():
    attrs = [
        {"section": "product_overview", "label_raw": "Marca", "value_raw": "De'Longhi",
         "position": 0, "source": "productOverview"},
        {"section": "technical_details", "label_raw": "Marca", "value_raw": "De'Longhi",
         "position": 0, "source": "prodDetails"},
        {"section": "technical_details", "label_raw": "Capacidad", "value_raw": "500 ml",
         "position": 1, "source": "prodDetails"},
    ]
    zh = render_details_zh(attrs)
    assert zh.count("品牌：De'Longhi") == 1          # 完全重复去重
    assert "容量：500毫升" in zh                      # 不同值保留


def test_render_details_same_label_keeps_longest():
    """同标签 overview 截断值 + technical 完整值 → 取最长（… Ver más 不出现）。"""
    attrs = [
        {"section": "product_overview", "label_raw": "Características especiales",
         "value_raw": "Impermeable, Lavable, Transpirable… Ver más",
         "position": 0, "source": "productOverview"},
        {"section": "technical_details", "label_raw": "Características especiales",
         "value_raw": "Impermeable, Lavable, Transpirable, Suave",
         "position": 0, "source": "prodDetails"},
    ]
    zh = render_details_zh(attrs)
    es = render_details_es(attrs)
    assert "… Ver más" not in zh and "… Ver más" not in es
    assert "防水, Lavable, 透气, Suave" in zh       # translate_value 生效
    assert zh.count("特殊功能") == 1
    assert "Impermeable, Lavable, Transpirable, Suave" in es   # 西语原文取完整值
    assert es.count("Características especiales") == 1


def test_render_details_zh_cleans_amazon_display_artifacts():
    attrs = [
        {"label_raw": "Características especiales", "value_raw": "Impermeable… Ver más"},
        {"label_raw": "Volumen del producto", "value_raw": "10 Modificador desconocido"},
        {"label_raw": "Número de unidades", "value_raw": "10.0 Conteo"},
        {"label_raw": "Garantía producto", "value_raw": "Actualizaciones de software garantizadas hasta: desconocido"},
        {"label_raw": "Número modelo", "value_raw": "Voir descriptif"},
    ]
    zh = render_details_zh(attrs)
    assert "查看更多" not in zh
    assert "未知修饰符" not in zh
    assert "软件更新保证至：未知" not in zh
    assert "Voir descriptif" not in zh
    assert "10件" in zh


def test_render_bullets_zh_cleans_truncation_and_count_artifacts():
    zh = render_bullets_zh(["Incluye 10.0 Conteo de piezas… Ver más"])
    assert "查看更多" not in zh
    assert "Ver más" not in zh
    assert "10件" in zh


def test_detail_bullets_can_supply_structured_details_when_tables_are_absent():
    attrs = detail_bullets_to_attributes([
        "Dimensiones del paquete ‏ : ‎ 9,4 x 8,9 x 5,5 cm; 290 g",
        "Número de modelo del producto ‏ : ‎ B2-20231106MZQ-FBA",
        "Actualizaciones de software garantizadas hasta ‏ : ‎ desconocido",
        "Opiniones de los clientes: 4,5 de 5 estrellas (270)",
    ])
    zh = render_details_zh(attrs)
    assert attrs[0]["label_raw"] == "Dimensiones del paquete"
    assert "B2-20231106MZQ-FBA" in zh
    assert "软件更新保证至" not in zh
    assert "Opiniones de los clientes" not in zh


def test_render_details_zh_merges_mapped_duplicates():
    """两个西语标签映射同一中文标签且值相同 → 中文层去重；西语原文层各保留。"""
    attrs = [
        {"section": "technical_details", "label_raw": "Función especial",
         "value_raw": "Elástico, Impermeable, Lavable", "position": 0, "source": "prodDetails"},
        {"section": "technical_details", "label_raw": "Características especiales",
         "value_raw": "Elástico, Impermeable, Lavable", "position": 1, "source": "prodDetails"},
    ]
    zh = render_details_zh(attrs)
    es = render_details_es(attrs)
    assert zh.count("特殊功能") == 1              # 中文只显示一行
    assert es.count("Función especial") == 1     # 西语证据层两行都在
    assert es.count("Características especiales") == 1


def test_render_bullets_es_raw(delonghi_html):
    d = parse_detail_page(delonghi_html, "B008YETL18")
    es = render_bullets_es(d["feature_bullets_raw"])
    assert "SOLUCIÓN SUAVE DE DESCALCIFICACIÓN" in es
    assert len(es.split("\n")) == 3


def test_render_bullets_zh_keyword_translation():
    bullets = ["Descalcificador para cafeteras", "Uso universal para todo tipo de café"]
    zh = render_bullets_zh(bullets)
    assert "除垢" in zh                               # 词典词被翻译
    assert "para cafeteras" in zh                     # 未覆盖词保留西语原文（不臆造）
    assert len(zh.split("\n")) == 2


def test_render_empty_inputs():
    assert render_details_es(None) == ""
    assert render_details_zh([]) == ""
    assert render_bullets_es(None) == ""
    assert render_bullets_zh([]) == ""


def test_display_rows_strip_amazon_bidi_marks():
    """真实回归（文具/宠物 100 SKU 实采）：Amazon 在属性值前插入 LEFT-TO-RIGHT
    MARK (\u200e)。数据层保留原文，展示层绝不能把不可见字符写进 Excel。
    """
    attributes = [
        {"section": "technical_details", "label_raw": "Marca",
         "value_raw": "‎BIC", "position": 0, "source": "prodDetails"},
        {"section": "technical_details", "label_raw": "Color",
         "value_raw": "‎Azul", "position": 1, "source": "prodDetails"},
    ]
    es = render_details_es(attributes)
    zh = render_details_zh(attributes)
    assert "‎" not in es
    assert "‎" not in zh
    assert "Marca: BIC" in es
    # 数据层不被修改（无损原始证据）
    assert attributes[0]["value_raw"] == "‎BIC"


def test_collapsed_ver_mas_value_is_restored_not_stripped():
    """真实回归 B00EOOQD0O 等 5 处：Amazon 把同一段文字渲染两遍（展开版 +
    折叠版），末尾跟 UI 按钮文字 "Ver más"。

    完整文本其实就在里面，正确做法是去掉重复和按钮文字后还原完整版，
    而不是简单删掉 "Ver más" 冒充完整，也不是整条丢弃。
    """
    text = "Alimente a sus peces dos veces al día."
    attributes = [{"section": "product_overview", "label_raw": "Usos específicos del producto",
                   "value_raw": "%s %s Ver más" % (text, text), "position": 0,
                   "source": "productOverview"}]
    es = render_details_es(attributes)
    assert "Ver más" not in es
    assert es.count(text) == 1
    assert text in es


def test_genuinely_truncated_value_is_reported_not_faked():
    """只有截断版本时不得假装完整：保留可见文本，但显式标记为截断。"""
    from amazon_es_bestseller.translation.full_detail import truncated_detail_labels
    attributes = [{"section": "product_overview", "label_raw": "Destacado del artículo",
                   "value_raw": "Texto largo que Amazon cortó aquí Ver más", "position": 0,
                   "source": "productOverview"}]
    assert truncated_detail_labels(attributes) == ["Destacado del artículo"]
    # 可还原的重复形态不算截断
    ok = [{"section": "product_overview", "label_raw": "X",
           "value_raw": "abc def abc def Ver más", "position": 0, "source": "productOverview"}]
    assert truncated_detail_labels(ok) == []


def test_placeholder_values_are_dropped_from_display():
    """真实回归（49/100）：Amazon 的 "Actualizaciones de software garantizadas
    hasta: desconocido" 是零信息占位，不应出现在完整商品详情里。数据层保留。"""
    attributes = [
        {"section": "additional_information",
         "label_raw": "Actualizaciones de software garantizadas hasta",
         "value_raw": "desconocido", "position": 0, "source": "detailBullets"},
        {"section": "additional_information", "label_raw": "Rango de edad (descripción)",
         "value_raw": "No aplicable", "position": 1, "source": "detailBullets"},
        {"section": "additional_information", "label_raw": "Marca",
         "value_raw": "Tetra", "position": 2, "source": "detailBullets"},
    ]
    es = render_details_es(attributes)
    assert "Actualizaciones de software" not in es
    assert "No aplicable" not in es
    assert "Marca: Tetra" in es
    # 数据层无损
    assert attributes[0]["value_raw"] == "desconocido"
