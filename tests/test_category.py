# -*- coding: utf-8 -*-
"""normalization/category.py 测试：类目层级（QA_RULES §12-§14/§73）。"""
from amazon_es_bestseller.normalization.category import resolve_leaf_category, category_zh


def test_resolve_leaf_category_most_specific():
    segs = [('Hogar y cocina', '52'), ('Juegos de recipientes', '1')]
    assert resolve_leaf_category(segs) == 'Juegos de recipientes'


def test_resolve_leaf_category_single_segment_no_leaf():
    # 单段即主类目，不冒充 leaf（V2 语义）
    assert resolve_leaf_category([('Hogar y cocina', '52')]) is None


def test_resolve_leaf_category_empty():
    assert resolve_leaf_category([]) is None
    assert resolve_leaf_category(None) is None


def test_category_zh_known():
    assert category_zh('Hogar y cocina') == '家居与厨房'
    assert category_zh('Juegos de recipientes') == '收纳盒套装'


def test_category_zh_unknown_is_none():
    # 未知类目 → None，绝不复制 L3 充数
    assert category_zh('Categoría inventada') is None
    assert category_zh('') is None
    assert category_zh(None) is None
