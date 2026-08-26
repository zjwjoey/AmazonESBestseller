# -*- coding: utf-8 -*-
"""normalization/price.py 测试（价格定义已冻结：QA_RULES §16-§18）。"""
from amazon_es_bestseller.normalization.price import parse_price, discount_rate, CURRENCY


def test_parse_price_spanish_euro():
    assert parse_price("12,62 €") == 12.62
    assert parse_price(" 12,62 EUR ") == 12.62
    assert parse_price("9,99") == 9.99
    assert parse_price("12.5") == 12.5


def test_parse_price_must_be_positive():
    assert parse_price("0,00") is None
    assert parse_price("-5") is None


def test_parse_price_invalid():
    assert parse_price(None) is None
    assert parse_price("") is None
    assert parse_price("abc") is None


def test_discount_rate_only_when_original_gt_current():
    assert discount_rate(9.99, 12.99) == 0.2309
    assert discount_rate("9,99", "12,99") == 0.2309


def test_discount_rate_guarded():
    assert discount_rate(12.99, 9.99) is None  # original < current
    assert discount_rate(10, 10) is None       # 持平不叫折扣
    assert discount_rate(None, 12.99) is None
    assert discount_rate(9.99, None) is None


def test_currency_constant():
    assert CURRENCY == "EUR"
