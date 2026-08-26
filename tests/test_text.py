# -*- coding: utf-8 -*-
"""normalization/text.py 测试。"""
from amazon_es_bestseller.normalization.text import (
    dec_comma,
    strip_zero_width,
    collapse_ws,
    as_clean_str,
)


def test_dec_comma_replaces_decimal_comma():
    assert dec_comma("9,99") == "9.99"
    assert dec_comma("12,62") == "12.62"


def test_dec_comma_ignores_thousands_dot_and_non_numeric():
    assert dec_comma("1.500") == "1.500"
    assert dec_comma("abc,def") == "abc,def"


def test_dec_comma_multiple_commas():
    assert dec_comma("1,234,567") == "1.234.567"


def test_strip_zero_width_removes_invisible_chars():
    # QA_RULES §25：零宽/双向控制/BOM/不间断空格
    assert strip_zero_width("BIS​SELL") == "BISSELL"
    assert strip_zero_width("A‎B") == "AB"
    assert strip_zero_width("A‏B") == "AB"
    assert strip_zero_width("A﻿B") == "AB"
    assert strip_zero_width("A B") == "AB"
    assert strip_zero_width("A B") == "AB"


def test_collapse_ws_folds_and_trims():
    assert collapse_ws("  a\n b \t c  ") == "a b c"
    assert collapse_ws("") == ""


def test_as_clean_str():
    assert as_clean_str(None) == ""
    assert as_clean_str("  x ") == "x"
    assert as_clean_str(5) == "5"
