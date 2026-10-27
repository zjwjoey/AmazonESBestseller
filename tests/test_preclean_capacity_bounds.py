import copy
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.translation.preclean import audit_records, numeric_profile


FIXTURE = Path(__file__).parent / 'fixtures/preclean_capacity_bounds_saved_sources.json'


@pytest.mark.parametrize('case', json.loads(FIXTURE.read_text(encoding='utf-8')),
                         ids=lambda case: case['asin'] + ':' + case['field'])
def test_saved_pages_distinguish_capacity_from_fill_limits(case):
    assert len(case['html_sha256']) == 64
    assert case['source_dom_paths']
    text = case['source_text']
    assert 'CONTRADICTORY_CAPACITY' not in numeric_profile(text)['issues']
    source = {'asin': case['asin'], case['field']: text}
    before = copy.deepcopy(source)
    field = audit_records([source])['translation_input_records'][0]['fields'][case['field']]
    assert field['source_text'] == text
    assert source == before
    assert field['translate_allowed']


@pytest.mark.parametrize('text', [
    'Capacidad mínima de 1 L y capacidad máxima de 0,5 L',
    'Capacidad máxima de 1 L y capacidad máxima de 2 L; capacidad mínima de 0,5 L',
    'Capacidad de 500 ml, capacidad útil de 550 ml',
    'Capacidad de 500 ml, cantidad de agua recomendada: 550 ml',
    'Capacidad total de 500 ml y capacidad útil de 450 ml; 600 ml',
    'Capacidad mínima de 0,3 L y capacidad máxima de 0,8 L; capacidad de 1 L',
    'Capacidad total de 500 ml y capacidad útil de 450 ml; capacidad útil de 400 ml',
    'Capacidad de 400 ml; Tamaño: 414 ml',
    'Capacidad de 1,5 litros y 1 litro y medio litro',
    'Capacidad de 1,5 litros y 1 litro y medio mililitro',
    'Capacidad de 500 ml; recomendación sin verificar de 400 ml',
    'Capacidad de 1 litro; mínimo desconocido; capacidad de 2 litros',
    'Capacidad total de 500 ml; recomendamos llenar el vaso con 450-400 ml',
    'Capacidad total de 500 ml; recomendamos llenar el vaso con 400-550 ml',
    'Capacidad mínima de 0,5 L; capacidad máxima de 1 L; llenado entre 1,2 y 1 L',
    'Capacidad mínima de 0,5 L; capacidad máxima de 1 L; llenado entre 0,3 y 1 L',
    'Recipientes con tapas de 24-946 ml de capacidad y 24-473 ml de tamaño pinta',
])
def test_unbound_extra_amounts_invalid_bounds_and_real_conflicts_still_block(text):
    assert 'CONTRADICTORY_CAPACITY' in numeric_profile(text)['issues']


def test_word_half_litre_is_exact_numeric_evidence_without_rewriting_source():
    text = 'Capacidad de 1,5 litros, botella de 1 litro y medio'
    profile = numeric_profile(text)
    assert [row['numeric_value'] for row in profile['values']] == [1.5, 1.5]
    assert [row['canonical_unit'] for row in profile['values']] == ['L', 'L']
    assert 'CONTRADICTORY_CAPACITY' not in profile['issues']


def test_a_separate_half_glass_is_not_half_a_litre():
    profile = numeric_profile('Capacidad de 1 litro y medio vaso')
    assert profile['values'][0]['numeric_value'] == 1


def test_unknown_capacity_cannot_borrow_recommended_scope_from_previous_sentence():
    text = 'Capacidad total de 500 ml. Cantidad de agua recomendada: 450 ml. Otra capacidad de 400 ml.'
    assert 'CONTRADICTORY_CAPACITY' in numeric_profile(text)['issues']
