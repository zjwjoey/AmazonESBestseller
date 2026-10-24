"""Regressions reproduced from the 698-product saved-page review."""
import copy
import gzip
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.collection.detail import parse_detail_page
from amazon_es_bestseller.normalization.specification import build_spec_es, classify_value_unit
from amazon_es_bestseller.qa.validators import validate_spec, validate_brand
from amazon_es_bestseller.qa.field_closure import _audit_one

FIXTURES=Path(__file__).parent/'fixtures/field_review_698'


def saved(asin):
    return gzip.decompress((FIXTURES/(asin+'.html.gz')).read_bytes()).decode('utf-8')


@pytest.mark.parametrize('line_endings', ['original', 'normalized_lf'])
def test_real_saved_seller_survives_script_parser_recovery(line_endings):
    html=saved('B0D9L982GG')
    if line_endings=='normalized_lf':html=html.replace('\r\n','\n')
    raw=parse_detail_page(html,'B0D9L982GG')
    assert raw['seller_raw']=='Haisenbao-EU'
    assert raw['seller_evidence']['source']=='merchantInfoFeature_feature_div'
    if line_endings=='normalized_lf':
        assert raw['seller_evidence']['dom_recovery']=='lxml_scoped_buybox'


def test_seller_never_falls_back_to_shipping_or_recommendation():
    html='''<div id="tabular-buybox"><div>Enviado desde
      <div class="tabular-buybox-text" role="text"><span>Amazon</span></div></div></div>
      <div id="recommendations"><a id="sellerProfileTriggerId">Other seller</a></div>'''
    assert parse_detail_page(html,'B000000001')['seller_raw']==''


def test_real_ring_collects_only_strongly_bound_current_column():
    raw=parse_detail_page(saved('B0FZPRQ9KS'),'B0FZPRQ9KS')
    attrs=[a for a in raw['attributes'] if a['section']=='current_model_comparison']
    assert any(a['label_raw']=='VIDEO QUALITY' and a['value_raw']=='2K' for a in attrs)
    assert any(a['label_raw']=='WIFI' and 'Wi-Fi 4' in a['value_raw'] for a in attrs)
    assert not any('4K' in a['value_raw'] or 'Wi-Fi 6' in a['value_raw'] for a in attrs)
    assert all(a['asin']=='B0FZPRQ9KS' for a in attrs)


@pytest.mark.parametrize('change', ['different_active','missing_active','two_active'])
def test_comparison_requires_unique_current_asin_column(change):
    html=saved('B0FZPRQ9KS')
    if change=='different_active':
        html=html.replace('aria-current="page"','aria-current="false"',1).replace('aria-current="false"','aria-current="page"',1)
        # Explicitly corrupt the current header links instead of using a title.
        html=html.replace('/dp/B0FZPRQ9KS?ref=amzdv_ucc_dp_lod_B0FZPRQ9KS_B0FZPRQ9KS',
                          '/dp/B0GZRSJGW8?ref=amzdv_ucc_dp_lod_B0FZPRQ9KS_B0GZRSJGW8')
    elif change=='missing_active':html=html.replace('aria-current="page"','aria-current="false"')
    else:html=html.replace('aria-current="false"','aria-current="page"',1)
    raw=parse_detail_page(html,'B0FZPRQ9KS')
    assert not any(a['section']=='current_model_comparison' for a in raw['attributes'])


def test_es_spec_selected_capacity_overrides_generic_capacity_without_mutation():
    raw=parse_detail_page(saved('B0001IW56S'),'B0001IW56S');before=copy.deepcopy(raw['attributes'])
    out=build_spec_es(raw['attributes'],variant=raw['selected_variation_raw'],title_es=raw['title_es_raw'])
    assert '0,75 L' in out and '4500 ml' not in out
    assert raw['attributes']==before


def test_es_spec_explicit_variant_count_not_lost_when_dimensions_exist():
    raw=parse_detail_page(saved('B00H52GC2G'),'B00H52GC2G')
    assert '15 piezas' in build_spec_es(raw['attributes'],variant=raw['selected_variation_raw'],title_es=raw['title_es_raw']).lower()


def test_es_spec_single_selected_pack_does_not_become_technical_twelve():
    attrs=[{'label_raw':'Número de artículos','value_raw':'12'},
           {'label_raw':'Dimensiones del producto','value_raw':'10 x 15 cm'}]
    out=build_spec_es(attrs,variant='1 unidad (Paquete de 1)',title_es='Pack de 12 unidades')
    assert '12' not in out and '1 unidad' in out


def test_length_axis_abbreviation_is_not_litres():
    assert classify_value_unit('19l. x 3,8an. centímetros')=='dimension'
    assert classify_value_unit('0,75 L')=='capacity'
    assert classify_value_unit('1 l.')=='capacity'


def test_mixed_dimension_value_and_mass_are_not_volume_mapping_error():
    status,issues=validate_spec({'asin':'B000000001','details_json':{
        'dimensiones_del_producto':'16 x 6,7 x 6,7 cm; 220 g','capacidad':'100 g'},
        'attributes':[{'label_raw':'Capacidad','value_raw':'100 g'}],
        'specification_es':'Capacidad: 100 g / Dimensiones: 16 x 6,7 x 6,7 cm; 220 g'})
    assert not any(i.code=='SPEC_UNIT_MISMATCH' for i in issues)
    # Explicit liquid-volume fields still reject weight.
    assert any(i.code=='SPEC_UNIT_MISMATCH' for i in validate_spec({'details_json':{'volumen_liquido':'100 g'}})[1])


def test_dimension_decimal_is_not_placeholder_substring():
    assert not any(i.code=='SPEC_SUSPICIOUS_VALUE' for i in validate_spec({'specification':'1×1×1,8 cm'})[1])
    assert any(i.code=='SPEC_SUSPICIOUS_VALUE' for i in validate_spec({'specification':'1×1×1 cm'})[1])


def test_capacity_validator_recognizes_spanish_unit_words():
    assert not any(i.code=='SPEC_VARIANT_MISMATCH' for i in validate_spec({
        'selected_variation_raw':'0,75 L','specification_es':'Capacidad: 0,75 Litros'})[1])
    assert any(i.code=='SPEC_VARIANT_MISMATCH' for i in validate_spec({
        'selected_variation_raw':'0,75 L','specification_es':'Capacidad: 4,5 Litros'})[1])


def test_generic_count_one_is_not_claimed_to_override_a_correct_summary():
    record={'details_json':{'numero_de_articulos':'1'},'title_es_raw':'Set de 15 piezas',
            'specification_es':'15 piezas / Tamaño: 10 cm'}
    assert not any(i.code=='SPEC_QUANTITY_CONFLICT' for i in validate_spec(record)[1])


def test_source_audit_never_uses_detail_bsr_as_bestseller_rank():
    record={'asin':'B000000001','ranking_source_type':'NEW_RELEASES','bestseller_rank':None}
    result=_audit_one('bestseller_rank',record['asin'],record,{}, {},'<div>n.º 12 en Hogar y cocina</div>',stage='source')
    assert result.classification=='NOT_APPLICABLE'


def test_category_branch_conflict_is_not_parser_omission():
    record={'asin':'B000000001','category_l1':'Hogar y cocina','category_l2':None,
            'detail_category_trail':['Oficina y papelería','Lápices']}
    result=_audit_one('category_l2',record['asin'],record,record,{'category_l1':'Hogar y cocina'},
        '<div id="wayfinding-breadcrumbs_feature_div"><a>Oficina y papelería</a><a>Lápices</a></div>',stage='source')
    assert result.classification=='CATEGORY_SOURCE_CONFLICT'


def test_real_distinct_source_rows_with_same_fact_are_preserved():
    raw=parse_detail_page(saved('B000QW4W8I'),'B000QW4W8I')
    matches=[a for a in raw['attributes'] if a['label_raw'].casefold()=='total del paquete según la medida elegida para referenciar precio']
    assert len(matches)==2


def test_explicit_long_brand_evidence_is_not_a_guessed_title_prefix():
    raw=parse_detail_page(saved('B002G6I5TY'),'B002G6I5TY')
    assert raw['brand_raw']=='WM Bartleet & Sons 1750'
    assert not any(i.code=='BRAND_FALSE_POSITIVE' for i in validate_brand(raw['brand_raw'],raw['brand_raw'],evidence=raw['brand_evidence'])[1])
    assert any(i.code=='BRAND_FALSE_POSITIVE' for i in validate_brand('Limpiador','Limpiador')[1])


def test_unrelated_brand_proof_does_not_disable_long_brand_guard():
    evidence=[{'source':'bylineInfo','value_raw':'Visita la tienda de Other Brand'}]
    assert any(i.code=='BRAND_FALSE_POSITIVE' for i in
               validate_brand('WM Bartleet & Sons 1750', evidence=evidence)[1])


@pytest.mark.parametrize('html', [
    '<script>var template = "<a id=\"sellerProfileTriggerId\">Wrong Seller</a>";</script>',
    '<div hidden><a id="sellerProfileTriggerId">Hidden Seller</a></div>',
    '<div id="merchantInfoFeature_feature_div"><a id="sellerProfileTriggerId">Seller A</a>'
    '<a id="sellerProfileTriggerId">Seller B</a></div>',
])
def test_seller_recovery_requires_visible_unambiguous_dom(html):
    assert parse_detail_page(html,'B000000001')['seller_raw']==''


def test_monthly_evidence_rejects_recommendation_and_script_noise():
    from amazon_es_bestseller.qa.field_closure import _html_visible_evidence
    assert not _html_visible_evidence('monthly_bought_min', '''
        <div id="recommendations"><div id="social-proofing-faceout">100+ comprados el mes pasado</div></div>
        <script>var text='1000+ comprados el mes pasado';</script>''')
    assert _html_visible_evidence('monthly_bought_min',
        '<div id="social-proofing-faceout">100+ comprados el mes pasado</div>')


def test_detail_evidence_requires_actual_pairs_or_bound_current_column():
    from amazon_es_bestseller.qa.field_closure import _html_visible_evidence
    assert not _html_visible_evidence('product_details_zh',
        '<div id="prodDetails">Características técnicas<table><tr><th></th><td></td></tr></table></div>')
    record={'asin':'B0FZPRQ9KS'}
    result=_audit_one('product_details_zh',record['asin'],record,{}, {},saved(record['asin']),stage='source')
    assert result.classification=='PARSER_MISSED'


def test_non_bestseller_source_with_populated_rank_stays_blocked():
    record={'asin':'B000000001','ranking_source_type':'NEW_RELEASES','bestseller_rank':12}
    result=_audit_one('bestseller_rank',record['asin'],record,record,record,'',stage='source')
    assert result.classification=='FACT_CHANGED' and result.severity=='P1'


def test_real_active_offer_seller_does_not_conflict_with_inactive_offer():
    raw=parse_detail_page(saved('B083ZMBSW1'),'B083ZMBSW1')
    assert raw['seller_raw']=='Villa SAM'


def test_real_single_length_axis_with_metros_is_not_capacity():
    raw=parse_detail_page(saved('B0863MBGTH'),'B0863MBGTH')
    value=next(a['value_raw'] for a in raw['attributes'] if '30l. metros' in a['value_raw'])
    assert classify_value_unit(value)=='dimension'


def test_current_model_column_with_spanning_header_is_ambiguous():
    html='''<div id="mars-comp-chart_feature_div"><table class="ucc-v2-widget__table">
      <tr><th>Device</th><th colspan="2"><a aria-current="page" href="/dp/B000000001">Current</a></th><th>Other</th></tr>
      <tr><th>VIDEO</th><td>2K</td><td>4K</td></tr></table></div>'''
    assert not any(a['section']=='current_model_comparison' for a in
                   parse_detail_page(html,'B000000001')['attributes'])


def test_source_audit_detects_saved_seller_omission_with_same_dom_recovery():
    record={'asin':'B0D9L982GG'}
    result=_audit_one('seller',record['asin'],record,{}, {},saved(record['asin']),stage='source')
    assert result.classification=='PARSER_MISSED'


def test_source_audit_does_not_claim_hidden_attribute_is_visible_evidence():
    from amazon_es_bestseller.qa.field_closure import _html_visible_evidence
    assert not _html_visible_evidence('product_details_zh',
        '<div id="prodDetails" hidden><table><tr><th>Material</th><td>Glass</td></tr></table></div>')


def test_raw_mass_evidence_normalizes_directional_marks_without_changing_raw():
    attrs=[{'label_raw':'Capacidad', 'value_raw':'\u200e500 g'}]
    record={'attributes':attrs, 'details_json':{'capacidad':'500 g'}}
    assert not any(i.code=='SPEC_UNIT_MISMATCH' for i in validate_spec(record)[1])
    assert attrs[0]['value_raw']=='\u200e500 g'


def test_named_brand_with_generic_word_requires_two_concordant_source_controls():
    # B0HK15HRKK exposes Clim Profesional in both its store byline and Marca.
    proof=json.loads((FIXTURES/'B0HK15HRKK.brand-evidence.json').read_text(encoding='utf-8'))['evidence']
    assert not any(i.code=='BRAND_FALSE_POSITIVE' for i in
                   validate_brand('Clim Profesional',evidence=proof)[1])
    assert any(i.code=='BRAND_FALSE_POSITIVE' for i in
               validate_brand('Clim Profesional',evidence=proof[:1])[1])
    generic=[{'source':'bylineInfo','value_raw':'Visita la tienda de Limpiador Universal'},
             {'label_raw':'Marca','value_raw':'Limpiador Universal'}]
    assert any(i.code=='BRAND_FALSE_POSITIVE' for i in
               validate_brand('Limpiador Universal',evidence=generic)[1])
