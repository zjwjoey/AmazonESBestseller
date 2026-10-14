"""Actual ACP carousel fragment ranks are distinct from grid card markup."""
from pathlib import Path
import hashlib,json
from bs4 import BeautifulSoup
import pytest
from amazon_es_bestseller.highspeed.adapters import _cards,parse_page
from amazon_es_bestseller.highspeed.ratings import validate_rating_fields

def fixture(label):
    path=Path('tests/fixtures/highspeed/real_v3_t1_'+label+'_native_carousel_acp.html').resolve()
    parent=path.with_name(path.stem+'_parent.html')
    proof=json.loads(path.with_suffix('.provenance.json').read_text(encoding='utf8'))
    assert hashlib.sha256(path.read_bytes()).hexdigest()==proof['fixture_sha256']
    assert hashlib.sha256(parent.read_bytes()).hexdigest()==proof['parent_fixture_sha256']
    assert proof['parent_fixture_sha256']==proof['ranking_context_binding']['page_evidence_sha256']
    source={'source_id':proof['source_id'],'source_type':proof['source_type'],'actual_source_url':proof['source_url'],
        'page_urls':[proof['source_url']],'parser_profile':'V3','research_category':None}
    return path,parent,proof,source

@pytest.mark.parametrize('label',['best','new'])
def test_actual_native_fragment_adds_six_exact_bound_ranked_cards(label):
    path,parent,proof,source=fixture(label);body=path.read_text(encoding='utf8')
    cards=_cards(BeautifulSoup(body,'lxml'),fragment=True)
    assert len(cards)==6
    response={'body':body,'evidence_path':str(path),'status_code':200,'observed_at':proof['observed_at'],
              'ranking_context_binding':proof['ranking_context_binding']}
    result=parse_page(parent.read_text(encoding='utf8'),source=source,run_id='offline_actual_response',
        page_url=proof['source_url'],page_number=1,observed_at=proof['observed_at'],evidence_path=str(parent),
        origin='OFFLINE_FIXTURE',acp_responses=[response])
    rows=[r for r in result['observations'] if r['representation']=='ACP_0']
    assert len(rows)==6 and [r['ranking_position'] for r in rows]==proof['expected_fragment_ranks']
    for row,card in zip(rows,cards):
        assert row['raw_product_url'] and row['product_title_raw']
        assert row['rank_context']==proof['ranking_context_binding']['ranking_context_url']
        assert row['evidence_sha256']==proof['fixture_sha256']
        assert row['observed_at']==row['rating_observed_at']==proof['observed_at']
        assert validate_rating_fields(row,card) is True
    assert result['page']['actual_count']>36
    assert result['page']['status']=='PARTIAL' and result['page']['page_complete'] is False

def test_native_fragment_without_exact_parent_binding_is_not_accepted():
    path,parent,proof,source=fixture('best')
    result=parse_page(parent.read_text(encoding='utf8'),source=source,run_id='offline_no_binding',
        page_url=proof['source_url'],page_number=1,observed_at=proof['observed_at'],evidence_path=str(parent),
        origin='OFFLINE_FIXTURE',acp_responses=[{'body':path.read_text(encoding='utf8'),'evidence_path':str(path),'status_code':200,'observed_at':proof['observed_at']}])
    assert not any(r['representation']=='ACP_0' for r in result['observations'])
    assert 'ACP_CAROUSEL_CONTEXT_UNVERIFIED' in result['page']['flags']
