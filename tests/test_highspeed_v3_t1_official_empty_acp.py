"""An actual official empty component is not an ancestor's pending ACP list."""
import hashlib,json
from pathlib import Path
import pytest
from bs4 import BeautifulSoup
from amazon_es_bestseller.highspeed.adapters import parse_page
from amazon_es_bestseller.highspeed.runner import explicit_source_empty

def actual_fixture(label):
    path=Path('tests/fixtures/highspeed/real_v3_t1_'+label+'_official_empty_acp_ancestor.html').resolve()
    proof=json.loads(path.with_suffix('.provenance.json').read_text(encoding='utf8'))
    assert hashlib.sha256(path.read_bytes()).hexdigest()==proof['fixture_sha256']
    source={'source_id':'actual_'+label,'source_type':proof['source_type'],'actual_source_url':proof['source_url'],
            'page_urls':[proof['source_url']],'parser_profile':'V3','research_category':None}
    return path,proof,source

def parse(path,proof,source,body=None):
    return parse_page(body or path.read_text(encoding='utf8'),source=source,run_id='offline_empty_regression',
        page_url=proof['source_url'],page_number=1,observed_at=proof['captured_at'],
        evidence_path=str(path),origin='OFFLINE_FIXTURE',http_status=200)

@pytest.mark.parametrize('label',['wish','gift'])
def test_actual_official_empty_has_no_ancestor_native_continuation(label):
    path,proof,source=actual_fixture(label)
    soup=BeautifulSoup(path.read_bytes(),'lxml');grid=soup.select_one('#zg-right-col .p13n-desktop-grid')
    assert json.loads(grid['data-client-recs-list'])==[]
    assert any(p.get('data-acp-path') for p in grid.parents)
    result=parse(path,proof,source);page=result['page']
    assert page['page_content_state']=='SOURCE_EMPTY'
    assert result['acp_pending']==[] and page['acp_required'] is False
    assert page['status']=='FAILED' and page['page_complete'] is False
    assert page['actual_count']==0 and result['observations']==[]
    assert page['expected_count'] is None and page['complete_evidence']==[]
    assert explicit_source_empty(result) is True

@pytest.mark.parametrize('mutation',['remove_message','remove_end','nonempty_metadata','product_href','expected_count','foreign_message'])
def test_ambiguous_empty_grid_cannot_suppress_native_pending(mutation):
    path,proof,source=actual_fixture('wish');soup=BeautifulSoup(path.read_bytes(),'lxml')
    grid=soup.select_one('#zg-right-col .p13n-desktop-grid')
    if mutation=='remove_message':grid.h4.decompose()
    elif mutation=='remove_end':grid.select_one('#endOfList').decompose()
    elif mutation=='nonempty_metadata':grid['data-client-recs-list']='["actual-opaque-id"]'
    elif mutation=='product_href':
        link=soup.new_tag('a',href='/dp/B077T9VSNZ');grid.append(link)
    elif mutation=='expected_count':grid['data-expected-count']='3'
    elif mutation=='foreign_message':grid.h4.string='Lo sentimos, no hay productos de los más regalados disponibles en esta categoría. Vuelve más tarde.'
    result=parse(path,proof,source,str(soup))
    assert result['page']['page_content_state']!='SOURCE_EMPTY'
    assert result['acp_pending']
