# -*- coding: utf-8 -*-
"""translation/zh.py 测试：合并词典 + 确定性翻译。"""
from amazon_es_bestseller.translation.zh import (
    TERMS,
    apply_terms,
    translate_value,
    spec_zh_from,
    summary_zh_from,
)


def test_terms_no_german_word():
    # D5：剔除德语 "Abnehmbar"（QA_RULES §36）
    assert "Abnehmbar" not in [es for es, _ in TERMS]


def test_terms_no_duplicate_es():
    assert len(TERMS) == len({es for es, _ in TERMS})


def test_terms_first_wins():
    # "Comida para llevar" 在 BASE=外卖食物 与 USO=外卖 冲突 → BASE 优先
    assert dict(TERMS)["Comida para llevar"] == "外卖食物"


def test_terms_contains_key_material():
    assert dict(TERMS)["Acero inoxidable"] == "不锈钢"
    assert dict(TERMS)["Reutilizable"] == "可重复使用"


def test_apply_terms():
    assert apply_terms("Acero inoxidable") == "不锈钢"
    assert apply_terms("Acero inoxidable y Vidrio") == "不锈钢 y 玻璃"


def test_translate_value_dimension_short_form():
    # D2/QA_RULES §40：简式，无 长/宽/高 标签
    assert translate_value("25,4 l. x 30 an. x 21 al. centímetros") == "25.4×30×21厘米"


def test_translate_value_capacity():
    assert translate_value("9 litros") == "9升"


def test_translate_value_material():
    assert translate_value("Acero inoxidable") == "不锈钢"


def test_translate_value_color():
    assert translate_value("Negro") == "黑色"


def test_translate_value_weight():
    assert translate_value("500 g") == "500克"


def test_translate_value_empty():
    assert translate_value("") == ""
    assert translate_value(None) == ""


def test_spec_zh_from():
    spec = "尺寸: 25,4 l. x 30 an. x 21 al. centímetros；容量: 9 litros；材质: Acero inoxidable"
    assert spec_zh_from(spec) == "尺寸：25.4×30×21厘米；容量：9升；材质：不锈钢"


def test_spec_zh_from_empty():
    assert spec_zh_from("") == ""
    assert spec_zh_from(None) == ""


def test_summary_zh_from_labels():
    summary = "Material: Acero inoxidable；Capacidad: 9 litros"
    assert summary_zh_from(summary) == "材质：不锈钢；容量：9升"


def test_summary_zh_from_last_feature_handcrafted():
    # 末尾卖点句（无标签前缀）优先用逐 ASIN 手译
    summary = "Material: Acero inoxidable；Almuerzo térmico para mantener comida"
    out = summary_zh_from(summary, asin="B071HSRTJN")
    assert "材质：不锈钢" in out
    assert "高效保温" in out


def test_summary_zh_from_last_feature_unknown_asin():
    # 未知 ASIN 回退确定性词典
    out = summary_zh_from("Material: Acero inoxidable；Almuerzo térmico", asin="B0INVALID")
    assert "材质：不锈钢" in out


def test_summary_zh_from_empty():
    assert summary_zh_from("") == ""
    assert summary_zh_from(None) == ""
