import gzip
import hashlib
from pathlib import Path

import pytest

from amazon_es_bestseller.collection.detail import parse_detail_page
from amazon_es_bestseller.collection.detail_v2 import parse_detail_evidence_v2
from amazon_es_bestseller.pipeline import normalize_product
from amazon_es_bestseller.collection.candidate_snapshot import parse_saved_candidate_pages
from amazon_es_bestseller.monitoring.candidate_detail_handoff import verify_candidate_selection


def test_saved_aplus_is_preserved_separately():
    path = Path(__file__).parent / 'fixtures/detail_completeness/B0167414DE_aplus.html.gz'
    result = parse_detail_page(gzip.decompress(path.read_bytes()).decode('utf8'), 'B0167414DE')
    assert any('alimentación responsable' in r['text_raw'] for r in result['aplus_content_raw'])
    assert not result['attributes']
    assert not result['product_description_raw']


def test_aplus_does_not_promote_recommendations_hidden_or_modal_text():
    body = '''<div id="aplus_feature_div"><p>Producto actual</p>
    <div class="aplus-comparison-table"><p>Otro producto</p></div>
    <div class="a-carousel"><p>Recomendado</p></div>
    <div role="dialog"><p>Modal</p></div><p hidden>Secreto</p></div>'''
    result = parse_detail_page(body, 'B000000001')
    assert [r['text_raw'] for r in result['aplus_content_raw']] == ['Producto actual']


def test_explicit_technical_brand_and_manufacturer_are_distinct():
    body = '''<table id="productDetails_techSpec_section_1">
    <tr><th>Marca</th><td>Marca real</td></tr>
    <tr><th>Fabricante</th><td>Fábrica real</td></tr>
    <tr><th>Atributo nuevo</th><td>A</td></tr>
    <tr><th>Atributo nuevo</th><td>B</td></tr></table>'''
    result = parse_detail_page(body, 'B000000001')
    assert result['brand_raw'] == 'Marca real'
    assert result['manufacturer_raw'] == 'Fábrica real'
    assert [a['value_raw'] for a in result['attributes'] if a['label_raw'] == 'Atributo nuevo'] == ['A', 'B']


def test_saved_bidi_manufacturer_label_keeps_original_evidence():
    path = Path(__file__).parent / 'fixtures/detail_completeness/B0167414DE_details.html.gz'
    record = parse_detail_page(gzip.decompress(path.read_bytes()).decode('utf8'), 'B0167414DE')
    assert 'Laboratorios Almond' in record['manufacturer_raw']
    assert any('\u200f' in a['label_raw'] for a in record['attributes'])


@pytest.mark.parametrize('kind', ['best_sellers', 'hot_new_releases', 'customer_favorites'])
def test_all_ranking_sources_keep_their_original_path_and_url(kind):
    context = {'ranking_source_type': kind, 'ranking_source_url': 'https://www.amazon.es/source',
               'ranking_source_category_path': 'Hogar > Cocina > Café > Molinillos'}
    result = parse_detail_evidence_v2('<div id="desktop-breadcrumbs_feature_div"><a>Electrónica</a></div>',
                                      'B000000001', ranking_context=context)
    evidence = result['category_evidence']
    assert evidence['ranking_category_path'] == ['Hogar', 'Cocina', 'Café', 'Molinillos']
    assert evidence['ranking_source_type'] == kind
    assert evidence['ranking_source_url'] == context['ranking_source_url']
    assert evidence['product_breadcrumb_category_path'] == ['Electrónica']


def test_normalization_never_splices_unrelated_ranking_and_product_paths():
    result = normalize_product({'asin': 'B000000001', 'category_l1': 'Hogar',
        'category_l2': 'Cocina', 'detail_category_trail': ['Electrónica', 'Audio', 'Auriculares']})
    assert result['category_l1'] == 'Hogar'
    assert result['category_l2'] == 'Cocina'
    assert not result.get('category_l3')
    assert result['detail_category_trail'] == ['Electrónica', 'Audio', 'Auriculares']


@pytest.mark.parametrize('kind', ['BEST_SELLERS', 'NEW_RELEASES', 'CUSTOMER_FAVORITES'])
def test_saved_identity_handoff_preserves_typed_source_context(kind):
    # Actual ranking/detail identity pair; alternate type values exercise the
    # metadata adapter only, not source-page classification or fake captures.
    root = Path(__file__).parent / 'fixtures/consolidation_v2'
    ranking = root / 'real_ranking_tools.html'
    snapshot = parse_saved_candidate_pages([{'source_type': 'BEST_SELLERS',
        'source_url': 'https://www.amazon.es/gp/bestsellers/tools/3049288031',
        'html_path': str(ranking), 'sha256': hashlib.sha256(ranking.read_bytes()).hexdigest(),
        'status_code': 200}], snapshot_id='handoff-category', observed_at='2026-10-01T21:21:23+02:00')
    candidate = next(c for c in snapshot['candidates'] if 'B013ERLQDK' in c['candidate_asins_observed'])
    for observation in candidate['observations']:
        observation['source_type'] = kind
        observation['ranking_source_category_path'] = 'Bricolaje > Herramientas'
    result = verify_candidate_selection(snapshot, root, candidate_ids=[candidate['candidate_id']])
    row = result['records'][0]
    assert row['ranking_source_type'] == kind
    evidence = row['detail_record']['category_evidence']
    assert evidence['ranking_category_path'] == ['Bricolaje', 'Herramientas']
    assert evidence['ranking_source_type'] == kind
