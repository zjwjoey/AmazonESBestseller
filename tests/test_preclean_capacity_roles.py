import copy
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.translation.preclean import (
    NUMERIC_UNIT_RE, UNIT_CANONICAL, _capacity_scope, _capacity_conflict, audit_records, numeric_profile,
)
from decimal import Decimal


FIXTURE = Path(__file__).parent / 'fixtures/preclean_capacity_roles_saved_sources.json'


@pytest.mark.parametrize('case', json.loads(FIXTURE.read_text(encoding='utf-8')),
                         ids=lambda case: case['asin'] + ':' + case['field'])
def test_saved_pages_require_a_direct_quantity_to_named_component_binding(case):
    assert len(case['html_sha256']) == 64
    assert case['source_dom_paths']
    text = case['source_text']
    assert ('CONTRADICTORY_CAPACITY' not in numeric_profile(text)['issues']) == case['expected_clear']
    source = {'asin': case['asin'], case['field']: text}
    before = copy.deepcopy(source)
    field = audit_records([source])['translation_input_records'][0]['fields'][case['field']]
    assert field['source_text'] == text
    assert source == before
    assert field['translate_allowed'] == case['expected_clear']


@pytest.mark.parametrize('text', [
    'Capacidad: tanque de agua limpia de 1300 ml y tanque de agua sucia de 1800 ml; 1600 ml',
    'Capacidad: tanque de agua limpia de 1300 ml, además 1600 ml, y tanque de agua sucia de 1800 ml',
    'Capacidad: tanque de agua limpia de 1300 ml y tanque de agua sucia de 1800 ml; tanque de agua limpia de 1400 ml',
    'Capacidad: tanque de agua de 1300 ml, tanque de agua limpia de 1600 ml y tanque de agua sucia de 1800 ml',
    'Capacidad: el primer compartimento de 500 ml y el segundo compartimento de 1200 ml; 900 ml',
    'Capacidad: el primer compartimento de 500 ml y el primer compartimento de 1200 ml',
    'Capacidad de la capa superior de 310 ml y la capa inferior de 560 ml. Capacidad: 560 ml + 320 ml',
    'Capacidad de la capa superior de 310 ml y la capa inferior de 560 ml. Capacidad: 560 ml',
    'Capacidad de la capa superior de 310 ml y la capa inferior de 560 ml. Capacidad: 560 ml + 310 ml + 900 ml',
    'Capacidad: recipiente grande de 3800 ml y recipiente pequeño de 1400 ml. INCLUYE 2 recipientes de vidrio (1400 ml y 3900 ml)',
    'Capacidad: recipiente grande de 3800 ml y recipiente pequeño de 1400 ml. INCLUYE 2 recipientes de vidrio (1400 ml)',
    'Capacidad: utiliza el cajón XXL de 5,2 L o el cajón de 3,1 L',
    'Capacidad: el compartimento superior de 310 ml. Otro valor de 560 ml',
    'Capacidad: tanque de agua de 1300 ml. 1800 ml sin identificar',
    'Capacidad: capa superior de 310 ml y la inferior, de 560 ml; la inferior de 600 ml',
    'Capacidad: el cajón XXL de 5,2 L y el cajón XXL de 3,1 L',
    'Capacidad: recipiente principal de 480 ml y compartimento superior de 120 ml. Capacidad: 480 ml + 130 ml',
    'Capacidad: recipiente grande de 3800 ml y recipiente pequeño de 1400 ml. NO INCLUYE 2 recipientes de vidrio (1400 ml y 3800 ml)',
    'Capacidad: recipiente de cristal de 5,7 l o cámbialo por el de 2,3 l. INCLUYE: dos recipientes de cristal de 5,7 y 2,5 l',
    'Capacidad: recipiente grande de 3800 ml y recipiente pequeño de 1400 ml. INCLUYE: 2 recipientes de vidrio (1400 ml y 3800 ml), 900 ml',
])
def test_unknown_amounts_duplicate_role_conflicts_and_incomplete_lists_stay_blocked(text):
    assert 'CONTRADICTORY_CAPACITY' in numeric_profile(text)['issues']


def test_explicitly_named_drawer_qualifiers_can_define_separate_capacities():
    text = 'Capacidad: el cajón XXL de 5,2 L y el cajón estándar de 3,1 L'
    assert 'CONTRADICTORY_CAPACITY' not in numeric_profile(text)['issues']


def test_complete_reordered_component_list_does_not_reverse_upper_and_lower_roles():
    text = ('Capacidad de la capa superior de 310 ml y la capa inferior de 560 ml. '
            'Capacidad: 560 ml + 310 ml')
    matches = [m for m in NUMERIC_UNIT_RE.finditer(text)
               if UNIT_CANONICAL.get(m.group('unit').casefold()) in {'ml', 'cl', 'L'}]
    roles = [_capacity_scope(text, m) for m in matches]
    assert roles[0] == roles[3]
    assert roles[1] == roles[2]
    assert roles[0] != roles[1]
    assert 'CONTRADICTORY_CAPACITY' not in numeric_profile(text)['issues']


def test_principal_container_has_its_own_direct_role_but_an_extra_recipe_is_unassigned():
    text = ('Capacidad: recipiente principal de 480 ml y compartimento superior de 120 ml. '
            'Capacidad: 120 ml + 480 ml. Añade 120 ml de leche')
    matches = [m for m in NUMERIC_UNIT_RE.finditer(text)
               if UNIT_CANONICAL.get(m.group('unit').casefold()) in {'ml', 'cl', 'L'}]
    roles = [_capacity_scope(text, m) for m in matches]
    assert roles[0] == roles[3] == 'vessel:principal'
    assert roles[1] == roles[2] == 'upper_container'
    assert roles[-1] == 'unknown'
    assert 'CONTRADICTORY_CAPACITY' in numeric_profile(text)['issues']


def test_saved_postlabel_restatement_preserves_the_two_previously_bound_parts():
    case = json.loads((FIXTURE.parent / 'preclean_capacity_roles_postlabel.json').read_text(encoding='utf-8'))
    assert len(case['html_sha256']) == 64 and case['source_dom_paths']
    text = case['source_text']
    matches = [m for m in NUMERIC_UNIT_RE.finditer(text)
               if UNIT_CANONICAL.get(m.group('unit').casefold()) in {'ml', 'cl', 'L'}]
    assert [m.group() for m in matches] == ['480 ml', '120 ml', '480 ml', '120 ml', '120 ml']
    assert [_capacity_scope(text, m) for m in matches] == [
        'vessel:principal', 'upper_container', 'vessel:principal', 'upper_container', 'unknown']
    # This is the exact quantity sequence after the separately tested recipe
    # filter removes the last dose; scope binding does not classify that dose.
    post_recipe = [(m, Decimal(m.group('number'))) for m in matches[:-1]]
    assert not _capacity_conflict(text, post_recipe, structured_label=False)


@pytest.mark.parametrize('restatement', [
    '480 ml: capacidad de sobra',
    '480 ml + 130 ml: capacidad de sobra',
    '480 ml + 120 ml + 900 ml: capacidad de sobra',
    '900 ml + 480 ml + 120 ml: capacidad de sobra',
    '480 ml + 120 ml + 480 ml: capacidad de sobra',
])
def test_postlabel_restatements_must_repeat_exactly_both_named_parts(restatement):
    text = ('Capacidad: recipiente principal de 480 ml y compartimento superior de 120 ml. '
            + restatement)
    assert 'CONTRADICTORY_CAPACITY' in numeric_profile(text)['issues']


@pytest.mark.parametrize('case', json.loads((FIXTURE.parent / 'preclean_capacity_roles_drawer_cohort.json').read_text(encoding='utf-8')),
                         ids=lambda case: case['asin'])
def test_full_saved_two_drawer_cohort_binds_a_literal_other_drawer_without_inventing_standard(case):
    assert len(case['html_sha256']) == 64 and case['source_dom_paths']
    text = case['source_text']
    matches = [m for m in NUMERIC_UNIT_RE.finditer(text)
               if UNIT_CANONICAL.get(m.group('unit').casefold()) in {'ml', 'cl', 'L'}]
    assert len(matches) == 2
    assert [_capacity_scope(text, m) for m in matches] == ['drawer:xxl', 'drawer:explicit_other']
    assert 'CONTRADICTORY_CAPACITY' not in numeric_profile(text)['issues']
    row = audit_records([{'asin': case['asin'], case['field']: text}])['translation_input_records'][0]
    assert row['fields'][case['field']]['translate_allowed']
    assert row['fields'][case['field']]['source_text'] == text
    assert '8,3' not in row['fields'][case['field']]['clean_text']


@pytest.mark.parametrize('alteration', ['remove_cohort', 'remove_other_names', 'unknown_extra', 'xxl_conflict', 'other_conflict'])
def test_other_drawer_proof_requires_the_full_two_drawer_cohort_and_every_quantity(alteration):
    text = ('Capacidad generosa: utiliza el cajón XXL de 5,2 L para cocinar un asado, '
            'ambos cajones para raciones o el cajón de 3,1 L para una porción.\n'
            'Comidas completas: cajón XXL y cajón estándar para dos platos.')
    if alteration == 'remove_cohort':
        text = text.replace('ambos cajones para raciones o ', '')
    elif alteration == 'remove_other_names':
        text = text.replace('cajón XXL y cajón estándar', 'dos cajones')
    elif alteration == 'unknown_extra':
        text += '\nOtro volumen de 4 L'
    elif alteration == 'xxl_conflict':
        text += '\nEl cajón XXL de 4 L'
    else:
        text += '\nEl otro cajón tiene capacidad de 4 L'
    assert 'CONTRADICTORY_CAPACITY' in numeric_profile(text)['issues']
