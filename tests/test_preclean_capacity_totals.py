import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from amazon_es_bestseller.translation.preclean import (
    NUMERIC_UNIT_RE, UNIT_CANONICAL, _explicit_capacity_total,
    _numeric_amount, audit_records, numeric_profile,
)


CASES = json.loads((Path(__file__).parent / 'fixtures' /
                   'preclean_capacity_totals_saved_sources.json').read_text(encoding='utf-8'))


def capacities(text):
    factors = {'ml': Decimal(1), 'cl': Decimal(10), 'L': Decimal(1000)}
    return [(m, _numeric_amount(text, m) * factors[UNIT_CANONICAL[m.group('unit').casefold()]])
            for m in NUMERIC_UNIT_RE.finditer(text)
            if UNIT_CANONICAL.get(m.group('unit').casefold()) in factors]


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['asin'] + ':' + c['field'])
def test_saved_total_candidates_require_explicit_component_binding(case):
    text = case['source_text']
    assert case['html_sha256_independently_verified']
    assert case['evidence_dom']
    assert _explicit_capacity_total(text, capacities(text)) is case['expected_total_supported']
    if case['expected_total_supported']:
        record = {'asin': case['asin'], case['field']: text}
        before = copy.deepcopy(record)
        row = audit_records([record])['translation_input_records'][0]['fields'][case['field']]
        assert 'CONTRADICTORY_CAPACITY' not in numeric_profile(text)['issues']
        assert row['translate_allowed']
        assert row['source_text'] == text
        assert record == before


@pytest.mark.parametrize('text', [
    'Capacidad: 100 litros / Tamaño: 2 x 50 L',
    'Capacidad: 650 ml / Tamaño: 500ml + 150ml',
    'Capacidad: 0,65 L / Tamaño: 500 ml + 150 ml',
    'Gran capacidad: 3 X 8L Total 24 Litros',
    'Capacidad total de 60 L: 20 L + 20 L + 20 L',
    'Dos recipientes de 3,8 L, capacidad total de 7,6 L',
    'Capacidad total de 48 litros; seis cajas de 8 litros cada una',
])
def test_legacy_literal_arithmetic_and_exact_count_forms_remain_supported(text):
    assert _explicit_capacity_total(text, capacities(text))
    assert 'CONTRADICTORY_CAPACITY' not in numeric_profile(text)['issues']


@pytest.mark.parametrize('text', [
    'Capacidad total de 2800 ml en 3 capas; cada capa tiene capacidad de 933 ml',
    'Capacidad total de 1 L: 700 ml + 310 ml',
    'Capacidad total de 1 L: 700 ml para encurtidos y 310 ml para caldo',
    'Capacidad de 1,7 L para siete tazas de 250 ml',
    'Capacidad 100 L; 2 x 50 L y 3 x 50 L',
    'Capacidad 100 L; 2 recipientes de 50 L y 3 recipientes de 50 L',
    'Capacidad total de 100 L; 2 bolsas de 15 L y 3 bolsas de 15 L',
    'Capacidad total de 100 L; proporción 2 x 50 L',
    'Capacidad total de 100 L; 2 piezas de regalo; capacidad de 50 L',
    'Capacidad 650 ml; 500 ml + 150 ml; capacidad de 500 ml',
    'Capacidad 650 ml; 500 ml + 150 ml; volumen desconocido de 150 ml',
    'Capacidad total de 650 ml; 500 ml + 150 ml; capacidad total de 700 ml',
    'Capacidad total de 100 L; 2 x 50 L; 3 x 50 L',
    'Capacidad total de 60 L; 20 L + 20 L + 20 L; otro volumen de 20 L',
    'Capacidad total de 60 L; cesta superior de 30 L y cesta superior de 30 L',
    'Capacidad total de 100 L; dos compartimentos y cada bolsa tiene 50 L',
    'Capacidad total de 100 L; dos compartimentos y cada botella tiene 50 L',
    'Seis cajas vacías. Capacidad total de 48 litros. La botella tiene capacidad de 8 litros cada una.',
    'Capacidad total de 7,6 L. NO INCLUYE dos recipientes de 3,8 L.',
    'Capacidad total de 7,6 L. Por ejemplo, dos recipientes de 3,8 L.',
    'No contiene 2 compartimentos. Capacidad total de 100 L. Cada compartimento tiene 50 L.',
    'Ejemplo de otro producto: capacidad total de 100 L; 2 x 50 L.',
    'No tiene capacidad total de 100 L; 2 x 50 L.',
    'Capacidad total de 100 L; una bolsa de 50 L y otra de 40 L',
])
def test_every_amount_occurrence_and_count_role_must_be_covered(text):
    assert not _explicit_capacity_total(text, capacities(text))
    assert 'CONTRADICTORY_CAPACITY' in numeric_profile(text)['issues']


def test_total_validation_does_not_mutate_match_lists_or_invent_missing_totals():
    text = 'Cajón XXL de 5,2 L y cajón estándar de 3,1 L'
    values = capacities(text)
    before = [(m.span(), amount) for m, amount in values]
    assert not _explicit_capacity_total(text, values)
    assert [(m.span(), amount) for m, amount in values] == before


OVEN_CASES = json.loads((Path(__file__).parent / 'fixtures' /
                        'preclean_capacity_totals_oven_sources.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', OVEN_CASES, ids=lambda c: c['asin'])
def test_repeated_included_product_total_after_verified_other_oven_scope(case):
    text = case['source_text']
    values = capacities(text)
    # An upstream, separately tested scope binds these particular occurrences
    # to compared ovens. This helper consumes the three remaining claims,
    # while the complete original comparison sentence stays untouched.
    assert [amount for _, amount in values] == [Decimal(9500), Decimal(68000), Decimal(71000),
                                               Decimal(9500), Decimal(4750)]
    remaining = [item for i, item in enumerate(values) if i not in {1, 2}]
    assert _explicit_capacity_total(text, remaining)
    assert '68 L' in text and '71 L' in text


@pytest.mark.parametrize('text', [
    'Gran capacidad de 9,5 L; INCLUYE: freidora de 10 L, 2 cestas de 4,75 L',
    'Gran capacidad de 9,5 L; INCLUYE: freidora de 9,5 L, 2 hornos; cestas de 4,75 L',
    'Gran capacidad de 9,5 L; INCLUYE: freidora de 9,5 L, 3 cestas de 4,75 L',
])
def test_included_product_header_does_not_hide_a_different_total_or_borrow_oven_count(text):
    assert not _explicit_capacity_total(text, capacities(text))


@pytest.mark.parametrize('text', [
    'Capacidad total de 50 L; 2 x 50 L',
    'Gran capacidad: 3 X 8L Total 8 Litros',
    'Capacidad total de 10 L; dos recipientes de 10 L',
    'Capacidad total de 60 L; 20 L + 20 L + 20 L; capacidad total de 65 L',
])
def test_explicit_arithmetic_contradiction_is_not_lost_when_scalars_are_equal(text):
    values = capacities(text)
    assert not _explicit_capacity_total(text, values)
    assert _explicit_capacity_total(text, values, conflict_only=True)


@pytest.mark.parametrize('text', [
    'Capacidad total de 50 L; 1 x 50 L',
    'Gran capacidad: 1 X 8L Total 8 Litros',
    'Capacidad total de 10 L; un recipiente de 10 L',
])
def test_one_component_equal_to_its_total_is_valid(text):
    values = capacities(text)
    assert _explicit_capacity_total(text, values)
    assert not _explicit_capacity_total(text, values, conflict_only=True)


@pytest.mark.parametrize('text', [
    'Capacidad total de 100 L; dos compartimentos y cada bolsa tiene 50 L',
    'Capacidad total de 100 L; dos compartimentos y cada botella tiene 50 L',
    'Capacidad total de 7,6 L. NO INCLUYE dos recipientes de 3,8 L.',
    'Un recipiente de 500 ml y otro de 150 ml',
    'Capacidad total de 650 ml; 500 ml + 150 ml; otro volumen de 900 ml',
])
def test_conflict_probe_requires_complete_proof_and_does_not_infer_unknown_roles(text):
    assert not _explicit_capacity_total(text, capacities(text), conflict_only=True)


@pytest.mark.parametrize('text', [
    'Capacidad total de 500 ml; 2 recipientes de 500 ml; volumen desconocido de 500 ml',
    'Total 500 ml; 2 x 500 ml; otra cantidad de 500 ml',
    'Total 500 ml; 2 x 500 ml; otra cantidad de 200 ml',
    'Capacidad total de 500 ml; 300 ml + 300 ml; volumen desconocido de 500 ml',
])
def test_unknown_amount_cannot_erase_an_independently_proven_subtotal_overflow(text):
    assert not _explicit_capacity_total(text, capacities(text))
    assert _explicit_capacity_total(text, capacities(text), conflict_only=True)
    assert 'CONTRADICTORY_CAPACITY' in numeric_profile(text)['issues']
    source = {'asin': 'B000000001', 'feature_bullets': text}
    assert not audit_records([source])['translation_input_records'][0]['fields']['feature_bullets']['translate_allowed']


@pytest.mark.parametrize('text', [
    'Capacidad total de 500 ml; 2 recipientes de 200 ml; volumen desconocido de 500 ml',
    'Total 500 ml; 2 x 200 ml; otra cantidad de 500 ml',
    'Capacidad total de 500 ml; dos compartimentos y cada botella tiene 500 ml; otro volumen de 500 ml',
    'Total 500 ml; NO INCLUYE 2 recipientes de 500 ml; otro volumen de 500 ml',
    'Total 500 ml; por ejemplo, 2 recipientes de 500 ml; otro volumen de 500 ml',
])
def test_partial_proof_does_not_invent_missing_capacity_or_borrow_another_object_count(text):
    assert not _explicit_capacity_total(text, capacities(text), conflict_only=True)


UNIT_SET_CASES = json.loads((Path(__file__).parent / 'fixtures' /
                            'preclean_capacity_totals_unit_vs_set_sources.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', UNIT_SET_CASES, ids=lambda c: c['asin'] + ':' + c['field'])
def test_saved_unit_capacity_is_not_a_stated_pack_total(case):
    text = case['source_text']
    assert case['html_hash_verified'] and len(case['html_sha256']) == 64
    profile = numeric_profile(text)
    assert ('CONTRADICTORY_CAPACITY' in profile['issues']) is case['expected_numeric_conflict']
    source = {'asin': case['asin'], case['field']: text}
    before = copy.deepcopy(source)
    field = audit_records([source])['translation_input_records'][0]['fields'][case['field']]
    assert field['translate_allowed'] is not case['expected_numeric_conflict']
    assert field['source_text'] == text and source == before


@pytest.mark.parametrize('text', [
    'Capacidad total de 1750 ml; 0,5 x 350 ml',
    'Capacidad total de 1750 ml; 1,5 x 350 ml',
    'Capacidad total de 1750 ml; 0.5x350 ml',
    'Capacidad total de 1750 ml; 1.5x350 ml',
    'Capacidad total de 1750 ml; 1,5 recipientes de 350 ml',
])
def test_fractional_count_cannot_lend_its_integer_tail_to_a_component(text):
    values = capacities(text)
    assert not _explicit_capacity_total(text, values)
    assert not _explicit_capacity_total(text, values, conflict_only=True)
