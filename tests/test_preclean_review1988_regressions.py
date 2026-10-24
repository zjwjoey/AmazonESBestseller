import copy
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.translation.preclean import audit_records, numeric_profile, clean_text
from amazon_es_bestseller.collection.detail import parse_detail_page

FIXTURES = Path(__file__).parent / 'fixtures/preclean_review1988_20261024/saved_cases.json'


@pytest.mark.parametrize('text', [
    'Estándar <300 g/m² Premium = 300-349 g/m² Élite >399 g/m²',
    'Tensión < 24 V y potencia > 5 W',
    'Modelo <B083ZMB5W1>',
])
def test_comparison_signs_and_bracketed_identity_are_not_html_tags(text):
    result = clean_text(text)
    assert result['clean_text'] == text
    assert result['html_detected'] is False


def test_actual_html_tags_are_still_cleaned():
    assert clean_text('<b>Acero</b><br>Texto')['clean_text'] == 'Acero Texto'


@pytest.mark.parametrize('text', [
    'Pintura brillante para bricolaje.',
    'Beneficios: bienestar y bolsillos resistentes.',
    'BENEFICIOS para bricolaje y acabado BRILLANTE.',
    'Clasificación de microfibra: élite >399 g/m².',
    'Facilita la clasificación y organización de sus pertenencias.',
    'Gracias por su valoración de nuestros productos.',
])
def test_ordinary_description_words_are_not_product_metadata(text):
    result = audit_records([{'asin': 'B000000001', 'product_description_raw': text}])
    field = result['translation_input_records'][0]['fields']['product_description']
    assert field['clean_status'] == 'CLEAN'
    assert field['translate_allowed']
    assert field['source_text'] == text


@pytest.mark.parametrize('text', [
    'ASIN B083ZMB5W1',
    'Producto B083ZMB5W1',
    'Valoración: 4,5 de 5 estrellas',
    'Clasificación en los más vendidos: nº 3',
    'Opiniones: 78',
])
def test_real_metadata_markers_remain_in_review(text):
    result = audit_records([{'asin': 'B000000001', 'product_description_raw': text}])
    field = result['translation_input_records'][0]['fields']['product_description']
    assert field['clean_status'] == 'NEEDS_REVIEW'
    assert not field['translate_allowed']


@pytest.mark.parametrize('text', [
    'Capacidad: 0,75 L / Dimensiones: 8,5l. x 8,5an. x 14,5al. centímetros',
    'Capacidad: 30 litros / Tamaño: 40L x 30A x 25A cm',
    'Capacidad: 100 litros / Tamaño: 2 x 50 L',
    'Capacidad: 650 ml / Tamaño: 500ml + 150ml',
    'Capacidad: 0,65 L / Tamaño: 500 ml + 150 ml',
    'Gran capacidad: 3 X 8L Total 24 Litros',
])
def test_dimensions_and_explicit_component_totals_are_not_capacity_conflicts(text):
    assert 'CONTRADICTORY_CAPACITY' not in numeric_profile(text)['issues']


@pytest.mark.parametrize('text', [
    'Capacidad: 9 L / 25,4 L',
    'Capacidad: 60 ml / Tamaño: 2 x Café solo (90ml)',
    'Gran capacidad: 3 X 8L Total 25 Litros',
    'Capacidad: 650 ml / Tamaño: 500ml + 160ml',
    'Capacidad: 650 ml / Tamaño: 500ml + 150ml / Otro volumen: 900ml',
    'Capacidad máxima de 53 centilitros por copa. Capacidad de 35 centilitros por copa.',
])
def test_unexplained_or_inconsistent_source_capacity_values_still_block(text):
    assert 'CONTRADICTORY_CAPACITY' in numeric_profile(text)['issues']


def test_exact_bullet_duplicates_normalize_without_hiding_other_defects():
    record = {'asin': 'B000000001', 'feature_bullets_raw': ['Texto original', 'Texto original']}
    before = copy.deepcopy(record)
    result = audit_records([record])
    row = result['translation_input_records'][0]
    assert record == before
    assert row['fields']['feature_bullets']['clean_status'] == 'NORMALIZED'
    assert row['fields']['feature_bullets']['translate_allowed']
    assert 'DUPLICATE_BULLET' in row['fields']['feature_bullets']['issues']
    assert len(row['bullet_items']) == 1
    assert not result['review_queue']
    bad = audit_records([{**record, 'feature_bullets_raw': ['Texto original', 'Texto original', '']}])
    assert not bad['translation_input_records'][0]['fields']['feature_bullets']['translate_allowed']


def test_duplicate_metrics_count_occurrences_and_affected_skus_separately():
    result = audit_records([{
        'asin': 'B000000001',
        'product_details_es': 'Modelo: X1\nModelo: X1\nModelo: X1',
        'feature_bullets_raw': ['Original', 'Original', 'Original'],
    }])
    assert result['summary']['structure']['safe_duplicate_rows'] == 2
    assert result['summary']['structure']['safe_duplicate_skus'] == 1
    assert result['summary']['bullets']['duplicate_bullets'] == 2
    assert result['summary']['bullets']['duplicate_bullet_skus'] == 1


def test_raw_attribute_label_boundaries_survive_flattened_colons():
    record = {
        'asin': 'B000000001',
        'product_details_es': 'Dimensiones: largo x ancho x alto: 10 x 20 x 30 cm\nDimensiones: 10 x 20 x 30 cm; 2 kg',
        'attributes': [
            {'label_raw': 'Dimensiones: largo x ancho x alto', 'value_raw': '10 x 20 x 30 cm'},
            {'label_raw': 'Dimensiones', 'value_raw': '10 x 20 x 30 cm; 2 kg'},
        ],
    }
    before = copy.deepcopy(record)
    result = audit_records([record]);row = result['translation_input_records'][0]
    assert record == before
    assert [x['label'] for x in row['details_structured']] == ['Dimensiones: largo x ancho x alto', 'Dimensiones']
    assert row['fields']['product_details']['translate_allowed']
    # A stale raw hint must not rewrite a different displayed fact or conceal a conflict.
    record['attributes'][0]['value_raw'] = 'DIFFERENT TEST_ONLY VALUE'
    assert not audit_records([record])['translation_input_records'][0]['fields']['product_details']['translate_allowed']


@pytest.mark.parametrize('case', json.loads(FIXTURES.read_text(encoding='utf-8')), ids=lambda x:x['asin'])
def test_saved_page_inputs_keep_raw_evidence_and_expected_admission(case):
    record = case['record']
    before = copy.deepcopy(record)
    row = audit_records([record])['translation_input_records'][0]
    envelope = row['fields'][case['field']]
    assert record == before
    assert envelope['clean_status'] == case['expected_status']
    assert envelope['translate_allowed'] == case['expected_allowed']


@pytest.mark.parametrize('case', [c for c in json.loads(FIXTURES.read_text(encoding='utf-8'))
                                  if c['field'] in {'product_description', 'feature_bullets'}], ids=lambda x:x['asin'])
def test_real_saved_dom_replays_into_the_preclean_input(case):
    page = (FIXTURES.parent / (case['asin'] + '.html')).read_text(encoding='utf-8')
    parsed = parse_detail_page(page, case['asin'])
    source_key = 'product_description_raw' if case['field'] == 'product_description' else 'feature_bullets_raw'
    assert parsed[source_key] == case['record'][source_key]
    row = audit_records([parsed])['translation_input_records'][0]
    assert row['fields'][case['field']]['clean_status'] == case['expected_status']
    assert row['fields'][case['field']]['translate_allowed'] == case['expected_allowed']
