# -*- coding: utf-8 -*-
"""真实生产错误永久回归（QA_RULES §26：真实错误 → 永久离线 fixtures）。

每条都是真实采集数据中发现的错误，固定为离线 fixture，防止复发。
证据来源：product_details.json / _feat_scan.txt / _audit_details_keys.txt。
对应计划 Phase A（P0-1 商品类型 / P0-2 规格 / P0-3 排名-BSR / P0-4 品牌）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from amazon_es_bestseller.normalization.brand import is_brand_suspicious
from amazon_es_bestseller.normalization.specification import (
    build_spec_v2,
    dim_zh,
    resolve_package_count,
)
from amazon_es_bestseller.translation.product_type import detect_product_type

REPO = Path(__file__).resolve().parent.parent

#: (ASIN, 真实标题, 期望商品类型) —— P0-1 商品类型误判的 8 个真实案例
REAL_PRODUCT_TYPES = [
    ("B0DHGR3WSS",
     "Coronel Tapioca - Bolsa Termica Porta Alimentos - Nevera por", "保温包"),
    ("B0B56CHMSC",
     "Lifewit Bolsa Termica Porta Alimentos 9L(12 Latas), Bolso Co", "保温包"),
    ("B0CL169YC8",
     "b.box Mini Lunch Box para Niños | Fiambrera Infantil Bento c", "便当盒"),
    ("B081RXYR2Q",
     "LÄSSIG Fiambrera para niños de acero inoxidable Fiambrera pa", "便当盒"),
    ("B000CELRGU",
     "KRUPS Pack de 10 Pastillas de Limpieza para Cafeteras Automá", "除垢片"),
    ("B002X3IDBK",
     "Oregon Aceite Orgánico y Biodegradable para Lubricar Cadenas", "链条润滑油"),
    ("B0BM5L4DKK",
     "SEESII 6'' Mini Motosierra Bateria 8000mAh, Motosierra Eléct", "迷你电锯"),
    ("B0D3VCV459",
     "edihome, Papel Freidora Aire, Air Fryer, 100 Unidades, 20-24 cm, "
     "BPA Free, Desechable, para Horno, Accesorios para Freidora sin "
     "Aceite de 5 a 8 litros (20-24 cm, Cuadrado)", "一次性"),
]


@pytest.mark.parametrize("asin,title,expected", REAL_PRODUCT_TYPES,
                         ids=[t[0] for t in REAL_PRODUCT_TYPES])
def test_real_product_type_regression(asin, title, expected):
    """P0-1：真实 ASIN 标题的商品类型必须正确（不猜不错）。"""
    assert detect_product_type(title) == expected, "真实商品类型误判: %s" % asin


def test_real_product_type_mutex_cleaning_vs_portafilter():
    """互斥：含除垢片证据绝不判滤杯手柄（真实回归类）。"""
    assert detect_product_type("Desincrustantes para portafiltro") == "除垢片"


def test_real_spec_tamano_set4():
    """P0-2：tamano='Fiambrera - Set 4 Estándar' → 4件套，不被泛型 set=1 覆盖。"""
    out = build_spec_v2({
        'tamano': 'Fiambrera - Set 4 Estándar',
        'capacidad': '1 litros',
        'numero_de_sets': '1',
    })
    assert '4件套' in out
    assert '1件套' not in out
    assert resolve_package_count(
        {'tamano': 'Set 4 Estándar', 'numero_de_sets': '1'}) == 4


def test_real_spec_generic_set1_no_count():
    """P0-2：泛型 numero_de_sets=1 不显示件数。"""
    out = build_spec_v2({'capacidad': '1 litros', 'numero_de_sets': '1'})
    assert '件' not in out
    assert '1升' in out


def test_real_spec_ancho_x_alto_2d():
    """P0-2：10an. x 15al. centímetros → 10×15厘米。"""
    assert dim_zh('10an. x 15al. centímetros') == '10×15厘米'
    assert dim_zh('17l. x 3,2an. x 25,2al. centímetros') == '17×3.2×25.2厘米'


def test_real_brand_false_positive_title_words():
    """P0-4：真实标题首词中的普通西语名词不得当品牌。"""
    for b in ("Toallas", "Renovador", "Recambios", "Lote"):
        assert is_brand_suspicious(b) is True, b


def test_real_brand_dataset_clean():
    """P0-4：30 条真实商品记录的品牌全部通过合理性校验（不误伤真品牌）。"""
    p = REPO / "product_details.json"
    if not p.exists():
        pytest.skip("product_details.json 不在仓库")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data, "product_details.json 为空"
    for r in data:
        b = (r.get("Brand") or "").strip()
        assert is_brand_suspicious(b) is False, "真实品牌被误判: %r" % (b,)
