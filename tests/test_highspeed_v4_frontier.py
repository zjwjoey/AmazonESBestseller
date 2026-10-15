"""Saved official DOM navigation regressions; never make network requests."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import pytest

from amazon_es_bestseller.highspeed import v4_frontier as frontier
from amazon_es_bestseller.highspeed.v4_navigation import verify_discovered_source
from amazon_es_bestseller.highspeed.v4_sources import SEEDS, source_for_url

FIXTURES = Path(__file__).parent / 'fixtures' / 'highspeed_v4'


def page_source(index=0, filename='F01_best_root_complete.html'):
    source = source_for_url(SEEDS[index]['url'])
    source['page_urls'] = [source['actual_source_url']]
    path = FIXTURES / filename
    page = {**{k: source[k] for k in ('source_id', 'source_type', 'source_subtype')},
        'page_url': source['actual_source_url'], 'evidence_path': str(path),
        'evidence_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'http_status': 200, 'access_status': 'NORMAL', 'status': 'VERIFIED_WITH_PRODUCTS',
        'completeness_status': 'UNKNOWN', 'observed_at': '2026-10-15T02:00:00Z'}
    return page, source


def write_dom(tmp_path, page, soup):
    path = tmp_path / 'modified_offline_fixture.html'
    path.write_text(str(soup), encoding='utf8')
    return {**page, 'evidence_path': str(path), 'evidence_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def stage(page, source):
    return {'run_id': 'test_only', 'pages': [page], 'sources': [source],
        'capacity_audit': {'sample_goal': 2000, 'sample_goal_shortfall': 1381}}


def test_saved_rank_root_includes_sidebar_and_carousel_but_no_products():
    page, source = page_source()
    rows, excluded = frontier.extract_page_navigation(page, source)
    assert any('zg_bs_nav_' in r['raw_href'] for r in rows)
    assert any('zg_bs_kitchen_sm' in r['raw_href'] for r in rows)
    assert all('/dp/' not in r['url'] for r in rows)
    assert all(r['destination_product_supply'] == 'UNVERIFIED' and not r['approved'] for r in rows)
    assert excluded.get('PRODUCT_LINK', 0) + excluded.get('PRODUCT_AD_HISTORY_OR_FOOTER', 0) > 0


def test_actual_t2_category_sidebar_and_root_alias_are_preserved():
    page, source = page_source()
    path = FIXTURES.parent / 'highspeed' / 'real_v4_t2_kitchen_grid.html'
    url = 'https://www.amazon.es/gp/bestsellers/kitchen/ref=zg_bs_kitchen_sm'
    source.update(actual_source_url=url, page_urls=[url])
    page.update(page_url=url, evidence_path=str(path), evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    rows, _ = frontier.extract_page_navigation(page, source)
    assert any('/kitchen/3244779031/' in r['url'] and r['status'] == 'PENDING_SOURCE_REVIEW' for r in rows)
    assert any(r['status'] == 'IGNORED_ROOT_ALIAS' for r in rows)


def test_saved_favorites_page_two_produces_literal_page_three():
    page, source = page_source(6, 'loading/favorites_page2_reordered.html')
    provenance = json.loads((FIXTURES / 'loading/favorites_page2_reordered.provenance.json').read_text('utf8'))
    page['page_url'] = provenance['requested_url']; source['page_urls'].append(page['page_url'])
    rows, _ = frontier.extract_page_navigation(page, source)
    nexts = [r for r in rows if r['kind'] == 'PAGINATION_LINK']
    assert len(nexts) == 1 and 'page=3' in nexts[0]['url']
    assert nexts[0]['status'] == 'PENDING_PAGINATION_REVIEW'
    assert nexts[0]['url'] == urljoin(page['page_url'], nexts[0]['raw_href'])


def test_unsupported_favorites_themes_remain_unknown_and_haul_owns_navigation():
    page, source = page_source(4, 'F07_favorites_global_complete.html')
    rows, _ = frontier.extract_page_navigation(page, source)
    assert any('/cml-home?' in r['url'] for r in rows)
    assert all(r['status'] == 'UNVERIFIED_CONTEXT' for r in rows)
    page, source = page_source(7, 'F08_haul_root_complete.html')
    rows, _ = frontier.extract_page_navigation(page, source)
    assert any('i=bazaar' in r['url'] for r in rows)
    assert not any('pageAsin=' in r['url'] for r in rows)


def test_changed_saved_html_and_parent_binding_are_rejected(tmp_path):
    page, source = page_source()
    page = {**page, 'evidence_path': str(tmp_path / 'tampered.html')}
    Path(page['evidence_path']).write_text('changed href', encoding='utf8')
    with pytest.raises(ValueError, match='HTML_SHA_MISMATCH'):
        frontier.extract_page_navigation(page, source)
    page['source_id'] = 'foreign'
    with pytest.raises(ValueError, match='SOURCE_BINDING_MISMATCH'):
        frontier.extract_page_navigation(page, source)


def test_modified_reference_cannot_pass_real_dom_verifier():
    page, source = page_source()
    rows, _ = frontier.extract_page_navigation(page, source)
    ref = next(r for r in rows if r['status'] == 'PENDING_SOURCE_REVIEW')
    with pytest.raises(ValueError, match='EXACT_DISCOVERED_HREF'):
        verify_discovered_source({**source, 'actual_source_url': ref['url'] + '?invented=1', 'navigation_evidence': ref})
    with pytest.raises(ValueError, match='NOT_PRESENT_IN_DOM'):
        verify_discovered_source({**source, 'actual_source_url': ref['url'], 'navigation_evidence': {**ref, 'label_raw': 'forged'}})


def test_foreign_context_ads_footer_history_and_product_card_links_are_excluded(tmp_path):
    page, source = page_source()
    soup = BeautifulSoup('<html><body><div id="zg-left-col"></div></body></html>', 'lxml')
    owned = soup.select_one('#zg-left-col')
    for markup in ('<div data-ad-id="sponsor"><a href="/gp/bestsellers/tools/ad">Ad</a></div>',
                   '<footer><a href="/gp/bestsellers/tools/footer">Footer</a></footer>',
                   '<div id="rhf"><a href="/gp/bestsellers/tools/history">History</a></div>',
                   '<div data-asin="B000000001"><a href="/gp/bestsellers/tools/card">Card</a></div>',
                   '<a href="https://example.org/gp/bestsellers/tools">External</a>',
                   '<a href="/gp/new-releases/tools">Other ranking</a>'):
        owned.append(BeautifulSoup(markup, 'html.parser'))
    page = write_dom(tmp_path, page, soup)
    rows, excluded = frontier.extract_page_navigation(page, source)
    assert len(rows) == 1 and rows[0]['status'] == 'UNVERIFIED_CONTEXT'
    assert excluded['PRODUCT_AD_HISTORY_OR_FOOTER'] == 4
    assert excluded['EXTERNAL_OR_NON_HTTP_OR_FRAGMENT'] == 1


def test_exact_dedup_preserves_unknown_parameters_and_all_anchor_provenance(tmp_path):
    page, source = page_source()
    soup = BeautifulSoup('<div id="zg-left-col"><a href="/gp/bestsellers/tools?opaque=A%2FB">A</a>'
        '<a href="/gp/bestsellers/tools?opaque=A%2FB">B</a>'
        '<a href="/gp/bestsellers/tools?opaque=A%2FC">C</a></div>', 'lxml')
    page = write_dom(tmp_path, page, soup)
    result = frontier._inventory_from_verified(stage(page, source), {'path': 'test_only'})
    assert result['exact_url_count'] == 2
    assert len(result['entries'][0]['provenance']) == 2
    assert len({p['anchor_dom_index'] for p in result['entries'][0]['provenance']}) == 2
    assert result['entries'][0]['exact_url'].endswith('opaque=A%2FB')
    assert not result['sample_goal_met'] and not result['thirty_thousand_supply_demonstrated']
    assert result['capacity_estimate'] is None and result['new_network_requests'] == 0


def test_latest_capture_compares_instants_and_preserves_empty_unknown(tmp_path):
    page, source = page_source()
    earlier = {**page, 'observed_at': '2026-10-15T09:00:00+08:00'}
    current = {**page, 'observed_at': '2026-10-15T02:00:00Z', 'status': 'EXPLICIT_EMPTY'}
    value = stage(current, source); value['pages'] = [earlier, current]
    result = frontier._inventory_from_verified(value, {'path': 'test_only'})
    assert len(result['parent_pages']) == 1 and result['parent_pages'][0]['status'] == 'EXPLICIT_EMPTY'
    assert result['parent_pages'][0]['completeness_status'] == 'UNKNOWN'
    assert all(p['observed_at'] == current['observed_at'] for e in result['entries'] for p in e['provenance'])


def test_only_sealed_t2_boundary_can_write_inventory_and_each_export_is_new(tmp_path, monkeypatch):
    page, source = page_source(4, 'F07_favorites_global_complete.html')
    calls = []
    def verify(ref, expected):
        calls.append((ref, expected))
        if ref['sha256'] == 'bad':
            raise ValueError('V4_STAGE_REFERENCE_SHA_MISMATCH')
        return stage(page, source)
    monkeypatch.setattr(frontier, 'verify_stage_reference', verify)
    with pytest.raises(ValueError, match='REFERENCE_SHA_MISMATCH'):
        frontier.review_navigation({'path': 'test_only', 'sha256': 'bad'}, tmp_path)
    assert not list(tmp_path.iterdir())
    ref = {'path': 'test_only', 'sha256': 'test_only'}
    one = frontier.review_navigation(ref, tmp_path); two = frontier.review_navigation(ref, tmp_path)
    assert one['directory'] != two['directory'] and calls[-1] == (ref, 'T2')
    directory = Path(one['directory']); manifest = json.loads((directory / 'manifest.json').read_text('utf8'))
    assert not manifest['approved'] and not manifest['T3_execution_enabled']
    assert not (directory / 'pending_exact_urls.txt').read_text('utf8')
    assert all(hashlib.sha256((directory / f['name']).read_bytes()).hexdigest() == f['sha256'] for f in manifest['files'])


def test_cli_review_navigation_has_no_live_switch_and_only_calls_offline_api(tmp_path, monkeypatch):
    from amazon_es_bestseller.highspeed.v4_cli import main
    manifest = tmp_path / 'test_only.json'; manifest.write_text('{}', encoding='utf8')
    calls = []
    monkeypatch.setattr(frontier, 'review_navigation', lambda ref, out: calls.append((ref, out)) or {'new_network_requests': 0})
    result = main(['review-navigation', '--t2', str(manifest), '--out', str(tmp_path)])
    assert result['new_network_requests'] == 0 and len(calls) == 1
    with pytest.raises(SystemExit):
        main(['review-navigation', '--t2', str(manifest), '--out', str(tmp_path), '--live'])
