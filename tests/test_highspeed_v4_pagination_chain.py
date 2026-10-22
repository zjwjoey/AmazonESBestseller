"""Exact Favorites pagination chains from immutable real T1/T2 DOM fixtures."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import pytest

from amazon_es_bestseller.highspeed.v4_authorization import validate_plan_v4
from amazon_es_bestseller.highspeed.v4_navigation import verify_pagination_reference
from amazon_es_bestseller.highspeed.v3_links import NORMALIZER_VERSION


FIXTURES = Path(__file__).parent / 'fixtures/highspeed_v4/loading'


def next_reference(path, parent):
    body = path.read_bytes()
    anchor = BeautifulSoup(body, 'lxml').select_one('a.s-pagination-next[href]')
    return {'page_url': parent, 'url': urljoin(parent, anchor['href']),
            'raw_href': anchor['href'], 'label_raw': anchor.get_text(' ', strip=True),
            'evidence_path': str(path), 'evidence_sha256': hashlib.sha256(body).hexdigest()}


def real_chain():
    proof = json.loads((FIXTURES / 'favorites_pagination_chain.provenance.json').read_text('utf8'))
    source = deepcopy(proof['source'])
    first = source['pagination_evidence'][0]
    first['evidence_path'] = str(FIXTURES / first['evidence_path'])
    second = next_reference(FIXTURES / 'favorites_chain_page2.html', first['url'])
    source['pagination_evidence'].append(second)
    source['page_urls'].append(second['url'])
    return source


def pending_plan(source):
    return {'mode': 'HIGH_SPEED_V4', 'snapshot_contract_version': 4, 'stage': 'T2',
            'normalizer_version': NORMALIZER_VERSION, 'origin': 'LIVE_CAPTURE',
            'max_requests_per_run': 50, 'max_requests_per_day': 50, 'max_pages_per_run': 20,
            'max_raw_observations_per_run': 20000, 'history_observation_limit': 40000,
            'target_unique_links': 2000, 'max_duration_hours': 1, 'request_pacing_seconds': 3,
            'parallelism': 1, 'category_cooldown_seconds': 0, 'global_access_gate': True,
            'detail_collection': False, 'translation': False, 'image_download': False,
            'action_filtering': False, 'live_enabled': False, 'sources': [source]}


def mutate_next(tmp_path, reference, new_href):
    """Adversarial test-only DOM; never represented as original Amazon evidence."""
    soup = BeautifulSoup(Path(reference['evidence_path']).read_bytes(), 'lxml')
    soup.select_one('a.s-pagination-next[href]')['href'] = new_href
    path = tmp_path / 'modified_test_only.html'
    path.write_text(str(soup), 'utf8')
    return next_reference(path, reference['page_url'])


def test_real_saved_root_page_two_page_three_chain_is_valid_offline():
    source = real_chain()
    plan = pending_plan(source)
    assert validate_plan_v4(plan) is plan
    assert plan['live_enabled'] is False
    assert source['pagination_evidence'][1]['url'].split('page=')[1].startswith('3&')
    assert source['page_urls'] == [source['actual_source_url'],
                                   *[r['url'] for r in source['pagination_evidence']]]


def test_real_saved_first_hop_remains_compatible():
    source = real_chain()
    first = source['pagination_evidence'][0]
    assert verify_pagination_reference(first, source) is first


def test_later_hop_cannot_be_verified_as_a_root_hop():
    source = real_chain()
    with pytest.raises(ValueError, match='EXACT_NEXT_HREF'):
        verify_pagination_reference(source['pagination_evidence'][1], source)


@pytest.mark.parametrize('failure', ['missing_first', 'reordered', 'duplicate', 'wrong_parent', 'extra_page'])
def test_chain_rejects_missing_reordered_duplicate_or_unproven_pages(failure):
    source = real_chain()
    if failure == 'missing_first':
        source['pagination_evidence'].pop(0)
        source['page_urls'].pop(1)
    elif failure == 'reordered':
        source['pagination_evidence'].reverse()
        source['page_urls'] = [source['actual_source_url'], *[r['url'] for r in source['pagination_evidence']]]
    elif failure == 'duplicate':
        source['pagination_evidence'].append(deepcopy(source['pagination_evidence'][-1]))
        source['page_urls'].append(source['page_urls'][-1])
    elif failure == 'wrong_parent':
        source['pagination_evidence'][1]['page_url'] = source['actual_source_url']
    else:
        source['page_urls'].append('https://www.amazon.es/s?k=unapproved')
    with pytest.raises(ValueError):
        validate_plan_v4(pending_plan(source))


@pytest.mark.parametrize('suffix', ['&unknown_filter=changed', '&k=new+search', '&rh=', '&page=2', '&srs=other'])
def test_even_literal_next_cannot_add_unknown_filter_search_or_duplicate_context(tmp_path, suffix):
    source = real_chain()
    first = source['pagination_evidence'][0]
    changed = mutate_next(tmp_path, first, first['raw_href'] + suffix)
    with pytest.raises(ValueError, match='SAME_CONTEXT'):
        verify_pagination_reference(changed, source)


@pytest.mark.parametrize('change', ['drop', 'alter', 'blank', 'encoded'])
def test_unknown_source_filter_must_not_change_or_disappear(tmp_path, change):
    source = real_chain()
    source['actual_source_url'] += '&future_filter=original'
    source['page_urls'][0] = source['actual_source_url']
    first = deepcopy(source['pagination_evidence'][0])
    first['page_url'] = source['actual_source_url']
    suffix = {'drop': '', 'alter': '&future_filter=other', 'blank': '&future_filter=',
              'encoded': '&future_filter=%6friginal'}[change]
    changed = mutate_next(tmp_path, first, first['raw_href'] + suffix)
    with pytest.raises(ValueError, match='SAME_CONTEXT'):
        verify_pagination_reference(changed, source)


def test_unknown_filter_can_be_preserved_byte_for_byte(tmp_path):
    source = real_chain()
    source['actual_source_url'] += '&future_filter=original'
    source['page_urls'][0] = source['actual_source_url']
    first = deepcopy(source['pagination_evidence'][0])
    first['page_url'] = source['actual_source_url']
    changed = mutate_next(tmp_path, first, first['raw_href'] + '&future_filter=original')
    assert verify_pagination_reference(changed, source) is changed


@pytest.mark.parametrize('page', ['1', '4', '03', '-1', 'invalid'])
def test_literal_next_must_advance_exactly_one_page(tmp_path, page):
    source = real_chain()
    first = source['pagination_evidence'][0]
    changed = mutate_next(tmp_path, first, first['raw_href'].replace('page=2', 'page=' + page))
    with pytest.raises(ValueError, match='SAME_CONTEXT'):
        verify_pagination_reference(changed, source)


def test_saved_dom_digest_and_literal_href_are_required():
    source = real_chain()
    reference = source['pagination_evidence'][0]
    changed = {**reference, 'evidence_sha256': '0' * 64}
    with pytest.raises(ValueError, match='SHA_MISMATCH'):
        verify_pagination_reference(changed, source)
    changed = {**reference, 'raw_href': reference['raw_href'] + '&ref_=invented'}
    with pytest.raises(ValueError, match='EXACT_NEXT_HREF'):
        verify_pagination_reference(changed, source)


def test_new_source_page_never_becomes_an_authorized_request():
    from amazon_es_bestseller.highspeed.v4_authorization import authorize_v4_request
    source = real_chain()
    scope = {k: source[k] for k in ('source_type', 'source_subtype', 'parser_profile')}
    scope['page_urls'] = source['page_urls'][:2]
    authorization = {'snapshot_contract_version': 4, 'sources': {source['source_id']: scope}}
    with pytest.raises(ValueError, match='UNREVIEWED_SOURCE_URL'):
        authorize_v4_request(source['page_urls'][2], source, authorization)
