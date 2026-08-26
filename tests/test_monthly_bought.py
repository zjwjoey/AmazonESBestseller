# -*- coding: utf-8 -*-
"""normalization/monthly_bought.py 测试（QA_RULES §22）：只取下限。"""
from amazon_es_bestseller.normalization.monthly_bought import parse_monthly_bought


def test_parse_monthly_bought_plain():
    assert parse_monthly_bought('100+') == 100


def test_parse_monthly_bought_mil():
    assert parse_monthly_bought('1 mil+') == 1000
    assert parse_monthly_bought('1,5 mil+') == 1500


def test_parse_monthly_bought_thousands_dot():
    assert parse_monthly_bought('1.500+') == 1500


def test_parse_monthly_bought_k():
    assert parse_monthly_bought('2k+') == 2000


def test_parse_monthly_bought_with_suffix_text():
    assert parse_monthly_bought('100+ comprados el mes pasado') == 100


def test_parse_monthly_bought_invalid():
    assert parse_monthly_bought('abc') is None
    assert parse_monthly_bought('') is None
    assert parse_monthly_bought(None) is None
