"""A singular Spanish part number is an identifier, not a piece count."""
import copy
import json
from pathlib import Path

from amazon_es_bestseller.normalization.specification import (
    attributes_to_spec_dict, build_spec_v2, build_spec_es, resolve_package_count,
)

CASE = Path(__file__).parent / 'fixtures/html/remaining_review_20261024/part_number_B00W9MIMN4.json'


def test_saved_part_number_never_becomes_millions_of_pieces():
    case = json.loads(CASE.read_text(encoding='utf-8'))
    attrs = case['attributes']; original = copy.deepcopy(attrs)
    details = attributes_to_spec_dict(attrs)
    assert details.get('numero_de_pieza') == '5196378'
    assert details.get('numero_de_piezas') != '5196378'
    assert resolve_package_count(details) is None
    assert '5196378件' not in build_spec_v2(details)
    assert build_spec_es(attributes=attrs) == 'Número de modelo: 5196378'
    assert attrs == original


def test_real_piece_count_is_not_displaced_by_numeric_part_identifier():
    attrs = [{'label_raw':'Número de pieza','value_raw':'5196378'},
             {'label_raw':'Número de piezas','value_raw':'4'}]
    details = attributes_to_spec_dict(attrs)
    assert details['numero_de_pieza'] == '5196378'
    assert details['numero_de_piezas'] == '4'
    assert resolve_package_count(details) == 4
    assert build_spec_v2(details) == '4件'


def test_part_identifier_with_leading_zeros_remains_opaque():
    details = attributes_to_spec_dict([{'label_raw':'Número de pieza','value_raw':'010106'}])
    assert details['numero_de_pieza'] == '010106'
    assert resolve_package_count(details) is None
