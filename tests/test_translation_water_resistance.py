"""Do not strengthen water-resistant evidence into waterproof guarantees."""
import json
from pathlib import Path

from amazon_es_bestseller.translation.terminology import postprocess
from amazon_es_bestseller.translation.dictionary_service import DictionaryService


def test_saved_field_preserves_water_resistance_degree():
    case = json.loads((Path(__file__).parent / 'fixtures/translation/water_resistance_B00OZREIMK.json').read_text(encoding='utf8'))
    assert postprocess('product_details_es', case['before'], case['source']) == case['after']


def test_waterproof_sibling_does_not_overwrite_local_resistant_value():
    source = 'Características especiales: Impermeable\nNivel de resistencia al agua: Resistente al agua'
    target = '特殊功能：防水\n防水等级：防水'
    assert postprocess('product_details_es', target, source) == '特殊功能：防水\n防水等级：耐水'


def test_semicolon_detail_rows_keep_other_claims_and_identity():
    source = 'Nivel de resistencia al agua: Resistente al agua'
    target = '功能：防水；防水等级：防水；型号：防水'
    assert postprocess('product_details_es', target, source) == '功能：防水；防水等级：耐水；型号：防水'


def test_structured_values_are_deterministic_and_keep_degrees_distinct():
    dictionary = DictionaryService()
    assert dictionary.lookup_value('Resistente al agua', field='Nivel de resistencia al agua') == '耐水'
    assert dictionary.lookup_value('A prueba de agua', field='Nivel de resistencia al agua') == '防水'
    assert postprocess('product_details', '防水', 'Resistente al agua') == '耐水'


def test_missing_negative_ambiguous_or_waterproof_source_cannot_be_relabelled():
    target = '防水等级：防水'
    for source in ['', 'No es resistente al agua', 'Nivel de resistencia al agua: A prueba de agua',
                   'Nivel de resistencia al agua: Resistente al agua\nNivel de resistencia al agua: A prueba de agua']:
        assert postprocess('product_details_es', target, source) == target
    assert postprocess('product_details', '防水', 'No resistente al agua') == '防水'
    assert DictionaryService().lookup_value('No resistente al agua', field='Nivel de resistencia al agua') is None


def test_identity_and_nonmatching_claims_are_not_rewritten():
    assert postprocess('brand', '防水', 'Resistente al agua') == 'Resistente al agua'
    assert postprocess('title_es_raw', '防水', 'Resistente al agua') == '防水'
    target = '型号：防水\n功能：防水\n防水等级：IP44'
    assert postprocess('product_details_es', target, 'Nivel de resistencia al agua: Resistente al agua') == target
