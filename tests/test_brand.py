# -*- coding: utf-8 -*-
"""normalization/brand.py 测试。"""
from amazon_es_bestseller.normalization.brand import clean_brand, normalize_brand_case


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
