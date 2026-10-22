from copy import deepcopy
import pytest

from amazon_es_bestseller.highspeed.authorization import authorize_request
from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page
from test_highspeed_v3_native_authorization import carousel
from test_highspeed_v3_actual_native_carousel_response import fixture


def v4_contract(tmp_path):
    endpoint, descriptor, source, auth = carousel(tmp_path)
    source['parser_profile'] = 'V4'
    auth['snapshot_contract_version'] = 4
    auth['sources'][source['source_id']]['parser_profile'] = 'V4'
    return endpoint, descriptor, source, auth


def test_v4_reuses_full_byte_owner_validator(tmp_path):
    endpoint, descriptor, source, auth = v4_contract(tmp_path)
    authorize_request(endpoint, source, auth, kind='ACP', descriptor=descriptor)
    descriptor['payload'] += ' '
    with pytest.raises(ValueError):
        authorize_request(endpoint, source, auth, kind='ACP', descriptor=descriptor)


def test_new_sources_cannot_borrow_ranking_native_grant(tmp_path):
    endpoint, descriptor, source, auth = v4_contract(tmp_path)
    source['source_type'] = 'DEALS'
    with pytest.raises(ValueError, match='NOT_APPROVED'):
        authorize_request(endpoint, source, auth, kind='ACP', descriptor=descriptor)


@pytest.mark.parametrize('label', ['best', 'new'])
def test_v4_real_native_fragment_preserves_source_and_fields(label):
    path, parent, proof, source = fixture(label)
    source.update(parser_profile='V4', source_subtype='BESTSELLER_SALES_RANKING' if label == 'best' else 'NEW_RELEASE_SALES_RANKING')
    result = parse_v4_page(parent.read_text(encoding='utf8'), source=source, page_url=proof['source_url'],
        run_id='v4_offline_native', observed_at=proof['observed_at'], evidence_path=str(parent),
        acp_responses=[{'body': path.read_text(encoding='utf8'), 'evidence_path': str(path),
                       'status_code': 200, 'observed_at': proof['observed_at'],
                       'ranking_context_binding': proof['ranking_context_binding']}])
    rows = [r for r in result['observations'] if r['representation'] == 'ACP_0']
    assert len(rows) == 6
    assert [r['ranking_position'] for r in rows] == proof['expected_fragment_ranks']
    assert all('rating_count_status' in r and 'current_price_raw' in r for r in rows)
    assert all(r['evidence_sha256'] == proof['fixture_sha256'] for r in rows)
    assert all(r['origin'] == 'OFFLINE_FIXTURE' for r in rows)
