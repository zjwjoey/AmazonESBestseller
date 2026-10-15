"""Real saved DOM completeness gaps must not be hidden by historical success."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from functools import lru_cache
from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page
from amazon_es_bestseller.highspeed.v4_sources import source_for_url, SEEDS
from amazon_es_bestseller.highspeed.v4_coverage import loading_coverage, assess_capacity_readiness

ROOT=Path(__file__).parent/'fixtures/highspeed_v4'


@lru_cache(maxsize=1)
def _saved_pages():
    pages=[];sources=[]
    for proof in json.loads((ROOT/'manifest.json').read_text(encoding='utf8')):
        if proof['fixture']=='F06_deals_root_complete.html' or proof['url'] not in {s['url'] for s in SEEDS}:continue
        s=source_for_url(proof['url']);sources.append(s)
        p=ROOT/proof['fixture']
        result=parse_v4_page(p.read_text(encoding='utf8'),source=s,page_url=proof['url'],
            run_id='offline-completeness',observed_at=proof['observed_at'],evidence_path=str(p),http_status=200)
        pages.append({**result['page'],'run_id':'offline-completeness'})
    return pages,sources


def saved_pages():
    return deepcopy(_saved_pages())


def test_saved_rank_root_has_current_metadata_gaps_not_global_complete():
    pages,sources=saved_pages()
    result=loading_coverage(pages,sources)
    assert result['current_finite_components']>0
    assert result['current_missing_metadata_slots']>0
    assert not result['current_finite_components_complete']
    assert result['global_source_completeness']=='UNKNOWN'
    assert all(c['is_current_capture'] and c['capture_sha256'] for c in result['finite_component_coverage'])


@pytest.mark.parametrize('bad',['none','restricted','unverified','missing','gap','qa','unsupported_exception'])
def test_explicit_empty_acceptance_does_not_hide_unknown_or_finite_gap(bad):
    pages,sources=saved_pages()
    flash=next(p for p in pages if p['source_subtype']=='LIGHTNING_DEAL_FILTER')
    s=next(s for s in sources if s['source_subtype']=='LIGHTNING_DEAL_FILTER')
    path=ROOT/'loading/lightning_explicit_empty.html'
    empty=parse_v4_page(path.read_text(encoding='utf8'),source=s,page_url=s['actual_source_url'],
        run_id='offline-completeness',observed_at=flash['observed_at'],evidence_path=str(path),http_status=200)['page']
    pages[pages.index(flash)]=empty
    loading={'all_loading_capabilities_observed':True,'current_finite_components_complete':True}
    accepted=[s['actual_source_url']]
    if bad=='restricted':empty['access_status']='CHALLENGE'
    if bad=='unverified':empty['status']='UNVERIFIED'
    if bad=='missing':pages.remove(empty)
    if bad=='gap':loading['current_finite_components_complete']=False
    if bad=='unsupported_exception':
        with pytest.raises(ValueError):assess_capacity_readiness(pages,sources,loading,central_dom_review_passed=True,accepted_empty_urls=[sources[0]['actual_source_url']])
        return
    r=assess_capacity_readiness(pages,sources,loading,central_dom_review_passed=bad!='qa',accepted_empty_urls=accepted)
    assert r['ready_for_T2_plan']==(bad=='none')
    assert r['capacity_verified'] is False and r['T2_live_authorized'] is False
