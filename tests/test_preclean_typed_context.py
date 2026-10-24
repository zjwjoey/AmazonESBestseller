import copy
import json
from pathlib import Path
import pytest
from amazon_es_bestseller.translation.preclean import audit_records, numeric_profile, parse_details

FIXTURE = Path(__file__).parent / 'fixtures/preclean_typed_context_20261024.json'


@pytest.mark.parametrize('case',json.loads(FIXTURE.read_text(encoding='utf-8')),ids=lambda c:c['asin'])
def test_hash_bound_saved_source_pairs_preserve_both_literal_values(case):
    raw=[{'label_raw':case['label'],'value_raw':v} for v in case['values']]
    before=copy.deepcopy(raw)
    parsed=parse_details(raw)
    assert parsed['status']=='NORMALIZED'
    assert 'CONFLICTING_DUPLICATE' not in parsed['issues']
    assert [r['value'] for r in parsed['rows']]==case['values']
    assert raw==before

@pytest.mark.parametrize('label,values', [
    ('Número de unidades', ['10 Conteo', '10']),
    ('Número de unidades', ['1.0 Conteo', '1']),
    ('Dimensiones del producto', ['10 x 20 x 30 centímetros', '100 x 200 x 300 mm; 500 g']),
    ('Dimensiones del producto', ['10l. x 20an. x 30al. centímetros', '10 x 20 x 30 cm; 0,5 kg']),
    ('Dimensiones del paquete', ['10 x 20 x 30 cm; 500 g', '100 x 200 x 300 mm; 0,5 kg']),
    ('Forma artículo', ['Circular', 'Redonda']),
    ('Peso del producto', ['500 g', '0,5 kg']),
])
def test_equivalent_typed_facts_remain_as_two_source_rows_without_blocking(label,values):
    raw=[{'label_raw':label,'value_raw':v,'position':i} for i,v in enumerate(values)]
    before=copy.deepcopy(raw);parsed=parse_details(raw)
    assert raw==before
    assert [r['value'] for r in parsed['rows']]==values
    assert parsed['status']=='NORMALIZED'
    assert 'EQUIVALENT_SOURCE_VALUES' in parsed['issues']
    row=audit_records([{'asin':'B000000001','detail_attributes_raw':raw}])['translation_input_records'][0]
    assert row['fields']['product_details']['translate_allowed']
    assert not audit_records([{'asin':'B000000001','detail_attributes_raw':raw}])['review_queue']

@pytest.mark.parametrize('label,values', [
    ('Número de unidades', ['10 Conteo', '12']),
    ('Número de unidades', ['1 paquete', '1']),
    ('Número de unidades', ['1.000 Conteo', '1']),
    ('Modelo', ['10', '10 Conteo']),
    ('Dimensiones del producto', ['5,7 x 5,7 x 17,8 cm', '5,72 x 5,72 x 17,78 cm']),
    ('Dimensiones del producto', ['10 x 20 cm', '10 x 20 x 30 cm']),
    ('Dimensiones del producto', ['10 x 20 x 30 cm; 1 kg', '10 x 20 x 30 cm; 2 kg']),
    ('Dimensiones del producto', ['10 x 20 x 30 cm Extra', '10 x 20 x 30 cm']),
    ('Dimensiones del producto', ['1.000 x 20 x 30 cm', '1 x 20 x 30 cm']),
    ('Forma artículo', ['Rectangular', 'Cuadrada']),
    ('Peso del producto', ['500 g', '0,6 kg']),
])
def test_distinct_or_incompletely_typed_values_remain_under_review(label,values):
    raw=[{'label_raw':label,'value_raw':v} for v in values]
    parsed=parse_details(raw)
    assert parsed['status']=='NEEDS_REVIEW'
    assert 'CONFLICTING_DUPLICATE' in parsed['issues']
    assert [r['value'] for r in parsed['rows']]==values

def test_equivalent_pair_cannot_hide_a_third_conflicting_value():
    raw=[{'label_raw':'Número de unidades','value_raw':v} for v in ['10 Conteo','10','12']]
    assert parse_details(raw)['status']=='NEEDS_REVIEW'

def test_volume_rates_are_not_container_capacities():
    text='Capacidad para calentar; caudal 300 L/min y 500 l/min'
    profile=numeric_profile(text)
    assert 'CONTRADICTORY_CAPACITY' not in profile['issues']
    assert [x['canonical_unit'] for x in profile['values']]==['L/min','L/min']

def test_real_volume_conflicts_survive_next_to_flow_rates():
    assert 'CONTRADICTORY_CAPACITY' in numeric_profile('Capacidad 9 L / 25,4 L; caudal 300 L/min')['issues']


def test_saved_hot_air_tool_flow_rates_do_not_become_container_capacity_conflicts():
    fixture=Path(__file__).parent/'fixtures/preclean_volume_rate_20261024.json'
    case=json.loads(fixture.read_text(encoding='utf-8'));record=case['record'];before=copy.deepcopy(record)
    row=audit_records([record])['translation_input_records'][0]
    envelope=row['fields']['feature_bullets']
    assert envelope['translate_allowed']
    assert 'CONTRADICTORY_CAPACITY' not in envelope['issues']
    assert '300l/min' in envelope['clean_text'] and '500l/min' in envelope['clean_text']
    assert record==before


@pytest.mark.parametrize('text', [
    'Volumen 1,5 L; escala interna 250 ml a 1500 ml',
    'Capacidad 260 ml / Tamaño 250-350ml',
    'Capacidad 17 litros cada uno. Ideales para bolsas de basura de 20 o 30 litros',
    'Papelera de 25 litros. Tiene capacidad para una bolsa de 30-35L',
    'Jarra de 2,3 L con 1,4 L de capacidad de filtrado de agua',
    'CAPACIDAD: El recipiente inferior tiene capacidad de 500 ml. El envase superior es de 200 ml',
    'Contenido: 1x cuenco 16 cm (capacidad 1 L), 1x cuenco 18 cm (capacidad 1,5 L)',
])
def test_explicit_ranges_and_distinct_object_roles_are_not_scalar_conflicts(text):
    assert 'CONTRADICTORY_CAPACITY' not in numeric_profile(text)['issues']


@pytest.mark.parametrize('text', [
    'Capacidad 500 ml / Tamaño 250-350ml',
    'Capacidad 260 ml / Tamaño 250-259,9 ml',
    'Capacidad 9 L / 25,4 L. Compatible con bolsas de 30 L',
    'Capacidad de filtrado: 1,4 L / 1,9 L',
    'Recipiente inferior capacidad 500 ml y capacidad 600 ml. Envase superior 200 ml',
])
def test_separate_object_claim_cannot_hide_a_conflict_within_one_role(text):
    assert 'CONTRADICTORY_CAPACITY' in numeric_profile(text)['issues']


CAPACITY_CASES=Path(__file__).parent/'fixtures/preclean_capacity_context_20261024.json'


@pytest.mark.parametrize('case',json.loads(CAPACITY_CASES.read_text(encoding='utf-8')),ids=lambda c:c['record']['asin'])
def test_saved_pages_keep_literal_values_while_scoping_capacity_claims(case):
    before=copy.deepcopy(case['record'])
    row=audit_records([case['record']])['translation_input_records'][0]
    envelope=row['fields'][case['field']]
    assert envelope['translate_allowed']==case['expected_allowed']
    assert case['record']==before
