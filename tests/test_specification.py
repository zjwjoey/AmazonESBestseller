# -*- coding: utf-8 -*-
"""normalization/specification.py 测试：规格构建 + 单位校验（QA_RULES §36-§45）。"""
from amazon_es_bestseller.normalization.specification import (
    cap_zh,
    dim_zh,
    is_suspicious_dimension,
    classify_value_unit,
    validate_spec_units,
    package_count,
    set_count,
    resolve_package_count,
    build_spec_v2,
)


# ---------- 回归：容量 ----------
def test_capacity_9l_not_25_4l():
    out = build_spec_v2({
        'capacidad': '9 litros',
        'dimensiones_del_producto': '25,4 x 30 x 21 cm',
    })
    assert '9升' in out
    assert '25.4升' not in out  # 25.4 是尺寸，绝不能变成容量


def test_capacity_30l_variant_beats_technical():
    out = build_spec_v2({'capacidad': '20 litros'}, variant='30L')
    assert '30升' in out
    assert '20升' not in out


# ---------- 回归：尺寸 ----------
def test_dimension_10x15cm_not_10x10mm():
    dz = dim_zh('10 x 15 cm')
    assert dz == '10×15厘米'
    assert dz != '10×10毫米'


def test_dimension_l_an_al_full():
    assert dim_zh('25,4 l. x 30 an. x 21 al. centímetros') == '25.4×30×21厘米'


def test_dimension_3d_plain():
    assert dim_zh('10 x 15 x 2 cm') == '10×15×2厘米'


def test_dimension_unparseable():
    assert dim_zh('A4') is None
    assert dim_zh('') is None
    assert dim_zh(None) is None


# ---------- 占位尺寸 ----------
def test_suspicious_1x1x1_dimension():
    assert is_suspicious_dimension('1×1×1cm') is True
    assert is_suspicious_dimension('1 x 1 x 1 cm') is True
    assert is_suspicious_dimension('10×15×2cm') is False


def test_build_spec_skips_suspicious_dimension():
    out = build_spec_v2({'capacidad': '9 litros', 'dimensiones_del_producto': '1 x 1 x 1 cm'})
    assert '9升' in out
    assert '1×1×1' not in out


# ---------- cap_zh ----------
def test_cap_zh_forms():
    assert cap_zh('9 litros') == '9升'
    assert cap_zh('30L') == '30升'
    assert cap_zh('300 mililitros') == '300毫升'
    assert cap_zh('300 ml') == '300毫升'
    assert cap_zh('') == ''


# ---------- 件数（QA_RULES §37-§38） ----------
def test_package_count_takes_max():
    d = {
        'numero_de_articulos': '1',
        'cantidad_de_articulos_en_el_paquete': '4',
        'total_del_paquete_segun_la_medida_elegida_para_referenciar_precio': '1',
    }
    assert package_count(d) == '4'


def test_package_count_fraction():
    assert package_count({'numero_de_piezas': '3,5'}) == '3.50'


def test_package_count_none():
    assert package_count({}) is None


def test_set_count():
    assert set_count({'numero_de_sets': '4'}) == 4
    assert set_count({'numero_de_sets': '2 juegos'}) == 2
    assert set_count({}) is None


def test_quantity_title_beats_generic_1():
    d = {'total_del_paquete_segun_la_medida_elegida_para_referenciar_precio': '1'}
    assert resolve_package_count(d, title_es='Fiambrera de cristal 4 piezas') == 4


def test_quantity_variant_priority():
    d = {'numero_de_articulos': '2'}
    assert resolve_package_count(d, variant='4 unidades', title_es='3 piezas') == 4
    assert resolve_package_count(d, title_es='Fiambrera 3 piezas') == 3


def test_quantity_generic_1_no_display():
    assert resolve_package_count({'total_del_paquete_segun_la_medida_elegida_para_referenciar_precio': '1'}) is None


def test_quantity_variant_volume_not_count():
    # 变体 30L 是容量，不应从变体文本产生虚假件数；技术件数仍生效
    assert resolve_package_count({'numero_de_articulos': '4'}, variant='30L') == 4


# ---------- 单位类别（QA_RULES §41-§42） ----------
def test_classify_value_unit():
    assert classify_value_unit('30cm') == 'dimension'
    assert classify_value_unit('992g') == 'weight'
    assert classify_value_unit('9 litros') == 'capacity'
    assert classify_value_unit('30L') == 'capacity'
    assert classify_value_unit('300 ml') == 'capacity'
    assert classify_value_unit('10 x 15 cm') == 'dimension'
    assert classify_value_unit('25,4 l. x 30 an. x 21 al. centímetros') == 'dimension'
    assert classify_value_unit('xyz') is None


def test_validate_spec_units():
    assert validate_spec_units('capacity', '30cm') is False   # cm 不能进容量
    assert validate_spec_units('capacity', '992g') is False   # g 不能进容量
    assert validate_spec_units('capacity', '9 litros') is True
    assert validate_spec_units('dimension', '10 x 15 cm') is True
    assert validate_spec_units('capacity', 'misterio') is True  # 无法判断 → 通过


def test_build_spec_skips_unit_mismatch():
    assert build_spec_v2({'capacidad': '30cm'}) == ''
    assert build_spec_v2({'capacidad': '992g'}) == ''


# ---------- 真实边缘（_audit_details_keys.txt 锚点） ----------
def test_real_tamano_set_4_counts():
    # tamano="Fiambrera - Set 4 Estándar" → 4件套（真实 "Set N" 写法，无 "de"）
    out = build_spec_v2({
        'tamano': 'Fiambrera - Set 4 Estándar',
        'capacidad': '1 litros',
        'numero_de_sets': '1',
    })
    assert '4件套' in out
    assert '1件套' not in out


def test_real_tamano_SET_4_package():
    assert build_spec_v2({'tamano': 'SET 4 PORTAEMBUTIDOS FRESH'}) == '4件套'


def test_real_generic_set_1_no_count():
    # numero_de_sets=1 是泛型数量，不显示件数；容量 1升 仍显示
    out = build_spec_v2({'capacidad': '1 litros', 'numero_de_sets': '1'})
    assert '1升' in out
    assert '件' not in out


def test_real_ancho_x_alto_2d():
    assert dim_zh('10an. x 15al. centímetros') == '10×15厘米'


def test_real_l_an_al_comma_decimals():
    # 17l. x 3,2an. x 25,2al. → 逗号小数 + 单位省略写法
    assert dim_zh('17l. x 3,2an. x 25,2al. centímetros') == '17×3.2×25.2厘米'


def test_real_capacidad_de_salida():
    assert cap_zh('354,88 ml') == '354.88毫升'


def test_real_tamano_not_overridden_by_generic_set_1():
    # 泛型 numero_de_sets=1 不得覆盖 tamano 显式 "Set 4" 证据
    assert resolve_package_count(
        {'tamano': 'Set 4 Estándar', 'numero_de_sets': '1'}) == 4


# ---------- 组合 ----------
def test_build_spec_v2_full(sample_detail_dict):
    out = build_spec_v2(sample_detail_dict)
    assert '4件套' in out
    assert '30升' in out
    assert '10×15×5厘米' in out


def test_build_spec_v2_empty():
    assert build_spec_v2({}) == ''
    assert build_spec_v2(None) == ''
