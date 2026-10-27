import copy
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.translation.preclean import audit_records, parse_details


@pytest.mark.parametrize('case', json.loads((Path(__file__).parent /
                         'fixtures/preclean_remaining1117_equivalents.json').read_text(encoding='utf-8')),
                         ids=lambda case: case['asin'])
def test_saved_page_literal_equivalents(case):
    raw = [{'label_raw': case['label'], 'value_raw': value} for value in case['values']]
    before = copy.deepcopy(raw)
    assert len(case['html_sha256']) == 64
    assert case['source_dom_paths']
    parsed = parse_details(raw)
    assert parsed['status'] == 'NORMALIZED'
    assert [row['value'] for row in parsed['rows']] == case['values']
    assert raw == before


@pytest.mark.parametrize('label,values', [
    ('Dimensiones del producto', ['26l. x 20an. x 2Grosor centímetros', '26 x 20 x 2 cm; 660 g']),
    ('Dimensiones del producto', ['99l. x 191an. x 25Grosor centímetros', '99 x 191 x 25 cm; 2,3 kg']),
    ('Edad recomendada por el fabricante', ['3 años y más', '3 año y más']),
    ('Edad recomendada por el fabricante', ['14 años y más', '14 año y más']),
    ('Forma artículo', ['Cuadrado', 'Cuadrada']),
])
def test_literal_morphology_and_thickness_axis_keep_both_source_rows(label, values):
    raw = [{'label_raw': label, 'value_raw': value} for value in values]
    before = copy.deepcopy(raw)
    parsed = parse_details(raw)
    assert parsed['status'] == 'NORMALIZED'
    assert 'EQUIVALENT_SOURCE_VALUES' in parsed['issues']
    assert [row['value'] for row in parsed['rows']] == values
    record = audit_records([{'asin': 'B000000001', 'detail_attributes_raw': raw}])['translation_input_records'][0]
    assert record['fields']['product_details']['translate_allowed']
    assert raw == before


@pytest.mark.parametrize('label,values', [
    ('Dimensiones del producto', ['26l. x 20an. x 2Grosor centímetros', '26 x 20 x 3 cm']),
    ('Dimensiones del producto', ['26l. x 20an. x 2Grosor centímetros', '20 x 26 x 2 cm']),
    ('Dimensiones del producto', ['26l. x 20an. x 2Grosor centímetros', '26 x 20 x 2 cm; 660 g', '26 x 20 x 2 cm; 670 g']),
    ('Edad recomendada por el fabricante', ['3 años y más', '4 año y más']),
    ('Edad recomendada por el fabricante', ['6 años y más', '6 - 100 años']),
    ('Edad recomendada por el fabricante', ['3 meses y más', '3 año y más']),
    ('Edad recomendada por el fabricante', ['3 años y más', '3 años']),
    ('Edad recomendada por el fabricante', ['3 años y más', '3 año y más', '4 años y más']),
    ('Edad recomendada por el fabricante', ['3 años y más', '3 año y más aproximadamente']),
    ('Forma artículo', ['Cuadrado', 'Rectangular']),
    ('Forma artículo', ['Cuadrado', 'Cuadrada', 'Circular']),
    ('Forma artículo', ['Cuadrado', 'Cuadrada con tapa']),
    ('Modelo', ['3 años y más', '3 año y más']),
])
def test_distinct_or_incomplete_facts_still_need_review(label, values):
    parsed = parse_details([{'label_raw': label, 'value_raw': value} for value in values])
    assert parsed['status'] == 'NEEDS_REVIEW'
    assert 'CONFLICTING_DUPLICATE' in parsed['issues']
    assert [row['value'] for row in parsed['rows']] == values


def test_same_asin_only_description_remains_held_without_losing_source():
    raw = {'asin': 'B083ZMBSW1', 'product_description_raw': 'B083ZMBSW1'}
    before = copy.deepcopy(raw)
    record = audit_records([raw])['translation_input_records'][0]
    field = record['fields']['product_description']
    assert field['source_text'] == 'B083ZMBSW1'
    assert field['clean_text'] == 'B083ZMBSW1'
    assert not field['translate_allowed']
    assert field['issues'] == ['POSSIBLE_METADATA_IN_DESCRIPTION']
    assert record['record_status'] == 'NEEDS_REVIEW'
    assert record['raw_fields']['product_description']['source_present']
    assert raw == before


@pytest.mark.parametrize('value', ['B083ZMBSW2', 'Producto B083ZMBSW1', 'ASIN: B083ZMBSW1'])
def test_different_identity_or_mixed_description_is_still_held(value):
    record = audit_records([{'asin': 'B083ZMBSW1', 'product_description_raw': value}])['translation_input_records'][0]
    field = record['fields']['product_description']
    assert field['clean_status'] == 'NEEDS_REVIEW'
    assert not field['translate_allowed']
    assert field['clean_text'] == value
