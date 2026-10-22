"""Offline regressions for the fresh 20k candidate snapshot task."""
from copy import deepcopy
import hashlib
from pathlib import Path

from bs4 import BeautifulSoup
import pytest

from amazon_es_bestseller.highspeed.global_target import MAIN_PRIORITY_POLICY, POLICY, is_global_policy
from amazon_es_bestseller.highspeed.three_contract import validate_three_plan
from amazon_es_bestseller.highspeed.three_metrics import measure_three
from amazon_es_bestseller.highspeed.three_runner import V4ThreeSourceRunner
from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page
from amazon_es_bestseller.highspeed.v4_quality import audit_v4, build_candidates_v4
from amazon_es_bestseller.highspeed.v4_sources import source_for_url
from amazon_es_bestseller.highspeed.snapshot_metadata import CONTRACT
from test_three_source_contract import scope

FIXTURE=Path('tests/fixtures/highspeed/real_v4_t2_kitchen_grid.html')


def real_source():
    root=Path('tests/fixtures/highspeed_v4/F01_best_root_complete.html')
    return source_for_url('https://www.amazon.es/gp/bestsellers/kitchen/ref=zg_bs_kitchen_sm',
        source_type='BEST_SELLERS',source_subtype='BESTSELLER_SALES_RANKING',
        navigation_evidence={'raw_href':'/gp/bestsellers/kitchen/ref=zg_bs_kitchen_sm',
            'page_url':'https://www.amazon.es/gp/bestsellers','evidence_path':str(root),
            'evidence_sha256':hashlib.sha256(root.read_bytes()).hexdigest()})


def parse(html=None, path=FIXTURE, contract=CONTRACT):
    source=real_source()
    result=parse_v4_page(html if html is not None else path.read_bytes().decode('utf8'),
        source=source,page_url=source['actual_source_url'],run_id='TEST_ONLY',
        observed_at='2026-10-21T01:00:00Z',evidence_path=str(path),http_status=200,
        origin='OFFLINE_FIXTURE',snapshot_metadata_contract=contract)
    result['page'].update(run_id='TEST_ONLY',snapshot_id='TEST_ONLY')
    return source,result


def test_main_priority_is_explicit_without_changing_old_sealed_policy(tmp_path):
    p,_=scope(tmp_path)
    p.update(target_policy=deepcopy(MAIN_PRIORITY_POLICY),target_unique_links=20000,
             source_targets=deepcopy(MAIN_PRIORITY_POLICY['source_minima']),snapshot_metadata_contract=CONTRACT)
    validate_three_plan(p)
    p['source_targets']['CUSTOMER_FAVORITES']=2999
    with pytest.raises(ValueError):validate_three_plan(p)
    assert POLICY['source_minima']['CUSTOMER_FAVORITES']==5001
    bad=deepcopy(MAIN_PRIORITY_POLICY);bad['source_minima']['CUSTOMER_FAVORITES']=2999
    with pytest.raises(ValueError):is_global_policy(bad)


def test_metric_and_scheduler_use_three_thousand_supplemental_floor():
    m=measure_three([], 'TEST_ONLY',target_policy=MAIN_PRIORITY_POLICY)
    assert m['sources']['CUSTOMER_FAVORITES']['target']==3000
    assert m['global_target']==20000 and m['global_shortfall']==20000
    runner=V4ThreeSourceRunner.__new__(V4ThreeSourceRunner)
    runner._global_target_met=False
    runner._global_metrics=deepcopy(m)
    runner._global_metrics['sources']['CUSTOMER_FAVORITES']['target_met']=True
    plan={'target_policy':MAIN_PRIORITY_POLICY}
    assert not runner.should_collect_source(plan,{'source_type':'CUSTOMER_FAVORITES'})
    assert runner.should_collect_source(plan,{'source_type':'BEST_SELLERS'})
    assert runner.should_collect_source(plan,{'source_type':'NEW_RELEASES'})


def test_real_department_category_is_preserved_and_replays_in_central_review():
    source,result=parse()
    rows=result['observations']
    assert rows and all(r['amazon_category_l1']=='Hogar y cocina' for r in rows)
    assert rows[0]['category_evidence'][0]['evidence_sha256']==hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    audit=audit_v4([result['page']],rows,[source])
    assert audit['central_full_dom_review_passed'],audit['errors'][:2]
    candidate=build_candidates_v4(rows)[0]
    assert candidate['source_memberships'][0]['amazon_category_l1']=='Hogar y cocina'
    tampered=deepcopy(rows);tampered[0]['amazon_category_l1']='INVENTED'
    assert not audit_v4([result['page']],tampered,[source])['central_full_dom_review_passed']


def test_legacy_replay_does_not_silently_acquire_new_metadata():
    _,result=parse(contract=None)
    assert 'snapshot_metadata_contract' not in result['page']
    assert all(r['amazon_category_l1'] is None for r in result['observations'])


def test_one_asin_conflict_is_retained_without_terminating_other_cards(tmp_path):
    soup=BeautifulSoup(FIXTURE.read_text('utf8'),'lxml')
    from amazon_es_bestseller.highspeed.adapters import _cards
    cards=_cards(soup)
    assert len(cards)>1
    cards[0]['data-asin']='B000000000'
    path=tmp_path/'real_kitchen_conflict_regression.html'
    path.write_text(str(soup),encoding='utf8')
    source,result=parse(path=path)
    rows=result['observations']
    conflicts=[r for r in rows if r['identity_observation_status']=='CONFLICT']
    assert len(conflicts)==1
    assert conflicts[0]['snapshot_eligible'] is False
    assert len(rows)>1 and any(r['identity_observation_status']!='CONFLICT' for r in rows)
    assert result['page']['status']=='VERIFIED_WITH_PRODUCTS'
    audit=audit_v4([result['page']],rows,[source])
    assert audit['central_full_dom_review_passed'],audit['errors'][:2]


def test_missing_asin_opaque_link_is_a_candidate_without_identity_promotion():
    from amazon_es_bestseller.highspeed.snapshot_metadata import annotate_observation
    row={'raw_product_url':'https://www.amazon.es/dp/opaque-product-token','binding_doubts':[]}
    annotate_observation(row,BeautifulSoup(FIXTURE.read_bytes(),'lxml'),real_source(),real_source()['actual_source_url'],'TEST_ONLY')
    assert row['identity_observation_status']=='ABSENT'
    assert row['identity_status']=='UNCONFIRMED'
    assert row['strict_buyable_confirmed'] is False
    assert row.get('target_eligible') is not False
    assert row['snapshot_eligible'] is True
    assert row['amazon_category_l1']=='Hogar y cocina'


def test_favorites_theme_is_preserved_without_inventing_canonical_department():
    from test_three_source_navigation import context, FIX
    from amazon_es_bestseller.highspeed.three_navigation import discover_navigation
    from amazon_es_bestseller.highspeed.three_contract import source_from_navigation
    from amazon_es_bestseller.highspeed.v4_sources import SEEDS
    from amazon_es_bestseller.highspeed.snapshot_metadata import source_category
    page,parent=context(FIX/'highspeed_v4/F07_favorites_global_complete.html',source_for_url(SEEDS[4]['url']))
    reference=next(r for r in discover_navigation(page,parent) if r['label_raw']=='Hogar')
    child=source_from_navigation(reference,parent)
    result=source_category(BeautifulSoup('<main/>','lxml'),child,child['actual_source_url'],'TEST_ONLY')
    assert result['source_category_l1_raw']=='Hogar'
    assert result['source_category_status']=='FAVORITES_THEME_ONLY'
    assert result['amazon_category_l1'] is None


def test_twenty_thousand_union_cannot_hide_favorites_shortfall(monkeypatch):
    from amazon_es_bestseller.highspeed import three_metrics as module
    from test_three_source_metrics import real_rows
    rows=[]
    for kind in MAIN_PRIORITY_POLICY['source_minima']:
        r=deepcopy(real_rows()[0]);r['source_type']=kind;rows.append(r)
    sizes={'BEST_SELLERS':8500,'NEW_RELEASES':8500,'CUSTOMER_FAVORITES':3000}
    def groups(items):
        kind=items[0]['source_type']
        return [{'eligible_for_target':True,'target_review_group':f'TEST_ONLY_{kind}_{i}',
                 'normalized_product_url':f'TEST_ONLY_{kind}_{i}'} for i in range(sizes[kind])]
    monkeypatch.setattr(module,'build_candidates_v4',groups)
    m=measure_three(rows,'TEST_ONLY',target_policy=MAIN_PRIORITY_POLICY)
    assert m['target_met'] and m['F_global_conservative_review_groups']==20000
    sizes.update(BEST_SELLERS=8501,CUSTOMER_FAVORITES=2999)
    m=measure_three(rows,'TEST_ONLY',target_policy=MAIN_PRIORITY_POLICY)
    assert m['F_global_conservative_review_groups']==20000 and not m['target_met']
    assert m['sources']['CUSTOMER_FAVORITES']['shortfall']==1


def test_runner_persists_conflict_and_continues_all_three_sources(tmp_path,monkeypatch):
    from test_three_source_runner import execution_scope,Advancing
    from test_global_30000_contract import bind_clock
    from test_highspeed_v4_execution import NOW
    from amazon_es_bestseller.highspeed.store import RunStore
    p,a=execution_scope(tmp_path,monkeypatch)
    p.update(target_policy=deepcopy(MAIN_PRIORITY_POLICY),target_unique_links=20000,
        source_targets=deepcopy(MAIN_PRIORITY_POLICY['source_minima']),snapshot_metadata_contract=CONTRACT)
    for key in ('target_policy','target_unique_links','source_targets','snapshot_metadata_contract'):
        a[key]=deepcopy(p[key])
    a['reviewed_at']=NOW.isoformat()
    bind_clock(p,a,tmp_path)
    class TestOnlyTransport(Advancing):
        def fetch_page(self,url):
            response=super().fetch_page(url)
            soup=BeautifulSoup(response['text'],'lxml')
            header=soup.select_one('#nav-global-location-popover-link')
            if header is not None:header.clear()
            else:
                header=soup.new_tag('span',id='nav-global-location-popover-link')
                soup.body.insert(0,header)
            header.string='Enviar a Madrid 28001'
            if '/bestsellers' in url:
                from amazon_es_bestseller.highspeed.adapters import _cards
                _cards(soup)[0]['data-asin']='B000000000'
            response['text']=str(soup)
            return response
    store=RunStore(a['store_root']);clock=[NOW]
    try:
        transport=TestOnlyTransport(p,a,clock)
        result=V4ThreeSourceRunner(store,clock=lambda:clock[0],sleep=lambda _:None).run(p,authorization=a,transport=transport)
        assert len(transport.calls)==3
        rows=list(store.iter_observations(p['run_id']))
        assert any(r['identity_observation_status']=='CONFLICT' for r in rows)
        assert {r['source_type'] for r in rows}==set(MAIN_PRIORITY_POLICY['source_minima'])
        assert result['quality']['central_full_dom_review_passed'],result['quality']['errors'][:3]
        assert result['receipt_audit']['diagnostic_passed'],result['receipt_audit']['errors'][:3]
    finally:store.close()


def test_delivery_preflight_preserves_actual_failure_evidence_without_retry(tmp_path):
    import json
    from amazon_es_bestseller.highspeed.delivery_preflight import configure_delivery
    class OfflineSession:
        attempts=0
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def ensure_spain_delivery(self,code):
            self.attempts+=1
            raise ValueError('TEST_ONLY_CHALLENGE')
        def delivery_diagnostics(self):
            return '<main>TEST_ONLY_CHALLENGE</main>',{'http_status':403,'access_status':'BLOCKED','delivery_is_spain':None}
    out=tmp_path/'evidence'
    with pytest.raises(ValueError,match='DELIVERY_PREFLIGHT_STOPPED'):
        configure_delivery(tmp_path/'same_profile',out,session_factory=OfflineSession)
    report=json.loads((out/'delivery_gate.json').read_text('utf8'))
    assert report['http_status']==403 and report['status']=='BLOCKED'
    assert report['automatic_retry'] is False and report['candidate_collection_started'] is False
    assert report['evidence_sha256']==hashlib.sha256((out/'delivery_page.html').read_bytes()).hexdigest()


def test_current_prepare_uses_reviewed_scope_not_old_897_source_gate(tmp_path):
    from amazon_es_bestseller.highspeed.global_prepare import build_global_scope
    p,a=scope(tmp_path)
    sources=[next(s for s in p['sources'] if s['source_type']==k) for k in MAIN_PRIORITY_POLICY['source_minima']]
    limits={k:{'requests':4,'PAGE':2,'native':0} for k in MAIN_PRIORITY_POLICY['source_minima']}
    proposal={'kind':'V4_MAIN_PRIORITY_SNAPSHOT_SCOPE_PROPOSAL','sources':sources,
              'exact_page_URLs':[u for s in sources for u in s['page_urls']],
              'target_global_conservative_review_groups':20000,
              'source_minima':deepcopy(MAIN_PRIORITY_POLICY['source_minima']),
              'suggested_family_limits':limits}
    plan,approval=build_global_scope(p,a,proposal,run_id='TEST_ONLY_20K',created_at=p['campaign_created_at'],
        task_reference=p['task_reference'],target_profile='main-priority')
    validate_three_plan(plan)
    assert plan['target_unique_links']==20000 and plan['source_targets']['CUSTOMER_FAVORITES']==3000
    assert len(plan['sources'])==3 and plan['max_requests_per_run']==12
    assert not plan['live_enabled'] and not approval['approved']
    assert approval['snapshot_metadata_contract']==CONTRACT
    assert p['target_unique_links']==15003


def test_root_section_categories_are_per_card_instead_of_one_global_heading():
    root=Path('tests/fixtures/highspeed_v4/F01_best_root_complete.html')
    source=source_for_url('https://www.amazon.es/gp/bestsellers')
    result=parse_v4_page(root.read_bytes().decode('utf8'),source=source,page_url=source['actual_source_url'],
        run_id='TEST_ONLY',observed_at='2026-10-21T01:00:00Z',evidence_path=str(root),
        http_status=200,origin='OFFLINE_FIXTURE',snapshot_metadata_contract=CONTRACT)
    categories={r['amazon_category_l1'] for r in result['observations'] if r['amazon_category_l1']}
    assert 'Hogar y cocina' in categories
    assert len(categories)>1


def test_unknown_category_is_retained_but_cannot_fill_quantity_goal():
    from amazon_es_bestseller.highspeed.snapshot_metadata import annotate_observation
    row={'raw_product_url':'https://www.amazon.es/dp/opaque-product-token','binding_doubts':[]}
    annotate_observation(row,BeautifulSoup('<main/>','lxml'),real_source(),real_source()['actual_source_url'],'TEST_ONLY')
    assert row['snapshot_eligible'] is False
    assert row['snapshot_exclusion_reasons']==['CATEGORY_EVIDENCE_MISSING']
