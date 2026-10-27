import json
from pathlib import Path

import pytest

from amazon_es_bestseller.normalization.specification import (
    _title_core_spec_es,
    build_spec_es,
    translate_spec_es_to_zh,
)


def saved_case():
    path = Path(__file__).parent / 'fixtures/html/remaining_review_20261024/mixed_axis_units_B01M3NSF41.json'
    return json.loads(path.read_text(encoding='utf-8'))


def test_saved_title_keeps_both_axis_values_and_units():
    case = saved_case()
    assert build_spec_es(title_es=case['title_es']) == case['expected_specification_es']


def test_saved_title_dimension_translation_keeps_each_axis_unit():
    case = saved_case()
    assert translate_spec_es_to_zh(case['expected_specification_es']) == case['expected_specification_zh']


@pytest.mark.parametrize(('title', 'spec', 'zh'), [
    ('Marco 1,4 m × 22 mm', '1,4 m × 22 mm', '1.4米×22毫米'),
    ('Pieza 3 cm x 25 mm x 0,2 m', '3 cm x 25 mm x 0,2 m', '3厘米×25毫米×0.2米'),
    ('Pieza 10 centímetros * 15 milímetros', '10 centímetros * 15 milímetros', '10厘米×15毫米'),
    ('Marco 10 x 15 cm', '10 x 15 cm', '10×15厘米'),
])
def test_axis_units_are_not_replaced_by_one_trailing_unit(title, spec, zh):
    assert _title_core_spec_es(title) == spec
    assert translate_spec_es_to_zh(spec) == zh


def test_capacity_package_is_not_misread_as_two_length_axes():
    assert _title_core_spec_es('Pack de 2 x 500 ml') == '500 ml'


def test_explicit_selected_variant_still_precedes_title_dimensions():
    assert build_spec_es(variant='25 mm', title_es='Palo 140 cm x 22 mm') == '25 mm'
