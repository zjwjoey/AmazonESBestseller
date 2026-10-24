"""Saved-page regressions from the 305+114 follow-up review; no live access."""
import copy
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.collection.detail import parse_detail_page, struck_price_from_html
from amazon_es_bestseller.normalization.specification import (
    attributes_to_spec_dict, build_spec_es, is_suspicious_dimension,
)

FIXTURES = Path(__file__).parent / 'fixtures/html/remaining_review_20261024'
CASES = json.loads((FIXTURES / 'spec_source_cases.json').read_text(encoding='utf-8'))


def test_real_unselected_variant_original_price_is_not_current_product_price():
    html = (FIXTURES / 'B00006JSUA_variant_prices.html').read_text(encoding='utf-8')
    parsed = parse_detail_page(html, 'B00006JSUA')
    assert parsed['current_price_raw'] == '48,01€'
    assert parsed['original_price_raw'] == ''
    assert struck_price_from_html(html) == ''


@pytest.mark.parametrize('wrapper', [
    '<div id="variation_size_name">{}</div>',
    '<div id="twister_feature_div">{}</div>',
    '<div class="apex_on_twister_price">{}</div>',
])
def test_other_variation_price_is_rejected_even_when_above_current(wrapper):
    other = '<div id="apex_price"><span class="a-text-price" data-a-strike="true"><span class="a-offscreen">99,99€</span></span></div>'
    html = '<div id="corePrice_feature_div"><span class="a-price"><span class="a-offscreen">48,01€</span></span></div>' + wrapper.format(other)
    assert parse_detail_page(html, 'B00006JSUA')['original_price_raw'] == ''


def test_variation_price_to_pay_is_not_a_buybox_fallback():
    html = '<div id="variation_color_name"><span class="a-price priceToPay"><span class="a-offscreen">12,00€</span></span></div>'
    assert parse_detail_page(html, 'B00006JSUA')['current_price_raw'] == ''


def test_real_buybox_original_price_survives_a_preceding_variant_card():
    html = '<div class="apex_on_twister_price" id="apex_price"><span class="a-text-price" data-a-strike="true"><span class="a-offscreen">15,00€</span></span></div>'
    html += '<div id="corePrice_feature_div"><span class="a-price"><span class="a-offscreen">48,01€</span></span><span class="a-text-price" data-a-strike="true"><span class="a-offscreen">59,99€</span></span></div>'
    assert parse_detail_page(html, 'B00006JSUA')['original_price_raw'] == '59,99€'


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['asin'])
@pytest.mark.parametrize('legacy', [False, True], ids=['attributes', 'legacy-details'])
def test_real_source_anomalies_are_preserved_raw_but_not_promoted_to_core_spec(case, legacy):
    before = copy.deepcopy(case)
    details = attributes_to_spec_dict(case['attributes'])
    result = build_spec_es(attributes=None if legacy else case['attributes'], details=details,
                           variant=case['selected_variation_raw'], title_es=case['title_es_raw'])
    assert '1 x 1 x 1' not in result and '1×1×1' not in result
    if 'SPEC_UNIT_MISMATCH' in case['issue_codes']:
        assert 'Capacidad:' not in result and 'capacidad:' not in result
        assert 'Capacidad de salida:' not in result
    assert case == before


@pytest.mark.parametrize('value', ['1 x 1 x 1 cm; 550 g', '1×1×1 centímetros; 160 g', '1,0 x 1,0 x 1,0 cm; 1 g'])
def test_placeholder_dimension_with_explicit_weight_suffix_is_still_placeholder(value):
    assert is_suspicious_dimension(value)


@pytest.mark.parametrize('value', ['1 x 1 x 10 cm; 550 g', '1 x 10 x 1 cm', '11 x 1 x 1 cm', '1 x 1 x 1 litros'])
def test_non_placeholder_lengths_and_volume_text_are_not_rejected(value):
    assert not is_suspicious_dimension(value)


def test_valid_dimensions_capacity_and_explicit_generic_mass_capacity_survive():
    attrs = [{'label_raw':'Dimensiones del producto','value_raw':'1 x 1 x 10 cm; 550 g'},
             {'label_raw':'Capacidad','value_raw':'30 Litros'}]
    result = build_spec_es(attrs)
    assert '1 x 1 x 10 cm; 550 g' in result and '30 Litros' in result
    assert '250 Gramos' in build_spec_es([{'label_raw':'Capacidad','value_raw':'250 Gramos'}])


def test_bare_variant_number_does_not_hide_exact_title_measure_after_bad_dimensions():
    case=next(c for c in CASES if c['asin']=='B01KCEVQBK')
    assert case['selected_variation_raw']=='142'
    assert build_spec_es(case['attributes'],variant='142',title_es=case['title_es_raw'])=='142 g'


def test_numeric_selected_size_without_a_better_title_measure_is_preserved():
    assert build_spec_es(variant='43',title_es='Calzado de trabajo')=='43'
