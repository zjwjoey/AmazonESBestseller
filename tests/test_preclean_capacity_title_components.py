import copy
import json
from pathlib import Path

import pytest
from amazon_es_bestseller.translation.preclean import audit_records


CASE = json.loads((Path(__file__).parent/'fixtures/preclean_capacity_title_components.json').read_text(encoding='utf-8'))


def test_saved_title_explicit_main_and_compartment_confirm_body_components():
    raw = {'asin': CASE['asin'], 'title_es_raw': CASE['title_from_saved_html'],
           'feature_bullets_raw': CASE['source_text']}
    before = copy.deepcopy(raw)
    record = audit_records([raw])['translation_input_records'][0]
    field = record['fields']['feature_bullets']
    assert field['translate_allowed']
    assert field['source_text'] == CASE['source_text']
    proof = field['numeric']['capacity_title_binding']
    assert proof['title_source_text'] == raw['title_es_raw']
    assert len(proof['title_source_hash']) == 64
    assert raw == before


@pytest.mark.parametrize('title', [
    '', 'Vaso para Yogur y Cereales 240 ml Pack de 2',
    'Vaso para Yogur y Cereales 241 ml Pack de 2 con Compartimento 120 ml',
    'Vaso para Yogur y Cereales 240 ml Pack de 2 con Compartimento 121 ml',
    'Vaso para Yogur y Cereales 240 ml Pack de 2 con Compartimento 120 ml; otro 500 ml',
])
def test_missing_or_conflicting_title_cannot_supply_inferred_component_roles(title):
    field = audit_records([{'asin': CASE['asin'], 'title_es_raw': title,
                           'feature_bullets_raw': CASE['source_text']}])['translation_input_records'][0]['fields']['feature_bullets']
    assert not field['translate_allowed']


def test_verified_title_does_not_hide_an_extra_body_amount():
    field = audit_records([{'asin': CASE['asin'], 'title_es_raw': CASE['title_from_saved_html'],
                           'feature_bullets_raw': CASE['source_text']+'\nCapacidad 500 ml.'}])['translation_input_records'][0]['fields']['feature_bullets']
    assert not field['translate_allowed']


@pytest.mark.parametrize('body', [
    'Capacidad de 240 ml + 120 ml. NO INCLUYE doble compartimento.',
    'Capacidad de 240 ml + 120 ml. Sistema de doble capa, pero sin compartimento.',
    'Capacidad de 240 ml + 120 ml. Sistema de doble capa. Ambos valores son capacidades del mismo recipiente principal.',
])
def test_title_cannot_override_explicit_body_counterclaims(body):
    field = audit_records([{'asin': CASE['asin'], 'title_es_raw': CASE['title_from_saved_html'],
                           'feature_bullets_raw': body}])['translation_input_records'][0]['fields']['feature_bullets']
    assert not field['translate_allowed']


def test_negated_title_is_not_positive_component_evidence():
    field = audit_records([{'asin': CASE['asin'], 'title_es_raw': 'No es un '+CASE['title_from_saved_html'],
                           'feature_bullets_raw': CASE['source_text']}])['translation_input_records'][0]['fields']['feature_bullets']
    assert not field['translate_allowed']
