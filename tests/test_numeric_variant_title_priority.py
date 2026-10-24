"""Numeric selection/title consistency; all HTML mutations are TEST_ONLY."""
import copy
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from amazon_es_bestseller.collection.detail import parse_detail_page
from amazon_es_bestseller.normalization.specification import build_spec_es

FIXTURE = Path(__file__).parent / 'fixtures/html/numeric_variant_title_20261024/B01KCEVQBK_selected_title.html'


@pytest.mark.parametrize('input_mode', ['empty', 'attributes', 'legacy'])
@pytest.mark.parametrize('variant,title', [
    ('750', 'TEST_ONLY Botella de agua 500 ml'),
    ('43', 'TEST_ONLY Plantillas talla 43, 27 x 9 cm'),
    ('27', 'TEST_ONLY Plantillas 27 x 9 cm'),
    ('750', 'TEST_ONLY Botella 0,75 L'),
    ('4', 'TEST_ONLY Juego de 3 piezas'),
    ('4', 'TEST_ONLY Adaptador de 3ª a 4ª generación'),
])
def test_conflicting_or_multivalue_title_never_replaces_numeric_selection(input_mode, variant, title):
    attrs=[{'label_raw':'Marca','value_raw':'TEST_ONLY','unknown_raw':'retain'}] if input_mode=='attributes' else None
    details={'marca':'TEST_ONLY'} if input_mode=='legacy' else None
    before=copy.deepcopy((attrs,details))
    assert build_spec_es(attrs,details,variant,title)==variant
    assert (attrs,details)==before


@pytest.mark.parametrize('variant,title,expected', [
    ('142', 'TEST_ONLY Adhesivo 1 tubo x 142 g', '142 g'),
    ('142.0', 'TEST_ONLY Adhesivo 142 g', '142 g'),
    ('1,50', 'TEST_ONLY Recipiente 1.5 L', '1.5 L'),
    ('1.5', 'TEST_ONLY Recipiente 1,50 L', '1,50 L'),
    ('24', 'TEST_ONLY Transformador 24 V', '24 V'),
    ('3', 'TEST_ONLY Juego de 3 piezas', 'Juego de 3'),
])
def test_single_matching_title_number_preserves_literal_unit_evidence(variant,title,expected):
    assert build_spec_es(variant=variant,title_es=title)==expected


@pytest.mark.parametrize('variant,title,expected', [
    ('750 ml','TEST_ONLY Botella 500 ml','750 ml'),
    ('43 EU','TEST_ONLY Plantillas 27 x 9 cm','43 EU'),
    ('43','TEST_ONLY Calzado de trabajo','43'),
    (None,'TEST_ONLY Botella 500 ml','500 ml'),
])
def test_explicit_or_absent_variation_keeps_existing_fallbacks(variant,title,expected):
    assert build_spec_es(variant=variant,title_es=title)==expected


def test_real_saved_title_and_numeric_selection_still_produce_142_grams():
    record=parse_detail_page(FIXTURE.read_text(encoding='utf-8'),'B01KCEVQBK')
    before=copy.deepcopy(record)
    assert record['selected_variation_raw']=='142'
    assert build_spec_es(record['attributes'],variant=record['selected_variation_raw'],title_es=record['title_es_raw'])=='142 g'
    assert record==before


def test_saved_selected_dom_survives_a_test_only_conflicting_title():
    dom=BeautifulSoup(FIXTURE.read_text(encoding='utf-8'),'lxml')
    # This is deliberately not a claim about an actual Amazon page.
    dom.select_one('#productTitle').string='TEST_ONLY Adhesivo 1 tubo x 100 g'
    record=parse_detail_page(str(dom),'B01KCEVQBK');before=copy.deepcopy(record)
    assert record['selected_variation_raw']=='142'
    assert build_spec_es(record['attributes'],variant=record['selected_variation_raw'],title_es=record['title_es_raw'])=='142'
    assert record==before
