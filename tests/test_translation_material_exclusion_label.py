import json
from pathlib import Path

from amazon_es_bestseller.translation.dictionary_service import DictionaryService


def test_material_free_label_preserves_negation_from_saved_detail_tables():
    # Saved B078SHCNPR: Material=Gres, Sin tipo de material=Plástico.
    # The exclusion value must never become a positive material claim.
    service = DictionaryService()
    fixture = Path(__file__).parent / 'fixtures/html/remaining_review_20261024/material_exclusion_B078SHCNPR.json'
    case = json.loads(fixture.read_text(encoding='utf8'))
    for row in case['rows']:
        assert service.lookup_attribute_label(row['label_raw']) == row['expected_label_zh']
    assert service.lookup_attribute_label(' sin TIPO de material ') == '不含材质'


def test_material_exclusion_does_not_relabel_positive_or_unknown_fields():
    service = DictionaryService()
    assert service.lookup_attribute_label('Material') == '材质'
    assert service.lookup_attribute_label('Sin tipo de material adicional') is None


def test_unresolved_material_code_does_not_become_a_dictionary_claim():
    service = DictionaryService()
    assert service.lookup_value('[TODO_EXCLUSO_CERÁMICA]', field='Sin tipo de material') is None
