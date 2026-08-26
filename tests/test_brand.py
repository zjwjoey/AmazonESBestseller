# -*- coding: utf-8 -*-
"""normalization/brand.py 测试。"""
from amazon_es_bestseller.normalization.brand import (
    clean_brand,
    is_brand_suspicious,
    normalize_brand_case,
)


def test_clean_brand_strips_visita_prefix():
    assert clean_brand("Visita la tienda de BISSELL") == "BISSELL"


def test_clean_brand_strips_marca_prefix():
    assert clean_brand("Marca: Tatay") == "Tatay"
    assert clean_brand("Marca:\tTatay") == "Tatay"


def test_clean_brand_strips_zero_width():
    assert clean_brand("BIS​SELL") == "BISSELL"


def test_clean_brand_chains_both_prefixes():
    assert clean_brand("Visita la tienda de Marca: X") == "X"


def test_clean_brand_empty():
    assert clean_brand("") == ""
    assert clean_brand(None) == ""


def test_clean_brand_does_not_infer_limpiador():
    # 品牌识别不做推断（QA_RULES §32）：非品牌词原样保留，由 QA 层拦截
    assert clean_brand("Limpiador ultrasónico") == "Limpiador ultrasónico"


def test_normalize_brand_case_canon_only():
    assert normalize_brand_case("bissell") == "BISSELL"
    assert normalize_brand_case("Bissell") == "BISSELL"
    assert normalize_brand_case("Tatay") == "Tatay"
    assert normalize_brand_case("") == ""


# ---------- 品牌合理性（QA_RULES §10：宁缺毋假） ----------
def test_is_brand_suspicious_single_words():
    # 真实标题首词证据 + 既有停用词，整词命中
    for b in ("Limpiador", "Toallas", "Renovador", "Recambios", "Lote",
              "Set", "Pack", "Bolsa", "Recipiente", "Universal"):
        assert is_brand_suspicious(b) is True, b


def test_is_brand_suspicious_phrase_with_stopword():
    # 短语内含停用词（整词）→ 可疑
    assert is_brand_suspicious("Juego de sábanas") is True
    assert is_brand_suspicious("Pack de limpieza 4 uds") is True


def test_is_brand_suspicious_long_phrase():
    # 超过 4 词的"品牌" → 标题片段误判
    assert is_brand_suspicious("Juego de sábanas de algodón egipcio") is True


def test_is_brand_suspicious_real_brands():
    # 真实品牌不得被误判
    for b in ("Tatay", "Utopia Bedding", "Amazon Basics", "Haberdashery Online",
              "VACTechPro", "Todocama", "Roca"):
        assert is_brand_suspicious(b) is False, b


def test_is_brand_suspicious_empty():
    assert is_brand_suspicious("") is False
    assert is_brand_suspicious(None) is False
