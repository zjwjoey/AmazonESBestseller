"""Real 2026-10-15 page-two DOM; transports and approvals are TEST_ONLY."""
from copy import deepcopy
from pathlib import Path
import hashlib
import pytest
from test_three_source_runner import execution_scope,Advancing
from test_highspeed_v4_execution import NOW
from amazon_es_bestseller.highspeed.three_navigation import discover_navigation
from amazon_es_bestseller.highspeed.three_contract import source_from_navigation
from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page,VERSION
from amazon_es_bestseller.highspeed.three_runner import V4ThreeSourceRunner
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.multisource.storage import digest

F=Path(__file__).parent/'fixtures/highspeed_v4/loading/real_kitchen_page2_20261015.html'
GRID=Path(__file__).parent/'fixtures/highspeed/real_v4_t2_kitchen_grid.html'


def paginated_scope(tmp_path,monkeypatch):
    p,a=execution_scope(tmp_path,monkeypatch);root=p['sources'][0]
    parent=Path(a['sources'][root['source_id']]['evidence']['path'])
    def frame(s,path):return {'source_id':s['source_id'],'source_type':s['source_type'],
        'source_subtype':s['source_subtype'],'page_url':s['actual_source_url'],
        'evidence_path':str(path),'evidence_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'observed_at':'2026-10-14T15:00:00Z','status':'VERIFIED_WITH_PRODUCTS','http_status':200,'access_status':'NORMAL'}
    ref=next(r for r in discover_navigation(frame(root,parent),root) if '/kitchen/' in r['url'])
    s=source_from_navigation(ref,root)
    next_ref=next(r for r in discover_navigation(frame(s,GRID),s) if r['relationship']=='NEXT_PAGE')
    s.update(page_urls=[s['actual_source_url'],next_ref['url']],pagination_evidence=[next_ref])
    assert next_ref['url']=='https://www.amazon.es/gp/bestsellers/kitchen/ref=zg_bs_pg_2_kitchen?ie=UTF8&pg=2'
    p['sources'][0]=s
    auth_scope=deepcopy(a['sources'].pop(root['source_id']))
    auth_scope.update(source_type=s['source_type'],source_subtype=s['source_subtype'],
        page_urls=s['page_urls'],parser_profile=s['parser_profile'],allow_native_acp=False,
        evidence={'path':str(parent),'sha256':hashlib.sha256(parent.read_bytes()).hexdigest(),'reviewed':True})
    a['sources'][s['source_id']]=auth_scope
    p['source_budgets']['BEST_SELLERS']={'requests':2,'PAGE':2,'native':0}
    p.update(max_requests_per_run=4,max_requests_per_day=4,max_pages_per_run=4)
    a.update(source_budgets=deepcopy(p['source_budgets']),max_requests_per_run=4,max_requests_per_day=4,max_pages_per_run=4)
    a['plan_sha256']=digest(p)
    return p,a,s


def parse(s,version=None):
    return parse_v4_page(F.read_text('utf8'),source=s,page_url=s['page_urls'][1],run_id='TEST_ONLY',
        observed_at='2026-10-15T10:44:00.910945Z',evidence_path=str(F),http_status=200,
        origin='OFFLINE_FIXTURE',page_number=2,parser_profile_version=version)


def test_real_owned_next_keeps_source_binding_and_ranks_51_to_80(tmp_path,monkeypatch):
    p,a,s=paginated_scope(tmp_path,monkeypatch);v=parse(s)
    assert v['page']['status']=='VERIFIED_WITH_PRODUCTS'
    assert len(v['observations'])==30
    assert {r['ranking_position'] for r in v['observations']}==set(range(51,81))
    assert v['page']['legacy_page']['source_context_valid'] is True
    assert len(v['acp_pending'])==1
    assert len(v['acp_pending'][0]['metadata']['entries'])==50
    assert all(r['source_id']==s['source_id'] and r['source_url']==s['actual_source_url'] for r in v['observations'])


def test_old_parser_version_replays_the_original_failed_page_without_rewriting_it(tmp_path,monkeypatch):
    p,a,s=paginated_scope(tmp_path,monkeypatch);v=parse(s,VERSION)
    assert v['page']['status']=='NAVIGATION_ONLY' and not v['observations']
    assert v['page']['legacy_page']['source_context_valid'] is False


def test_page_two_cannot_gain_context_without_exact_owned_next_proof(tmp_path,monkeypatch):
    p,a,s=paginated_scope(tmp_path,monkeypatch)
    s['pagination_evidence'][0]['raw_href']+='&TEST_ONLY_unapproved_filter=1'
    with pytest.raises(ValueError):parse(s)


def test_two_page_execution_uses_source_authorization_not_hashed_page_key(tmp_path,monkeypatch):
    p,a,s=paginated_scope(tmp_path,monkeypatch);clock=[NOW]
    class Pages(Advancing):
        def fetch_page(self,url):
            if url in s['page_urls']:
                from datetime import timedelta
                self.calls.append(url);self.clock[0]+=timedelta(seconds=5)
                return {'status_code':200,'url':url,'text':(GRID if url==s['actual_source_url'] else F).read_text('utf8')}
            return super().fetch_page(url)
    store=RunStore(a['store_root'])
    try:
        t=Pages(p,a,clock);result=V4ThreeSourceRunner(store,clock=lambda:clock[0],sleep=lambda n:None).run(p,authorization=a,transport=t)
        assert len(t.calls)==4
        page=next(x for x in store.pages(p['run_id']) if x['page_url']==s['page_urls'][1])
        assert page['page_key']!=s['source_id'] and page['source_id']==s['source_id']
        assert page['actual_card_count']==30
        assert result['quality']['central_full_dom_review_passed'],result['quality']['errors']
        assert result['receipt_audit']['diagnostic_passed'],result['receipt_audit']['errors']
        events=[e for e in store.events(p['run_id']) if e['kind']=='V4_DISCOVERED_NAVIGATION']
        assert all(e['source_id'] in a['sources'] for e in events)
    finally:store.close()
