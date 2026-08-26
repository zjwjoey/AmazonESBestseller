# -*- coding: utf-8 -*-
"""normalization/category.py 测试：类目层级（QA_RULES §6/§12-§14/§73）。

主源 = 榜单节点路径（category_levels）；详情 BSR 段（resolve_leaf_category）为次源。
"""
from amazon_es_bestseller.normalization.category import (
    category_levels,
    category_zh,
    resolve_leaf_category,
)


def test_resolve_leaf_category_most_specific():
    segs = [('Hogar y cocina', '52'), ('Juegos de recipientes', '1')]
    assert resolve_leaf_category(segs) == 'Juegos de recipientes'


def test_resolve_leaf_category_single_segment_no_leaf():
    # 单段即主类目，不冒充 leaf（V2 语义）
    assert resolve_leaf_category([('Hogar y cocina', '52')]) is None


def test_resolve_leaf_category_empty():
    assert resolve_leaf_category([]) is None
    assert resolve_leaf_category(None) is None


def test_category_levels_three_level_trail():
    # B1 主源：面包屑 3 级 → L1/L2/L3 齐，leaf==L3（定义恒等，非复制充数）
    trail = ["Hogar y cocina", "Almacenamiento y organización", "Juegos de recipientes"]
    assert category_levels(trail) == (
        "Hogar y cocina", "Almacenamiento y organización",
        "Juegos de recipientes", "Juegos de recipientes")


def test_category_levels_two_level_trail():
    assert category_levels(["Hogar y cocina", "Juegos de recipientes"]) == (
        "Hogar y cocina", "Juegos de recipientes", None, "Juegos de recipientes")


def test_category_levels_single_level_no_leaf():
    # 单段即主类目，不冒充 leaf（V2 语义）
    assert category_levels(["Hogar y cocina"]) == (
        "Hogar y cocina", None, None, None)


def test_category_levels_empty_is_null():
    # QA_RULES §73：无证据 → 全 None
    assert category_levels([]) == (None, None, None, None)
    assert category_levels(None) == (None, None, None, None)


def test_category_levels_four_level_keeps_top3_plus_leaf():
    # 更深层级保持 null，leaf=最具体级
    trail = ["Hogar y cocina", "Almacenamiento y organización",
             "Recipientes y almacenamiento", "Juegos de recipientes"]
    assert category_levels(trail) == (
        "Hogar y cocina", "Almacenamiento y organización",
        "Recipientes y almacenamiento", "Juegos de recipientes")


def test_category_zh_known():
    assert category_zh('Hogar y cocina') == '家居与厨房'
    assert category_zh('Juegos de recipientes') == '收纳盒套装'


def test_category_zh_unknown_is_none():
    # 未知类目 → None，绝不复制 L3 充数
    assert category_zh('Categoría inventada') is None
    assert category_zh('') is None
    assert category_zh(None) is None
