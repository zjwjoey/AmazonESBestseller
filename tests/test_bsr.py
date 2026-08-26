# -*- coding: utf-8 -*-
"""normalization/bsr.py 测试：榜单/详情 BSR 段解析。"""
from amazon_es_bestseller.normalization.bsr import (
    extract_bsr_segments,
    bsr_main_and_leaf,
    detail_bsr_segments,
)


def test_extract_bsr_segments_basic(sample_bsr_text):
    segs = extract_bsr_segments(sample_bsr_text)
    assert segs == [('Hogar y cocina', '52'), ('Juegos de recipientes', '1')]


def test_greedy_bug_fixed_not_single_chunk():
    # 原贪婪 bug：类目名吞掉整个 "Hogar y cocina (Ver el Top 100…)…"
    segs = extract_bsr_segments(
        "n.º 52 en Hogar y cocina (Ver el Top 100 en Hogar y cocina) "
        "nº1 en Juegos de recipientes"
    )
    assert len(segs) == 2
    assert segs[0] == ('Hogar y cocina', '52')


def test_extract_bsr_segments_strips_rank_thousands_sep():
    segs = extract_bsr_segments("n.º 1.500 en Hogar y cocina")
    assert segs == [('Hogar y cocina', '1500')]


def test_extract_bsr_segments_ver_tail_stripped():
    segs = extract_bsr_segments("nº1 en Juegos de recipientes (Ver los 100 más vendidos)")
    assert segs == [('Juegos de recipientes', '1')]


def test_extract_bsr_segments_empty():
    assert extract_bsr_segments(None) == []
    assert extract_bsr_segments("") == []
    assert extract_bsr_segments("sin contexto") == []


def test_bsr_main_and_leaf_v1_compat():
    segs = [('Hogar y cocina', '52'), ('Juegos de recipientes', '1')]
    assert bsr_main_and_leaf(segs) == ('Hogar y cocina', '52', 'Juegos de recipientes', '1')


def test_bsr_main_and_leaf_single_segment_no_leaf():
    assert bsr_main_and_leaf([('Hogar y cocina', '52')]) == ('Hogar y cocina', '52', None, None)


def test_bsr_main_and_leaf_empty():
    assert bsr_main_and_leaf([]) == (None, None, None, None)


def test_detail_bsr_segments_context():
    assert detail_bsr_segments("n.º 233 en Hogar y cocina") == [('Hogar y cocina', '233')]


def test_detail_bsr_segments_bare_number():
    # QA_RULES §72：详情页 BSR 裸数值（如 180285）必须可识别，供 QA 与榜单排名比对
    assert detail_bsr_segments("180285") == [('', '180285')]


def test_detail_bsr_segments_garbage():
    assert detail_bsr_segments("n/a") == []
    assert detail_bsr_segments(None) == []
