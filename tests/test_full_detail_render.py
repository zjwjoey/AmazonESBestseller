# -*- coding: utf-8 -*-
"""translation/full_detail.py 测试：无损全量详情 → 西语原文 / 中文派生渲染。"""
from amazon_es_bestseller.collection.detail import parse_detail_page
from amazon_es_bestseller.translation.full_detail import (
    render_bullets_es, render_bullets_zh, render_details_es, render_details_zh)


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
