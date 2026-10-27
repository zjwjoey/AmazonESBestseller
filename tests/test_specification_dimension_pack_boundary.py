"""Saved-page regression: 40x70 Pack 6 is six towels, not seventy packs."""
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.normalization.specification import (
    build_spec_es, resolve_package_count, _title_core_spec_es,
)

CASE = Path(__file__).parent / 'fixtures/html/remaining_review_20261024/dimension_pack_B0GY51GZ3T.json'


def test_saved_towel_title_does_not_turn_dimension_axis_into_package_count():
    case = json.loads(CASE.read_text(encoding='utf-8'))
    attrs_before = json.dumps(case['attributes'], ensure_ascii=False)
    assert resolve_package_count({}, title_es=case['title_es']) == 6
    spec = build_spec_es(attributes=case['attributes'], variant='Aqua', title_es=case['title_es'])
    assert 'Cantidad explícita: Pack 6' in spec
    assert '70 Pack' not in spec
    assert '70l. x 40an. centímetros' in spec
    assert json.dumps(case['attributes'], ensure_ascii=False) == attrs_before


@pytest.mark.parametrize('axes', ['40x70', '40 x 70', '40×70', '40 * 70'])
def test_dimension_axis_before_pack_is_not_a_count(axes):
    title = f'Toallas {axes} Pack 6 Algodón'
    assert resolve_package_count({}, title_es=title) == 6
    assert _title_core_spec_es(title) == 'Pack 6'


@pytest.mark.parametrize(('title', 'count'), [
    ('Juego 6 Toallas 40x70 Pack 6', 6),
    ('Toallas 40x70 cm Pack de 6', 6),
    ('Pack de 2 toallas 40x70', 2),
    ('6 packs de pañuelos', 6),
    ('Toallas 40 x 70 Pack', None),
])
def test_real_count_and_unresolved_unitless_axes_stay_distinct(title, count):
    assert resolve_package_count({}, title_es=title) == count


def test_selected_packaging_still_has_priority_and_multiplies_units():
    assert resolve_package_count({}, variant='1 Unidad (Paquete de 12)', title_es='Pack 6') == 12
    assert resolve_package_count({}, variant='2 Unidades (Paquete de 3)', title_es='Pack 6') == 6
