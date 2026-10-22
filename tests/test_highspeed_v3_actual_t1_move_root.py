"""Actual T1 root with no cards or navigation cannot become a terminal success."""
import hashlib
import json
from pathlib import Path
from bs4 import BeautifulSoup
from amazon_es_bestseller.highspeed.adapters import parse_page
from amazon_es_bestseller.highspeed.runner import source_navigation_only, explicit_source_empty, empty_continuation_unavailable


def test_actual_move_root_without_child_navigation_or_native_contract_stays_unknown():
    path=Path('tests/fixtures/highspeed/real_v3_t1_movers_shakers_empty_unverified.html').resolve()
    provenance=json.loads(path.with_suffix('.provenance.json').read_text(encoding='utf8'))
    raw=path.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==provenance['fixture_sha256']==provenance['original_html_reference']['sha256']
    assert provenance['status_code']==200 and provenance['access_state']=='NORMAL'
    url=provenance['source_url']
    source={'source_id':'actual_move_root','source_type':'MOVERS_SHAKERS','actual_source_url':url,
            'page_urls':[url],'research_category':None,'parser_profile':'V3'}
    parsed=parse_page(raw.decode('utf8'),source=source,run_id='actual_move_offline_regression',
                      page_url=url,page_number=1,observed_at=provenance['original_captured_at'],
                      evidence_path=str(path),origin='OFFLINE_FIXTURE',http_status=200)
    page=parsed['page']
    assert page['source_type_verified'] is True and page['source_context_valid'] is True
    assert page['access_status']=='NORMAL' and page['status']=='FAILED'
    assert page['page_content_state']=='PARSER_EMPTY_UNVERIFIED'
    assert page['page_complete'] is False and page['actual_count']==0
    assert not parsed['observations'] and not parsed.get('acp_pending')
    soup=BeautifulSoup(raw,'lxml')
    right=soup.select_one('#zg-right-col');left=soup.select_one('#zg-left-col')
    assert right is not None and not right.find(True) and not right.get_text(strip=True)
    assert left is not None and 'Cualquier departamento' in left.get_text(' ',strip=True)
    assert not any('/gp/movers-and-shakers/' in a['href'] for a in left.select('a[href]'))
    assert not soup.select('[data-client-recs-list],[data-acp-path],[data-expected-count],[data-a-carousel-options]')
    assert source_navigation_only(source,url,parsed) is False
    assert explicit_source_empty(parsed) is False
    assert empty_continuation_unavailable(parsed,{'status_code':200,'access_state':'NORMAL'},
                                         'ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED',{'state':'NORMAL'}) is False
