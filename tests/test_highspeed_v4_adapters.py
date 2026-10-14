import hashlib
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page
from amazon_es_bestseller.highspeed.v4_sources import SEEDS, source_for_url

FIXTURES = Path(__file__).parent / 'fixtures' / 'highspeed_v4'
MANIFEST = json.loads((FIXTURES / 'manifest.json').read_text(encoding='utf8'))


def parse_fixture(name):
    proof = next(x for x in MANIFEST if x['fixture'] == name + '.html')
    path = FIXTURES / proof['fixture']
    return parse_v4_page(path.read_text(encoding='utf8'), source=source_for_url(proof['url']),
                         page_url=proof['url'], run_id='offline_v4', observed_at=proof['observed_at'],
                         evidence_path=str(path), http_status=None, origin='OFFLINE_FIXTURE')


@pytest.mark.parametrize('name,count', [
    ('F01_best_root_complete', 36), ('F02_new_root_complete', 36),
    ('F06b_deals_settled_complete', 18), ('F07_favorites_global_complete', 163),
    ('F08_haul_root_complete', 35), ('S06_favorites_home_complete', 12),
    ('S07_favorites_results_complete', 24), ('S08_flash_complete', 12)])
def test_real_card_boundaries(name, count):
    result = parse_fixture(name)
    assert len(result['observations']) == count
    assert result['page']['status'] == 'VERIFIED_WITH_PRODUCTS'
    assert result['page']['completeness_status'] == 'UNKNOWN'
    assert result['page']['http_status'] is None
    for row in result['observations']:
        assert row['product_title_es']
        assert row['normalized_product_url']
        assert row['source_evidence']['raw_product_url'] == row['raw_product_url']
        assert row['origin'] == 'OFFLINE_FIXTURE'
        if row['source_type'] not in {'BEST_SELLERS', 'NEW_RELEASES'}:
            assert row['ranking_position'] is None


def test_real_monthly_price_and_evaluation_semantics():
    rows = parse_fixture('S06_favorites_home_complete')['observations']
    arcos = next(r for r in rows if 'B0812BKN39' in r['raw_product_url'])
    assert arcos['rating_value'] == 4.8
    assert arcos['rating_count'] == 6956
    assert arcos['monthly_bought_min'] == 5000
    assert arcos['current_price'] == 5.95
    assert arcos['reference_price_raw']
    assert arcos['written_review_count'] is None
    assert arcos['monthly_bought_semantics'] == 'PUBLIC_PURCHASE_LOWER_BOUND'
    assert arcos['ranking_position'] is None


def test_loading_frame_is_not_official_empty():
    result = parse_fixture('F06_deals_root_complete')
    assert not result['observations']
    assert result['page']['status'] == 'UNVERIFIED'
    assert result['page']['completeness_status'] == 'UNKNOWN'


def test_no_implicit_search_source_or_foreign_url():
    for url in ['https://www.amazon.es/s?k=anything', 'https://evil.example/deals',
                'https://www.amazon.es/deals-pretend']:
        with pytest.raises(ValueError):
            source_for_url(url)


def test_real_haul_signals_do_not_become_sales():
    rows = parse_fixture('F08_haul_root_complete')['observations']
    assert any(r['promotion_badge_raw'] for r in rows)
    assert any(r['cart_interest_raw'] for r in rows)
    assert all(r['monthly_bought_min'] is None and r['ranking_position'] is None for r in rows)
    assert all(r['current_price'] is not None for r in rows)


def test_fixtures_are_complete_immutable_dom_not_live_receipts():
    for proof in MANIFEST:
        body = (FIXTURES / proof['fixture']).read_bytes()
        assert hashlib.sha256(body).hexdigest() == proof['sha256']
        assert b'[Truncated]' not in body
        assert proof['http_status'] is None
        assert proof['origin'] == 'OFFLINE_FIXTURE'


@pytest.mark.parametrize('code', [403, 429, 503])
def test_access_restricted_no_rows(code):
    proof = MANIFEST[7]
    source = source_for_url(SEEDS[2]['url'])
    result = parse_v4_page('<div data-testid="product-card"></div>', source=source,
                           page_url=source['actual_source_url'], run_id='r',
                           observed_at=proof['observed_at'], evidence_path='x', http_status=code)
    assert not result['observations']
    assert result['page']['status'] == 'ACCESS_RESTRICTED'
