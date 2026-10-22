from copy import deepcopy
from bs4 import BeautifulSoup
import pytest

from test_highspeed_v4_adapters import parse_fixture, FIXTURES, MANIFEST
from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page
from amazon_es_bestseller.highspeed.v4_sources import source_for_url
from amazon_es_bestseller.highspeed.v3_links import normalize_product_url_v3


def mutated_home(change):
    name = 'S06_favorites_home_complete'
    proof = next(x for x in MANIFEST if x['fixture'] == name + '.html')
    soup = BeautifulSoup((FIXTURES / proof['fixture']).read_text(encoding='utf8'), 'lxml')
    change(soup)
    return parse_v4_page(str(soup), source=source_for_url(proof['url']), page_url=proof['url'],
                         run_id='mutation', observed_at=proof['observed_at'], evidence_path='mutation.html')


def test_foreign_review_anchor_cannot_supply_rating():
    def change(soup):
        card = soup.select_one('[data-cy="asin-faceout-container"]')
        a = soup.new_tag('a', href='/product-reviews/FOREIGN123', attrs={'aria-label': '900 calificaciones'})
        card.append(a)
    result = mutated_home(change)
    assert result['observations'][0]['rating_count_status'] == 'UNVERIFIED'
    assert result['observations'][0]['rating_count'] is None


def test_history_widget_cannot_supply_candidates():
    def change(soup):
        history = soup.new_tag('div', id='rhf')
        history.append(deepcopy(soup.select_one('[data-cy="asin-faceout-container"]')))
        soup.body.append(history)
    assert len(mutated_home(change)['observations']) == 12


def test_same_card_conflicting_rating_is_not_chosen():
    def change(soup):
        card = soup.select_one('[data-cy="asin-faceout-container"]')
        card.append(soup.new_tag('span', attrs={'aria-label': '1,2 de 5 estrellas'}))
    first = mutated_home(change)['observations'][0]
    assert first['rating_status'] == 'SOURCE_CONFLICT'
    assert first['rating_value'] is None


def test_root_rank_contexts_are_not_global_1_to_36():
    rows = parse_fixture('F01_best_root_complete')['observations']
    assert len({r['rank_context'] for r in rows}) == 6
    assert {r['ranking_position'] for r in rows} == {1, 2, 3, 4, 5, 6}


def test_real_pagination_is_discovered_but_not_approved_or_visited():
    result = parse_fixture('S07_favorites_results_complete')
    assert result['pagination']
    assert any('Siguiente' in n['label_raw'] for n in result['pagination'])
    assert all(n['approved'] is False and n['status'] == 'DISCOVERED_NOT_VISITED' for n in result['pagination'])


def test_variants_and_unknown_parameters_stay_separate():
    urls = ['/dp/OPAQUE0001?psc=1&color=red', '/dp/OPAQUE0001?psc=1&color=blue',
            '/dp/OPAQUE0001?mystery=a', '/dp/OPAQUE0001?mystery=b']
    normalized = [normalize_product_url_v3(u) for u in urls]
    assert len({r['candidate_id'] for r in normalized}) == 4
    assert all(r['url_identity_status'] == 'IDENTITY_UNCONFIRMED' for r in normalized)


def test_live_unknown_http_and_challenge_cannot_be_products():
    p = MANIFEST[0]
    s = source_for_url(p['url'])
    with pytest.raises(ValueError, match='HTTP_RECEIPT'):
        parse_v4_page('', source=s, page_url=p['url'], run_id='r', observed_at=p['observed_at'],
                      evidence_path='x', origin='LIVE_CAPTURE')
    result = parse_v4_page('<h1>Robot Check</h1>', source=s, page_url=p['url'], run_id='r',
                           observed_at=p['observed_at'], evidence_path='x', http_status=200)
    assert result['page']['status'] == 'ACCESS_RESTRICTED'
    assert not result['observations']
