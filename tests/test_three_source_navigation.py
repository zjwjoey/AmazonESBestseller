"""Real saved navigation: no HTTP and no fabricated destination supply."""
import hashlib
from pathlib import Path
from copy import deepcopy
import pytest
from amazon_es_bestseller.highspeed import three_navigation as nav
from amazon_es_bestseller.highspeed.v4_sources import source_for_url, SEEDS
from test_highspeed_v4_pagination_chain import real_chain

FIX = Path(__file__).parent / 'fixtures'


def context(path, source):
    source = deepcopy(source); source.setdefault('page_urls', [source['actual_source_url']])
    p = {'source_id': source['source_id'], 'source_type': source['source_type'],
         'source_subtype': source['source_subtype'], 'page_url': source['actual_source_url'],
         'evidence_path': str(path), 'evidence_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
         'http_status': 200, 'access_status': 'NORMAL', 'status': 'VERIFIED_WITH_PRODUCTS',
         'observed_at': '2026-10-15T01:00:00Z', 'completeness_status': 'UNKNOWN'}
    return p, source


def test_real_favorites_themes_are_owned_pending_links_not_products():
    p, s = context(FIX/'highspeed_v4/F07_favorites_global_complete.html', source_for_url(SEEDS[4]['url']))
    rows = nav.discover_navigation(p, s)
    home = next(r for r in rows if r['label_raw'] == 'Hogar')
    assert '/cml-home?' in home['url'] and home['relationship'] == 'THEME_LINK'
    assert home['status'] == 'PENDING_REVIEW' and not home['approved']
    assert any(r['label_raw'] == 'Tecnología' for r in rows)
    assert not any('/dp/' in r['url'] or 'product-reviews' in r['url'] for r in rows)
    nav.verify_navigation_reference(home, s)


def test_real_favorites_promo_tile_does_not_promote_amazon_resale_to_collection():
    p,s=context(FIX/'highspeed_v4/F07_favorites_global_complete.html',source_for_url(SEEDS[4]['url']))
    rows=nav.discover_navigation(p,s)
    resale=next(r for r in rows if '/amazonresale?' in r['url'])
    assert resale['status']=='UNVERIFIED' and resale['relationship']=='OUTSIDE_SOURCE_LINK'
    with pytest.raises(ValueError):nav.verify_navigation_reference(resale,s)


def test_real_kitchen_children_and_ranked_next_page_are_distinct():
    url = 'https://www.amazon.es/gp/bestsellers/kitchen/ref=zg_bs_kitchen_sm'
    s = source_for_url(SEEDS[0]['url']); s.update(actual_source_url=url, page_urls=[url])
    p, s = context(FIX/'highspeed/real_v4_t2_kitchen_grid.html', s)
    rows = nav.discover_navigation(p, s)
    child = next(r for r in rows if '2165211031' in r['url'])
    assert child['relationship'] == 'CHILD_CATEGORY' and child['browse_node_id'] == '2165211031'
    nxt = next(r for r in rows if r['relationship'] == 'NEXT_PAGE')
    assert 'pg=2' in nxt['url']; nav.verify_next(nxt, s, parent_page_url=p['page_url'])
    with pytest.raises(ValueError): nav.verify_next({**nxt, 'url': nxt['url'].replace('pg=2','pg=3')}, s, parent_page_url=p['page_url'])


def test_real_next_favorites_keeps_original_chain():
    s = real_chain(); p, _ = context(FIX/'highspeed_v4/loading/favorites_chain_page2.html', s)
    p['page_url'] = s['page_urls'][1]
    rows = nav.discover_navigation(p, s)
    nxt = next(r for r in rows if r['relationship'] == 'NEXT_PAGE')
    assert nxt['url'] == s['page_urls'][2]
    nav.verify_next(nxt, s, parent_page_url=p['page_url'])


@pytest.mark.parametrize('field,value', [('raw_href','/s?k=unapproved'), ('parent_source_id','invented'),
    ('evidence_sha256','0'*64), ('label_raw','fabricated'), ('anchor_dom_index',-1)])
def test_provenance_tampering_is_rejected(field, value):
    p, s = context(FIX/'highspeed_v4/F07_favorites_global_complete.html', source_for_url(SEEDS[4]['url']))
    row = nav.discover_navigation(p, s)[0]
    with pytest.raises(ValueError): nav.verify_navigation_reference({**row, field:value}, s)


def test_unknown_access_does_not_create_pending_scope():
    p, s = context(FIX/'highspeed_v4/F07_favorites_global_complete.html', source_for_url(SEEDS[4]['url']))
    p['access_status'] = 'UNKNOWN'
    assert all(r['status'] == 'UNVERIFIED' for r in nav.discover_navigation(p,s))


def test_tree_keeps_unvisited_nodes_and_does_not_invent_depth_or_leaf():
    p, s = context(FIX/'highspeed_v4/F01_best_root_complete.html', source_for_url(SEEDS[0]['url']))
    tree = nav.build_navigation_tree([p], [s])
    child = next(n for n in tree['nodes'] if any('kitchen/' in u for u in n['actual_urls']))
    assert child['visit_status'] == 'UNVISITED' and child['leaf_confirmed'] is False
    assert child['parent_ids'] and tree['global_tree_complete'] is False
    assert all(n['approved'] is False for n in tree['nodes'])
