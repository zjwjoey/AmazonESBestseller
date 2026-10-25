import copy
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.translation.preclean import audit_records, numeric_profile


@pytest.mark.parametrize('case', json.loads((Path(__file__).parent /
                         'fixtures/preclean_capacity_scenarios.json').read_text(encoding='utf-8')),
                         ids=lambda case: case['asin'] + ':' + case['field'])
def test_saved_quantity_scenarios_preserve_source_and_bind_every_amount(case):
    source = {'asin': case['asin'], case['field']: case['source_text']}
    before = copy.deepcopy(source)
    assert len(case['source_evidence']['sha256']) == 64
    assert case['evidence_dom']
    field = audit_records([source])['translation_input_records'][0]['fields'][case['field']]
    assert field['translate_allowed']
    assert field['source_text'] == case['source_text']
    assert source == before


@pytest.mark.parametrize('text', [
    'Capacidad de 1,7 L. Hierve una taza de 235 ml. Capacidad de 1 L.',
    'Capacidad de 1,7 L. Capacidad de 1 L. Hierve 0,5 litros de agua en 90 segundos.',
    'Capacidad de 0,3 L. Hierve 0,5 litros de agua en 90 segundos.',
    'Capacidad de 1,7 L; 7 tazas de 250 ml.',
    'Capacidad de 1 L; tazas de 250 ml y 600 ml.',
    'Bolsa de 3 L con capacidad para 12 latas de bebidas de 330 ml.',
    'Capacidad de 9,5 L. Hornos de 68 L y 71 L. Capacidad real de esta freidora: 68 L.',
    'Capacidad de 9,5 L. Prueba frente a hornos de 68 L de clase A y 71 L de clase A+. Capacidad de 68 L.',
    'Capacidad de 500 ml; vasos de 110 ml y 240 ml.',
    'Capacidad de 500 ml; compatibles de 110 ml y 240 ml.',
    'Capacidad de 500 ml. Compatibles con los tamaños de 4L y 15L de la familia XYZ. Capacidad de 600 ml.',
    'Capacidad total de 2,4 L para 2,8 L de agua filtrada.',
    'Capacidad total de 2,4 L para 1,4 L de agua filtrada. Capacidad total de 1,8 L.',
    'Capacidad: 1 litro de agua y 1,2 L de té.',
    'Capacidad: 1 litro de agua y 0,75 L de té; capacidad de 500 ml.',
    'Capacidad: 1 litro de agua y 0,75 L de té; capacidad de agua 2 L.',
    'Contenido: 1 rollo - 0,75 L; 2 rollos - 1 L.',
    'Diluir en agua: 1 rollo - 0,75 L; 2 rollos - 1 L; contenido de 3 L.',
    'Capacidad de 1 L. Una sola carga completa permite preparar 50 tazas (60 ml) o 40 tazas (80 ml). Capacidad de 2 L.',
    'Capacidad del recipiente principal de 100 ml. Receta: para 50 g de copos de avena, 120 ml de leche y toppings.',
    'Capacidad de 240 ml + 120 ml. Sistema de doble capa.',
    'Capacidad de 0,3 L. Hervir un vaso de agua (500 ml) en comparación a 1 L.',
    'Capacidad de 0,3 L. 1 taza = 500 ml frente a 1 L.',
    'Capacidad de 500 ml; 0 tazas de 1000 ml.',
    'Capacidad de 0,3 L. Una sola carga completa permite preparar 50 tazas (500 ml) o 40 tazas (800 ml).',
])
def test_unbound_quantities_or_real_contradictions_still_block(text):
    assert 'CONTRADICTORY_CAPACITY' in numeric_profile(text)['issues']


def test_compact_count_times_volume_is_not_lost_by_numeric_boundary():
    result = numeric_profile('Capacidad total de 100 L; 2x50L')
    assert [(v['numeric_value'], v['canonical_unit']) for v in result['values']] == [(100, 'L'), (50, 'L')]
    assert not result['issues']


@pytest.mark.parametrize('text', [
    'Capacidad total de 100 L; 3x50L',
    'Capacidad total de 100 L; 2x50L; capacidad de 75 L',
    'Capacidad total de 650 ml; 2x300ml',
    'Capacidad total de 50 L; 2x50L',
    '3 X 8L Total 8 Litros',
    'Capacidad total de 1000 ml; 3X320ml',
])
def test_compact_count_does_not_hide_an_inconsistent_total(text):
    assert 'CONTRADICTORY_CAPACITY' in numeric_profile(text)['issues']


def test_model_codes_are_not_compact_volume_counts():
    result = numeric_profile('Modelo AX320ml con capacidad de 1 L')
    assert [(v['numeric_value'], v['canonical_unit']) for v in result['values']] == [(1, 'L')]


@pytest.mark.parametrize('count', ['0x', '0X', '0 x ', '03x', '0,5x', '0.5x', '1,5X'])
def test_zero_or_ambiguous_leading_zero_multiplier_is_not_ignored(count):
    text = 'Capacidad total de 960 ml; ' + count + '320ml'
    result = numeric_profile(text)
    assert 'INVALID_VOLUME_MULTIPLIER' in result['issues']
    record = audit_records([{'asin': 'B000000001', 'feature_bullets': text}])['translation_input_records'][0]
    assert not record['fields']['feature_bullets']['translate_allowed']


def test_cubic_centimetre_volume_preserves_raw_unit_and_exact_ml_value():
    profile = numeric_profile('Capacidad: 100 cc / 100 ml')
    assert [(v['numeric_value'],v['unit_raw'],v['canonical_unit']) for v in profile['values']] == [
        (100,'cc','ml'),(100,'ml','ml')]
    assert not profile['issues']


def test_conflicting_cc_ml_values_do_not_disappear_as_an_unknown_unit():
    assert 'CONTRADICTORY_CAPACITY' in numeric_profile('Capacidad: 100 cc / 150 ml')['issues']
